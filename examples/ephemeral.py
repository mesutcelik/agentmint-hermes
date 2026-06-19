"""Ephemeral mode — the default (v0.6.0+).

Every `delegate_task(background=True)` call in Hermes hits the server-side
`agent.run.stateless` method. AgentMint runs the prompt on a warm cloud
worker, wipes state, and returns the result. Zero cold start; no
client-side mint or destroy.

Matches Hermes-native delegate_task semantics (stateless per call) on
isolated AgentMint cloud sandboxes with server-managed credentials.

Use this when you want:
  - Hermes' native ergonomics preserved (no agent_name, no pre-mint)
  - Multi-subagent fan-out via tasks=[...] (each task is its own
    isolated cloud sandbox automatically)
  - Cloud isolation without local CPU/RAM consumption

Don't use this when you want a subagent that REMEMBERS across calls —
for that, use the persistent.py setup OR the agentmint_delegate plugin
tool (see plugin.py).

Prerequisites:
    1. Bootstrap a Stripe-Link credit wallet (one-time, min $10):
       link-cli mpp pay https://api.agentmint.store/a2a -X POST \\
         -H 'Content-Type: application/json' \\
         -d '{"jsonrpc":"2.0","id":1,"method":"credits.topup","params":{"amount_usd":10}}'
       export AGENTMINT_JWT=<access_token from the response>

    2. pip install agentmint-hermes-runner (in Hermes' venv)

Cost per call: smoothed $0.01–$0.075 USDC (same band as all-inclusive
`agent.run`), keyed at the principal level. Requires AgentMint API ≥ 0.8.0.

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
        poll_interval=5.0,
    )
    # That's it. Every delegate_task(background=True) inside Hermes now
    # hits agent.run.stateless server-side. AgentMint owns the lifecycle.


if __name__ == "__main__":
    main()
