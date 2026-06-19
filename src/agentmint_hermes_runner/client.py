import json
import uuid
from typing import Any

from .auth.base import Auth
from .exceptions import AgentMintError


class Client:
    """Minimal JSON-RPC 2.0 client for AgentMint /a2a.

    Auth strategy owns the HTTP transport; this class only handles envelope
    serialization and error mapping.
    """

    def __init__(self, endpoint: str, auth: Auth):
        self.endpoint = endpoint
        self.auth = auth

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": method,
                "params": params or {},
            }
        ).encode("utf-8")
        resp_bytes = self.auth.call(self.endpoint, method, body)
        try:
            data = json.loads(resp_bytes)
        except json.JSONDecodeError as e:
            raise AgentMintError(f"non-JSON response from {self.endpoint}: {e}") from e
        if "error" in data:
            err = data["error"]
            raise AgentMintError(
                f"{err.get('code', 'unknown')}: {err.get('message', '')}",
                data=err.get("data"),
            )
        return data.get("result")
