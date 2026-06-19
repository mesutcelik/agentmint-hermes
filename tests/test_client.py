import json

import pytest

from agentmint_hermes_runner.client import Client
from agentmint_hermes_runner.exceptions import AgentMintError


class FakeAuth:
    def __init__(self, response_bytes: bytes):
        self.response_bytes = response_bytes
        self.calls: list[tuple[str, str, bytes]] = []

    def call(self, endpoint: str, method: str, body: bytes) -> bytes:
        self.calls.append((endpoint, method, body))
        return self.response_bytes


def test_call_returns_result():
    response = json.dumps({"jsonrpc": "2.0", "id": "x", "result": {"hello": "world"}}).encode()
    auth = FakeAuth(response)
    client = Client("https://example.test/a2a", auth)
    assert client.call("agent.list") == {"hello": "world"}
    assert auth.calls[0][0] == "https://example.test/a2a"
    envelope = json.loads(auth.calls[0][2])
    assert envelope["jsonrpc"] == "2.0"
    assert envelope["method"] == "agent.list"
    assert envelope["params"] == {}


def test_call_raises_on_error():
    response = json.dumps(
        {"jsonrpc": "2.0", "id": "x", "error": {"code": "boom", "message": "kaboom"}}
    ).encode()
    client = Client("https://example.test/a2a", FakeAuth(response))
    with pytest.raises(AgentMintError, match="kaboom"):
        client.call("agent.list")


def test_call_raises_on_non_json():
    client = Client("https://example.test/a2a", FakeAuth(b"<html>500</html>"))
    with pytest.raises(AgentMintError, match="non-JSON"):
        client.call("agent.list")
