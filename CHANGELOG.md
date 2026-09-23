# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-23

### Added

- `herdr-jev-router spawn` asks Jev for one routing decision, validates and
  audits it, starts the child with the stock `herdr agent start` CLI, and
  delivers the task once with `herdr agent prompt`. The caller cannot choose a
  harness, model, effort, or raw launch argument, and a direct
  `herdr agent start` stays possible. `--pane` is optional. When it is omitted,
  `spawn` splits the caller's current pane and starts the child in the new pane.
- `herdr-jev-router explain` previews the routing decision for a task without
  starting anything.
- `herdr-jev-router usage` shows the normalized current provider capacity from
  the local quota caches.
- `herdr-jev-router doctor` validates the local installation and state
  directory. `doctor --human` prints one actionable line per check for a first
  run.
- Harness detection and opt-in. A harness that is not installed is removed
  before Jev exactly like an exhausted provider, so the router works with only
  Claude Code or only Codex. OpenCode and Pi are enabled only when
  `HERDR_JEV_ROUTER_OPENCODE` or `HERDR_JEV_ROUTER_PI` names the provider and
  the three tier models.
- The agent skill in `skills/herdr-jev-router/SKILL.md`, which teaches a parent
  agent to delegate through the router.
- The Claude Code quota collector `herdr-jev-quota-claude` captures one
  status-line document from standard input.
- The Codex quota collector `herdr-jev-quota-codex` refreshes Codex quota once
  through the local app-server interface.
- The plugin build installs the three console scripts on `PATH` with
  `uv tool install --force .`.
- Owner-only state and audit files under `$XDG_STATE_HOME/herdr-jev-router`,
  with an append-only redacted routing audit.

### Security

- The Jev credential is read only from `TYPESAFE_API_KEY` and never appears in
  audit records or quota caches.
- A spawn that cannot reach Jev, that receives an invalid decision, or that
  cannot write its audit record starts no child and reports a stable denial.
- The task text is rejected with `invalid_request` when it contains a control
  character other than newline or tab, because a control sequence could drive
  the child terminal instead of being delivered as text.
- Herdr CLI output is discarded or bounded, the pane split reply is size
  checked, and a closed stdout pipe is handled without a traceback.

[Unreleased]: https://github.com/boriscardano/herdr-jev-router/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/boriscardano/herdr-jev-router/releases/tag/v0.1.0
