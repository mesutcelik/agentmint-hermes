"""Strategy B: wire AgentMint into Hermes so `delegate_task(background=True)`
transparently routes to a persistent AgentMint subagent.

Prerequisites:
    1. Bootstrap an AgentMint credit wallet via link-cli (one-time, $10 min):
       link-cli mpp pay https://api.agentmint.store/a2a -X POST \\
         -H 'Content-Type: application/json' \\
         -d '{"jsonrpc":"2.0","id":1,"method":"credits.topup","params":{"amount_usd":10}}'
       export AGENTMINT_JWT=<the access_token from the response>

    2. Pre-mint your subagent (this is the entity that will REMEMBER across
       every Hermes delegation):
       curl -X POST https://api.agentmint.store/a2a \\
         -H "Authorization: Bearer $AGENTMINT_JWT" \\
         -H 'Content-Type: application/json' \\
         -d '{"jsonrpc":"2.0","id":1,"method":"agent.create",
              "params":{"name":"default-worker","harness":"opencode",
                        "model":"openrouter/fusion"}}'

    3. pip install agentmint-hermes-runner inside Hermes' virtualenv.

Drop this snippet into your Hermes gateway startup code (before any
delegate_task call). That's the entire wiring — no HTTPS endpoint, no
webhook secret, no HTTP route.
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

    install_delegate_task_wrapper(
        dispatcher=dispatcher,
        default_agent_name="default-worker",
        poll_interval=5.0,
    )
    # From here on, every delegate_task(background=True) call inside Hermes
    # routes to the AgentMint subagent named "default-worker". Its
    # /workspace/MEMORY.md accumulates context across every delegation.


if __name__ == "__main__":
    main()
