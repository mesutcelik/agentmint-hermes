import json
from unittest.mock import MagicMock, patch

import pytest

from agentmint_hermes_runner.auth.bearer import BearerAuth


def _build_mock_client(response_content: bytes):
    mock_response = MagicMock()
    mock_response.content = response_content
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_response)
    return mock_client, mock_response


def test_bearer_requires_jwt():
    with pytest.raises(ValueError, match="jwt is required"):
        BearerAuth(jwt="")


def test_bearer_sends_authorization_header():
    auth = BearerAuth(jwt="test-jwt")
    response_body = json.dumps({"jsonrpc": "2.0", "id": "x", "result": "ok"}).encode()
    mock_client, _ = _build_mock_client(response_body)
    with patch("agentmint_hermes_runner.auth.bearer.httpx.Client", return_value=mock_client):
        out = auth.call("https://example.test/a2a", "agent.list", b'{"jsonrpc":"2.0"}')
    assert out == response_body
    _, kwargs = mock_client.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer test-jwt"
    assert kwargs["headers"]["Content-Type"] == "application/json"
