"""Monkey-patch Hermes' `tools.async_delegation.dispatch_async_delegation` so
`delegate_task(background=True)` routes to a named, persistent AgentMint
subagent.

The LLM picks the target subagent in one of two ways:

1. **Default routing** — operator sets `default_agent_name="..."` at install
   time; every background dispatch lands in that one subagent.

2. **Per-call routing via the `toolsets` argument** (workaround) — the LLM
   includes `"agentmint-<subagent-name>"` in the `toolsets` list:

       delegate_task(background=True, goal="Review the diff",
                     toolsets=["terminal", "file", "agentmint-pr-reviewer"])

   The adapter parses the `agentmint-*` entry, routes to that subagent
   (NOT the default), and strips the entry from the toolsets list before
   the prompt is composed. This is an explicit hack because Hermes'
   `delegate_task` has no native dispatcher-target parameter. We've
   submitted an upstream proposal — see docs/hermes-feature-request.md.

If neither default nor toolset routing yields a name, the patch falls
through to Hermes-native `delegate_task` (no AgentMint involvement).

Completion delivery: polling against AgentMint's `agent.run.status`
endpoint (Bearer-only, free). No public HTTPS endpoint, no webhook secret,
no HTTP route to register.
"""
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from .dispatcher import AgentMintDispatcher

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "timeout"})

# Matches `agentmint-<name>` where <name> follows AgentMint's
# AGENT_NAME_RE on the server side: lowercase alphanumeric, dash,
# underscore; 1-40 chars; must start with [a-z0-9].
_AGENTMINT_TOOLSET_RE = re.compile(r"^agentmint-([a-z0-9][a-z0-9_-]{0,39})$")


def install_delegate_task_wrapper(
    dispatcher: AgentMintDispatcher,
    default_agent_name: str | None = None,
    poll_interval: float = 5.0,
) -> Callable[[], None]:
    """Patch Hermes' async-delegation rail to route through AgentMint.

    Must be called ONCE at gateway startup, BEFORE any
    `delegate_task(background=True)` call. Returns a callable that
    reverses the patch (useful in tests / shutdown).

    Parameters
    ----------
    dispatcher : AgentMintDispatcher
        Pre-built dispatcher with auth attached.
    default_agent_name : str | None
        Fallback subagent name used when the LLM doesn't include an
        `agentmint-<name>` entry in the `toolsets` argument. If both
        the LLM-provided routing and `default_agent_name` are absent,
        the patch falls through to Hermes-native delegate_task.
    poll_interval : float
        Seconds between `agent.run.status` polls (default 5.0). Polling
        thread uses exponential backoff on errors up to 60s.
    """
    try:
        import tools.async_delegation as _ad
    except ImportError as e:
        raise RuntimeError(
            "Hermes module 'tools.async_delegation' not importable. "
            "Run install_delegate_task_wrapper() inside the same Python "
            "environment + process as Hermes' gateway."
        ) from e

    original = _ad.dispatch_async_delegation

    def patched(**kwargs: Any) -> dict:
        goal = kwargs.get("goal", "")
        context = kwargs.get("context")
        role = kwargs.get("role")
        model = kwargs.get("model")
        session_key = kwargs.get("session_key", "")
        toolsets = kwargs.get("toolsets")

        name_override, scrubbed_toolsets = _extract_target_from_toolsets(toolsets)
        target_name = name_override or default_agent_name
        if not target_name:
            # No routing target — let Hermes handle natively.
            return original(**kwargs)

        if name_override:
            logger.info(
                "agentmint-hermes: toolset routing -> %s (workaround; "
                "track upstream proposal in docs/hermes-feature-request.md)",
                name_override,
            )

        try:
            return _dispatch_persistent(
                dispatcher=dispatcher,
                agent_name=target_name,
                goal=goal,
                context=context,
                toolsets=scrubbed_toolsets,
                role=role,
                model=model,
                session_key=session_key,
                poll_interval=poll_interval,
            )
        except Exception:
            logger.exception(
                "agentmint patched dispatch failed — falling back to Hermes-native"
            )
            return original(**kwargs)

    _ad.dispatch_async_delegation = patched
    logger.info(
        "agentmint-hermes: installed delegate_task wrapper "
        "(default_agent=%s, poll_interval=%.1fs, toolset_routing=enabled)",
        default_agent_name or "<none>", poll_interval,
    )

    def uninstall() -> None:
        _ad.dispatch_async_delegation = original

    return uninstall


