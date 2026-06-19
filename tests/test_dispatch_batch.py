import json
import threading
import time

from agentmint_hermes_runner.dispatcher import AgentMintDispatcher
from agentmint_hermes_runner.models import Task


class TrackingAuth:
    """Auth stub that records ordered call history + can simulate delay."""

    def __init__(self, delay_seconds: float = 0.0, fail_for_agents: set[str] | None = None):
        self.delay_seconds = delay_seconds
        self.fail_for_agents = fail_for_agents or set()
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    def call(self, endpoint: str, method: str, body: bytes) -> bytes:
        envelope = json.loads(body)
        with self.lock:
            self.calls.append({"method": envelope["method"], "params": envelope["params"]})
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        agent_name = envelope["params"].get("name", "")
        if agent_name in self.fail_for_agents:
            return json.dumps(
                {"jsonrpc": "2.0", "id": "x", "error": {"code": "boom", "message": "bad"}}
            ).encode()
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "x",
                "result": {"status": "ok", "delegation_id": f"del_{agent_name}"},
            }
        ).encode()


def _tasks(n: int) -> list[Task]:
    return [Task(goal=f"goal-{i}", agent_name=f"bot-{i}") for i in range(n)]


def test_batch_returns_in_input_order():
    auth = TrackingAuth(delay_seconds=0.02)
    dispatcher = AgentMintDispatcher(auth=auth)
    results = dispatcher.dispatch_batch(_tasks(5), max_concurrent_children=3)
    assert len(results) == 5
    for i, r in enumerate(results):
        assert r.delegation_id == f"del_bot-{i}"


def test_batch_respects_max_concurrent():
    auth = TrackingAuth(delay_seconds=0.1)
    dispatcher = AgentMintDispatcher(auth=auth)
    t0 = time.monotonic()
    dispatcher.dispatch_batch(_tasks(6), max_concurrent_children=2)
    elapsed = time.monotonic() - t0
    # 6 tasks, 2 at a time, 0.1s each → at least 3 batches = ~0.3s
    assert elapsed >= 0.25, f"expected serial behaviour, ran in {elapsed:.2f}s"


def test_batch_continues_on_per_task_failure():
    auth = TrackingAuth(fail_for_agents={"bot-1"})
    dispatcher = AgentMintDispatcher(auth=auth)
    results = dispatcher.dispatch_batch(_tasks(3))
    assert results[0].status == "ok"
    assert results[1].status == "failed"
    assert "bad" in results[1].extra["error"]
    assert results[2].status == "ok"


def test_batch_rejects_invalid_concurrency():
    import pytest

    dispatcher = AgentMintDispatcher(auth=TrackingAuth())
    with pytest.raises(ValueError, match="max_concurrent_children"):
        dispatcher.dispatch_batch(_tasks(1), max_concurrent_children=0)


def test_batch_empty_returns_empty():
    dispatcher = AgentMintDispatcher(auth=TrackingAuth())
    assert dispatcher.dispatch_batch([]) == []


def test_batch_cancel_event_marks_pending_interrupted():
    auth = TrackingAuth(delay_seconds=0.2)
    dispatcher = AgentMintDispatcher(auth=auth)
    cancel = threading.Event()
    # Set the cancel event immediately so tasks that haven't started see it.
    cancel.set()
    results = dispatcher.dispatch_batch(_tasks(3), max_concurrent_children=1, cancel_event=cancel)
    statuses = [r.status for r in results]
    assert "interrupted" in statuses


def test_batch_prompt_includes_context_and_toolsets():
    auth = TrackingAuth()
    dispatcher = AgentMintDispatcher(auth=auth)
    tasks = [
        Task(
            goal="research wasm",
            agent_name="bot-1",
            context="2026 state",
            toolsets=["file"],  # excludes terminal — restriction appears
            role="leaf",
        )
    ]
    dispatcher.dispatch_batch(tasks)
    sent_prompt = auth.calls[0]["params"]["prompt"]
    assert "research wasm" in sent_prompt
    assert "2026 state" in sent_prompt
    assert "Do not run shell commands." in sent_prompt
