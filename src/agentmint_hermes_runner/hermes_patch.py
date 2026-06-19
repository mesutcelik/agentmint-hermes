"""Monkey-patch Hermes' `tools.async_delegation.dispatch_async_delegation` so
every `delegate_task(background=True, single-task)` routes through an
AgentMint cloud subagent.

Two modes:

- **ephemeral** (default): per-call, the adapter mints a fresh subagent with
  a random UUID name, runs it, then deletes it on completion. Matches
  Hermes-native delegate_task semantics (stateless subagent per call) but
  runs on isolated AgentMint sandboxes with independent credentials.
- **persistent**: every call routes to the same named subagent
  (`default_agent_name`). Its `/workspace/MEMORY.md` accumulates across
  delegations — use for one long-lived specialist.

Mode is auto-detected when not specified: presence of `default_agent_name`
implies persistent; absence implies ephemeral.

Sync `delegate_task` is untouched (Hermes-native fan-out / batch behaviour
preserved). Multi-task `background=True` is rejected upstream in Hermes
itself, so we never see it.

Completion delivery: polling against AgentMint's `agent.run.status`
endpoint (Bearer-only, free). No public HTTPS endpoint, no webhook secret,
no HTTP route to register.
"""
import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from .dispatcher import AgentMintDispatcher

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "timeout"})

_VALID_MODES = frozenset({"ephemeral", "persistent"})


def install_delegate_task_wrapper(
    dispatcher: AgentMintDispatcher,
    default_agent_name: str | None = None,
    mode: str | None = None,
    ephemeral_harness: str = "opencode",
    ephemeral_model: str = "openrouter/fusion",
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
        Persistent mode only — name of the pre-minted AgentMint subagent
        every background delegation routes to. The subagent's
        `/workspace/MEMORY.md` accumulates context across all delegations.
        Omit to use ephemeral mode.
    mode : "ephemeral" | "persistent" | None
        Explicit mode selection. If None, inferred from `default_agent_name`
        (set → persistent; unset → ephemeral).
    ephemeral_harness : str
        Harness used for ephemeral subagents (default `"opencode"`).
    ephemeral_model : str
        Model used for ephemeral subagents (default `"openrouter/fusion"`).
    poll_interval : float
        Seconds between `agent.run.status` polls (default 5.0). The
        polling thread uses exponential backoff on errors up to 60s.
    """
    if mode is None:
        mode = "persistent" if default_agent_name else "ephemeral"
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}, got {mode!r}")
    if mode == "persistent" and not default_agent_name:
        raise ValueError("mode='persistent' requires default_agent_name")

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
        toolsets = kwargs.get("toolsets")
        role = kwargs.get("role")
        model = kwargs.get("model")
        session_key = kwargs.get("session_key", "")

        try:
            if mode == "persistent":
                return _dispatch_persistent(
                    dispatcher=dispatcher,
                    agent_name=default_agent_name,
                    goal=goal,
                    context=context,
                    toolsets=toolsets,
                    role=role,
                    model=model,
                    session_key=session_key,
                    poll_interval=poll_interval,
                )
            return _dispatch_ephemeral(
                dispatcher=dispatcher,
                goal=goal,
                context=context,
                toolsets=toolsets,
                role=role,
                model=model,
                session_key=session_key,
                ephemeral_harness=ephemeral_harness,
                ephemeral_model=ephemeral_model,
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
        "(mode=%s, default_agent=%s, poll_interval=%.1fs)",
        mode, default_agent_name or "<none>", poll_interval,
    )

    def uninstall() -> None:
        _ad.dispatch_async_delegation = original

    return uninstall


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
    }


def _dispatch_ephemeral(
    *,
    dispatcher: AgentMintDispatcher,
    goal: str,
    context: str | None,
    toolsets: list[str] | None,
    role: str | None,
    model: str | None,
    session_key: str,
    ephemeral_harness: str,
    ephemeral_model: str,
    poll_interval: float,
) -> dict:
    """Per-call lifecycle: create → dispatch → poll → delete."""
    auto_name = f"ephem-{uuid.uuid4().hex[:12]}"

    dispatcher.create(
        name=auto_name,
        harness=ephemeral_harness,
        model=ephemeral_model,
    )
    try:
        result = dispatcher.dispatch(
            agent_name=auto_name,
            goal=goal,
            context=context,
            toolsets=toolsets,
            role=role or "leaf",
            async_=True,
            hermes_context={
                "session_key": session_key,
                "model": model,
                "ephemeral": True,
            },
        )
    except Exception:
        # Dispatch failed AFTER create succeeded — best-effort cleanup.
        try:
            dispatcher.delete(auto_name)
        except Exception:
            logger.warning(
                "agentmint-hermes: failed to clean up orphaned ephemeral "
                "subagent %s after dispatch error", auto_name,
            )
        raise

    run_id = result.run_id or result.delegation_id
    if not run_id:
        # Cleanup before re-raising
        try:
            dispatcher.delete(auto_name)
        except Exception:
            pass
        raise RuntimeError("AgentMint async dispatch returned no run_id")

    _spawn_poller(
        dispatcher=dispatcher,
        run_id=run_id,
        goal=goal,
        context=context,
        session_key=session_key,
        poll_interval=poll_interval,
        cleanup_agent_name=auto_name,
    )
    return {
        "status": "dispatched",
        "delegation_id": run_id,
        "goal": goal,
        "mode": "background",
        "source": "agentmint",
        "ephemeral_agent": auto_name,
    }


def _spawn_poller(
    *,
    dispatcher: AgentMintDispatcher,
    run_id: str,
    goal: str,
    context: str | None,
    session_key: str,
    poll_interval: float,
    cleanup_agent_name: str | None = None,
) -> threading.Thread:
    """Background daemon thread: polls `agent.run.status` until terminal,
    then pushes a Hermes async_delegation completion event onto Hermes'
    completion_queue.

    If `cleanup_agent_name` is set (ephemeral mode), also calls
    `dispatcher.delete(cleanup_agent_name)` AFTER pushing the completion
    event. Delete failures are logged but never raised.

    Returns the thread (mostly for tests). Exits on terminal status or
    after a hard cap of 30 minutes — the AgentMint run's own 30-minute
    server-side TTL means the record disappears anyway past that point.
    """
    HARD_CAP_SECONDS = 30 * 60

    # Capture Hermes' completion hooks AT SPAWN TIME (not per-push). This
    # prevents a long-running poller from accidentally pushing into a
    # newer module reference if `tools.async_delegation` is rebound at
    # runtime (e.g. during tests with monkeypatch fixtures, or if Hermes
    # hot-reloads modules — neither is supposed to happen, but it makes
    # the poller robust to it).
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

    def cleanup() -> None:
        if not cleanup_agent_name:
            return
        try:
            dispatcher.delete(cleanup_agent_name)
        except Exception:
            logger.warning(
                "agentmint-hermes: failed to delete ephemeral subagent %s "
                "after completion (server-side TTL will reap)",
                cleanup_agent_name,
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
                cleanup()
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
                    cleanup()
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
