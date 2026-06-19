"""Tests for ephemeral mode of install_delegate_task_wrapper.

Ephemeral lifecycle: per `delegate_task(background=True)` call, the patch
mints a fresh subagent with a uuid name, dispatches, polls, then deletes
on completion. Verifies the create → dispatch → delete sequence and that
failures clean up orphan subagents.
"""
import json
import sys
import threading
import time
import types
from queue import Queue
from unittest.mock import MagicMock

import pytest

from agentmint_hermes_runner.dispatcher import AgentMintDispatcher


class TrackingAuth:
    """Auth stub that records every JSON-RPC method called + their params."""

    def __init__(self, status_after_polls: int = 2, fail_method: str | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.lock = threading.Lock()
        self.status_after_polls = status_after_polls
        self.fail_method = fail_method
        self._poll_count = 0

    def call(self, endpoint: str, method: str, body: bytes) -> bytes:
        envelope = json.loads(body)
        rpc_method = envelope["method"]
        params = envelope.get("params", {})
        with self.lock:
            self.calls.append((rpc_method, params))

        if self.fail_method and rpc_method == self.fail_method:
            return json.dumps({
                "jsonrpc": "2.0", "id": "x",
                "error": {"code": "boom", "message": f"simulated {rpc_method} failure"},
            }).encode()

        if rpc_method == "agent.create":
            return json.dumps({
                "jsonrpc": "2.0", "id": "x",
                "result": {"agent_id": "agt_x", "name": params.get("name")},
            }).encode()
        if rpc_method == "agent.run":
            return json.dumps({
                "jsonrpc": "2.0", "id": "x",
                "result": {"status": "dispatched", "run_id": "arun_xyz"},
            }).encode()
        if rpc_method == "agent.run.status":
            self._poll_count += 1
            status = "completed" if self._poll_count >= self.status_after_polls else "pending"
            return json.dumps({
                "jsonrpc": "2.0", "id": "x",
                "result": {"status": status, "billed_usdc": 0.05, "completed_at": 123},
            }).encode()
        if rpc_method == "agent.delete":
            return json.dumps({"jsonrpc": "2.0", "id": "x", "result": {}}).encode()
        return json.dumps({"jsonrpc": "2.0", "id": "x", "result": {}}).encode()

    def methods_called(self) -> list[str]:
        with self.lock:
            return [m for m, _ in self.calls]


@pytest.fixture
def fake_hermes(monkeypatch):
    """Install fake Hermes modules into sys.modules."""
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

    return {"async_delegation": fake_async_delegation, "push_events": push_events}


def test_ephemeral_full_lifecycle(fake_hermes):
    from agentmint_hermes_runner.hermes_patch import install_delegate_task_wrapper

    auth = TrackingAuth(status_after_polls=2)
    dispatcher = AgentMintDispatcher(auth=auth)
    uninstall = install_delegate_task_wrapper(
        dispatcher, mode="ephemeral", poll_interval=0.02
    )
    try:
        result = fake_hermes["async_delegation"].dispatch_async_delegation(
            goal="hello", context=None, toolsets=None, role="leaf",
            model="claude", session_key="sess_1",
            runner=lambda: None, interrupt_fn=lambda: None,
            max_async_children=3,
        )
        assert result["status"] == "dispatched"
        assert result["source"] == "agentmint"
        assert result["ephemeral_agent"].startswith("ephem-")
        # Wait briefly for the poller to complete + cleanup
        time.sleep(0.2)
    finally:
        uninstall()

    methods = auth.methods_called()
    # Order: create, run, status (x2 pending->completed), delete
    assert methods[0] == "agent.create"
    assert methods[1] == "agent.run"
    assert "agent.run.status" in methods
    assert methods[-1] == "agent.delete"
    # Completion event was pushed
    assert any(e["status"] == "completed" for e in fake_hermes["push_events"])


def test_ephemeral_auto_detected_when_no_default(fake_hermes):
    from agentmint_hermes_runner.hermes_patch import install_delegate_task_wrapper

    auth = TrackingAuth(status_after_polls=2)
    dispatcher = AgentMintDispatcher(auth=auth)
    uninstall = install_delegate_task_wrapper(
        dispatcher, poll_interval=0.02
    )  # No default_agent_name, no mode
    try:
        result = fake_hermes["async_delegation"].dispatch_async_delegation(
            goal="hi", context=None, toolsets=None, role="leaf",
            model=None, session_key="",
            runner=lambda: None, interrupt_fn=lambda: None,
            max_async_children=3,
        )
        # ephemeral_agent in result confirms ephemeral path was taken
        assert "ephemeral_agent" in result
    finally:
        uninstall()


def test_persistent_auto_detected_when_default_set(fake_hermes):
    from agentmint_hermes_runner.hermes_patch import install_delegate_task_wrapper

    auth = TrackingAuth(status_after_polls=2)
    dispatcher = AgentMintDispatcher(auth=auth)
    uninstall = install_delegate_task_wrapper(
        dispatcher, default_agent_name="default-worker", poll_interval=0.02
    )
    try:
        result = fake_hermes["async_delegation"].dispatch_async_delegation(
            goal="hi", context=None, toolsets=None, role="leaf",
            model=None, session_key="",
            runner=lambda: None, interrupt_fn=lambda: None,
            max_async_children=3,
        )
        # No ephemeral_agent in result; no agent.create call (uses existing)
        assert "ephemeral_agent" not in result
        # Persistent path skips create + delete
        methods = auth.methods_called()
        assert "agent.create" not in methods
    finally:
        uninstall()


def test_persistent_mode_requires_default_name(fake_hermes):
    from agentmint_hermes_runner.hermes_patch import install_delegate_task_wrapper

    dispatcher = AgentMintDispatcher(auth=TrackingAuth())
    with pytest.raises(ValueError, match="default_agent_name"):
        install_delegate_task_wrapper(dispatcher, mode="persistent")


def test_invalid_mode_raises(fake_hermes):
    from agentmint_hermes_runner.hermes_patch import install_delegate_task_wrapper

    dispatcher = AgentMintDispatcher(auth=TrackingAuth())
    with pytest.raises(ValueError, match="mode must be one of"):
        install_delegate_task_wrapper(dispatcher, mode="banana")


def test_ephemeral_cleans_up_on_dispatch_failure(fake_hermes):
    """If agent.run fails after agent.create succeeded, the orphan is deleted."""
    from agentmint_hermes_runner.hermes_patch import install_delegate_task_wrapper

    auth = TrackingAuth(fail_method="agent.run")
    dispatcher = AgentMintDispatcher(auth=auth)
    uninstall = install_delegate_task_wrapper(
        dispatcher, mode="ephemeral", poll_interval=0.02
    )
    try:
        # Should NOT raise — patched falls back to Hermes-native on inner failure
        result = fake_hermes["async_delegation"].dispatch_async_delegation(
            goal="hi", context=None, toolsets=None, role="leaf",
            model=None, session_key="",
            runner=lambda: None, interrupt_fn=lambda: None,
            max_async_children=3,
        )
        # Fell back to Hermes-native
        assert result["delegation_id"] == "hermes_native_x"
    finally:
        uninstall()

    methods = auth.methods_called()
    # Must contain: create (ok), run (fails), delete (cleanup) — in that order
    assert methods[0] == "agent.create"
    assert methods[1] == "agent.run"
    # The orphan must have been cleaned up
    assert "agent.delete" in methods
