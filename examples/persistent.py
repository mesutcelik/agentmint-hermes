"""Generic-offload wiring: every unrouted `delegate_task(background=True)`
goes to ONE persistent AgentMint subagent ("general-worker"). Its
/workspace/MEMORY.md accumulates context across the whole session.

For per-call specialist routing (e.g. pr-reviewer / data-analyst /
slack-bot), the LLM passes `toolsets=["agentmint-<name>"]` on each
specific call — that overrides the default. See README "Setup —
per-call specialist routing" and the pattern-discipline note.

Pattern discipline:
    - `default_agent_name` is for GENERIC workers only. Never a specialist.
    - Specialists are addressed only via the `toolsets` directive.
    - One catch-all default + N specialist overrides = the recommended
      production setup.

Prerequisites:
    1. Bootstrap an AgentMint credit wallet on any rail (one-time, $1 min):

       # Stripe-Link example (link-cli):
       link-cli mpp pay https://api.agentmint.store/a2a -X POST \\
         -H 'Content-Type: application/json' \\
         -d '{"jsonrpc":"2.0","id":1,"method":"credits.topup","params":{"amount_usd":5}}'
       # x402 Base example: see SKILL.md "Quick examples"
       # Tempo MPP example: tempo-request -X POST --json '...' <url>

       export AGENTMINT_JWT=<the access_token from the response>

    2. Pre-mint your generic worker (kept GENERIC on purpose — see
       discipline note above; if you also want specialists, mint them
       under specialist names like pr-reviewer / data-analyst, and the
       LLM will address them via toolsets):

       curl -X POST https://api.agentmint.store/a2a \\
         -H "Authorization: Bearer $AGENTMINT_JWT" \\
         -H 'Content-Type: application/json' \\
         -d '{"jsonrpc":"2.0","id":1,"method":"agent.create",
              "params":{"name":"general-worker","mode":"all-inclusive",
                        "persona":"General-purpose worker. Handle whatever delegation you receive. Append a 1-2 sentence summary to /workspace/MEMORY.md after each run."}}'

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
        default_agent_name="general-worker",   # catch-all; never a specialist
        poll_interval=5.0,
    )
    # Routing rules now active:
    #   - delegate_task(background=True, ...) with no toolsets directive
    #     → routes to "general-worker"
    #   - delegate_task(background=True, toolsets=["agentmint-pr-reviewer", ...])
    #     → routes to "pr-reviewer" (must be pre-minted)
    #   - any number of "agentmint-<name>" entries can co-exist with other
    #     real toolsets like "terminal", "file"; first agentmint-* match wins


if __name__ == "__main__":
    main()
