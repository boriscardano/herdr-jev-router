"""Guard the release assets against security and release-correctness regressions.

These tests encode the release rules for this repository: every GitHub action is
pinned to a full commit SHA, checkout never persists credentials, a release runs
only from a version tag, PyPI publishing uses trusted publishing with no stored
token, the tag must match the package version, and the published files come from
the build the gate approved.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

_USES = re.compile(r"uses:[ \t]*(\S+)([^\n]*)")
_PINNED_REF = re.compile(r"([^@\s]+)@(?:[0-9a-f]{40})\Z")
_VERSION_COMMENT = re.compile(r"[ \t]*#[ \t]*v\d+\.\d+\.\d+[ \t]*\Z")


def test_every_github_action_is_pinned_to_a_commit_sha_with_a_version_comment() -> None:
    for workflow in (CI_WORKFLOW, RELEASE_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        entries = _USES.findall(text)
        assert entries, f"{workflow.name} declares no GitHub actions"
        for ref, comment in entries:
            assert _PINNED_REF.fullmatch(ref), f"{workflow.name} runs unpinned {ref}"
            assert _VERSION_COMMENT.fullmatch(comment), (
                f"{workflow.name} lacks a version comment for {ref}"
            )


def test_workflows_do_not_persist_checkout_credentials() -> None:
    for workflow in (CI_WORKFLOW, RELEASE_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        checkouts = text.count("actions/checkout@")
        assert checkouts
        assert text.count("persist-credentials: false") == checkouts


def test_release_workflow_only_runs_for_version_tags() -> None:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert re.search(r'^[ \t]+tags: \[ "v\*" \]$', text, re.MULTILINE)
    assert "pull_request" not in text
    assert "workflow_dispatch" not in text
    assert "branches:" not in text


def test_release_workflow_refuses_a_tag_that_does_not_match_the_version() -> None:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "uv version --short" in text
    assert "${GITHUB_REF_NAME}" in text


def test_release_workflow_publishes_with_trusted_publishing() -> None:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    build_job, _, publish_job = text.partition("\n  publish:")

    assert "permissions:" in text
    assert "contents: read" in text
    assert "pypa/gh-action-pypi-publish@" in publish_job
    assert re.search(r"^[ \t]+environment:$", publish_job, re.MULTILINE)
    assert re.search(r"^[ \t]+name: pypi$", publish_job, re.MULTILINE)
    assert "id-token: write" in publish_job
    assert "id-token: write" not in build_job
    assert "secrets." not in text
    for forbidden in ("password:", "PYPI_API_TOKEN", "TWINE_PASSWORD", "api-token"):
        assert forbidden not in text


def test_release_workflow_publishes_the_artifacts_built_by_the_gate() -> None:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    build_job, _, publish_job = text.partition("\n  publish:")

    assert "actions/upload-artifact@" in build_job
    assert "path: dist/" in build_job
    assert "if-no-files-found: error" in build_job
    assert "actions/download-artifact@" in publish_job
