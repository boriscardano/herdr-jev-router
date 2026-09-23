"""Keep the Agent Skills file aligned with the real router CLI.

`skills/herdr-jev-router/SKILL.md` is the instruction file a parent agent
reads before delegating a child spawn. If it names a router flag that the
`spawn`, `explain` or `doctor` parser does not accept, the parent follows a
command that cannot work. These tests read the skill, check every router
`--flag` it mentions against the parser, and keep the stock Herdr flags in one
explicit allowlist so the skill cannot drift from the CLI.
"""

import argparse
import re
from pathlib import Path

import herdr_jev_router.cli as cli_module
from herdr_jev_router.harness import OPT_IN_VARIABLES

ROOT = Path(__file__).parents[2]
SKILL = ROOT / "skills" / "herdr-jev-router" / "SKILL.md"

_FLAG = re.compile(r"--[A-Za-z][A-Za-z0-9-]*")
_ROUTER_BLOCK = re.compile(
    r"```console\n(herdr-jev-router (spawn|explain)\b.*?)```", re.DOTALL
)

# Flags owned by the stock Herdr CLI that the skill names for the pane and
# follow commands. They are not router flags, so the parser check cannot cover
# them. `--pane` is deliberately absent because it is also a router flag and the
# parser check must own it.
_STOCK_HERDR_FLAGS = frozenset(
    {
        "--cwd",  # herdr pane split
        "--direction",  # herdr pane split
        "--no-focus",  # herdr pane split
    }
)


def _command_parser(command: str) -> argparse.ArgumentParser:
    """Return the router subparser for one command name."""

    parser = cli_module._parser({})
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[command]
    raise AssertionError("the router parser has no subcommands")


def _parser_flags(command: str) -> frozenset[str]:
    """Return every long option string accepted by one router command."""

    return frozenset(
        option
        for action in _command_parser(command)._actions
        for option in action.option_strings
        if option.startswith("--")
    )


def _skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_skill_exists_with_agent_skills_frontmatter() -> None:
    text = _skill_text()

    assert text.startswith("---\n")
    frontmatter = text.split("---\n", 2)[1]
    fields = dict(
        line.split(":", 1) for line in frontmatter.splitlines() if ":" in line
    )
    assert fields.get("name", "").strip() == "herdr-jev-router"
    assert fields.get("description", "").strip()


def test_skill_is_short() -> None:
    assert len(_skill_text().splitlines()) <= 84


def test_every_flag_in_the_skill_exists_in_a_router_parser() -> None:
    mentioned = set(_FLAG.findall(_skill_text()))
    router_flags = (
        _parser_flags("spawn") | _parser_flags("explain") | _parser_flags("doctor")
    )

    assert mentioned - router_flags - _STOCK_HERDR_FLAGS == set()


def test_documented_router_flags_exist_in_their_own_command_parser() -> None:
    documented: dict[str, set[str]] = {"spawn": set(), "explain": set()}
    for block, command in _ROUTER_BLOCK.findall(_skill_text()):
        documented[command].update(_FLAG.findall(block))

    assert documented["spawn"]
    assert documented["explain"]
    assert documented["spawn"] <= _parser_flags("spawn")
    assert documented["explain"] <= _parser_flags("explain")


def test_stock_flag_allowlist_has_no_router_flags_and_no_dead_entries() -> None:
    router_flags = (
        _parser_flags("spawn") | _parser_flags("explain") | _parser_flags("doctor")
    )
    mentioned = set(_FLAG.findall(_skill_text()))

    assert _STOCK_HERDR_FLAGS & router_flags == set()
    assert _STOCK_HERDR_FLAGS <= mentioned


def test_the_role_list_in_the_skill_matches_the_cli() -> None:
    match = re.search(r"--role ([a-z|]+)", _skill_text())

    assert match is not None
    assert set(match.group(1).split("|")) == set(cli_module._ROLES)


def test_explain_flags_are_a_subset_of_spawn_flags() -> None:
    assert _parser_flags("explain") <= _parser_flags("spawn")


def test_the_skill_tracks_the_merged_denial_codes() -> None:
    text = _skill_text()

    assert "invalid_harness_configuration" in text
    assert "no_eligible_provider" in text
    assert "mandatory_mode_active" not in text


def test_the_opt_in_variables_in_the_skill_match_the_cli() -> None:
    text = _skill_text()

    for variable in OPT_IN_VARIABLES.values():
        assert variable in text


def _skill_description() -> str:
    """Return the frontmatter description value."""

    frontmatter = _skill_text().split("---\n", 2)[1]
    for line in frontmatter.splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("the skill frontmatter has no description")


def test_the_description_names_delegation_and_builtins() -> None:
    description = _skill_description().lower()

    assert "delegat" in description or "parallel" in description
    assert "built-in subagents" in description
    assert "herdr agent start" in description


def test_the_description_is_at_most_two_sentences() -> None:
    sentences = [
        sentence
        for sentence in re.split(r"(?<=[.!?])\s+", _skill_description().strip())
        if sentence
    ]

    assert 1 <= len(sentences) <= 2
