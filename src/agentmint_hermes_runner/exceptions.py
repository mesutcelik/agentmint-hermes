from typing import Any


class AgentMintError(Exception):
    def __init__(self, message: str, data: Any = None):
        super().__init__(message)
        self.data = data


class DispatchTimeout(AgentMintError):
    """Raised when a child_timeout_seconds limit fires for a single dispatch.

    The adapter has already issued `agent.cancel` to the AgentMint server
    by the time this is raised; the cancellation is best-effort (the
    underlying run may still complete depending on timing).
    """


class DispatchInterrupted(AgentMintError):
    """Raised inside a batch dispatch when the caller's cancel_event was set."""


class UnsupportedToolset(AgentMintError):
    """Raised when the caller requests a Hermes toolset that AgentMint does
    not yet support (e.g. `"web"` in v0.2 — there is no canonical AgentMint
    web-fetch skill yet; the three supported harnesses have built-in web
    capability but we don't expose it as a structured toolset).
    """
