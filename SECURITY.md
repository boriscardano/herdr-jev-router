# Security Policy

## Project status

Herdr Jev Router provides advisory Jev routing for child agents on stock,
unpatched Herdr. It is experimental. It does not enforce routing.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository's
private security advisory form and include the affected revision, impact, and
minimal reproduction steps. Do not include live credentials or private task
data.

If the private reporting form is unavailable, contact a repository maintainer
through a private channel before disclosing details publicly.

## Before you run it

- The full task text is sent to TypeSafe (Jev) to make the routing decision. It
  leaves your machine. Do not put secrets or private data in the task.
- `spawn` passes the task to `herdr agent prompt` as a command-line argument, so
  another local user on a shared machine can see it in `ps`. This is stock
  Herdr behavior and the router cannot avoid it.
- The router resolves `herdr` and the harness executables through `PATH`, and
  stock Herdr resolves the selected harness executable through the pane's
  `PATH`. If an attacker can change `PATH` or place a binary earlier in it,
  they can control what runs. Run the router from a trusted environment.
- The Jev key is read from `TYPESAFE_API_KEY` when it is set, otherwise from
  the owner-only key file `$XDG_CONFIG_HOME/herdr-jev-router/key`. Do not export
  the variable from a shell startup file, because that exposes it to commands
  and agents in that shell.

## Security boundary

The router applies only to child agents started through `herdr-jev-router
spawn`. It does not prevent direct process execution from an unrestricted
shell, reroute an existing agent, or provide a sandbox. Herdr has no routing
hook, so a direct `herdr agent start` or a harness binary bypasses the router.

The caller supplies the task, name, pane, role and constraints. The caller
cannot choose the harness, model, effort, or raw launch arguments. The router
accepts only the documented roles and boolean constraints, and returns only an
allowlisted launch profile for Claude Code, Codex, OpenCode, or Pi. Only
harnesses installed on `PATH` and enabled can be selected, and OpenCode and Pi
require opt-in configuration. See
[docs/multi-harness-routing.md](docs/multi-harness-routing.md).

The router reads the Jev key from `TYPESAFE_API_KEY` or, when that is unset,
from the key file at `$XDG_CONFIG_HOME/herdr-jev-router/key`
(`~/.config/herdr-jev-router/key` by default). It accepts the file only when it
is a regular file owned by the current user with mode 0600, opened with
`O_NOFOLLOW` and verified with `fstat`, inside a parent directory owned by the
current user that is not group- or world-writable. It rejects symlinks, foreign
owners, other modes, oversized files, and non-UTF-8 content, and it treats any
such file exactly like a missing key: `configuration_failed`, with no child
started. The key is never printed, logged, written to the audit, placed in
argv, or passed to the Herdr CLI. The router removes `TYPESAFE_API_KEY` from
the environment it passes to the Herdr CLI, and the Herdr CLI never reads the
key file. The router never writes the key,
authorization headers, raw provider responses, session identifiers, transcript
paths, working directories, prompts, responses, or repository paths to its
state or audit files.

Quota caches and the JSONL audit live in an owner-only state directory, mode
`0700`, and are owner-only files with mode `0600`. The router refuses a
symlinked path or a file it does not own, and it treats an unsafe cache as
`unknown`. The audit is written before a child is started. If Jev, input
validation, decision validation, audit writing, or provider state fails, the
router returns a stable denial and starts no child. Unknown quota is distinct
from exhausted quota. Only valid zero remaining capacity or an explicit reached
signal removes a provider before Jev.

`spawn` waits for the stock `herdr agent start` readiness check and delivers
the task exactly once. A readiness timeout or changed pane state denies the
operation and must not retry delivery.

## Known limitations

Herdr launches the selected harness by typing into the pane's interactive
shell, so a shell alias or function can change the effective command after the
router builds the launch profile. The alias runs in the pane, not in the
router, so the router's launch profile is unchanged even though the effective
command is not.
