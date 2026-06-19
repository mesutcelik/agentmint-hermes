import pytest

from agentmint_hermes_runner.exceptions import UnsupportedToolset
from agentmint_hermes_runner.translation import (
    DEFAULT_TOOLSETS,
    ROLE_HINTS,
    TOOLSET_RESTRICTION_HINTS,
    compose_prompt,
)


def test_compose_goal_only():
    out = compose_prompt(goal="say hello")
    assert out == "## Goal\nsay hello"


def test_compose_goal_and_context():
    out = compose_prompt(goal="say hello", context="we already greeted once")
    assert "## Goal\nsay hello" in out
    assert "## Context\nwe already greeted once" in out


def test_compose_strips_whitespace():
    out = compose_prompt(goal="  say hello  \n", context="  ctx\n")
    assert "say hello" in out
    assert "  say hello" not in out  # stripped


def test_compose_requires_goal():
    with pytest.raises(ValueError, match="goal is required"):
        compose_prompt(goal="")
    with pytest.raises(ValueError, match="goal is required"):
        compose_prompt(goal="   ")


def test_toolsets_restrict_unlisted():
    out = compose_prompt(goal="x", toolsets=["terminal"])  # excludes file
    assert TOOLSET_RESTRICTION_HINTS["file"] in out
    assert TOOLSET_RESTRICTION_HINTS["terminal"] not in out


def test_toolsets_none_means_no_restrictions():
    out = compose_prompt(goal="x", toolsets=None)
    for hint in TOOLSET_RESTRICTION_HINTS.values():
        assert hint not in out


def test_toolsets_full_set_means_no_restrictions():
    out = compose_prompt(goal="x", toolsets=list(DEFAULT_TOOLSETS))
    for hint in TOOLSET_RESTRICTION_HINTS.values():
        assert hint not in out


def test_web_toolset_raises_unsupported():
    with pytest.raises(UnsupportedToolset, match="not supported"):
        compose_prompt(goal="x", toolsets=["terminal", "file", "web"])


def test_web_toolset_alone_raises():
    with pytest.raises(UnsupportedToolset, match="'web'"):
        compose_prompt(goal="x", toolsets=["web"])


def test_role_leaf_hint():
    out = compose_prompt(goal="x", role="leaf")
    assert ROLE_HINTS["leaf"] in out


def test_role_orchestrator_hint():
    out = compose_prompt(goal="x", role="orchestrator")
    assert ROLE_HINTS["orchestrator"] in out


def test_role_unknown_raises():
    with pytest.raises(ValueError, match="unknown role"):
        compose_prompt(goal="x", role="banana")


def test_max_iterations_appears_as_hint():
    out = compose_prompt(goal="x", max_iterations=42)
    assert "~42 actions" in out


def test_all_sections_in_order():
    out = compose_prompt(
        goal="g",
        context="c",
        toolsets=["terminal"],
        role="leaf",
        max_iterations=10,
    )
    g_idx = out.index("## Goal")
    c_idx = out.index("## Context")
    k_idx = out.index("## Constraints")
    assert g_idx < c_idx < k_idx
