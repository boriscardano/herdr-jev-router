"""Guard the CI workflow against supply-chain regressions.

This repository does not publish to PyPI. These tests keep the rules that
still apply to ci.yml: every GitHub action is pinned to a full commit SHA
with a version comment, and checkout never persists credentials.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

_USES = re.compile(r"uses:[ \t]*(\S+)([^\n]*)")
_PINNED_REF = re.compile(r"([^@\s]+)@(?:[0-9a-f]{40})\Z")
_VERSION_COMMENT = re.compile(r"[ \t]*#[ \t]*v\d+\.\d+\.\d+[ \t]*\Z")


def test_every_github_action_is_pinned_to_a_commit_sha_with_a_version_comment() -> None:
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    entries = _USES.findall(text)
    assert entries, f"{CI_WORKFLOW.name} declares no GitHub actions"
    for ref, comment in entries:
        assert _PINNED_REF.fullmatch(ref), f"{CI_WORKFLOW.name} runs unpinned {ref}"
        assert _VERSION_COMMENT.fullmatch(comment), (
            f"{CI_WORKFLOW.name} lacks a version comment for {ref}"
        )


def test_ci_workflow_does_not_persist_checkout_credentials() -> None:
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    checkouts = text.count("actions/checkout@")
    assert checkouts
    assert text.count("persist-credentials: false") == checkouts
