from typing import Protocol


class Auth(Protocol):
    """Transport strategy for AgentMint /a2a calls.

    Implementations own the full HTTP exchange so we can support both
    header-based auth (Bearer JWT) and external-CLI auth (Tempo's
    `tempo request` subprocess that handles the 402 dance end-to-end).
    """

    def call(self, endpoint: str, method: str, body: bytes) -> bytes:
        """POST `body` to `endpoint` and return the response body bytes.

        `method` is the JSON-RPC method name (for logging only — the actual
        envelope is already serialized into `body`).
        """
        ...
