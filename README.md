# agentmint-hermes-runner

Python adapter that bridges Hermes' `delegate_task(background=True)` to **named, persistent AgentMint subagents** — specialists that accumulate `/workspace/MEMORY.md` across calls.

One install path: monkey-patch `delegate_task` so every async delegation can route to AgentMint. The LLM picks the target subagent via either `default_agent_name` (set at install) or by including `"agentmint-<name>"` in the `toolsets` list (per-call routing).

> The Hermes-installable skill that drives this adapter lives in a separate catalog repo: **[mesutcelik/agentmint-skills](https://github.com/mesutcelik/agentmint-skills)** — `hermes skills install mesutcelik/agentmint-skills/hermes-delegate-task`. The skill references this package by its PyPI name (`pip install agentmint-hermes-runner`).

## Status

**v0.9.0** — alpha. Auth backends: `BearerAuth` (any rail — Stripe-Link / x402 / Tempo MPP), `TempoAuth` (Tempo USDC.e — Tier 1 direct only; the `delegate_task` patches require Bearer).

**Breaking change in 0.9.0**: the `agentmint_delegate` plugin tool has been removed. There is now exactly one routing surface — the patched `delegate_task`. If you were using `set_dispatcher(...)` to register the plugin tool, replace it with `install_delegate_task_wrapper(dispatcher, default_agent_name="...")`. The `toolsets=["agentmint-<name>"]` per-call routing hack keeps working unchanged.

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

See `examples/persistent.py` for a complete operator setup snippet.

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

If you want to drive AgentMint directly without the `delegate_task` patch:

```python
result = dispatcher.dispatch(
    agent_name="reviewer-myrepo",
    goal="Review the diff at /workspace/pr-42.diff and flag risks.",
    context="Project at /workspace, Python 3.11, uses Flask + PyJWT.",
    toolsets=["terminal", "file"],     # "web" raises UnsupportedToolset
    role="leaf",                        # or "orchestrator"
    max_iterations=50,
    child_timeout_seconds=600,
    workspace_files=[                   # ship inputs into the sandbox before the run
        {"path": "/workspace/pr-42.diff", "content": "diff --git a/foo ..."},
    ],
    cleanup_paths=["/workspace/pr-42.diff"],  # wipe them after the run
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
