# Design

Herdr Jev Router routes one child agent with Jev on stock, unpatched Herdr. It
is advisory. Herdr has no routing hook, so it cannot stop a direct
`herdr agent start` or a harness binary run from a shell.

## What it does

The `herdr-jev-router` CLI has four commands:

- `herdr-jev-router spawn TASK --name NAME --pane PANE` asks Jev which harness,
  model and effort should run a task, audits the decision, starts the child in
  an existing pane with the stock `herdr agent start` CLI, and delivers the task
  once with `herdr agent prompt`.
- `herdr-jev-router explain TASK` asks the same question, audits the same
  decision, prints a human-readable review, and starts nothing.
- `herdr-jev-router usage` prints the normalized provider capacity from the
  local quota caches, with each harness marked detected or enabled.
- `herdr-jev-router doctor` reports each harness as detected and enabled, and
  validates the credential, state directory, cache and audit file permissions.

`spawn` and `explain` also take `--role`, the boolean constraints `--read-only`,
`--worktree` and `--network-required`, `--state-dir`, and `--audit-path`.

Before routing, `spawn` and `explain` detect the harness executables on `PATH`.
Claude Code and Codex are enabled when installed. OpenCode and Pi also require
an opt-in environment variable that names their provider and three model tiers.
A harness that is missing or not enabled is removed before Jev, exactly like an
exhausted provider. See [multi-harness-routing.md](multi-harness-routing.md).

## What it guarantees

- The harness, model and effort are the ones Jev returned and the router
  validated against the current capacity snapshot. The launch arguments come
  only from a closed mapping in this repository.
- Only harnesses whose executable is on `PATH` and, for OpenCode and Pi, that
  are opted in can be selected through Jev. A missing or disabled harness is
  forced to `exhausted` before the Jev call.
- Jev receives the derived 5-hour and weekly remaining quota for every eligible
  provider. A known window under 10 percent that resets more than 12 hours away
  makes the provider `critical`, and critical providers are removed like
  exhausted ones unless every remaining provider would be removed.
- The caller cannot choose a harness, model, effort, executable, working
  directory, or raw launch argument, and the task text is never parsed for them.
- Every routing decision is written to the audit before any child starts. A
  failed audit denies the operation.
- If capacity, Jev, validation, the audit write, or launch-profile building
  fails, no child starts.
- `spawn` delivers the task exactly once, after `herdr agent start` reports the
  child ready.
- The router reads the Jev key from `TYPESAFE_API_KEY` when it is set, and
  otherwise from the owner-only key file `$XDG_CONFIG_HOME/herdr-jev-router/key`,
  and removes `TYPESAFE_API_KEY` from the environment it passes to the Herdr CLI.

## What it does not guarantee

- It is not enforcement, as described above.
- It does not verify the child pane beyond the stock `herdr agent start`
  readiness check. A successful command return is not independent proof that
  the pane is running the intended harness, model and effort.
- It does not reroute a running agent, aggregate quota across machines, or
  choose more than one child per invocation.
- It does not sandbox the child or restrict what the child may do.

## Architecture

```text
quota caches (codex, claude, opencode, pi)
        |
        v
capacity snapshot  ->  recommend()  ->  Jev system_one (six Choice questions)
                             |                        |
                             |                        v
                             |                 validate answers
                             |                        |
                             v                        v
                     audit record  <----------  RoutingDecision
                             |
              +--------------+---------------+
              |                              |
              v                              v
        explain: print review          spawn: launch_profile()
                                              |
                                              v
                              herdr agent start NAME --kind KIND --pane PANE -- ARGS
                                              |
                                              v
                                   herdr agent prompt NAME TASK
```

`recommend()` is the single routing path. It reads the capacity snapshot, calls
Jev once with six independent `Choice` questions (`harness`, the four branch
models, and `effort`), validates the answers against the eligible providers,
writes the audit record, and returns the decision. `explain` prints it. `spawn`
maps it to an allowlisted launch profile and runs the two stock Herdr commands.

The capacity snapshot comes from owner-only JSON caches in the state directory.
Two console scripts fill them: `herdr-jev-quota-claude` captures one Claude
Code status-line document, and `herdr-jev-quota-codex` refreshes Codex quota
through the local app-server interface. A missing or malformed cache is
`unknown`. A harness that is missing from `PATH` or not enabled is forced to
`exhausted`, so it never reaches the Jev choices. Only a valid zero remaining
window or an explicit reached signal is `exhausted`. Unknown providers stay
eligible with a deterministic penalty of 1. See
[quota-sources.md](quota-sources.md).

## Capacity policy

The router derives two quota numbers per provider window from the normalized
cache, never from provider-specific window names. A window of about 5 hours
fills the 5-hour slot and a window of about 7 days fills the weekly slot, so
Claude's `five_hour`/`seven_day` and Codex's `primary`/`secondary` map the same
way. The state is one of `surplus`, `on_pace`, `conserve`, `unknown`,
`critical`, or `exhausted`:

- `unknown`: the cache is missing or older than the 6-hour maximum. Jev
receives `null` for every quota number and `null` for the age, never a guess.
Unknown providers stay eligible with penalty 1.
- Any unexpired cache is used, fresh or stale. Codex is refreshed every few
minutes and skips a refresh while the cache is under five minutes old, so it is
often stale at routing time; a weekly window cannot recover that fast. The
existing `fresh`/`stale` label is still reported, and every provider carries
`age_hours`, the rounded cache age (`null` when there is no usable cache).
- `exhausted`: a valid zero-remaining window or an explicit reached signal.
Exhausted providers are always removed before Jev.
- `critical`: a known window, fresh or stale, has under 10 percent remaining and
resets more than 12 hours from now. Critical providers are removed before Jev
exactly like exhausted ones. When removing them would leave no provider at all,
they stay eligible with penalty 2, so the user always has a route.
- `surplus`, `on_pace`, `conserve`: the existing pace comparison against the
remaining fraction of each window.

