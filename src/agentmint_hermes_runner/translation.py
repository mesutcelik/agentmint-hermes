"""Translate Hermes `delegate_task` semantics into AgentMint /a2a calls.

The translation is deliberately client-side and prompt-based:

  - AgentMint's `agent.run` has a single `prompt` field. Hermes' `goal`
    + `context` are concatenated into that prompt under labelled sections.
  - AgentMint sandboxes always have terminal + filesystem. Restrictions
    can't be enforced structurally — they become soft system-prompt hints
    ("Do not run shell commands.").
  - Role (leaf vs orchestrator) and max_iterations are similarly soft —
    the harness reads them out of the prompt if it knows to.

The `"web"` toolset is **unsupported in v0.2**. AgentMint's three
harnesses (claude-code / codex / opencode) all have a built-in WebFetch
tool, but we don't yet ship a Hermes-symmetric web-fetch skill that the
adapter can advertise or restrict — passing `"web"` in toolsets raises
`UnsupportedToolset`. Track: GitHub issues on agentmint-hermes.

This mirrors Hermes' load-bearing rule: "The parent agent must pass
everything the subagent needs in the call" — but where Hermes spawns a
fresh AIAgent, AgentMint dispatches to a persistent sandbox whose
`/workspace/MEMORY.md` accumulates context across calls.
"""

from .exceptions import UnsupportedToolset

# Web is intentionally excluded — see module docstring + UnsupportedToolset.
DEFAULT_TOOLSETS: tuple[str, ...] = ("terminal", "file")

TOOLSET_RESTRICTION_HINTS: dict[str, str] = {
    "terminal": "Do not run shell commands.",
    "file": "Do not read or write files outside /workspace.",
}

UNSUPPORTED_TOOLSETS: frozenset[str] = frozenset({"web"})

ROLE_HINTS: dict[str, str] = {
    "leaf": (
        "You are a leaf subagent. Focus on completing this task end-to-end. "
        "Do not delegate further."
    ),
    "orchestrator": (
        "You are an orchestrator subagent. You may break complex subtasks "
        "into further delegations if your installed skills allow it."
    ),
}


def compose_prompt(
    goal: str,
    context: str | None = None,
    toolsets: list[str] | None = None,
    role: str | None = None,
    max_iterations: int | None = None,
) -> str:
    """Concat goal + context + soft-enforcement hints into one prompt string.

    `toolsets` is interpreted as "the only allowed toolsets" — any default
    toolset NOT listed becomes a restriction hint. Pass `None` to mean
    "no restrictions" (Hermes default). Listing `"web"` raises
    `UnsupportedToolset`.
    """
    if not goal or not goal.strip():
        raise ValueError("goal is required")

    if toolsets is not None:
        unsupported = sorted(t for t in toolsets if t in UNSUPPORTED_TOOLSETS)
        if unsupported:
            raise UnsupportedToolset(
                f"toolset(s) {unsupported!r} not supported in this version — "
                f"AgentMint does not yet ship a Hermes-symmetric skill for them. "
                f"Drop them from the toolsets list to proceed."
            )

    sections: list[str] = [f"## Goal\n{goal.strip()}"]
    if context and context.strip():
        sections.append(f"## Context\n{context.strip()}")

    hints: list[str] = []
    if toolsets is not None:
        restricted = [t for t in DEFAULT_TOOLSETS if t not in toolsets]
        hints.extend(TOOLSET_RESTRICTION_HINTS[t] for t in restricted)
    if role:
        if role not in ROLE_HINTS:
            raise ValueError(f"unknown role: {role!r} (expected 'leaf' or 'orchestrator')")
        hints.append(ROLE_HINTS[role])
    if max_iterations:
        hints.append(f"Soft iteration budget: ~{max_iterations} actions.")

    if hints:
        sections.append("## Constraints\n" + "\n".join(f"- {h}" for h in hints))

    return "\n\n".join(sections)
