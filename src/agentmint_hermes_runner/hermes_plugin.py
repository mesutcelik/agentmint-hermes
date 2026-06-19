"""Hermes plugin entry-point for the `agentmint_delegate` tool.

Registered with Hermes via the `hermes_agent.plugins` entry-point group
(see pyproject.toml). Hermes calls `register(ctx)` at startup; we wire
the `agentmint_delegate` tool into Hermes' tool registry.

Operator usage:

    # in Hermes gateway startup, BEFORE Hermes builds its tool registry:
    from agentmint_hermes_runner import AgentMintDispatcher, BearerAuth, set_dispatcher
    set_dispatcher(AgentMintDispatcher(auth=BearerAuth(jwt=os.environ["AGENTMINT_JWT"])))

LLM-facing tool signature (auto-exposed once the package is pip-installed
and the dispatcher is bound):

    agentmint_delegate(
        agent_name: str,        # required — which named AgentMint subagent
        goal: str,              # required — task description
        context: str = None,    # optional — background concatenated into prompt
        async_: bool = True,    # optional — re-inject when ready (default true)
        toolsets: list = None,  # optional — soft hints
        role: str = "leaf",     # optional — soft hint
        max_iterations: int = None,
    )
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .dispatcher import AgentMintDispatcher

logger = logging.getLogger(__name__)

_dispatcher: AgentMintDispatcher | None = None


def set_dispatcher(d: AgentMintDispatcher) -> None:
    """Bind the dispatcher the plugin handler will use. Operator must call
    this once at gateway startup, BEFORE any LLM call to `agentmint_delegate`.
    """
    global _dispatcher
    _dispatcher = d
    logger.info("agentmint-hermes: plugin dispatcher bound")


def get_dispatcher() -> AgentMintDispatcher | None:
    """Return the bound dispatcher (mostly for tests)."""
    return _dispatcher


def _agentmint_delegate_handler(args: dict, **_kwargs: Any) -> str:
    """Tool handler invoked by Hermes when the LLM calls `agentmint_delegate`.

    Returns a JSON string (per Hermes' tool handler contract — see
    `tools/registry.py` ToolEntry).
    """
    if _dispatcher is None:
        return json.dumps({
            "error": (
                "agentmint_delegate dispatcher not bound — operator must call "
                "agentmint_hermes_runner.set_dispatcher() at gateway startup."
            )
        })

    agent_name = args.get("agent_name")
    goal = args.get("goal")
    if not agent_name or not goal:
        return json.dumps({"error": "agent_name and goal are both required"})

    try:
        result = _dispatcher.dispatch(
            agent_name=agent_name,
            goal=goal,
            context=args.get("context"),
            toolsets=args.get("toolsets"),
            role=args.get("role", "leaf"),
            max_iterations=args.get("max_iterations"),
            async_=args.get("async_", True),
        )
        return json.dumps({
            "status": result.status,
            "delegation_id": result.delegation_id or result.run_id,
            "agent_name": agent_name,
            "source": "agentmint",
        })
    except Exception as e:
        logger.exception("agentmint_delegate handler failed for agent=%s", agent_name)
        return json.dumps({
            "error": str(e),
            "type": type(e).__name__,
        })


_AGENTMINT_DELEGATE_SCHEMA = {
    "type": "object",
    "properties": {
        "agent_name": {
            "type": "string",
            "description": (
                "Name of the AgentMint subagent to dispatch to. Must be a "
                "subagent the calling principal owns (use agent.list via "
                "terminal to enumerate). Subagent must already exist — "
                "create via agent.create before calling this tool."
            ),
        },
        "goal": {
            "type": "string",
            "description": "The task description for the subagent.",
        },
        "context": {
            "type": "string",
            "description": (
                "Background information the subagent needs. Concatenated "
                "into the prompt under a '## Context' section. Subagents "
                "already accumulate /workspace/MEMORY.md across calls, so "
                "you can skip context the subagent has already seen."
            ),
        },
        "async_": {
            "type": "boolean",
            "description": (
                "Dispatch asynchronously (default true). The call returns "
                "immediately with a delegation_id; the result re-injects "
                "as a new conversation turn when the subagent finishes. "
                "Set false for a synchronous dispatch that blocks until "
                "done."
            ),
        },
        "toolsets": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Soft hints for which toolsets the subagent should use "
                "('terminal', 'file'). Sandbox can't structurally enforce; "
                "treated as system-prompt guidance."
            ),
        },
        "role": {
            "type": "string",
            "enum": ["leaf", "orchestrator"],
            "description": "Soft hint — 'leaf' (do not delegate further) or 'orchestrator'.",
        },
        "max_iterations": {
            "type": "integer",
            "description": "Soft iteration budget hint for the subagent's harness.",
        },
    },
    "required": ["agent_name", "goal"],
    "additionalProperties": False,
}


def register(ctx: Any) -> None:
    """Hermes plugin entry-point. Called once at Hermes startup with a
    PluginContext exposing `register_tool(name, toolset, schema, handler, ...)`.

    See `hermes_cli/plugins.py:PluginContext` for the full contract.
    """
    ctx.register_tool(
        name="agentmint_delegate",
        toolset="agentmint",
        schema=_AGENTMINT_DELEGATE_SCHEMA,
        handler=_agentmint_delegate_handler,
        description=(
            "Delegate to a NAMED persistent AgentMint subagent. The "
            "subagent's /workspace/MEMORY.md accumulates context across "
            "every call — use this when you want a specialist that "
            "REMEMBERS prior delegations (PR reviewer that learns your "
            "repo, support agent that remembers a customer's history, "
            "etc.). For one-shot delegation without persistence, use Hermes' "
            "built-in delegate_task instead."
        ),
        emoji="🧠",
    )
