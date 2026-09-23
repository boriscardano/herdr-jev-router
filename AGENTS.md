# Repository instructions

Herdr Jev Router is a Python extension that routes child-agent spawning through
Jev on stock, unpatched Herdr. It is advisory and cannot enforce routing.

## Development method

- Use TDD, test-driven development, for every production behavior.
- Start with a focused failing test and confirm it fails for the expected reason.
- Implement the smallest change that makes the test pass.
- Refactor only while the focused test and full suite remain green.
- Begin every bug fix with a regression test.
- Test failure paths and prohibited behavior explicitly.
- Run `uv run pytest` before committing production changes.
- Use `ruff` for linting and formatting.
- Use `uv` for Python package management.

## Scope and quality

- Keep the implementation minimal and limited to advisory spawning on stock Herdr.
- Prefer a small amount of obvious repetition over a premature abstraction. Extract shared code only after repeated behavior and tests show a stable boundary.
- Never store or log API keys, OAuth tokens, authorization headers, or raw credential responses.
- Keep provider arithmetic and hard policy in code. Use Jev only for typed semantic judgments.
- Fail closed when Jev or audit logging is unavailable.
- Do not claim enforcement: stock Herdr has no routing hook, so a direct `herdr agent start` or harness binary bypasses the router.
- Verify the child pane is running the selected harness, model, and effort. A successful command return is not end-to-end proof.
- Mark a behavior verified only when the repository or CI can prove it. A one-off run in a disposable sandbox is not evidence. Commit the artifact, or write "not verified".
- Before a live spawn test, run `explain` and do not start an agent on a provider the user has declared unavailable. Never start an uninspected default.
- Add explicit, measurable acceptance criteria before implementing a feature.
- Document non-obvious compatibility workarounds, credential boundaries, and security-sensitive paths next to the code and in the relevant design document.
- Give public classes and non-trivial functions one concise sentence describing their responsibility. Do not restate the implementation.
- When introducing a new pattern, point to one existing file that demonstrates the intended form.
- Treat failures as user-visible behavior. Return stable errors, degrade honestly to unknown quota where specified, and never hide partial failure.
- Never mutate production systems or production data without explicit human authorization. Prefer censored, read-only production telemetry for diagnosis.
- Never push directly to `main`. Work on a feature branch and use a pull request.

## Design and agentic loops

- For substantial work, write or update the design document before implementation. Include architecture, data model, API contract, implementation steps, testing plan, security boundaries, rollout, rollback, and open decisions.
- Ask the user about unresolved product choices before encoding them in behavior.
- Implement from the accepted design using TDD, then generate and run end-to-end tests in the closest safe real environment.
- Do not begin final polish rounds until the complete real end-to-end path passes. After polishing, rerun the local gate and end-to-end verification.
- Allow unattended work only when success criteria, permissions, rollback, and verification are explicit.
- Parallel agents must have bounded, non-overlapping ownership and observable completion conditions.
- After a user correction or a repeated failure, update this shared file or the routed documentation so future agents receive the lesson automatically.
- Review these instructions at least every six months. Delete stale rules and move accumulated knowledge into the routed documents.

## Documentation routing

Keep this file short. Put knowledge in the adjacent document that owns it and consult only what the task requires.

| Topic | Source |
| --- | --- |
| Architecture, guarantees, trust boundaries, and failure behaviour | `docs/design.md` |
| Advisory command contract and error schema | `docs/advisory-mode.md` |
| Security boundaries and vulnerability reporting | `SECURITY.md` |
| Installation and usage | `README.md` |
| Harness and model mapping | `docs/multi-harness-routing.md` |
| Quota sources and cache format | `docs/quota-sources.md` |
| Jev API contract | `docs/jev-api-contract.md` |
| Contributor workflow and local gate | `CONTRIBUTING.md` |

Do not duplicate detailed knowledge here. Add a rule here only when it changes agent behavior across the repository.
