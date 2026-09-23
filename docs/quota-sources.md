# Quota sources

The router uses two local quota collectors and reads their normalized caches
from the router state directory. The collectors do not change provider
configuration. The cache files are `codex-quota.json` and
`claude-quota.json`; the router also reads `opencode-quota.json` and
`pi-quota.json` when present. OpenCode and Pi have no collector yet, so when
they are installed and opted in they are reported as `unknown` capacity and
remain eligible with the deterministic penalty. A harness that is missing from
`PATH` or not enabled is forced to `exhausted` before Jev. See
[multi-harness-routing.md](multi-harness-routing.md).

The router defaults to
`$XDG_STATE_HOME/herdr-jev-router`, or
`~/.local/state/herdr-jev-router` when `XDG_STATE_HOME` is unset. Set
`HERDR_JEV_ROUTER_STATE_DIR` to override it. Pass `--state-dir` for one
invocation and `--audit-path` to place the redacted `routing.jsonl` audit
elsewhere. The state directory must be owner-only, mode `0700`, and owned by
the current user. State and audit files are created owner-only and written with
mode `0600`. The router refuses a symlinked path or a file it does not own, and
an unsafe cache is treated as `unknown`.

The collectors supply capacity to the advisory `spawn` and `explain` commands.
They do not start a child agent and cannot enforce routing against direct
shell invocations.

The current integration has two independent local sources:

1. Codex, queried through the documented local `codex app-server` stdio JSON-RPC interface and `account/rateLimits/read`.
2. Claude Code, captured from the documented `statusLine` JSON feed.

The Claude OAuth usage endpoint used by the pinned upstream project is not
used. It is undocumented, requires handling a user credential, and adds a
second failure and privacy boundary without being necessary when a status line
is configured.

## Decision and boundary

```text
Codex app-server JSONL ─┐
                        ├─ parse ─ normalize ─ owner-only cache ─ capacity state
Claude statusLine JSON ─┘
```

Each source owns its own cache and freshness. A failure in one source must not erase or relabel the other source. A missing or stale source is `unknown`, not `exhausted`. Only an explicit provider signal or a valid window at zero remaining capacity can produce `exhausted`.

The collector must persist normalized quota only. It must never persist source response bodies, credentials, authorization headers, session identifiers, transcript paths, prompts, responses, repository paths, or account identifiers.

## Pinned upstream reference

