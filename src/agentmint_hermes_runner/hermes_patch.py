"""Monkey-patch Hermes' `tools.async_delegation.dispatch_async_delegation` so
every `delegate_task(background=True, single-task)` routes to a named,
persistent AgentMint subagent.

Sync `delegate_task` is untouched (Hermes-native fan-out / batch behaviour
preserved). Multi-task `background=True` is rejected upstream in Hermes
itself, so we never see it.

Completion is delivered exclusively via **polling** against AgentMint's
`agent.run.status` endpoint (Bearer-only, free). No public HTTPS endpoint,
no webhook secret, no HTTP route to register.
"""
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .dispatcher import AgentMintDispatcher

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "timeout"})


def install_delegate_task_wrapper(
    dispatcher: AgentMintDispatcher,
    default_agent_name: str,
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
    default_agent_name : str
        Name of the pre-minted AgentMint subagent every background
        delegation routes to. The subagent's persistent `/workspace`
        accumulates context across all delegations.
    poll_interval : float
        Seconds between `agent.run.status` polls (default 5.0). The
        polling thread uses exponential backoff on errors up to 60s.
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
        toolsets = kwargs.get("toolsets")
        role = kwargs.get("role")
        model = kwargs.get("model")
        session_key = kwargs.get("session_key", "")

        try:
            result = dispatcher.dispatch(
                agent_name=default_agent_name,
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
        except Exception:
            logger.exception(
                "agentmint patched dispatch failed — falling back to Hermes-native"
            )
            return original(**kwargs)

    _ad.dispatch_async_delegation = patched
    logger.info(
        "agentmint-hermes: installed delegate_task wrapper "
        "(default_agent=%s, poll_interval=%.1fs)",
        default_agent_name, poll_interval,
    )

    def uninstall() -> None:
        _ad.dispatch_async_delegation = original

    return uninstall


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

    def push_completion(status: str, payload: dict) -> None:
        try:
            from tools.async_delegation import _push_completion_event
            _push_completion_event(
                delegation_id=run_id,
                status=status,
                result=payload,
            )
            return
        except Exception:
            logger.warning(
                "agentmint-hermes: _push_completion_event unavailable; "
                "falling back to direct queue.put"
            )
        try:
            from hermes.gateway.process_registry import completion_queue
            completion_queue.put({
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
        except Exception:
            logger.exception(
                "agentmint-hermes: completion_queue push failed; "
                "completion will not re-inject"
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
