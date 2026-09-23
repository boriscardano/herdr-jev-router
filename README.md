# Herdr Jev Router

Herdr Jev Router lets a parent agent describe a task and have Jev choose the
harness, model, and effort for the child agent, using the task text and the
remaining subscription capacity. It runs on stock Herdr through the stock
`herdr agent start` and `herdr agent prompt` commands and needs no fork or
patch. It is advisory. Herdr has no routing hook, so a direct `herdr agent
start` still bypasses the router.

https://github.com/user-attachments/assets/df4027c9-d009-4083-b310-fac505ad3bd2

Real run on stock Herdr: asked in plain language to do three things in parallel,
a master agent found the skill on its own and every child was routed by Jev.

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
uv tool install git+https://github.com/boriscardano/herdr-jev-router@v0.1.4
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

Missing or expired quota is `unknown`, never exhausted. See
[docs/quota-sources.md](docs/quota-sources.md) for the field rules and the
freshness limits.

Jev receives the real numbers, not just a label: for every launchable provider
it gets the remaining percent and hours-to-reset for the 5-hour and weekly
windows, mapped by window length so Claude and Codex both work, plus
`age_hours`, the rounded age of the cache. Any unexpired cache is used even
when it is labeled stale, because a weekly window cannot recover between
refreshes. A provider with under 10 percent left in a window that resets more
than 12 hours away is `critical`. Critical, exhausted, and unknown are all
information for Jev, not filters: every installed and enabled provider is
offered with its numbers and Jev decides. `usage`, `explain`, and
`doctor --human` show the state with the reason, and the audit record keeps
the same numbers.

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

Add `--show-request` to `explain` to print the exact JSON sent to Jev before
the review lines: the `state` object (task, role, constraints, and each
provider's quota numbers) and the six questions with their instructions and
criteria. It never prints the key or any header. `spawn` rejects the flag.

`spawn` never accepts a harness, model, effort, or raw launch argument. The
task may contain newline and tab, but any other C0 or C1 control character, a
lone surrogate, or text with no visible character after whitespace and Unicode
format characters are removed is rejected with `invalid_request`. See
[docs/advisory-mode.md](docs/advisory-mode.md) for the failure codes and limits.

## Steer the routing

The router sends every launchable provider to Jev with its real quota numbers
and applies no quota-based limits of its own. Write your standing preferences
in plain words at `$XDG_CONFIG_HOME/herdr-jev-router/preferences.md` (default
`~/.config/herdr-jev-router/preferences.md`). The file is plain text, must be
owned by you, is read without following symlinks, and is capped at 8 KiB. When
it is missing, the router sends a short built-in default that prefers the
cheapest model that can do the task well, avoids a provider whose quota is
nearly used up, and reserves higher effort for hard tasks.

```markdown
Use deepseek-v4.1-flash through Pi as the workhorse for routine tasks, it is
capable and cheap.

Prefer Codex terra for hard implementation work, and Claude opus only when the
task needs it.

Avoid any provider whose weekly quota is under 10 percent unless nothing else
fits.
```

`explain --show-request` prints these preferences as part of the Jev request.
The audit never stores the text, only whether a file or the default was used
and its length. An unsafe or oversized file makes the command fail closed with
`invalid_preferences`.

## Make your master agent use it

The CLI install does not install the agent skill. Copy
`skills/herdr-jev-router/SKILL.md` into your agent's skills directory, for
example Claude Code's `~/.claude/skills/herdr-jev-router/SKILL.md`.

```console
mkdir -p ~/.claude/skills/herdr-jev-router
cp /path/to/checkout/skills/herdr-jev-router/SKILL.md ~/.claude/skills/herdr-jev-router/SKILL.md
```

Installing the skill alone was not enough in testing: a master agent given a
plain request to parallelize work used its own built-in subagents and never
called Jev. The one-line standing instruction below is required. Add it to the
master agent's standing instructions, in `CLAUDE.md` or `AGENTS.md`:

> When you delegate work to child agents in Herdr, spawn them through herdr-jev-router (use the herdr-jev-router skill), not built-in subagents and not `herdr agent start`.

With that line in place the master loaded the skill on its own and routed every
child through the router. That is enough for the master to call
`herdr-jev-router spawn` with no wrapper and no key in its own environment: the
router reads the owner-only key file.

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

## Troubleshooting

A harness's own interactive startup screen, such as an update available prompt,
a new-model announcement, or a folder trust question, blocks automatic task
delivery. The task text lands in that screen instead of the agent prompt. Start
that harness once by hand in the same directory, the way you normally start it,
clear the screen, then retry the spawn.

`spawn` still reports `started` in this case, because Herdr saw the harness as
ready before the screen appeared. A `started` line is not proof that the child
received the task. Read the pane with `herdr agent read NAME` to confirm.

## Links

- [docs/design.md](docs/design.md): architecture and the capacity policy.
- [SECURITY.md](SECURITY.md): what leaves your machine and the trust boundary.
- [CONTRIBUTING.md](CONTRIBUTING.md): development setup and the local gate.
- [CHANGELOG.md](CHANGELOG.md): release history.
- [LICENSE](LICENSE) and [NOTICE](NOTICE): Apache License 2.0.
