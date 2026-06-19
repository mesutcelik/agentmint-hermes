# agentmint-hermes-runner

Python adapter that bridges Hermes' `delegate_task(background=True)` to AgentMint's cloud subagents. Three setup paths — pick one (or combine):

| Mode | What it does | Best for |
|---|---|---|
| **Ephemeral** *(default)* | Each `delegate_task(background=True)` mints a fresh AgentMint subagent, runs it, deletes it. Matches Hermes-native stateless semantics, runs on isolated cloud sandbox. | Stateless fan-out + cloud isolation |
| **Persistent** | Every `delegate_task(background=True)` routes to ONE pre-minted named subagent whose `/workspace/MEMORY.md` accumulates across calls. | One long-running specialist that learns |
| **Plugin tool** | Hermes auto-discovers a new `agentmint_delegate(agent_name, goal, ...)` tool from this package's entry-point. LLM picks the subagent per call. | Fleet of named specialists, LLM-driven routing |

> The Hermes-installable skill that drives this adapter lives in a separate catalog repo: **[mesutcelik/agentmint-skills](https://github.com/mesutcelik/agentmint-skills)** — `hermes skills install mesutcelik/agentmint-skills/hermes-delegate-task`. The skill references this package by its PyPI name (`pip install agentmint-hermes-runner`).

## Status

**v0.5.0** — alpha. Auth backends: `BearerAuth` (Stripe-Link), `TempoAuth` (Tempo USDC.e — Tier 1 direct only; the `delegate_task` patches require Bearer for status polling). Requires AgentMint API ≥ 0.7.0.

## Setup — ephemeral (default)

```python
import os
from agentmint_hermes_runner import AgentMintDispatcher, BearerAuth, install_delegate_task_wrapper

dispatcher = AgentMintDispatcher(auth=BearerAuth(jwt=os.environ["AGENTMINT_JWT"]))
install_delegate_task_wrapper(dispatcher)   # no default_agent_name → ephemeral
```

Each `delegate_task(background=True)` mints a fresh `ephem-<uuid>` subagent, dispatches, polls until done, pushes the completion to Hermes' `completion_queue`, and deletes the subagent. ~$0.16 USDC per call (0.10 create + 0.05 run + 0.01 delete).

No pre-mint step. Multiple `delegate_task` calls in flight = multiple isolated subagents in parallel.

## Setup — persistent (specialist)

```python
dispatcher = AgentMintDispatcher(auth=BearerAuth(jwt=os.environ["AGENTMINT_JWT"]))
install_delegate_task_wrapper(dispatcher, default_agent_name="default-worker")
```

Pre-mint `default-worker` once (`agent.create` via curl). Every `delegate_task(background=True)` lands in that one subagent forever — its MEMORY.md grows over time. ~$0.05/call after the one-time $0.10 mint.

## Setup — plugin tool (named fleet)

```python
from agentmint_hermes_runner import AgentMintDispatcher, BearerAuth, set_dispatcher

dispatcher = AgentMintDispatcher(auth=BearerAuth(jwt=os.environ["AGENTMINT_JWT"]))
set_dispatcher(dispatcher)   # registers agentmint_delegate via the entry-point
```

Hermes' plugin discovery (`hermes_agent.plugins` entry-point) auto-registers `agentmint_delegate` when this package is pip-installed. The LLM can now call:

```
agentmint_delegate(agent_name="reviewer-myrepo", goal="Review the diff", async_=True)
agentmint_delegate(agent_name="support-acme",    goal="Reply to ticket #42", async_=True)
```

Each dispatch goes to a different named subagent (pre-minted via curl). Combine with `install_delegate_task_wrapper(dispatcher)` to ALSO have `delegate_task(background=True)` go ephemeral.

See `examples/ephemeral.py`, `examples/persistent.py`, `examples/plugin.py` for complete operator setup snippets.

## Install

```bash
pip install agentmint-hermes-runner
```

## Test

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## Lower-level surface

If you want to drive AgentMint directly without the `delegate_task` patch or the plugin tool:

```python
result = dispatcher.dispatch(
    agent_name="reviewer-myrepo",
    goal="Review the diff and flag risks.",
    context="Project at /workspace, Python 3.11, uses Flask + PyJWT.",
    toolsets=["terminal", "file"],     # "web" raises UnsupportedToolset
    role="leaf",                        # or "orchestrator"
    max_iterations=50,
    child_timeout_seconds=600,
)

# Batch dispatch (Hermes tasks=[…] analog):
results = dispatcher.dispatch_batch(
    tasks=[
        Task(agent_name="researcher-wasm", goal="WASM 2026 survey"),
        Task(agent_name="researcher-riscv", goal="RISC-V 2026 survey"),
    ],
    max_concurrent_children=3,
    child_timeout_seconds=900,
)
```

## Known unsupported

- **`toolsets=["web"]`** — no canonical AgentMint web-fetch skill yet. Raises `UnsupportedToolset`.
- **`max_spawn_depth`** — AgentMint sandboxes aren't structurally bounded by depth.
- **Tempo + the `delegate_task` patches** — polling against `agent.run.status` is Bearer-only. Tempo customers can use Tier 1 (direct curl) but not the install/plugin paths above. A future hybrid-auth release may close this.

## License

MIT
