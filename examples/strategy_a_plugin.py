"""Strategy A — register `agentmint_delegate` as an extra Hermes tool.

Coexists with (and is independent of) install_delegate_task_wrapper.
The LLM gets a NEW tool alongside delegate_task:

    agentmint_delegate(
        agent_name="reviewer-myrepo",
        goal="Review the diff in /workspace/pr-42",
        context="Project uses Flask + PyJWT",
        async_=True,
    )

The LLM picks the subagent by name per call. Use this when you want a
FLEET of named persistent specialists (the persistence value prop), each
addressable by the LLM at dispatch time.

Hermes auto-discovers this plugin via the `hermes_agent.plugins`
entry-point declared in pyproject.toml — pip-installing
agentmint-hermes-runner is enough to expose the tool. Operator's only
job is to bind the dispatcher BEFORE Hermes builds its tool registry:

Prerequisites:
    1. Bootstrap a Stripe-Link credit wallet (one-time, min $10):
       link-cli mpp pay https://api.agentmint.store/a2a -X POST \\
         -H 'Content-Type: application/json' \\
         -d '{"jsonrpc":"2.0","id":1,"method":"credits.topup","params":{"amount_usd":10}}'
       export AGENTMINT_JWT=<access_token from the response>

    2. Pre-mint each specialist subagent the LLM may want to address:
       curl -X POST https://api.agentmint.store/a2a \\
         -H "Authorization: Bearer $AGENTMINT_JWT" \\
         -H 'Content-Type: application/json' \\
         -d '{"jsonrpc":"2.0","id":1,"method":"agent.create",
              "params":{"name":"reviewer-myrepo","harness":"opencode",
                        "model":"openrouter/fusion"}}'
       # ...one curl per specialist

    3. pip install agentmint-hermes-runner (in Hermes' venv)

Drop this snippet into your Hermes gateway startup code.
"""
import os

from agentmint_hermes_runner import (
    AgentMintDispatcher,
    BearerAuth,
    set_dispatcher,
)


def main() -> None:
    dispatcher = AgentMintDispatcher(
        auth=BearerAuth(jwt=os.environ["AGENTMINT_JWT"]),
    )

    # Bind the dispatcher the plugin handler will use. Hermes' plugin
    # discovery already registered the `agentmint_delegate` tool from
    # this package's entry-point at startup — set_dispatcher just
    # connects it to your auth.
    set_dispatcher(dispatcher)

    # From here on, the LLM in any Hermes session can call:
    #     agentmint_delegate(agent_name="reviewer-myrepo",
    #                        goal="Review the diff", async_=True)
    # ...and dispatch to any subagent the principal owns. Results
    # re-inject as new conversation turns when ready (same mechanism as
    # delegate_task(background=True)).

    # OPTIONAL: combine with install_delegate_task_wrapper to ALSO patch
    # delegate_task — that lets the LLM use whichever tool is appropriate:
    #
    #   - delegate_task(background=True)        → ephemeral cloud subagent
    #   - agentmint_delegate(agent_name=..., …) → named persistent specialist
    #
    # from agentmint_hermes_runner import install_delegate_task_wrapper
    # install_delegate_task_wrapper(dispatcher)   # ephemeral mode


if __name__ == "__main__":
    main()
