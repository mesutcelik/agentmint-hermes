"""Ephemeral mode — the default.

Every `delegate_task(background=True)` call in Hermes mints a FRESH
AgentMint subagent (auto-named with a UUID), runs it, and deletes it on
completion. Matches Hermes-native delegate_task semantics (stateless
per call) but runs on an isolated AgentMint cloud sandbox with
independent credentials.

Use this when you want:
  - Hermes' native ergonomics preserved (no agent_name, no pre-mint)
  - Multi-subagent fan-out via tasks=[...] (each task is its own
    isolated subagent automatically)
  - Cloud isolation without local CPU/RAM consumption

Don't use this when you want a subagent that REMEMBERS across calls —
for that, use the persistent.py setup OR the agentmint_delegate plugin
tool (see strategy_a_plugin.py).

Prerequisites:
    1. Bootstrap a Stripe-Link credit wallet (one-time, min $10):
       link-cli mpp pay https://api.agentmint.store/a2a -X POST \\
         -H 'Content-Type: application/json' \\
         -d '{"jsonrpc":"2.0","id":1,"method":"credits.topup","params":{"amount_usd":10}}'
       export AGENTMINT_JWT=<access_token from the response>

    2. pip install agentmint-hermes-runner (in Hermes' venv)

Cost per call: ~$0.16 USDC (0.10 create + 0.05 run + 0.01 delete).

Drop this snippet into your Hermes gateway startup BEFORE any
delegate_task(background=True) call.
"""
import os

from agentmint_hermes_runner import (
    AgentMintDispatcher,
    BearerAuth,
    install_delegate_task_wrapper,
)


def main() -> None:
    dispatcher = AgentMintDispatcher(
        auth=BearerAuth(jwt=os.environ["AGENTMINT_JWT"]),
    )

    # No default_agent_name → ephemeral mode auto-detected.
    install_delegate_task_wrapper(
        dispatcher=dispatcher,
        # Optional overrides:
        ephemeral_harness="opencode",
        ephemeral_model="openrouter/fusion",
        poll_interval=5.0,
    )
    # That's it. Every delegate_task(background=True) inside Hermes now
    # mints a fresh AgentMint subagent, runs it, and cleans up on completion.


if __name__ == "__main__":
    main()
