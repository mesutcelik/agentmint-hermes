"""Lower-level wiring example. The 0.11.0+ runner auto-wires this for
you via the `hermes_agent.plugins` entry-point in `autoload.py`; you
only need this code if you're injecting the JWT from a secret manager
or otherwise can't use the on-disk `~/.agentmint/credentials.json`
cache that `agentmint-hermes-init` writes.

Routing model (opt-in):
    - LLM calls delegate_task with `toolsets=["agentmint-<name>"]`
      → routes to that AgentMint subagent
    - LLM omits the directive
      → falls through to Hermes-native delegate_task untouched

No catch-all. AgentMint is never selected transparently — the LLM
has to opt in via the toolsets directive. Install the routing-convention
skill so it knows the convention:
    hermes skills install mesutcelik/agentmint-skills/hermes-delegate-task

Prerequisites:
    1. Bootstrap an AgentMint credit wallet on any rail (one-time, $1 min):

       # Stripe-Link via link-cli:
       link-cli mpp pay https://api.agentmint.store/a2a -X POST \\
         -H 'Content-Type: application/json' \\
         -d '{"jsonrpc":"2.0","id":1,"method":"credits.topup","params":{"amount_usd":5}}'
       # x402 Base / Tempo MPP variants are in SKILL.md.

       export AGENTMINT_JWT=<the access_token from the response>

    2. Mint your subagents (one per use case). Example — a code-review
       specialist that uses the `pr-review` skill:

       curl -X POST https://api.agentmint.store/a2a \\
         -H "Authorization: Bearer $AGENTMINT_JWT" \\
         -H 'Content-Type: application/json' \\
         -d '{"jsonrpc":"2.0","id":1,"method":"agent.create",
              "params":{"name":"pr-reviewer","mode":"all-inclusive",
                        "persona":"You review GitHub PRs. Follow the pr-review skill exactly.",
                        "skills":["mesutcelik/agentmint-skills/pr-review"]}}'

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
        default_agent_name=None,   # opt-in routing only; no catch-all
        poll_interval=5.0,
    )
    # Routing rules now active:
    #   - delegate_task(background=True, toolsets=["agentmint-pr-reviewer", ...])
    #     → routes to "pr-reviewer" (must be pre-minted)
    #   - delegate_task(background=True, ...) with no agentmint-* entry
    #     in toolsets → falls through to Hermes-native delegate_task
    #   - any number of real toolsets ("terminal", "file") co-exist
    #     with the routing directive; first agentmint-* match wins


if __name__ == "__main__":
    main()
