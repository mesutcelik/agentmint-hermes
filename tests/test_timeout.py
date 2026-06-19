import json
import time

import pytest

from agentmint_hermes_runner.dispatcher import CHILD_TIMEOUT_FLOOR, AgentMintDispatcher
from agentmint_hermes_runner.exceptions import DispatchTimeout


class SlowAuth:
    """Auth stub that sleeps to simulate a long-running run.

    Tracks whether agent.cancel was issued.
    """

    def __init__(self, delay_seconds: float):
        self.delay_seconds = delay_seconds
        self.cancel_called = False

    def call(self, endpoint: str, method: str, body: bytes) -> bytes:
        envelope = json.loads(body)
        if envelope["method"] == "agent.cancel":
            self.cancel_called = True
            return json.dumps({"jsonrpc": "2.0", "id": "x", "result": {}}).encode()
        time.sleep(self.delay_seconds)
        return json.dumps(
            {"jsonrpc": "2.0", "id": "x", "result": {"status": "ok"}}
        ).encode()


def test_timeout_raises_dispatch_timeout(monkeypatch):
    # Reduce the floor for fast test runs.
    monkeypatch.setattr(
        "agentmint_hermes_runner.dispatcher.CHILD_TIMEOUT_FLOOR", 0.2
    )
    auth = SlowAuth(delay_seconds=2.0)
    dispatcher = AgentMintDispatcher(auth=auth)
    with pytest.raises(DispatchTimeout, match="exceeded"):
        dispatcher.dispatch(agent_name="bot-1", goal="hi", child_timeout_seconds=0.1)
    assert auth.cancel_called


def test_no_timeout_lets_call_complete():
    auth = SlowAuth(delay_seconds=0.05)
    dispatcher = AgentMintDispatcher(auth=auth)
    result = dispatcher.dispatch(agent_name="bot-1", goal="hi")
    assert result.status == "ok"
    assert not auth.cancel_called


def test_floor_applies():
    # Sanity: floor is 30s in production code; we verify the constant exists.
    assert CHILD_TIMEOUT_FLOOR == 30.0
