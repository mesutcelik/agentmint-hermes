from __future__ import annotations

import threading
from concurrent.futures import CancelledError, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from .auth.base import Auth
from .client import Client
from .exceptions import DispatchInterrupted, DispatchTimeout
from .models import AgentRecord, DispatchResult, Task
from .translation import compose_prompt

CHILD_TIMEOUT_FLOOR = 30.0  # Hermes parity: floor 30s when timeout is enabled


class AgentMintDispatcher:
    """High-level wrapper around AgentMint's /a2a JSON-RPC surface.

    Translates Hermes' `delegate_task` ergonomics (goal + context + toolsets
    + role) into AgentMint's single-`prompt` agent.run model. The
    persistent-sandbox semantics are NOT abstracted — that's the value: a
    Hermes operator calls `dispatch()` and the work lands in a sandbox
    whose /workspace survives across calls.
    """

    def __init__(
        self,
        auth: Auth,
        endpoint: str = "https://api.agentmint.store/a2a",
    ):
        self.client = Client(endpoint, auth)

    # ----- direct method passthroughs ------------------------------------

    def create(self, name: str, **kwargs: Any) -> AgentRecord:
        params = {"name": name, **kwargs}
        result = self.client.call("agent.create", params)
        return AgentRecord.from_dict(result or {})

    def get(self, agent_name: str) -> AgentRecord:
        result = self.client.call("agent.get", {"name": agent_name})
        return AgentRecord.from_dict(result or {})

    def list(self, limit: int = 50, offset: int = 0) -> list[AgentRecord]:
        result = self.client.call("agent.list", {"limit": limit, "offset": offset})
        agents = (result or {}).get("agents", [])
        return [AgentRecord.from_dict(a) for a in agents]

    def delete(self, agent_name: str) -> None:
        self.client.call("agent.delete", {"name": agent_name})

    def cancel(self, agent_name: str) -> None:
        self.client.call("agent.cancel", {"name": agent_name})

    def run_status(self, run_id: str) -> dict[str, Any]:
        """Read the status of an async dispatch by `run_id` (returned from
        `dispatch(async_=True)`).

        Bearer-only / Stripe-Link-only on the server side (v0.7.0+). Free.
        Used by the polling delivery mode in `hermes_patch`.
        """
        return self.client.call("agent.run.status", {"run_id": run_id})

    # ----- single dispatch -----------------------------------------------

    def dispatch(
        self,
        agent_name: str,
        goal: str,
        context: str | None = None,
        toolsets: list[str] | None = None,
        role: str = "leaf",
        max_iterations: int | None = None,
        files: list[dict] | None = None,
        cleanup_paths: list[str] | None = None,
        async_: bool = False,
        hermes_context: dict | None = None,
        child_timeout_seconds: float | None = None,
    ) -> DispatchResult:
        """Dispatch one task to a named subagent.

        `goal` and `context` are concatenated client-side (Hermes' rule:
        the parent passes everything). `toolsets` / `role` / `max_iterations`
        become soft system-prompt hints — AgentMint sandboxes can't
        structurally enforce them.

        Async dispatches return immediately with a `run_id`; poll
        completion via `run_status(run_id)`.

        If `child_timeout_seconds` is set, the call is wrapped with a hard
        cap (floor 30s); on expiry, `agent.cancel` is fired and
        `DispatchTimeout` is raised.
        """
        prompt = compose_prompt(goal, context, toolsets, role, max_iterations)
        params: dict[str, Any] = {"name": agent_name, "prompt": prompt}
        if files:
            params["files"] = files
        if cleanup_paths:
            params["cleanup_paths"] = cleanup_paths
        if async_:
            params["async"] = True
        if hermes_context:
            params["metadata"] = {"hermes": hermes_context}

        if child_timeout_seconds is None:
            result = self.client.call("agent.run", params)
            return DispatchResult.from_dict(result or {})

        cap = max(float(child_timeout_seconds), CHILD_TIMEOUT_FLOOR)
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(self.client.call, "agent.run", params)
            try:
                result = future.result(timeout=cap)
            except FutureTimeoutError as e:
                try:
                    self.client.call("agent.cancel", {"name": agent_name})
                except Exception:
                    pass
                raise DispatchTimeout(
                    f"dispatch of {agent_name!r} exceeded {cap}s; agent.cancel issued"
                ) from e
        return DispatchResult.from_dict(result or {})

    # ----- batch dispatch ------------------------------------------------

    def dispatch_batch(
        self,
        tasks: list[Task],
        max_concurrent_children: int = 3,
        child_timeout_seconds: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[DispatchResult]:
        """Fan out N tasks in parallel against named subagents.

        - Results are returned in **input order** (matches Hermes' contract
          "Results are sorted by task index to match input order regardless
          of completion order").
        - Up to `max_concurrent_children` run simultaneously
          (`ThreadPoolExecutor`).
        - On exception per task, a `DispatchResult` with `status="failed"`
          (or `"timeout"` / `"interrupted"`) is inserted at that index — the
          batch never aborts partial.
        - `cancel_event` (a `threading.Event`) — when set, in-flight tasks
          receive a best-effort `agent.cancel` and remaining tasks raise
          `DispatchInterrupted`.
        """
        if max_concurrent_children < 1:
            raise ValueError("max_concurrent_children must be >= 1")
        if not tasks:
            return []

        results: list[DispatchResult | None] = [None] * len(tasks)

        def run_one(idx: int, task: Task) -> None:
            if cancel_event is not None and cancel_event.is_set():
                results[idx] = DispatchResult(
                    status="interrupted",
                    extra={"error": "cancel_event set before task started"},
                )
                return
            try:
                results[idx] = self.dispatch(
                    agent_name=task.agent_name,
                    goal=task.goal,
                    context=task.context,
                    toolsets=task.toolsets,
                    role=task.role,
                    max_iterations=task.max_iterations,
                    files=task.files,
                    cleanup_paths=task.cleanup_paths,
                    hermes_context=task.hermes_context,
                    child_timeout_seconds=child_timeout_seconds,
                )
            except DispatchTimeout as e:
                results[idx] = DispatchResult(status="timeout", extra={"error": str(e)})
            except DispatchInterrupted as e:
                results[idx] = DispatchResult(status="interrupted", extra={"error": str(e)})
            except Exception as e:
                results[idx] = DispatchResult(
                    status="failed",
                    extra={"error": str(e), "type": type(e).__name__},
                )

        watcher: threading.Thread | None = None
        with ThreadPoolExecutor(max_workers=max_concurrent_children) as ex:
            futures = [ex.submit(run_one, i, t) for i, t in enumerate(tasks)]

            if cancel_event is not None:
                def watch() -> None:
                    cancel_event.wait()
                    for f in futures:
                        f.cancel()
                    seen: set[str] = set()
                    for t in tasks:
                        if t.agent_name in seen:
                            continue
                        seen.add(t.agent_name)
                        try:
                            self.client.call("agent.cancel", {"name": t.agent_name})
                        except Exception:
                            pass

                watcher = threading.Thread(target=watch, daemon=True)
                watcher.start()

            for f in futures:
                try:
                    f.result()
                except CancelledError:
                    # The cancel_event watcher cancelled this pending future
                    # before run_one had a chance to set results[idx]. Leave
                    # the slot as None; the synthesizer below fills it.
                    pass

        # Fill any None slots — either because the watcher cancelled a
        # pending future, or (defensively) some other untracked path.
        cancelled = cancel_event is not None and cancel_event.is_set()
        fallback_status = "interrupted" if cancelled else "failed"
        return [
            r if r is not None else DispatchResult(
                status=fallback_status,
                extra={"error": f"task did not start ({fallback_status})"},
            )
            for r in results
        ]
