"""Tests for ephemeral mode of install_delegate_task_wrapper.

In v0.6.0+, ephemeral mode dispatches a SINGLE call to the server-side
`agent.run.stateless` method. AgentMint owns the pool + the lifecycle;
the adapter just dispatches and polls. No client-side agent.create or
agent.delete.
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

        if rpc_method == "agent.run.stateless":
            return json.dumps({
                "jsonrpc": "2.0", "id": "x",
                "result": {"status": "dispatched", "run_id": "arun_xyz",
                           "billed_usdc": 0.05, "stateless": True},
            }).encode()
        if rpc_method == "agent.run.status":
            self._poll_count += 1
            status = "completed" if self._poll_count >= self.status_after_polls else "pending"
            return json.dumps({
                "jsonrpc": "2.0", "id": "x",
                "result": {"status": status, "billed_usdc": 0.05, "completed_at": 123},
            }).encode()
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


def test_ephemeral_dispatches_to_stateless(fake_hermes):
    """A single agent.run.stateless call — no create, no delete on the client."""
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
        assert result["delegation_id"] == "arun_xyz"
        # No ephemeral_agent in the response — server owns the box name now
        assert "ephemeral_agent" not in result
        # Wait briefly for the poller to complete
        time.sleep(0.2)
    finally:
        uninstall()

    methods = auth.methods_called()
    # Order: agent.run.stateless, then agent.run.status polls. NO agent.create/agent.delete.
    assert methods[0] == "agent.run.stateless"
    assert "agent.run.status" in methods
    assert "agent.create" not in methods
    assert "agent.delete" not in methods


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
        # agent.run.stateless was called → confirms ephemeral path was taken
        assert "agent.run.stateless" in auth.methods_called()
        assert result["status"] == "dispatched"
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
        fake_hermes["async_delegation"].dispatch_async_delegation(
            goal="hi", context=None, toolsets=None, role="leaf",
            model=None, session_key="",
            runner=lambda: None, interrupt_fn=lambda: None,
            max_async_children=3,
        )
        # Persistent path hits agent.run (not stateless), with the default name
        methods = auth.methods_called()
        assert "agent.run" in methods
        assert "agent.run.stateless" not in methods
        # First call must be agent.run with the configured name
        run_envelope = next(c for c in auth.calls if c[0] == "agent.run")
        assert run_envelope[1]["name"] == "default-worker"
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


def test_ephemeral_falls_back_to_native_on_dispatch_failure(fake_hermes):
    """If agent.run.stateless fails, the patched function falls back to Hermes-native."""
    from agentmint_hermes_runner.hermes_patch import install_delegate_task_wrapper

    auth = TrackingAuth(fail_method="agent.run.stateless")
    dispatcher = AgentMintDispatcher(auth=auth)
    uninstall = install_delegate_task_wrapper(
        dispatcher, mode="ephemeral", poll_interval=0.02
    )
    try:
        result = fake_hermes["async_delegation"].dispatch_async_delegation(
            goal="hi", context=None, toolsets=None, role="leaf",
            model=None, session_key="",
            runner=lambda: None, interrupt_fn=lambda: None,
            max_async_children=3,
        )
        # Fell back to Hermes-native (the MagicMock canned response)
        assert result["delegation_id"] == "hermes_native_x"
    finally:
        uninstall()

    methods = auth.methods_called()
    # Just one stateless attempt — no client-side cleanup needed because
    # there's nothing to clean (server owns the pool).
    assert methods == ["agent.run.stateless"]
