# Multi-harness routing: OpenCode and Pi

Status: implemented extension to the advisory routing contract.

## Decision

Add `opencode` and `pi` as first-class routed harnesses alongside `claude`
and `codex`. The router maps a semantic tier to the launch arguments, and
stock `herdr agent start` accepts all four `--kind` labels, so no Herdr change
is needed.

OpenCode and Pi are reachable in this environment through the OpenCode Go
provider and `OPENCODE_API_KEY`. Claude and Codex remain unchanged.

## Harnesses and model tiers

| Harness | Semantic tiers | Launch mapping |
| --- | --- | --- |
| `claude` | `haiku`, `sonnet`, `opus` | `--model <tier> --effort <effort>` |
| `codex` | `luna`, `terra`, `sol` | `-m gpt-5.6-<tier> -c model_reasoning_effort="<effort>"` |
| `opencode` | `deepseek`, `glm`, `kimi` | `--model opencode-go/<launch>` |
| `pi` | `deepseek`, `glm`, `kimi` | `--provider opencode-go --model <launch> --thinking <effort>` |

Concrete launch models:

| Tier | OpenCode (`--model`) | Pi (`--model`) |
| --- | --- | --- |
| `deepseek` | `opencode-go/deepseek-v4.1-flash` | `deepseek-v4.1-flash` |
| `glm` | `opencode-go/glm-5.3` | `glm-5.3` |
| `kimi` | `opencode-go/kimi-k3` | `kimi-k3` |

Effort stays the closed set `low`, `medium`, `high`, `xhigh`, `max`. Pi
receives it as `--thinking`, whose documented levels are a superset. The
OpenCode interactive TUI rejects `--variant` (it exists only on `opencode
run`), so OpenCode's effort is decided and audited but not passed on the
command line.

## Jev request

The one `system_one` call now sends six independent `Choice` questions:

1. `harness`: the eligible harnesses.
2. `claude_model`: `haiku`, `sonnet`, `opus`.
3. `codex_model`: `luna`, `terra`, `sol`.
4. `opencode_model`: `deepseek`, `glm`, `kimi`.
5. `pi_model`: `deepseek`, `glm`, `kimi`.
6. `effort`: `low`, `medium`, `high`, `xhigh`, `max`.

The four model questions remain speculative branch questions; only the model
belonging to the selected harness affects the launch. All six answers are
retained for validation and audit.

## Validation and audit

`RoutingDecision` carries every branch model plus the selected harness and
effort. `validate_decision` requires exactly the six answer keys, closed
values, and a currently eligible harness. The audit's `recommended_decision`
keeps the compact `{harness, model, effort}` shape, where `model` is the branch
model for the selected harness.

## Testing

- Router: model/decision validation, launch mapping for all four harnesses,
  the six-question Jev payload, and the capacity matrix.
- End to end: not run. No `spawn` selecting OpenCode or Pi has been observed
  on a real host.

## Harness detection and opt-in configuration

The router does not assume that every harness is installed, and it does not
assume that OpenCode and Pi run against one private subscription.

Before any Jev call, `spawn` and `explain` detect harness executables on `PATH`
through the same command lookup that `doctor` uses. A harness whose executable
is missing is removed from the capacity snapshot exactly like an exhausted
provider: it never appears in the Jev harness choices and can never be
selected. When no harness is installed or enabled, routing fails closed with
the stable `no_eligible_provider` code before Jev is called.

Claude Code and Codex need no configuration. They are enabled whenever their
executable is found. OpenCode and Pi are opt in because their provider and
model identifiers depend on the user's own subscription.

| Harness | Executable | Opt-in variable |
| --- | --- | --- |
| Claude Code | `claude` | none |
| Codex | `codex` | none |
| OpenCode | `opencode` | `HERDR_JEV_ROUTER_OPENCODE` |
| Pi | `pi` | `HERDR_JEV_ROUTER_PI` |

The variable takes effect only when the executable is also found on `PATH`.
Its value names the provider and the three tier models as comma-separated
`key=value` pairs. The tier keys are generic, because a small model may be any
brand:

| Key | Internal tier | Jev description |
| --- | --- | --- |
| `small` | `deepseek` | small and fast |
| `balanced` | `glm` | balanced |
| `large` | `kimi` | highest capability |

```console
export HERDR_JEV_ROUTER_OPENCODE='provider=opencode-go,small=deepseek-v4.1-flash,balanced=glm-5.3,large=kimi-k3'
export HERDR_JEV_ROUTER_PI='provider=opencode-go,small=deepseek-v4.1-flash,balanced=glm-5.3,large=kimi-k3'
```

All four keys are required. OpenCode receives `--model <provider>/<model>` and
Pi receives `--provider <provider> --model <model>`. The internal `deepseek`,
`glm` and `kimi` tiers, the model enums and the Jev contract are unchanged. A
value that does not match this shape is a stable
`invalid_harness_configuration` denial rather than a silently disabled harness.
There is no configuration file, so nothing new is installed and nothing needs
to be backed up.

`usage` and `doctor` report `detected` and `enabled` for every harness.
`doctor` is healthy when `herdr` is present, at least one harness is enabled,
and the credential, state directory and files are sound. OpenCode or Pi being
absent or opted out is not an installation failure.

## Security boundaries

Unchanged. The caller still cannot supply harness, model, effort, executable,
or arguments. Launch arguments still come only from the router's trusted
mapping. No credential is added to the router. Pi and OpenCode read
`OPENCODE_API_KEY` from the pane environment.
