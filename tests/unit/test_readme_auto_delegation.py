"""Guard the automatic-delegation guidance in the README.

Two real runs on stock Herdr showed that installing the skill alone did not make
a master agent route through Jev, while one standing instruction did. The README
must say that plainly and give the exact line, and it must explain that a
harness's own interactive startup screen blocks task delivery even though
`spawn` reports `started`.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
README = ROOT / "README.md"

STANDING_INSTRUCTION = (
    "When you delegate work to child agents in Herdr, spawn them through "
    "herdr-jev-router (use the herdr-jev-router skill), not built-in subagents "
    "and not `herdr agent start`."
)


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _section(title: str) -> str:
    """Return the body of one level-two README section."""

    match = re.search(
        rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)",
        _readme(),
        re.DOTALL | re.MULTILINE,
    )
    assert match is not None, f"README has no '{title}' section"
    return match.group(1)


def test_master_agent_section_says_the_skill_alone_was_not_enough() -> None:
    section = _section("Make your master agent use it").lower()

    assert "skill" in section
    assert "not enough" in section or "was not enough" in section
    assert "required" in section


def test_master_agent_section_gives_the_exact_standing_instruction() -> None:
    assert STANDING_INSTRUCTION in _readme()


def test_troubleshooting_names_the_blocking_startup_screens() -> None:
    section = _section("Troubleshooting").lower()

    assert "update available" in section
    assert "new-model announcement" in section
    assert "folder trust" in section


def test_troubleshooting_says_to_clear_the_screen_by_hand_then_retry() -> None:
    section = _section("Troubleshooting").lower()

    assert "by hand" in section
    assert "same directory" in section
    assert "retry" in section


def test_troubleshooting_explains_started_despite_a_blocked_child() -> None:
    section = _section("Troubleshooting").lower()

    assert "started" in section
    assert "ready" in section
