# agentmint-hermes-runner

Python adapter that routes Hermes `delegate_task(background=True)` to named, persistent AgentMint subagents.

> The Hermes-installable skill that drives this adapter lives in a separate catalog repo: **[mesutcelik/agentmint-skills](https://github.com/mesutcelik/agentmint-skills)** — install via `hermes skills install mesutcelik/agentmint-skills/hermes-delegate-task`. The skill's setup steps reference this package by its PyPI name (`pip install agentmint-hermes-runner`).

## Status

**v0.4.0** — alpha. Auth backends: `BearerAuth` (Stripe-Link), `TempoAuth` (Tempo USDC.e). Polling-only delivery. Requires AgentMint API ≥ 0.7.0 for the `agent.run.status` polling endpoint.

## Three-line Hermes wiring (Strategy B)

```python
import os
from agentmint_hermes_runner import (
    AgentMintDispatcher, BearerAuth, install_delegate_task_wrapper,
)

dispatcher = AgentMintDispatcher(auth=BearerAuth(jwt=os.environ["AGENTMINT_JWT"]))
install_delegate_task_wrapper(dispatcher, default_agent_name="default-worker")
```

Every `delegate_task(background=True)` inside Hermes now routes to AgentMint's `default-worker` subagent. Its `/workspace/MEMORY.md` accumulates across every delegation. A daemon thread polls `agent.run.status` (free, Bearer-only) every 5 s and pushes completions onto Hermes' `completion_queue` directly. No HTTPS endpoint, no webhook secret.

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

## Surface

```python
from agentmint_hermes_runner import (
    AgentMintDispatcher,
    BearerAuth, TempoAuth,
    Task,
    install_delegate_task_wrapper,
)

# Single dispatch (Hermes delegate_task analog):
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

- **`toolsets=["web"]`** — no canonical AgentMint web-fetch skill yet. The supported harnesses (claude-code / codex / opencode) have built-in web access, but we don't expose a Hermes-symmetric toolset for it. Raises `UnsupportedToolset` at compose time.
- **`max_spawn_depth`** — AgentMint sandboxes aren't structurally bounded by depth.

## License

MIT
