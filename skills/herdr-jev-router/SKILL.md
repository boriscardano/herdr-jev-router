---
name: herdr-jev-router
description: Route every task you delegate or parallelize to child agents while running in Herdr through the Jev router, which replaces built-in subagents and herdr agent start for that purpose. Use when a parent agent in a Herdr pane must spawn a child agent for delegated work.
---

# Route a child agent with Jev

Use this when a parent agent in a Herdr pane must delegate or parallelize work
to a new child agent. The parent must never choose the harness, model or effort
itself, and must never call `herdr agent start` directly for delegated work.
The router is advisory, so following it is a choice the parent makes.

## 1. Get a pane (optional)

The child runs in a pane at a shell prompt. Omit `--pane` and `spawn` splits the
caller's pane for you. Or split one and read `result.pane.pane_id`:

```console
herdr pane split --direction down --cwd /path/to/repo --no-focus
```

## 2. Spawn through the router

```console
herdr-jev-router spawn "TASK TEXT" --name NAME [--pane PANE_ID]
    [--role worker|reviewer|debugger|researcher]
    [--read-only] [--worktree] [--network-required]
    [--state-dir PATH] [--audit-path PATH]
```

`spawn` is installed with `uv tool install /path/to/this/checkout`. It accepts no
harness, model, effort or raw launch flag, and it never reads one out of the task
text. Call `herdr-jev-router spawn` directly. The router reads the key from
`TYPESAFE_API_KEY` or the owner-only key file and never prints it. A wrapper is
optional: if the user installed one, call it. Never read, print or export the key
yourself. The router strips the key from the environment it hands to `herdr`.
Claude Code and Codex are enabled whenever their executable is on `PATH`.
OpenCode and Pi are used only when the user opted in with `HERDR_JEV_ROUTER_OPENCODE` or
`HERDR_JEV_ROUTER_PI` and the executable is on `PATH`. On success `spawn` prints one
line such as `started harness=codex model=terra effort=high pane=w1:p2 name=worker`. Run `herdr-jev-router doctor --human` for a first-run check.

## 3. Preview a choice with explain

`explain` routes the same task and prints the decision without starting
anything, so it is the safe way to see what the router would pick.

```console
herdr-jev-router explain "TASK TEXT" [--role worker|reviewer|debugger|researcher]
    [--read-only] [--worktree] [--network-required]
    [--state-dir PATH] [--audit-path PATH]
```

It prints the recommended harness, the selected model, the effort and one
capacity line per harness, such as `capacity pi: not enabled (set
HERDR_JEV_ROUTER_PI)`. The router needs a key from the environment or the key file.

## 4. Follow the child

```console
herdr agent read NAME
herdr agent wait NAME
```

`wait` returns when the child is idle, done or blocked.

## Spawn error codes

A failure prints one JSON denial on stdout with `version`, `request_id` and
`denial.code`. No task is sent on any failure except `task_delivery_failed`.

| Code | Exit | What to do |
| --- | --- | --- |
| `no_eligible_provider` | 1 | No harness is installed or enabled, or all are exhausted. Enable one or free capacity, then retry. |
| `jev_failed` | 1 | The Jev call failed. Retry later. |
| `validation_failed` | 1 | Jev answers failed validation. Retry, report a repeat. |
| `audit_failed` | 1 | The audit write failed. Check the state dir. Nothing started. |
| `launch_profile_failed` | 1 | The launch mapping rejected the decision. Report it. Nothing started. |
| `pane_split_failed` | 1 | The automatic pane split failed. Pass `--pane` or retry. Nothing started. |
| `launch_failed` | 1 | Inspect the pane and close any partial child, then retry. No task sent. |
| `task_delivery_failed` | 1 | The child started but the task did not. Read the pane and prompt it. |
| `routing_failed` | 1 | An unexpected internal error. Nothing started. Report it with the `request_id`. |
| `invalid_request` | 2 | Fix the arguments. A task with a C0 or C1 control character other than a newline or tab is rejected here. Nothing started. |
| `invalid_harness_configuration` | 2 | Fix the opt-in variable value, then retry. Nothing started. |
| `configuration_failed` | 2 | Provide a key (environment or owner-only key file), or install `herdr` for `spawn`. |