For every eligible provider, the Jev state carries `state`, `penalty`,
`age_hours`, `five_hour_remaining_percent`, `five_hour_resets_in_hours`,
`weekly_remaining_percent`, and `weekly_resets_in_hours`. The reset values are
relative hours, not raw timestamps. The harness question tells Jev to prefer
the provider with more remaining quota when more than one fits.

## Trust boundaries

- The caller supplies only the task, name, pane, role and constraint booleans.
  Everything that determines what runs is chosen by Jev and validated in code.
- The router builds the launch argv from a closed mapping. It never builds a
  shell command, and it passes the task to Herdr as one argument, not a shell
  string.
- The Jev key comes from `TYPESAFE_API_KEY` or the owner-only key file. It is
  never written to the audit, a cache, or stdout, and never passed to a child
  process.
- The state directory is owner-only, mode `0700`, and the audit and cache files
  are owner-only, mode `0600`. The router refuses a symlinked path or a file it
  does not own. The files never contain the
  task text, launch arguments, credentials, provider response bodies, session
  identifiers, transcript paths, or working directories.
- The full task text is sent to TypeSafe for the routing decision, and `spawn`
  passes it on the `herdr agent prompt` command line. See
  [../SECURITY.md](../SECURITY.md).

## Failure behaviour

`spawn` and `explain` write one stable JSON denial to stdout on failure:

```json
{"version":2,"request_id":"...","denial":{"code":"...","message":"routing denied"}}
```

| Exit | Code | Meaning |
| --- | --- | --- |
| 1 | `no_eligible_provider` | Every provider is exhausted. Audited. |
| 1 | `jev_failed` | The Jev call failed. Audited. |
| 1 | `validation_failed` | Jev's answers failed validation. Audited. |
| 1 | `audit_failed` | The audit write failed. Nothing started. |
| 1 | `launch_profile_failed` | The launch mapping rejected the decision. Nothing started. |
| 1 | `launch_failed` | `herdr agent start` failed, timed out, or is missing. |
| 1 | `task_delivery_failed` | The child started but `herdr agent prompt` failed. |
| 2 | `invalid_request` | Bad or injected arguments. |
| 2 | `invalid_harness_configuration` | A malformed opt-in harness configuration. |
| 2 | `configuration_failed` | No `TYPESAFE_API_KEY` or owner-only key file, or no `herdr` executable. |

A readiness timeout can leave a partially started agent in the pane, because
stock Herdr does not roll it back. The router sends no task in that case and
adds no cleanup code. Inspect and close the pane before retrying. The full
contract is in [advisory-mode.md](advisory-mode.md).

## Jev answer validation

Every answer must carry the exact expected option set, each probability finite
and in `[0, 1]`, the chosen option at least tied for the highest probability,
and a confidence in `[0, 1]`. The probabilities must sum to one within the
two-decimal rounding bound of `0.005 * options`: Jev rounds each probability to
two decimals, so a valid three-option answer can sum to 0.99 or 1.01. A sum
further from one, such as 0.8 or 1.2, is still rejected. Both the request-level
and the audit-level validator call one shared tolerance helper so they cannot
diverge.

## Audit record

Both commands write one `recommendation`-phase record through an owner-only,
fsynced, append-only JSONL writer:

```json
{
  "schema_version": 2,
  "phase": "recommendation",
  "request_id": "0f8c...",
  "timestamp": 1780000000,
  "request": {"role": "reviewer", "constraints": {"read_only": true, "worktree": false, "network_required": false}},
  "capacity": {"claude": {"state": "on_pace", "penalty": 0, "age_hours": 0.1, "five_hour_remaining_percent": 85, "five_hour_resets_in_hours": 2, "weekly_remaining_percent": 35, "weekly_resets_in_hours": 100, "reason": null}, "codex": {"state": "critical", "penalty": 0, "age_hours": 0.1, "five_hour_remaining_percent": null, "five_hour_resets_in_hours": null, "weekly_remaining_percent": 6, "weekly_resets_in_hours": 38, "reason": "codex weekly 6% left, resets in 38h"}},
  "jev": {"model": "jev-1.13.0", "answers": {"harness": {"label": "codex", "probabilities": {"claude": 0.4, "codex": 0.6}, "confidence": 0.5}}},
  "recommended_decision": {"harness": "codex", "model": "terra", "effort": "high"},
  "error_category": null
}
```

The example abbreviates the `jev.answers` map. The record always carries all
four providers and all six Jev answers. The capacity snapshot records the same
quota numbers that were sent to Jev, so a later reader can explain why a
provider was removed or chosen.

The record is written before any child starts. Failures carry an
`error_category` such as `capacity`, `jev`, `validation`, or `audit` and no
decision. The record never contains the task text or a credential.

A failed Jev call additionally records `jev_error_code`, Jev's stable code such
as `connection` or `invalid_response`, or `null` when the failure did not come
from a typed `JevError`. The code makes a recurring failure diagnosable from
the record; the raw message and response body are never recorded.
