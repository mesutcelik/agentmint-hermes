# agentmint-hermes-runner

Python adapter that bridges Hermes' `delegate_task(background=True)` to **named, persistent AgentMint subagents** — specialists that accumulate `/workspace/MEMORY.md` across calls. Two setup paths; combine freely:

| Mode | What it does | Best for |
|---|---|---|
| **Persistent** | `delegate_task(background=True)` routes to a pre-minted named subagent (via `default_agent_name`, or via an `agentmint-<name>` entry the LLM passes in `toolsets`). Its `/workspace/MEMORY.md` accumulates across calls. | One long-running specialist that learns OR LLM picking among many specialists per call (via toolset hack — see below) |
| **Plugin tool** | Hermes auto-discovers a new `agentmint_delegate(agent_name, goal, ...)` tool from this package's entry-point. LLM picks the subagent per call. | Cleaner per-call routing when you can expose a new tool to the LLM |

> The Hermes-installable skill that drives this adapter lives in a separate catalog repo: **[mesutcelik/agentmint-skills](https://github.com/mesutcelik/agentmint-skills)** — `hermes skills install mesutcelik/agentmint-skills/hermes-delegate-task`. The skill references this package by its PyPI name (`pip install agentmint-hermes-runner`).

## Status

**v0.7.0** — alpha. Stateless mode was removed (it didn't carry its weight; persistent specialists are AgentMint's actual value prop). Auth backends: `BearerAuth` (Stripe-Link), `TempoAuth` (Tempo USDC.e — Tier 1 direct only; the `delegate_task` patches require Bearer). Requires AgentMint API ≥ 0.7.0.

## Setup — persistent (default routing)

Every `delegate_task(background=True)` lands in one named subagent:

```python
import os
from agentmint_hermes_runner import AgentMintDispatcher, BearerAuth, install_delegate_task_wrapper

dispatcher = AgentMintDispatcher(auth=BearerAuth(jwt=os.environ["AGENTMINT_JWT"]))
install_delegate_task_wrapper(dispatcher, default_agent_name="default-worker")
```

Pre-mint `default-worker` once (`agent.create` via curl). Its `MEMORY.md` grows across every delegation.

## Setup — persistent (per-call routing via toolset hack)

When the LLM should pick a specialist per call, but you don't want to expose a new tool, encode the target name in `toolsets`:

```python
# Operator wires the adapter the same way — default_agent_name is optional:
install_delegate_task_wrapper(dispatcher, default_agent_name="default-worker")

# The LLM then dispatches like this:
delegate_task(
    background=True,
    goal="Review the diff",
    toolsets=["terminal", "file", "agentmint-pr-reviewer-myrepo"],
)
```

The adapter parses `agentmint-pr-reviewer-myrepo` from `toolsets`, routes to that subagent (overriding `default_agent_name`), and strips the entry from the toolset list before composing the prompt.

This is a **workaround** for Hermes' `delegate_task` not accepting a dispatcher-target argument. A formal proposal is in `docs/hermes-feature-request.md` — when an upstream extension lands, this hack will be deprecated.

## Setup — plugin tool (named fleet, cleanest)

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

Combine with `install_delegate_task_wrapper(dispatcher, default_agent_name=...)` if you want BOTH `delegate_task` and `agentmint_delegate` to dispatch to AgentMint.

See `examples/persistent.py` and `examples/plugin.py` for complete operator setup snippets.

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
- **Tempo + the `delegate_task` patches** — polling against `agent.run.status` is Bearer-only. Tempo customers can use Tier 1 (direct curl) but not the install/plugin paths above.

## License

MIT
