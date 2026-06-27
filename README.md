# agentmint-hermes-runner

Python adapter that bridges Hermes' `delegate_task(background=True)` to **named, persistent AgentMint subagents** — specialists that accumulate `/workspace/MEMORY.md` across calls.

One install path: monkey-patch `delegate_task` so every async delegation can route to AgentMint. The LLM picks the target subagent via either `default_agent_name` (set at install) or by including `"agentmint-<name>"` in the `toolsets` list (per-call routing).

> The Hermes-installable skill that drives this adapter lives in a separate catalog repo: **[mesutcelik/agentmint-skills](https://github.com/mesutcelik/agentmint-skills)** — `hermes skills install mesutcelik/agentmint-skills/hermes-delegate-task`. The skill references this package by its PyPI name (`pip install agentmint-hermes-runner`).

## Status

**v0.9.0** — alpha. Auth backends: `BearerAuth` (any rail — Stripe-Link / x402 / Tempo MPP), `TempoAuth` (Tempo USDC.e — Tier 1 direct only; the `delegate_task` patches require Bearer).

**Breaking change in 0.9.0**: the `agentmint_delegate` plugin tool has been removed. There is now exactly one routing surface — the patched `delegate_task`. If you were using `set_dispatcher(...)` to register the plugin tool, replace it with `install_delegate_task_wrapper(dispatcher, default_agent_name="...")`. The `toolsets=["agentmint-<name>"]` per-call routing hack keeps working unchanged.

## Routing model

There are exactly two ways the runner picks the target subagent. They serve different intents — don't conflate them.

| `default_agent_name` | Role |
|---|---|
| **`"general-worker"`** (or similar generic name) | Catch-all for unrouted delegations. Use when Hermes wants to offload arbitrary subtasks to a single persistent worker whose `MEMORY.md` accumulates as the session breadcrumb trail. **The default should always be a generic worker — never a specialist.** Naming a specialist as the default breaks the moment you add a second specialist. |
| `None` (no default) | Unrouted delegations fall through to Hermes-native `delegate_task`. Use when AgentMint involvement is opt-in per call. |

| `toolsets=["agentmint-<name>"]` directive | Role |
|---|---|
| Per-call specialist routing. LLM dispatches to a specific named expert (`pr-reviewer`, `data-analyst`, `slack-bot`, …). Each specialist has its own MEMORY.md. **Always use this for specialists — not the default slot.** |

### Setup — generic offload

Every unrouted `delegate_task(background=True)` lands in one persistent worker:

```python
import os
from agentmint_hermes_runner import AgentMintDispatcher, BearerAuth, install_delegate_task_wrapper

dispatcher = AgentMintDispatcher(auth=BearerAuth(jwt=os.environ["AGENTMINT_JWT"]))
install_delegate_task_wrapper(dispatcher, default_agent_name="general-worker")
```

Pre-mint `general-worker` once (`agent.create` via curl). Its `MEMORY.md` grows across every delegation that doesn't carry a specialist directive.

### Setup — per-call specialist routing

The LLM picks the target specialist on each call via the `toolsets` list:

```python
# Operator setup is identical — generic default + LLM-driven overrides.
install_delegate_task_wrapper(dispatcher, default_agent_name="general-worker")

# The LLM then dispatches like this:
delegate_task(
    background=True,
    goal="Review PR 42 in mesutcelik/agentmint-mono",
    toolsets=["terminal", "file", "agentmint-pr-reviewer"],
)
```

The adapter parses `agentmint-pr-reviewer` from `toolsets`, routes that call to that subagent (overriding `default_agent_name`), and strips the entry from the toolset list before composing the prompt the subagent receives.

This is a **workaround** for Hermes' `delegate_task` not accepting a dispatcher-target argument. A formal proposal is in `docs/hermes-feature-request.md` — when an upstream extension lands, this hack will be deprecated in favor of a first-class `dispatcher` or `metadata` parameter.

### Pattern discipline

- `default_agent_name` → generic worker only (`general-worker`, `default-worker`, etc.)
- Specialists → only via `toolsets=["agentmint-<name>"]`
- Never name a specialist as the default. Specialists scale; defaults are catch-all.

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
