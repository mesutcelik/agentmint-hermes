"""Dispatch a generic 'hello' goal to a named AgentMint subagent via Stripe-Link.

Prerequisites:
    1. Bootstrap a credit wallet via link-cli (one-time, min $10):
       link-cli mpp pay https://api.agentmint.store/a2a \\
         -X POST -H 'Content-Type: application/json' \\
         -d '{"jsonrpc":"2.0","id":1,"method":"credits.topup","params":{"amount_usd":10}}'
       The response includes `result.access_token` — the wallet JWT.

    2. export AGENTMINT_JWT=<the jwt>
    3. Pre-mint a subagent once (or call dispatcher.create() in code):
       agentmint agent create --name hello-bot
    4. export AGENT_NAME=hello-bot

Run:
    python examples/stripe_link.py
"""
import os

from agentmint_hermes_runner import AgentMintDispatcher, BearerAuth


def main() -> None:
    dispatcher = AgentMintDispatcher(
        endpoint=os.environ.get("AGENTMINT_ENDPOINT", "https://api.agentmint.store/a2a"),
        auth=BearerAuth(jwt=os.environ["AGENTMINT_JWT"]),
    )

    agent_name = os.environ["AGENT_NAME"]
    result = dispatcher.dispatch(
        agent_name=agent_name,
        goal="Say hello and tell me what you remember from prior calls.",
    )
    print(result)


if __name__ == "__main__":
    main()