The closest existing implementation is [terry-li-hm/herdr-model-lanes](https://github.com/terry-li-hm/herdr-model-lanes), pinned at tag `v3.8.0`. The annotated tag resolves to commit `8568d996298c2da3495124904bbff4048d443347`, dated 2026-08-25.

The upstream release is Apache License 2.0. Its Codex reader was the main behavioral reference for this design. Its Claude reader is an experimental macOS helper for the unofficial OAuth usage endpoint, not the preferred design here. The `v3.8.0` tree has no Claude status-line helper, so do not copy a status-line implementation from an unpinned branch while claiming it came from this release.

### Upstream files and functions used as design references

No code was copied from the upstream project. The entries below record which parts of it informed the design and what this repository did with each idea.

| Upstream path | Functions or types | How this design used it |
| --- | --- | --- |
| [`herdr_model_lanes.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/herdr_model_lanes.py#L90-L110) | `QuotaWindow`, `CodexUsage`, `ClaudeUsage` | Design reference for immutable normalized window data. This repository uses one small shared record for both sources. |
| [`herdr_model_lanes.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/herdr_model_lanes.py#L204-L227) | `_pick_weekly_window`, `parse_codex_usage` | Design reference for percentage validation, reset validation, plan handling, and conversion from `usedPercent` to remaining capacity. The fixed 10,080-minute selection is upstream policy, not a universal protocol guarantee. |
| [`herdr_model_lanes.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/herdr_model_lanes.py#L348-L381) | `_parse_claude_window`, `parse_claude_usage` | Design reference for percentage conversion and optional Claude windows. The input shape here is the upstream helper shape, not the status-line shape. |
| [`herdr_model_lanes.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/herdr_model_lanes.py#L478-L730) | `_window_to_dict`, `_window_from_dict`, `_save_json`, `_cache_lock`, `_discard_expired` | Design reference for atomic normalized caching, a per-file lock, and expiry. This repository adds an explicit owner-only mode assertion. |
| [`herdr_model_lanes.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/herdr_model_lanes.py#L738-L852) | `_codex_request`, `query_codex` | Design reference for the bounded stdio process, initialize sequence, `account/read` authentication check, `account/rateLimits/read` request, response-ID matching, and cleanup. |
| [`herdr_model_lanes.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/herdr_model_lanes.py#L860-L890) | `default_claude_command`, `query_claude` | Not the model for the Claude design here. It informed only the bounded helper execution and the credential-free normalized output. |
| [`herdr_model_lanes.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/herdr_model_lanes.py#L1132-L1188) | `_refresh_codex`, `_refresh_claude` | Design reference for independent refresh intervals, last-valid-value retention, stale marking, and source-specific error reporting. |
| [`claude_max_usage.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/claude_max_usage.py#L1-L145) | `parse_keychain_payload`, `read_oauth_token`, `fetch_usage`, `main` | Consulted only for the security comparison. The router does not read the Keychain or the OAuth token. |
| [`tests/test_model_lanes.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/tests/test_model_lanes.py#L20-L128) | Codex and Claude parser tests | Design reference for duration-based Codex selection, boundary rejection, optional windows, and reset validation. |
| [`tests/test_model_lanes.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/tests/test_model_lanes.py#L193-L219) | Cache round-trip test | Design reference for asserting that only normalized fields are cached. |
| [`tests/test_model_lanes.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/tests/test_model_lanes.py#L312-L433) | Refresh and backoff tests | Design reference for independent refresh, stale fallback, and failed-attempt backoff. |
| [`tests/test_claude_max_usage.py`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/tests/test_claude_max_usage.py#L29-L205) | Credential and helper tests | Informed the negative tests for bounded credential access and token redaction. These tests are not a reason to add the OAuth fallback. |
| [`LICENSE`](https://github.com/terry-li-hm/herdr-model-lanes/blob/8568d996298c2da3495124904bbff4048d443347/LICENSE) | Apache License 2.0 | The upstream license, recorded here because it would apply to any copied or materially adapted upstream code. |

The upstream release uses a single large module. This repository implements
only the behavior it needs in `quota_codex.py` and `quota_claude.py`, and it
does not carry over unrelated provider readers, sidebar formatting, or
model-lane selection.

## Codex source

The [official Codex App Server documentation](https://developers.openai.com/codex/app-server/) defines newline-delimited JSON over stdio, the `initialize` and `initialized` handshake, request and response IDs, and `account/rateLimits/read`. The documentation describes `rateLimits` as a backward-compatible single-bucket view and `rateLimitsByLimitId` as the optional multi-bucket view.

The app-server command and WebSocket transport are marked experimental in the
provider documentation. The collector binds behavior to the installed Codex
version, rejects unknown shapes, and fails honestly to `unknown` rather than
guessing. This is the preferred source because it is local, documented, and
avoids reading Codex credential files or transcripts.

### Collection sequence

Start the process with an argv array equivalent to:

```text
codex app-server --listen stdio://
```

Use one absolute deadline of 15 seconds for startup, writes, response reads, and shutdown. The minimum request sequence is:

```text
initialize, id 1
initialized, notification
account/read, id 2
account/rateLimits/read, id 3
```

Ignore unrelated notifications while waiting for the requested response ID. A JSON-RPC error, closed output, invalid response, or deadline failure is a source failure. Terminate the child in `finally`, wait up to two seconds, then kill it if needed. The collector must not leave a reader thread or app-server child behind.

Reject an account whose `authMethod` is API-key based. The `account/read` result can expose authentication metadata, but only the normalized subscription decision may leave the collector. Do not read `~/.codex/auth.json`, other credential stores, or transcript files.

### Source shape and normalization

The relevant response shape is:

```json
{
  "rateLimits": {
    "limitId": "codex",
    "limitName": null,
    "primary": {
      "usedPercent": 31,
      "windowDurationMins": 10080,
      "resetsAt": 1730947200
    },
    "secondary": null,
    "planType": "plus",
    "rateLimitReachedType": null
  },
  "rateLimitsByLimitId": {
    "codex": {
      "primary": {
        "usedPercent": 31,
        "windowDurationMins": 10080,
        "resetsAt": 1730947200
      }
    }
  }
}
```

The current parser:

1. Prefer the top-level `rateLimits` object, since the provider documents it as the compatible account view.
2. If that object is absent, use `rateLimitsByLimitId.codex` only. Do not infer applicability from arbitrary opaque limit IDs or display names.
3. Parse `primary` and `secondary` independently. A null window is absent, not an error.
4. Require numeric `usedPercent` in the inclusive range 0 to 100 and numeric `windowDurationMins` greater than zero. Convert `windowDurationMins` to `window_seconds` by multiplying by 60.
5. Require a numeric `resetsAt` for a window used in pace calculations. A valid percentage without a reset is retained only as `unknown` for that window, never as fresh capacity.
6. Preserve `limitId`, `planType`, and `rateLimitReachedType` only as normalized enums or redacted labels needed for classification. Never cache an entire provider response.

The classifier uses valid future-reset windows. A valid zero remaining window or
an explicit reached state makes Codex `exhausted`. Missing, malformed, or
expired source data becomes `unknown`. A fresh window under 10 percent
remaining that resets more than 12 hours from now makes Codex `critical`. The
implementation does not treat the 10,080-minute weekly window as the only
possible Codex quota.

## Claude Code source

The [official Claude Code status-line documentation](https://code.claude.com/docs/en/statusline) says that Claude runs a configured command locally, sends JSON session data on stdin, and renders the command's stdout. Configure a small capture script through `statusLine.type = "command"`. Set `refreshInterval` to 1800 seconds so a quiet session can refresh the local observation periodically.

This source is local and does not require an OAuth token, network request, Keychain access, credentials-file access, or transcript parsing. It does require the user to configure the status line. The capture script should print a short local status line or nothing, depending on the user's chosen presentation, after atomically writing the normalized cache.

### Source shape and normalization

The documented input contains many fields. The collector must read only `rate_limits` and may record the provider `version` as a non-sensitive diagnostic value. Relevant fields are:

```json
{
  "rate_limits": {
    "five_hour": {
      "used_percentage": 23.5,
      "resets_at": 1738425600
    },
    "seven_day": {
      "used_percentage": 41.2,
      "resets_at": 1738857600
    },
    "spend_limit": {
      "used_percentage": 62.8,
      "resets_at": 1740787200
    }
  },
  "session_id": "discard-this",
  "transcript_path": "/discard/this/path",
  "cwd": "/discard/this/path"
}
```

The status-line contract says that `rate_limits` may be absent before the first API response, each window may be independently absent, and expired windows are dropped. The parser must accept this partial shape. It must not interpret an absent window as zero usage or exhausted capacity.

Normalize `five_hour`, `seven_day`, and, when present, `spend_limit` to the
shared window record. The official feed does not provide the upstream helper's
`seven_day_sonnet` field, so the implementation omits that model-specific
signal rather than reconstructing it. The feed allows
`spend_limit.used_percentage` above 100 after the limit is exceeded. Clamp its
remaining value to zero and classify it as exhausted. For the other windows,
reject percentages outside 0 to 100.

The capture command must discard the complete stdin document after extracting the allowed fields. It must never write session IDs, transcript paths, prompts, responses, working directories, repository identity, or model metadata to the cache. A malformed or missing feed must leave the last valid cache unchanged and report `unknown` or stale state to the router.

### Operating the current collectors

The Codex collector is `collect_codex(state_dir)`. It performs one bounded
local app-server refresh, uses a per-provider lock, writes a successful
normalized snapshot, and retains the last valid snapshot when refresh fails.
The Claude collector is `capture_status_line(document, cache_path,
captured_at=...)`. Configure Claude Code's command status line to send its JSON
input to a small wrapper that calls this function. The router reads the cache
files on each command. It does not make a network request for quota.

The router does not accept quota data from Herdr. It reads only the
owner-only cache files in `--state-dir`, so a caller cannot claim capacity by
supplying provider fields.

## Shared normalized record

The two sources produce the same minimal shape:

```json
{
  "schema_version": 1,
  "provider": "codex",
  "source": "codex_app_server",
  "observed_at": 1780000000,
  "captured_at": 1780000001,
  "freshness": "fresh",
  "reached": false,
  "windows": [
    {
      "name": "primary",
      "used_percent": 31,
      "remaining_percent": 69,
      "window_seconds": 604800,
      "resets_at": 1780604800
    }
  ]
}
```

`observed_at` is the source observation time when the source provides one. For status-line input, use the local capture time because the feed does not provide a quota-observation timestamp. `captured_at` is the local write time. Neither timestamp may be advanced when a refresh fails.

The cache schema must reject unknown top-level fields and invalid percentages. A provider result with no valid window is `unknown`. The capacity layer, not the parser, owns the `surplus`, `on_pace`, `conserve`, `unknown`, `critical`, and `exhausted` labels described in `docs/design.md`.

The capacity layer maps windows to the 5-hour and weekly slots by length, not by
provider name: about 5 hours and about 7 days, each with a 10 percent
tolerance. Claude's `five_hour`/`seven_day` and Codex's `primary`/`secondary`
therefore map the same way. When two windows fall within tolerance of one slot,
the window whose length is closest to the nominal value wins, and a tie prefers
the lower remaining percent. A window of any other length is not sent to Jev.
Claude's `spend_limit` window stores a synthetic length (`resets_at` minus the
capture time), so a short spend limit can look about 5 hours long. The
closest-length rule keeps the real `five_hour` window when both are present,
but a spend limit can fill the 5-hour slot when no real 5-hour window is cached.

## Cache and freshness behavior

The owner-only state directory contains separate `codex-quota.json`,
`claude-quota.json`, `opencode-quota.json`, and `pi-quota.json` files. Each
file is a mode `0600` regular file owned by the current user and contains only
the shared normalized record.

The minimum policy is:

| Rule | Codex | Claude status line |
| --- | --- | --- |
| Normal refresh floor | 300 seconds | 1800 seconds, via `statusLine.refreshInterval` and the capture script's last-attempt record |
| Maximum stale age | 6 hours | 6 hours |
| Concurrent refreshes | Per-source file lock | Per-source file lock |
| Write behavior | Temporary file in the same directory, then atomic replace | Same |
| Permissions | Owner-readable and owner-writable only, mode `0600` | Same |
| Unsafe path or owner | Refused, treated as `unknown` | Same |
| Failed refresh with valid cache | Keep the last value and mark it stale | Keep the last value and mark it stale |
| Failed refresh without cache | Return `unknown` | Return `unknown` |
| Value older than maximum age | Discard for routing and return `unknown` | Discard for routing and return `unknown` |

The cache lock prevents duplicate refreshes, not stale-data promotion. A successful refresh updates `observed_at`. A failed refresh updates only a last-attempt marker if one is needed for backoff. It must not make stale data appear fresh. The two sources retain independent errors and freshness so a working provider remains usable when the other is offline.

The collector makes one bounded Codex attempt per interval and one local
status-line parse per invocation. A retry would need one overall deadline and
a test that proves it cannot create orphan processes.

## Failure semantics

| Failure | Result | Routing effect |
| --- | --- | --- |
| Codex binary missing, app-server timeout, malformed JSONL, RPC error, or shutdown failure | Source error, last valid cache may remain stale | Codex is stale or unknown, never fresh |
| Codex API-key authentication | Source is not a subscription source | Codex is unknown and ineligible for subscription balancing |
| Codex valid zero remaining or explicit reached state | Valid exhausted window | Codex is removed before Jev |
| Claude status-line feed absent before first API response | No observation | Claude is unknown, not exhausted |
| Claude individual window absent or expired | Omit only that window | Other valid Claude windows remain usable |
| Claude status-line parse or cache write failure | Source error, last valid cache may remain stale | Claude is stale or unknown |
| Any cache older than six hours | Expired cache | Provider is unknown |
| All providers unknown | Honest unknown state for every provider | Follow the `docs/design.md` capacity policy for whether unknown capacity remains eligible |

The quota collector does not deny a spawn solely because a provider is
unknown. The router removes exhausted providers, retains unknown providers
with penalty `1`, and records source errors without secrets. Jev never
receives raw timestamps, source response bodies, or credentials.

## Sources

- [Codex App Server documentation](https://developers.openai.com/codex/app-server/), including stdio JSONL, JSON-RPC sequencing, `account/read`, and `account/rateLimits/read`.
- [Claude Code status-line documentation](https://code.claude.com/docs/en/statusline), including `statusLine` configuration, stdin delivery, refresh behavior, and `rate_limits` fields.
- [herdr-model-lanes v3.8.0](https://github.com/terry-li-hm/herdr-model-lanes/tree/v3.8.0), resolved to commit `8568d996298c2da3495124904bbff4048d443347`.
- Upstream `herdr_model_lanes.py`, `claude_max_usage.py`, `tests/test_model_lanes.py`, `tests/test_claude_max_usage.py`, `README.md`, `SECURITY.md`, and `LICENSE` at that commit.
- This repository's [`docs/design.md`](design.md), especially quota collection, cache behavior, and privacy.
