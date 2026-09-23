# Advisory mode

Advisory mode is the product. It runs on stock, unpatched Herdr and adds the
advisory commands `herdr-jev-router spawn` and `herdr-jev-router explain`.
`spawn` asks Jev which harness, model and effort should run a child agent, then
starts that child through the stock `herdr` CLI. `explain` asks the same
question, audits the same decision, and never starts anything. Advisory mode is
advisory: Herdr does not require it, and any process that can reach the socket
can still run `herdr agent start` directly.

## Command contract

```console
herdr-jev-router spawn TASK --name NAME --pane PANE
    [--role worker|reviewer|debugger|researcher]
    [--read-only] [--worktree] [--network-required]
    [--state-dir PATH] [--audit-path PATH]

herdr-jev-router explain TASK
    [--role worker|reviewer|debugger|researcher]
    [--read-only] [--worktree] [--network-required]
    [--state-dir PATH] [--audit-path PATH]
```

| Input | Meaning |
| --- | --- |
| `TASK` | Task text delivered to the child once by `spawn`, verbatim, as one argument. |
| `--name` | Herdr agent name for the child. `spawn` only. |
| `--pane` | Existing pane, at its interactive shell prompt, to start the child in. `spawn` only. |
| `--role` | Routing role. Defaults to `worker`. |
| `--read-only`, `--worktree`, `--network-required` | The three constraint booleans. Default false. |
| `--state-dir`, `--audit-path` | The capacity-cache and audit paths. |

Neither command accepts a harness, model, effort, `cwd`, or raw launch
argument, and the task text is never parsed for them. The routing decision is
built only from Jev's validated answers over the cached capacity snapshot.

Both commands call `recommend()` with the same inputs, capacity snapshot, Jev
call, validation and audit record. `spawn` then maps the recommended decision
with the trusted `launch_profile()` code. There is no second mapping.

## What it guarantees

- The child harness, model and effort are the ones Jev recommended and the
  router validated. The launch argv is built from the allowlisted profile.
- `spawn` delivers the task exactly once, after `herdr agent start` reports the
  child ready, using `herdr agent prompt`.
- Every routing decision is audited before any process starts. The audit record
  contains the request id, role, constraints, capacity, Jev answers and the
  recommended decision, and never the task text or any credential.
- Fail closed: if capacity, Jev, validation, the audit write, or launch-profile
  building fails, nothing is started and the command exits non-zero.
- Child processes receive no Jev key. The router strips `TYPESAFE_API_KEY` from
  the environment it passes to `herdr`, and the Herdr CLI never reads the key
  file.

## What it does not guarantee

- It is not enforcement. Herdr has no routing hook, so `herdr agent start` and
  a harness binary can be run directly. Advisory mode cannot see or stop that.
- It does not verify the child pane beyond the stock `herdr agent start`
  readiness check. A successful command return is not independent proof that
  the pane is running the intended harness, model and effort.
- It does not reroute running agents, aggregate quota across machines, or
  choose anything other than one child per invocation.

## Failure behaviour

Output is one short human-readable line on success:

```
started harness=codex model=terra effort=high pane=w1:p2 name=worker
```

On any failure it writes one stable JSON denial to stdout and sends no task:

```json
{"version":2,"request_id":"...","denial":{"code":"...","message":"routing denied"}}
```

A usage failure that happens before a request id is generated still uses this
shape with `"request_id": null`. A `launch_failed` readiness timeout is the one
case where stock Herdr can leave a partially started agent in the pane. Inspect
and close it as described below.

| Exit | Denial code | Meaning |
| --- | --- | --- |
| 0 | none | `spawn` started the child and delivered the task, or `explain` printed the review. |
| 1 | `no_eligible_provider` | No harness is installed and enabled. Audited. |
| 1 | `jev_failed` | The Jev call failed. Audited. |
| 1 | `validation_failed` | Jev's answers failed validation. Audited. |
| 1 | `audit_failed` | The audit write failed. Nothing started. |
| 1 | `launch_profile_failed` | The trusted launch mapping rejected the decision. Nothing started. |
| 1 | `launch_failed` | `herdr agent start` failed, timed out, or is missing. No task sent, and a partial agent may remain in the pane. |
| 1 | `task_delivery_failed` | The child started but `herdr agent prompt` failed. |
| 2 | `invalid_request` | Bad or injected arguments. |
| 2 | `configuration_failed` | No `TYPESAFE_API_KEY` or owner-only key file, or no `herdr` executable for `spawn`. |

A `launch_failed` result caused by the start timeout means the stock CLI did not
confirm readiness within the bound. Stock Herdr does not roll back a partially
started agent, so the harness process can stay running in the pane. Inspect the
pane and close it before retrying. A common cause is Claude Code stopping at
its folder-trust prompt when the pane's directory is new. Start the child in a
directory Claude already trusts. `spawn` sends no task after a start failure
and deliberately adds no cleanup code, because it cannot safely decide whether
a visible process is the one it started.

## Audit behaviour

Both commands write a `recommendation`-phase audit record through the same
owner-only, fsynced, `schema_version: 2` audit writer. The record is durable
before `herdr agent start` runs. It never contains the task text, launch
arguments, API key, authorization header, or raw provider response. A failed
Jev call records `error_category: "jev"` and Jev's stable `jev_error_code`
(for example `connection` or `invalid_response`), so the cause is diagnosable
without the raw message.

## Acceptance criteria

1. `spawn` starts exactly the recommended harness/model/effort and delivers the
   task once, verified by recording the argv of a fake `herdr` executable.
2. Harness, model, effort and raw arguments cannot be injected through any flag
   or through the task text. Such invocations are rejected with
   `invalid_request` and start nothing.
3. Each failure path above returns its stable code and exit status, and starts
   nothing unless the failure is `task_delivery_failed`.
4. `explain` audits the same decision and starts nothing.
5. No credential appears in stdout, stderr, the audit record, or the child
   environment, whether it came from the environment or the key file.
6. `herdr-plugin.toml` satisfies the stock 0.9.1 manifest parser so
   `herdr plugin install`/`link` of this repository works on stock Herdr.
