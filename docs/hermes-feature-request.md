# Feature request: per-call dispatch target on `delegate_task`

## Problem

`tools/delegate_tool.py`'s `delegate_task(...)` signature has no mechanism for an external plugin to route a specific call to a custom dispatcher. Today, plugins registered via the `hermes_agent.plugins` entry-point can add NEW tools (`PluginContext.register_tool`) but cannot extend `delegate_task`'s parameters.

Our use case (AgentMint × Hermes integration via `agentmint-hermes-runner`): we want the LLM to be able to dispatch a `delegate_task(background=True)` call to a specific named AgentMint subagent — a persistent specialist (PR reviewer, support agent, etc.) whose `/workspace/MEMORY.md` accumulates across calls.

The current workaround in v0.7.0 of the adapter: encode the routing target in `toolsets` (e.g. `toolsets=["terminal", "file", "agentmint-pr-reviewer"]`), parse it via a monkey-patch on `dispatch_async_delegation`, strip it before the prompt is composed. This works but:

- Fragile (string-parsing convention; collides with any future Hermes toolset named with `agentmint-` prefix)
- Opaque (LLM has no schema-level signal that this is a routing directive — it looks like a toolset)
- Adds noise to the toolset surface

## Proposal

Add ONE of the following to `delegate_task`'s signature:

### Option A — `dispatcher: str | None = None`

Hermes maintains a registry of named dispatchers; plugins register theirs at startup. The LLM passes a fully-qualified target:

```python
delegate_task(
    goal="Review the diff",
    background=True,
    dispatcher="agentmint:pr-reviewer",
)
```

Plugins register via `PluginContext.register_dispatcher("agentmint", handler_fn)`. Hermes routes calls whose `dispatcher` arg starts with `"agentmint:"` to the registered handler with the remainder of the string.

**Pros:** discoverable in the tool schema (LLM sees `dispatcher` as a first-class arg); namespaced (no collision); plugins can advertise multiple dispatchers.

**Cons:** new registry concept to add to Hermes; bigger API surface.

### Option B — `metadata: dict | None = None` *(lower impact)*

Add a generic pass-through dict that plugins can read their own keys from:

```python
delegate_task(
    goal="Review the diff",
    background=True,
    metadata={"agentmint_agent": "pr-reviewer"},
)
```

Plugins parse `metadata` keys they own. No registry; existing monkey-patch path still works but is now reading a structured arg instead of grepping `toolsets`.

**Pros:** smaller surface; backwards-compatible; lets multiple plugins coexist without naming a "primary" dispatcher.

**Cons:** less discoverable (LLM has to know to set the right key per plugin); easier to misuse.

## Why this matters

The current `delegate_task` surface forces external integrations into two suboptimal patterns:

1. **Replace the tool entirely** — register a parallel `agentmint_delegate` tool. Works, but the LLM has to know about a separate tool; can't transparently leverage Hermes' existing `delegate_task` ergonomics.
2. **String-parsing workarounds on existing fields** — encode routing in `toolsets`, `goal`, or `context`. Fragile.

A first-class `dispatcher` or `metadata` arg is the missing extension point. It would also benefit other planned integrations (e.g. routing to specialized cloud sandboxes, A/B-testing different model providers, sharding work across regional dispatchers).

## Prior art

- `tools/registry.py:234` — `ToolRegistry.register(...)` is the existing pattern for plugin-supplied tools. A parallel `register_dispatcher(...)` would mirror it.
- `tools/async_delegation.py` (PR #40946) — already has a clean async-dispatch entry point (`dispatch_async_delegation`). Adding a target parameter is incremental.
- `hermes_cli/plugins.py:1490` — entry-point discovery already loads external packages at startup; same path can call `register_dispatcher`.

## Compatibility

Both options are additive — existing callers (no `dispatcher` / no `metadata`) keep current behaviour (Hermes-native subagent). Plugins opt in.

## Asks

1. Confirm the preferred option (A vs B vs alternative).
2. If accepted, point us at the right entry point to PR against (`tools/delegate_tool.py`'s signature + `tools/async_delegation.py`'s dispatcher branch + `hermes_cli/plugins.py`'s `PluginContext`).

We're happy to submit the PR ourselves.
