# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Rounded Jev probabilities no longer fail a spawn. Jev rounds each answer's
  probabilities to two decimals, so a valid three-option answer can sum to
  0.99 or 1.01. The router now accepts a sum within the two-decimal rounding
  bound of `0.005 * options`, while a genuinely different distribution such as
  0.8 or 1.2 is still rejected.
- A failed Jev call now records Jev's stable error code in the audit record as
  `jev_error_code`, so a recurring failure is diagnosable from the record
  alone. The raw message and response body are still never audited.

## [0.1.3] - 2026-09-23

### Added

- Jev now receives the real remaining quota for every eligible provider: the
  remaining percent and hours-to-reset for the 5-hour and weekly windows, mapped
  by window length so Claude's `five_hour`/`seven_day` and Codex's
  `primary`/`secondary` both work, plus `age_hours`, the rounded cache age.
  Missing or expired windows are `null`, never a guess, and any unexpired cache
  is used even when it is labeled stale, because a quota window cannot recover
  between refreshes. The harness question tells Jev to prefer the provider with
  more remaining quota when a task fits either.
- A new `critical` capacity state: a known window with under 10 percent
  remaining that resets more than 12 hours from now, fresh or stale. Critical
  providers are removed before Jev like exhausted ones, unless every remaining
  provider would be removed, in which case they stay eligible with a penalty so
  the user always has a route. `usage`, `explain`, and `doctor --human` show
  `critical` with the reason, and the audit record keeps the same quota numbers
  and age.

## [0.1.2] - 2026-09-23

### Changed

- The skill description now tells a master agent to use the router for all
  delegation on its own ([#5](https://github.com/boriscardano/herdr-jev-router/pull/5)).
- Tests no longer read the developer's key file ([#6](https://github.com/boriscardano/herdr-jev-router/pull/6)).

### Fixed

- Lone surrogates and invisible-only task text are rejected with
  `invalid_request` ([#7](https://github.com/boriscardano/herdr-jev-router/pull/7)).

## [0.1.1] - 2026-09-23

### Added

- The Jev key can come from the owner-only key file
  `$XDG_CONFIG_HOME/herdr-jev-router/key` when `TYPESAFE_API_KEY` is unset, so a
  master agent can run `herdr-jev-router spawn` with no wrapper and no key in
  its environment. The file is accepted only when it is a regular file owned by
  the current user with mode 0600, inside a directory the current user owns
  that is not group- or world-writable. An unsafe or empty file fails closed
  like a missing key.
- `doctor` and `doctor --human` report whether a key was found and whether it
  came from the environment or the key file, never the value, and name the fix
  for an unsafe key file.

### Fixed

- Reject C1 control characters in task text and identifiers, because U+009B is
  the 8-bit CSI introducer and an embedded `ESC[201~` could close the Herdr
  bracketed paste early ([#2](https://github.com/boriscardano/herdr-jev-router/pull/2)).

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

[Unreleased]: https://github.com/boriscardano/herdr-jev-router/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/boriscardano/herdr-jev-router/releases/tag/v0.1.3
[0.1.2]: https://github.com/boriscardano/herdr-jev-router/releases/tag/v0.1.2
[0.1.1]: https://github.com/boriscardano/herdr-jev-router/releases/tag/v0.1.1
[0.1.0]: https://github.com/boriscardano/herdr-jev-router/releases/tag/v0.1.0
