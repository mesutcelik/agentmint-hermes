# agentmint-hermes-runner

Python adapter that bridges Hermes' `delegate_task(background=True)` to **named, persistent AgentMint subagents** — specialists that accumulate `/workspace/MEMORY.md` across calls.

One install path: monkey-patch `delegate_task` so every async delegation can route to AgentMint. The LLM picks the target subagent via either `default_agent_name` (set at install) or by including `"agentmint-<name>"` in the `toolsets` list (per-call routing).

> The Hermes-installable skill that drives this adapter lives in a separate catalog repo: **[mesutcelik/agentmint-skills](https://github.com/mesutcelik/agentmint-skills)** — `hermes skills install mesutcelik/agentmint-skills/hermes-delegate-task`. The skill references this package by its PyPI name (`pip install agentmint-hermes-runner`).

## Status

**v0.12.0** — alpha. Auth backends: `BearerAuth` (any rail — Stripe-Link / x402 / Tempo MPP), `TempoAuth` (Tempo USDC.e — Tier 1 direct only; the `delegate_task` patches require Bearer).

**Breaking change in 0.12.0**: dropped the `agentmint-hermes-init` CLI. Operators bootstrap a JWT via [agentmint.store/SKILL.md](https://agentmint.store/SKILL.md) (any rail), then either set `$AGENTMINT_JWT` in Hermes' env OR write the JWT into `~/.agentmint/credentials.json`. The autoload entry-point reads from either source at Hermes boot.

## Routing model

**Opt-in only.** The patched `delegate_task`:

- LLM includes `"agentmint-<name>"` in the `toolsets` list → routes to that AgentMint subagent
- LLM does NOT include the directive → falls through to Hermes-native `delegate_task` unchanged

There is no catch-all default. AgentMint is never selected transparently — the LLM has to consciously opt in by emitting the toolsets directive. Install the `hermes-delegate-task` skill so the LLM knows the convention.

If you want a catch-all for a specific deployment, set `$AGENTMINT_DEFAULT_AGENT_NAME` in Hermes' env before boot — explicit override only.

### Setup

```bash
# 1. Install the runner
pip install agentmint-hermes-runner

# 2. Bootstrap a JWT via the AgentMint API — pick a rail, topup ≥ $1.
#    See https://agentmint.store/SKILL.md for the per-rail curl/CLI flow.
#    Then put the resulting JWT somewhere the autoload can find it:
export AGENTMINT_JWT=<the access_token>
#    OR write it to ~/.agentmint/credentials.json (shape below).

# 3. Install the routing-convention skill so the LLM knows the
#    `toolsets=["agentmint-<name>"]` directive exists.
hermes skills install mesutcelik/agentmint-skills/hermes-delegate-task

# 4. Restart Hermes
#    The autoload entry-point fires, reads $AGENTMINT_JWT (or the
#    credentials cache), and auto-wires `delegate_task` in opt-in mode.
```

If you prefer the file cache over an env var (e.g. so the JWT survives shell restarts), `~/.agentmint/credentials.json` has this shape — same as what the agentmint CLI / link-cli flows produce:

```json
{
  "tokens": {
    "link_stripe:cus_…": {
      "access_token": "eyJhbGciOiJI…",
      "saved_at": 1782152633
    }
  }
}
```

Permissions: `0700` on the directory, `0600` on the file. The autoload picks the first token in the map; set `AGENTMINT_JWT` explicitly to disambiguate if multiple principals are cached.

Then mint subagents per use case (one curl per specialist). For example, a code-review specialist:

```bash
JWT=$(jq -r '.tokens | to_entries[0].value.access_token' ~/.agentmint/credentials.json)
curl -X POST https://api.agentmint.store/a2a \
  -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"agent.create","params":{
    "name":"pr-reviewer",
    "mode":"all-inclusive",
    "persona":"You review GitHub PRs. Follow the pr-review skill exactly.",
    "skills":["mesutcelik/agentmint-skills/pr-review"]
  }}'
```

After that, the Hermes LLM can dispatch to it:

```python
delegate_task(
    background=True,
    goal="Review PR 42 in owner/repo",
    toolsets=["terminal", "file", "agentmint-pr-reviewer"],
)
```

If you'd rather wire the adapter by hand (e.g. injecting the JWT from a secret manager, not a file on disk), the lower-level API still works:

```python
import os
from agentmint_hermes_runner import AgentMintDispatcher, BearerAuth, install_delegate_task_wrapper

dispatcher = AgentMintDispatcher(auth=BearerAuth(jwt=os.environ["AGENTMINT_JWT"]))
install_delegate_task_wrapper(dispatcher, default_agent_name=None)
```

The autoload entry-point becomes a no-op if `AGENTMINT_JWT` is unset AND `~/.agentmint/credentials.json` is absent — safe to leave installed even in setups that bring their own wiring.

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
