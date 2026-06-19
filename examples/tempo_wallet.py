"""Dispatch a generic 'hello' goal via Tempo (per-call USDC.e on eip155:4217).

Prerequisites:
    1. tempo wallet login (one-time, browser-based)
    2. Funded USDC.e on Tempo mainnet (eip155:4217)
    3. tempo-request@0.5.2 — newer versions break with "Invalid base64 JSON
       header". Downgrade with: `tempo cli 0.0.0 downgrade tempo request cli to 0.5.2`
    4. Pre-mint a subagent once:
       tempo request -X POST --json \\
         '{"jsonrpc":"2.0","id":1,"method":"agent.create","params":{"name":"hello-bot"}}' \\
         https://api.agentmint.store/a2a
    5. export AGENT_NAME=hello-bot
    6. (optional) export TEMPO_ACCOUNT=<account-name>

Run:
    python examples/tempo_wallet.py
"""
import os

from agentmint_hermes_runner import AgentMintDispatcher, TempoAuth


def main() -> None:
    dispatcher = AgentMintDispatcher(
        endpoint=os.environ.get("AGENTMINT_ENDPOINT", "https://api.agentmint.store/a2a"),
        auth=TempoAuth(account=os.environ.get("TEMPO_ACCOUNT")),
    )

    agent_name = os.environ["AGENT_NAME"]
    result = dispatcher.dispatch(
        agent_name=agent_name,
        goal="Say hello and tell me what you remember from prior calls.",
    )
    print(result)


if __name__ == "__main__":
    main()