def _extract_target_from_toolsets(
    toolsets: list[str] | None,
) -> tuple[str | None, list[str] | None]:
    """Parse `toolsets` for an `agentmint-<name>` routing directive.

    Returns `(agent_name | None, scrubbed_toolsets | None)`. First match
    wins; any additional `agentmint-*` entries are logged + stripped.
    Non-string entries are passed through untouched. If `toolsets` is None
    or empty, returns `(None, toolsets)` without allocating.
    """
    if not toolsets:
        return None, toolsets

    name: str | None = None
    out: list[str] = []
    for t in toolsets:
        if not isinstance(t, str):
            out.append(t)
            continue
        m = _AGENTMINT_TOOLSET_RE.match(t)
        if m is None:
            out.append(t)
            continue
        if name is None:
            name = m.group(1)
        else:
            logger.warning(
                "agentmint-hermes: multiple agentmint-* toolsets in one call; "
                "using first (%s), ignoring %s",
                name, t,
            )
        # Strip both first-match and subsequent duplicates from scrubbed list.
    return name, out


def _dispatch_persistent(
    *,
    dispatcher: AgentMintDispatcher,
    agent_name: str,
    goal: str,
    context: str | None,
    toolsets: list[str] | None,
    role: str | None,
    model: str | None,
    session_key: str,
    poll_interval: float,
) -> dict:
    result = dispatcher.dispatch(
        agent_name=agent_name,
        goal=goal,
        context=context,
        toolsets=toolsets,
        role=role or "leaf",
        async_=True,
        hermes_context={"session_key": session_key, "model": model},
    )
    run_id = result.run_id or result.delegation_id
    if not run_id:
        raise RuntimeError("AgentMint async dispatch returned no run_id")

    _spawn_poller(
        dispatcher=dispatcher,
        run_id=run_id,
        goal=goal,
        context=context,
        session_key=session_key,
        poll_interval=poll_interval,
    )
    return {
        "status": "dispatched",
        "delegation_id": run_id,
        "goal": goal,
        "mode": "background",
        "source": "agentmint",
        "agent_name": agent_name,
    }


def _spawn_poller(
    *,
    dispatcher: AgentMintDispatcher,
    run_id: str,
    goal: str,
    context: str | None,
    session_key: str,
    poll_interval: float,
) -> threading.Thread:
    """Background daemon thread: polls `agent.run.status` until terminal,
    then pushes a Hermes async_delegation completion event onto Hermes'
    completion_queue.

    Returns the thread (mostly for tests). Exits on terminal status or
    after a hard cap of 30 minutes — the AgentMint run's own 30-minute
    server-side TTL means the record disappears anyway past that point.
    """
    HARD_CAP_SECONDS = 30 * 60

    # Capture Hermes' completion hooks AT SPAWN TIME so a long-running
    # poller doesn't push into a newer module reference if
    # `tools.async_delegation` is rebound (e.g. during tests).
    try:
        from tools.async_delegation import (
            _push_completion_event as _captured_push,
        )
    except Exception:
        _captured_push = None
    try:
        from hermes.gateway.process_registry import (
            completion_queue as _captured_queue,
        )
    except Exception:
        _captured_queue = None

    def push_completion(status: str, payload: dict) -> None:
        if _captured_push is not None:
            try:
                _captured_push(
                    delegation_id=run_id,
                    status=status,
                    result=payload,
                )
                return
            except Exception:
                logger.warning(
                    "agentmint-hermes: _push_completion_event call failed; "
                    "falling back to direct queue.put"
                )
        if _captured_queue is not None:
            try:
                _captured_queue.put({
                    "type": "async_delegation",
                    "delegation_id": run_id,
                    "status": status,
                    "result": payload,
                    "task_source": {
                        "goal": goal,
                        "context": context,
                        "session_key": session_key,
                        "source": "agentmint",
                    },
                })
                return
            except Exception:
                pass
        logger.exception(
            "agentmint-hermes: completion delivery failed for %s; "
            "Hermes will not re-inject this result",
            run_id,
        )

    def loop() -> None:
        backoff = poll_interval
        started = time.monotonic()
        while True:
            time.sleep(backoff)
            if time.monotonic() - started > HARD_CAP_SECONDS:
                logger.warning(
                    "agentmint-hermes: poller for %s exceeded 30-minute cap; "
                    "emitting timeout completion", run_id,
                )
                push_completion("timeout", {"task_source": {
                    "goal": goal, "context": context, "session_key": session_key,
                }})
                return
            try:
                resp = dispatcher.run_status(run_id)
                status = (resp or {}).get("status", "pending")
                if status in _TERMINAL_STATUSES:
                    push_completion(status, {
                        "billed_usdc": (resp or {}).get("billed_usdc"),
                        "completed_at": (resp or {}).get("completed_at"),
                        "task_source": {
                            "goal": goal,
                            "context": context,
                            "session_key": session_key,
                        },
                    })
                    return
                # Reset backoff on a successful poll (status still pending).
                backoff = poll_interval
            except Exception:
                logger.exception(
                    "agentmint-hermes: poller iteration failed for %s; "
                    "backing off", run_id,
                )
                backoff = min(backoff * 1.5, 60.0)

    t = threading.Thread(
        target=loop,
        daemon=True,
        name=f"agentmint-poll-{run_id[:12]}",
    )
    t.start()
    return t
