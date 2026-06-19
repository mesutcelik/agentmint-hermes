"""Tests for hermes_patch.install_delegate_task_wrapper.

The actual Hermes module isn't installed in the test env, so we fabricate
fake `tools.async_delegation` and `hermes.gateway.process_registry` modules
via sys.modules before importing the patch.
"""
import json
import sys
import types
from queue import Queue
from unittest.mock import MagicMock

import pytest

from agentmint_hermes_runner.dispatcher import AgentMintDispatcher


class FakeAuth:
    def __init__(self, response: dict):
        self._response = response
        self.last_envelope: dict | None = None
        self._call_count = 0

    def call(self, endpoint: str, method: str, body: bytes) -> bytes:
        self.last_envelope = json.loads(body)
        self._call_count += 1
        return json.dumps({"jsonrpc": "2.0", "id": "x", "result": self._response}).encode()


@pytest.fixture
def fake_hermes(monkeypatch):
    """Install fake Hermes modules into sys.modules so the patch can import them."""
    push_events: list[dict] = []
    completion_q: Queue = Queue()

    def _push(*, delegation_id, status, result):
        push_events.append({"delegation_id": delegation_id, "status": status, "result": result})

    fake_async_delegation = types.ModuleType("tools.async_delegation")
    fake_async_delegation.dispatch_async_delegation = MagicMock(
        return_value={"status": "dispatched", "delegation_id": "hermes_native_x"}
    )
    fake_async_delegation._push_completion_event = _push

    fake_tools_pkg = types.ModuleType("tools")
    fake_tools_pkg.async_delegation = fake_async_delegation

    fake_pr = types.ModuleType("hermes.gateway.process_registry")
    fake_pr.completion_queue = completion_q
    fake_gateway = types.ModuleType("hermes.gateway")
    fake_gateway.process_registry = fake_pr
    fake_hermes_pkg = types.ModuleType("hermes")
    fake_hermes_pkg.gateway = fake_gateway

    monkeypatch.setitem(sys.modules, "tools", fake_tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.async_delegation", fake_async_delegation)
    monkeypatch.setitem(sys.modules, "hermes", fake_hermes_pkg)
    monkeypatch.setitem(sys.modules, "hermes.gateway", fake_gateway)
    monkeypatch.setitem(sys.modules, "hermes.gateway.process_registry", fake_pr)

    return {
        "async_delegation": fake_async_delegation,
        "push_events": push_events,
        "completion_queue": completion_q,
    }


def test_install_patches_dispatch_async_delegation(fake_hermes):
    from agentmint_hermes_runner.hermes_patch import install_delegate_task_wrapper

    auth = FakeAuth({"status": "dispatched", "run_id": "arun_abc"})
    dispatcher = AgentMintDispatcher(auth=auth)
    original = fake_hermes["async_delegation"].dispatch_async_delegation
    uninstall = install_delegate_task_wrapper(
        dispatcher, default_agent_name="default-worker", poll_interval=0.05
    )
    try:
        assert fake_hermes["async_delegation"].dispatch_async_delegation is not original

        result = fake_hermes["async_delegation"].dispatch_async_delegation(
            goal="hello",
            context=None,
            toolsets=None,
            role="leaf",
            model="claude",
            session_key="sess_1",
            runner=lambda: None,
            interrupt_fn=lambda: None,
            max_async_children=3,
        )
        assert result["status"] == "dispatched"
        assert result["source"] == "agentmint"
        assert result["delegation_id"] == "arun_abc"
        # The AgentMint /a2a call happened
        assert auth.last_envelope["method"] == "agent.run"
        assert auth.last_envelope["params"]["name"] == "default-worker"
        assert auth.last_envelope["params"]["async"] is True
    finally:
        uninstall()
    assert fake_hermes["async_delegation"].dispatch_async_delegation is original


def test_patched_falls_back_to_native_on_error(fake_hermes):
    from agentmint_hermes_runner.hermes_patch import install_delegate_task_wrapper

    class FailingAuth:
        def call(self, *a, **k):
            raise RuntimeError("network down")

    dispatcher = AgentMintDispatcher(auth=FailingAuth())
    uninstall = install_delegate_task_wrapper(dispatcher, default_agent_name="default")
    try:
        result = fake_hermes["async_delegation"].dispatch_async_delegation(
            goal="hello", context=None, toolsets=None, role="leaf",
            model="claude", session_key="s",
            runner=lambda: None, interrupt_fn=lambda: None,
            max_async_children=3,
        )
        # The original was called (and our MagicMock returned its canned shape)
        assert result["delegation_id"] == "hermes_native_x"
    finally:
        uninstall()


def test_poller_pushes_completion_on_terminal_status(fake_hermes):
    """The poller should call _push_completion_event once the run is done."""
    from agentmint_hermes_runner.hermes_patch import _spawn_poller

    statuses = iter([
        {"status": "pending"},
        {"status": "pending"},
        {"status": "completed", "billed_usdc": 0.04, "completed_at": 123},
    ])

    class StatusAuth:
        def __init__(self):
            self.calls = 0

        def call(self, endpoint, method, body):
            envelope = json.loads(body)
            assert envelope["method"] == "agent.run.status"
            self.calls += 1
            return json.dumps(
                {"jsonrpc": "2.0", "id": "x", "result": next(statuses)}
            ).encode()

    dispatcher = AgentMintDispatcher(auth=StatusAuth())
    thread = _spawn_poller(
        dispatcher=dispatcher,
        run_id="arun_42",
        goal="hi",
        context=None,
        session_key="s",
        poll_interval=0.02,
    )
    thread.join(timeout=2.0)
    assert not thread.is_alive(), "poller did not exit after terminal status"

    events = fake_hermes["push_events"]
    assert len(events) == 1
    assert events[0]["delegation_id"] == "arun_42"
    assert events[0]["status"] == "completed"
    assert events[0]["result"]["billed_usdc"] == 0.04
