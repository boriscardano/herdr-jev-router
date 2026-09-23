# Herdr Jev Router

Herdr Jev Router lets a parent agent describe a task and have Jev choose the
harness, model, and effort for the child agent, using the task text and the
remaining subscription capacity. It runs on stock Herdr through the stock
`herdr agent start` and `herdr agent prompt` commands and needs no fork or
patch. It is advisory. Herdr has no routing hook, so a direct `herdr agent
start` still bypasses the router.

[![Herdr Jev Router trailer](docs/assets/trailer.gif)](https://github.com/boriscardano/herdr-jev-router/releases/download/v0.1.0/herdr-jev-router-trailer.mp4)

Real run on stock Herdr where a master agent delegates three tasks and Jev routes each one. [Watch the full-quality video](https://github.com/boriscardano/herdr-jev-router/releases/download/v0.1.0/herdr-jev-router-trailer.mp4).

## Requirements

- Stock Herdr 0.9.1 or newer.
- macOS or Linux. Windows is not supported.
- Python 3.11 or newer and `uv`.
- A TypeSafe API key for Jev. Create one at <https://typesafe.ai>.
- Claude Code and/or Codex on `PATH`.
- Optional: OpenCode and Pi, each enabled with an opt-in variable.

## Install

Install the Herdr plugin:

```console
herdr plugin install boriscardano/herdr-jev-router
```

Its build command runs `uv tool install --force .` in the plugin checkout, so
the three console scripts land in `uv tool dir --bin`, usually
`~/.local/bin`. Keep that directory on your `PATH`.

Install the CLI directly from the repository, optionally at a release tag, or
from a local checkout:

```console
uv tool install git+https://github.com/boriscardano/herdr-jev-router
uv tool install git+https://github.com/boriscardano/herdr-jev-router@v0.1.1
uv tool install /path/to/this/checkout
```

## Provide the key safely

`spawn`, `explain`, and `doctor` read the key from
`TYPESAFE_API_KEY` when it is set, and otherwise from the owner-only key file
`$XDG_CONFIG_HOME/herdr-jev-router/key` (`~/.config/herdr-jev-router/key` when
`XDG_CONFIG_HOME` is unset). Write the file once, reading the key from your
Keychain or 1Password so it never lands in your shell history:

```console
mkdir -p ~/.config/herdr-jev-router
install -m 600 /dev/null ~/.config/herdr-jev-router/key
security find-generic-password -s typesafe -w > ~/.config/herdr-jev-router/key
# or: op read 'op://private/typesafe/credential' > ~/.config/herdr-jev-router/key
```

The file stays owner-only: the router accepts it only when it is a regular file
owned by you with mode 0600, inside a directory you own that is not group- or
world-writable. An unsafe or empty file fails closed exactly like a missing
key, and the key never appears in output, audit records, or a child process
environment. Do not export it from `.zshrc`, `.bashrc`, or another shell
startup file, because that exposes it to every command and agent in that shell.
`doctor --human` reports whether a key was found and whether it came from the
environment or the key file, never the value.

An optional wrapper on `PATH` keeps the key in one process instead of a file,
for example `~/.local/bin/jev`:

```sh
#!/bin/sh
# ~/.local/bin/jev: run herdr-jev-router with the key for this one process.
# Keep one source line and comment out the others.
# key=$(security find-generic-password -s typesafe -w)
# key=$(op read 'op://private/typesafe/credential')
key=$(cat ~/.config/herdr-jev-router.key)
TYPESAFE_API_KEY=$key exec herdr-jev-router "$@"
```

Make it executable with `chmod 700 ~/.local/bin/jev`. With a wrapper, agents
call `jev spawn ...` instead of `herdr-jev-router spawn ...`.

## First run

```console
herdr-jev-router doctor --human
```

It prints one line per check with what to do when something is missing, and it
does not print the key or private paths. `doctor` without `--human` prints the
same checks as JSON for scripts.

## Quota collectors

The router reads two local quota caches so Jev can weigh remaining subscription
capacity. Both collectors are local and make no request on the router's behalf.
The default state directory is `$XDG_STATE_HOME/herdr-jev-router`, or
`~/.local/state/herdr-jev-router` when `XDG_STATE_HOME` is unset. Set
`HERDR_JEV_ROUTER_STATE_DIR` or pass `--state-dir` to change it.

Claude Code: add this to your Claude Code settings so its status line writes
the cache. Use absolute paths.

```json
{
  "statusLine": {
    "type": "command",
    "command": "/absolute/path/to/herdr-jev-quota-claude --state-dir /absolute/state/herdr-jev-router",
    "refreshInterval": 1800
  }
}
```

If you already have a status-line script, read stdin once and pipe the same
bytes to the collector instead. For example, in your script:

```sh
input=$(cat)
printf '%s' "$input" | herdr-jev-quota-claude --state-dir /absolute/state/herdr-jev-router
```

Codex: refresh on demand, or from a timer such as a cron entry or a systemd
timer.

```console
herdr-jev-quota-codex --state-dir ~/.local/state/herdr-jev-router
```

Missing or stale quota is `unknown`, never exhausted. See
[docs/quota-sources.md](docs/quota-sources.md) for the field rules and the
freshness limits.

## Use

Route a child into a new pane. `spawn` splits the caller's pane when `--pane`
is omitted, so run it from inside a Herdr pane.

```console
herdr-jev-router spawn 'Review the authentication redesign for security risks.' \
  --name security-reviewer --role reviewer
```

Or start the child in a pane you already have at a shell prompt.

```console
herdr-jev-router spawn 'Fix the flaky quota test.' --name quota-fix --pane w1:p2
```

On success it prints one line.

```text
started harness=codex model=terra effort=high pane=w1:p3 name=security-reviewer
```

Preview the decision without starting anything.

```console
herdr-jev-router explain 'Review the authentication redesign.' --role reviewer
```

`spawn` never accepts a harness, model, effort, or raw launch argument. The
task may contain newline and tab, but any other C0 or C1 control character is
rejected with `invalid_request`. See [docs/advisory-mode.md](docs/advisory-mode.md) for
the failure codes and limits.

## Make your master agent use it

The CLI install does not install the agent skill. Copy
`skills/herdr-jev-router/SKILL.md` into your agent's skills directory, for
example Claude Code's `~/.claude/skills/herdr-jev-router/SKILL.md`.

```console
mkdir -p ~/.claude/skills/herdr-jev-router
cp /path/to/checkout/skills/herdr-jev-router/SKILL.md ~/.claude/skills/herdr-jev-router/SKILL.md
```

Then add one line to the master agent's standing instructions, in `CLAUDE.md`
or `AGENTS.md`:

> When you delegate work to child agents in Herdr, spawn them through
> `herdr-jev-router`.

That is enough for the master to call `herdr-jev-router spawn` with no wrapper
and no key in its own environment: the router reads the owner-only key file.

## OpenCode and Pi

OpenCode and Pi are opt in, because their provider and model identifiers depend
on your subscription. Set one variable per harness, naming the provider and the
three tiers `small`, `balanced`, and `large`. The executable must also be on
`PATH`.

```console
export HERDR_JEV_ROUTER_OPENCODE='provider=opencode-go,small=deepseek-v4.1-flash,balanced=glm-5.3,large=kimi-k3'
export HERDR_JEV_ROUTER_PI='provider=opencode-go,small=deepseek-v4.1-flash,balanced=glm-5.3,large=kimi-k3'
```

Without the variable the harness is unavailable. A malformed value fails
closed with `invalid_harness_configuration`. See
[docs/multi-harness-routing.md](docs/multi-harness-routing.md) for the tier
mapping.

## Links

- [docs/design.md](docs/design.md): architecture and the capacity policy.
- [SECURITY.md](SECURITY.md): what leaves your machine and the trust boundary.
- [CONTRIBUTING.md](CONTRIBUTING.md): development setup and the local gate.
- [CHANGELOG.md](CHANGELOG.md): release history.
- [LICENSE](LICENSE) and [NOTICE](NOTICE): Apache License 2.0.
