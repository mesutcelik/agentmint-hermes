"""Tests for the Hermes plugin entry-point (hermes_plugin.register).

Verifies the plugin wires `agentmint_delegate` into Hermes' tool registry
with the correct schema + handler, and that the handler dispatches through
the bound dispatcher.
"""
import json

import pytest

from agentmint_hermes_runner import set_dispatcher
from agentmint_hermes_runner.dispatcher import AgentMintDispatcher
from agentmint_hermes_runner.hermes_plugin import (
    _agentmint_delegate_handler,
    get_dispatcher,
    register,
)


class FakeAuth:
    def __init__(self, result: dict):
        self._result = result

    def call(self, endpoint: str, method: str, body: bytes) -> bytes:
        return json.dumps({"jsonrpc": "2.0", "id": "x", "result": self._result}).encode()


class FakePluginContext:
    """Minimal stand-in for Hermes' PluginContext."""

    def __init__(self):
        self.registered: list[dict] = []

    def register_tool(self, **kwargs) -> None:
        self.registered.append(kwargs)


@pytest.fixture(autouse=True)
def _clear_dispatcher():
    """Ensure each test starts with no dispatcher bound."""
    set_dispatcher(None)  # type: ignore[arg-type]
    yield
    set_dispatcher(None)  # type: ignore[arg-type]


def test_register_wires_agentmint_delegate_tool():
    ctx = FakePluginContext()
    register(ctx)
    assert len(ctx.registered) == 1
    entry = ctx.registered[0]
    assert entry["name"] == "agentmint_delegate"
    assert entry["toolset"] == "agentmint"
    assert callable(entry["handler"])
    schema = entry["schema"]
    assert "agent_name" in schema["properties"]
    assert "goal" in schema["properties"]
    assert set(schema["required"]) == {"agent_name", "goal"}


def test_handler_errors_when_dispatcher_not_bound():
    out = _agentmint_delegate_handler({"agent_name": "x", "goal": "hi"})
    parsed = json.loads(out)
    assert "error" in parsed
    assert "dispatcher not bound" in parsed["error"]


def test_handler_errors_on_missing_required_args():
    set_dispatcher(AgentMintDispatcher(auth=FakeAuth({"status": "dispatched"})))
    out = _agentmint_delegate_handler({"agent_name": "x"})  # missing goal
    parsed = json.loads(out)
    assert "error" in parsed
    assert "required" in parsed["error"]


def test_handler_dispatches_through_bound_dispatcher():
    set_dispatcher(AgentMintDispatcher(
        auth=FakeAuth({"status": "dispatched", "delegation_id": "del_abc", "run_id": "arun_xyz"})
    ))
    out = _agentmint_delegate_handler({
        "agent_name": "reviewer-myrepo",
        "goal": "Review the diff",
        "context": "Project at /workspace",
        "async_": True,
    })
    parsed = json.loads(out)
    assert parsed["status"] == "dispatched"
    assert parsed["delegation_id"] == "del_abc"
    assert parsed["agent_name"] == "reviewer-myrepo"
    assert parsed["source"] == "agentmint"


def test_handler_catches_dispatcher_exceptions():
    class FailingAuth:
        def call(self, *a, **k):
            raise RuntimeError("network down")

    set_dispatcher(AgentMintDispatcher(auth=FailingAuth()))
    out = _agentmint_delegate_handler({"agent_name": "x", "goal": "hi"})
    parsed = json.loads(out)
    assert "error" in parsed
    assert "network down" in parsed["error"]


def test_set_dispatcher_makes_get_dispatcher_return_it():
    d = AgentMintDispatcher(auth=FakeAuth({}))
    set_dispatcher(d)
    assert get_dispatcher() is d
