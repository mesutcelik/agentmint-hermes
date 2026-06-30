"""Hermes plugin entry-point.

Auto-wires `install_delegate_task_wrapper` at Hermes gateway boot by
reading a cached Bearer JWT from `$AGENTMINT_JWT` (env, wins if set)
or `~/.agentmint/credentials.json` (the canonical cache location
written by `agentmint-hermes-init`).

**Opt-in routing model.** The autoload installs the patch with
`default_agent_name=None`, meaning:

  - LLM calls `delegate_task(background=True, toolsets=["agentmint-<name>"])`
    → routes to that AgentMint subagent
  - LLM calls `delegate_task(background=True, ...)` WITHOUT the
    `agentmint-<name>` directive → falls through to Hermes-native
    `delegate_task` untouched

AgentMint is never picked transparently — the LLM has to ask for it
via the toolsets directive. Install the `hermes-delegate-task` skill
(`hermes skills install mesutcelik/agentmint-skills/hermes-delegate-task`)
so the LLM knows the convention.

The operator can override with `$AGENTMINT_DEFAULT_AGENT_NAME` if
they want catch-all routing for a specific deployment — but that's
explicit opt-in.

Failures are logged and swallowed — gateway boot must NOT fail just
because AgentMint isn't bootstrapped yet. If no JWT is found, the
patch simply doesn't install and `delegate_task` retains Hermes'
native behavior.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from typing import Any

logger = logging.getLogger(__name__)

CREDS_PATH = pathlib.Path.home() / ".agentmint" / "credentials.json"
ENV_JWT = "AGENTMINT_JWT"
ENV_DEFAULT_AGENT = "AGENTMINT_DEFAULT_AGENT_NAME"


def register(_plugin_context: Any | None = None) -> None:
    """Entry point called by Hermes at gateway boot.

    Hermes' plugin discovery walks `hermes_agent.plugins` entry-points
    and invokes `register(context)` on each. We accept the context
    arg for forward-compatibility and ignore it; today's
    auto-wiring doesn't need it.
    """
    jwt = _resolve_jwt()
    if not jwt:
        logger.info(
            "agentmint: no AgentMint JWT cached "
            "(run `agentmint-hermes-init` once); auto-wire skipped"
        )
        return

    try:
        from .auth.bearer import BearerAuth
        from .dispatcher import AgentMintDispatcher
        from .hermes_patch import install_delegate_task_wrapper

        dispatcher = AgentMintDispatcher(auth=BearerAuth(jwt=jwt))
        # Opt-in routing: no catch-all. The LLM must include
        # toolsets=["agentmint-<name>"] for the patch to dispatch to
        # AgentMint; otherwise the call falls through to Hermes-native.
        # Operator can override via env to force a catch-all default.
        default_agent = os.environ.get(ENV_DEFAULT_AGENT) or None
        install_delegate_task_wrapper(
            dispatcher=dispatcher,
            default_agent_name=default_agent,
        )
        if default_agent:
            logger.info(
                "agentmint: auto-wired delegate_task with catch-all "
                "default_agent=%s (overridden via %s)",
                default_agent,
                ENV_DEFAULT_AGENT,
            )
        else:
            logger.info(
                "agentmint: auto-wired delegate_task (opt-in routing — "
                "LLM uses `toolsets=[\"agentmint-<name>\"]` to dispatch)"
            )
    except Exception:
        logger.exception("agentmint: auto-wire failed; delegate_task unchanged")


def _resolve_jwt() -> str | None:
    """Return a JWT from env or the credentials cache; None if absent."""
    env = os.environ.get(ENV_JWT)
    if env:
        return env.strip()

    if not CREDS_PATH.exists():
        return None
    try:
        data = json.loads(CREDS_PATH.read_text())
    except Exception:
        logger.exception("agentmint: failed reading %s", CREDS_PATH)
        return None

    tokens = (data or {}).get("tokens") or {}
    if not tokens:
        return None

    # Pick the first cached JWT. If multiple principals are stored
    # (e.g. operator has both a Stripe-Link wallet and a Tempo wallet),
    # the operator should set AGENTMINT_JWT explicitly to disambiguate.
    first = next(iter(tokens.values()))
    token = first.get("access_token")
    return token if isinstance(token, str) and token else None
