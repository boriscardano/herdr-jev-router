## Summary

Describe the user-visible outcome and why the change is needed.

## TDD evidence

- [ ] A focused test failed first for the expected reason.
- [ ] The smallest implementation made the focused test pass.
- [ ] The full suite remained green after simplification.

## Local gate

- [ ] `uv run pytest`
- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`

## Review

- [ ] The diff contains no unrelated changes or speculative abstractions.
- [ ] Security and privacy implications are documented.
- [ ] Documentation matches the implemented behavior.
- [ ] No credentials, private task data, personal paths, or account identifiers are present.
