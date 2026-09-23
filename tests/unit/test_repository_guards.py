from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_ci_scans_the_full_git_history_for_secrets() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow
    assert "gitleaks git --no-banner --redact --exit-code 1 ." in workflow
