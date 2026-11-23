# Desktop JSON API v1

Status: frozen for Codex Local MCP 0.6.0 implementation, 2026-11-23.
Owner: desktop_api.py. Consumer: macos native app. Product author: Sugar.

## Transport

Single subprocess invocation:
python -B /absolute/resources/desktop_api.py --support /absolute/support

One UTF-8 JSON request on stdin, then EOF. api_version may be omitted by v1 clients (defaults to 1); an unsupported explicit version is rejected. One JSON response on stdout, newline terminated.
No shell expansion, HTTP listener, arbitrary commands or real key in argv.
Input limit 2 MiB; bounded output. Errors are safe user messages, never raw tracebacks/credential subprocess output.

Request:
{"api_version":1,"action":"snapshot","payload":{}}

Response:
{"api_version":1,"ok":true,"data":{},"error":null}
{"api_version":1,"ok":false,"data":null,"error":{"code":"validation","message":"请选择真实存在的文件夹。","field_errors":{"root":"文件夹不存在"},"retryable":false,"saved":false}}

Optional error fields: field_errors, retryable, saved. For a partially successful save (saved:true), data contains the current snapshot so the UI can show persisted values without losing recovery context.
UI must handle unknown additional fields and unknown error codes gracefully.
All JSON field names use snake_case.

## Snapshot

action snapshot, payload {}. Read-only; never starts/restarts a tunnel and never requests OS permissions.
data:
{
 "app":{"name":"Codex Local MCP","version":"0.6.0","author":"Sugar"},
 "connection":{"configured":true,"computer_name":"我的 MacBook","tunnel_id":"tunnel_...","credential_status":"stored"},
 "status":{"state":"ready","title":"本机已就绪","message":"","managed":true,"upgrade_pending":false,"pid":123,"port":8080},
 "workspace_document":{"workspaces":[{"id":"project","root":"/absolute/path","allow_write":true,"allow_exec":false}],"state_dir":"/..."},
 "revision":"64 lowercase hex characters",
 "runtime":{"available":true,"message":"","python":"/runtime/python"}
}

credential_status: stored | missing | unavailable.
status.state: needs_setup | stopped | connecting | ready | error | port_conflict; upgrade_pending is a separate boolean.
revision covers current settings and full workspace document; absent files are distinct.
No secret, raw keychain entry or development environment values in snapshot.
Only expose fields needed by the UI. Preserve complete supported workspace document so custom limits survive round-trips.

## Save operations

action save_connection:
payload {
 "expected_revision":"...",
 "connection":{"computer_name":"...","tunnel_id":"..."},
 "runtime_key":"optional new key, omitted/empty retains existing",
 "restart":true
}

action save_workspaces:
payload {"expected_revision":"...","document":{FULL_WORKSPACE_DOCUMENT},"restart":true}

action setup:
payload {
 "expected_revision":"...",
 "connection":{"computer_name":"...","tunnel_id":"..."},
 "runtime_key":"...",
 "document":{FULL_WORKSPACE_DOCUMENT},
 "restart":true
}

UI final button explicitly communicates restart. No side effects while filling forms/selecting folders.
Lock, re-read and compare revision, validate all inputs before writes, then atomically replace private files.
On I/O failure restore original file contents. Never discard unrelated supported document fields.
If settings persisted but connection startup fails, report saved:true and offer retry; do not pretend the save failed.
Empty runtime_key retains the existing key. First setup requires a key unless already present.
Read-only snapshot must not fetch key content to indicate existence.

Success data for all lifecycle/save mutations:
{"snapshot":{SNAPSHOT},"saved":true,"restarted":true,"restart_required":false,"message":"设置已保存，本机已就绪。"}
For lifecycle only saved:false. If restart:false, save without restarting and report restart_required accurately.

## Lifecycle

start payload {}: require configured settings; no AppleScript wizard.
stop payload {"confirm":true}
restart payload {"confirm":true}
Only affect this app's managed launchctl job. Recheck listener ownership before stopping.
Cannot claim ChatGPT end-to-end verification solely from local health.

## Folder suggestion

suggest_workspace payload {"root":"/chosen/folder","existing_ids":["project"]}
data {"workspace":{"id":"suggested-id","root":"/canonical/folder","allow_write":true,"allow_exec":false}}
Never save. Folder picker is native UI. Existing permissions stay unchanged.

## Diagnostics

diagnostics payload {"kind":"permissions"|"office","verify":false,"include_libreoffice":false}
data {
 "kind":"office","summary":"...",
 "checks":[{"id":"xlsx","title":"Excel 文件","status":"available","detail":"基础读写可用"}]
}
Check status: available | missing | denied | unknown | unavailable | error.
Permission checks and document verification only run after user requests.
request_permission payload {"kind":"screen_capture"|"accessibility"|"post_event","confirm":true}
Returns safe message and updated check results when obtainable.
Opening fixed macOS privacy settings and documented help URLs is native UI responsibility.
Do not manufacture Chrome integration connection status.

## Logs

logs payload {"source":"errors","lines":150}
source must belong to fixed allowlist: bootstrap -> bootstrap.log, runtime -> tunnel.stdout.log, errors -> tunnel.stderr.log.
data {"source":"errors","text":"redacted bounded content","truncated":false,"available":true}
No arbitrary file paths, symlinks or secret readback.
UI defaults to latest useful errors, manual refresh, can copy redacted content.
Opening the known support/logs directory uses native UI.

## Native app operational interface

--preview: isolated illustrative state; never reads or writes actual settings/keychain/launchctl.
--page overview|folders|connection|diagnostics|logs: initial page for preview/QA.
--support absolute-path: explicitly isolated support directory for tests.
--screenshot absolute-path: render actual NSHostingView/window to PNG for QA.
--self-test: native model/protocol contract checks without user settings.

Normal launch prepares runtime asynchronously but does not restart an existing tunnel.
The old support namespace and bundle/Keychain identifiers are compatibility details, not UI branding.

## Support-directory isolation

Normal operation reuses the legacy support directory. Preview never invokes the real backend. An explicitly different --support used in tests must not stop/restart the default production launchctl label; use a distinct deterministic label or reject lifecycle actions. Tests use fixtures/mocks for Keychain and launchctl. No test may contact a real remote tunnel with invented credentials.

Normal distributed-App launch first uses install-app.sh to install into ~/Applications/Codex Local MCP.app, relaunching the installed copy. --preview, --support, --self-test and screenshot fixture modes skip auto-installation. Preparation remains asynchronous and cannot stop an existing connection.

## Conversation activity (0.6.0 additive contract)

The read-only snapshot includes an activity object. The desktop bridge asks the current MCP process for activity_snapshot through the existing private Unix socket, with a 1-second request deadline. This is not a public MCP tool, does not create a network listener, and never contributes to conversation activity or the activity audit log. Tool discovery remains the existing 15 tools when write and execution are enabled. SDK-injected Context is never part of a tool input schema.

Successful example:

    {
      "available": true,
      "reason": null,
      "message": "本次连接启动后统计；会话活动不代表聊天窗口持续在线。",
      "window_seconds": 600,
      "observed_at": "2026-11-23T10:00:00+00:00",
      "statistics_started_at": "2026-11-23T09:00:00+00:00",
      "instance_id": "0123456789abcdef0123456789abcdef",
      "active_sessions": 1,
      "recent_sessions": 2,
      "active_calls": 2,
      "unknown_active_calls": 0,
      "unknown_recent_calls": 0,
      "running_tasks": 1,
      "metadata_seen": true,
      "coverage_complete": true,
      "capacity_limited": false,
      "last_activity_at": "2026-11-23T09:59:58+00:00",
      "last_operation": "read_file",
      "last_workspace": "project",
      "sessions": [
        {
          "number": 1,
          "active_calls": 2,
          "last_activity_at": "2026-11-23T09:59:58+00:00",
          "last_operation": "read_file",
          "last_workspace": "project"
        },
        {
          "number": 2,
          "active_calls": 0,
          "last_activity_at": "2026-11-23T09:58:00+00:00",
          "last_operation": "workspace_list",
          "last_workspace": null
        }
      ],
      "sessions_truncated": false
    }

### Field semantics

| Field | Type when available | Meaning |
| --- | --- | --- |
| active_sessions | nonnegative integer | Distinct recognized conversations with at least one unfinished tool invocation. |
| recent_sessions | nonnegative integer | Distinct recognized conversations that began a call in the last 600 seconds, plus conversations still executing a call even when it began earlier. |
| active_calls | nonnegative integer | All unfinished remote tool invocations, including unknown and capacity-limited origins. Multiple calls in one conversation remain one active session. |
| unknown_active_calls | nonnegative integer | Unfinished calls without valid openai/session metadata. |
| unknown_recent_calls | nonnegative integer | Calls started in the rolling 600-second window without valid metadata. This is a request count, not an inferred conversation count. |
| running_tasks | nonnegative integer | Managed command leaders still running. Completed retained jobs are excluded. A run_command response ends its invocation even when its command continues. |
| metadata_seen | boolean | At least one valid conversation metadata value has been seen during this process lifetime. |
| coverage_complete | boolean | False whenever current/recent unidentified calls or registry capacity loss prevent complete conversation counts. |
| capacity_limited | boolean | Some current/window activity could not be retained within bounded memory. Counts must be shown as lower bounds or partial coverage. |
| observed_at | ISO 8601 UTC string | When this fresh aggregate was sampled. |
| statistics_started_at | ISO 8601 UTC string | Start of this process's in-memory statistics. |
| instance_id | 32 lowercase hex characters | Random per-process marker. A restart changes it and resets all statistics. |
| last_activity_at | ISO 8601 UTC string or null | Most recent remote invocation start, or null before the first invocation. |
| last_operation | registered tool name or null | Tool name only; never arguments or content. |
| last_workspace | workspace ID or null | Bounded configured-style ID only; no filesystem path. |
| sessions | array, at most 32 rows | Anonymous recognized conversations, prioritizing those currently invoking tools. |
| sessions_truncated | boolean | More retained sessions exist than the displayed detail rows. This alone does not make aggregate counts incomplete. |

Each session row contains number (positive local sequence number), active_calls, last_activity_at, last_operation and nullable last_workspace. Display numbers as local anonymous labels such as “会话 1”. Numbers and keys are neither account IDs nor persistent identifiers and cannot correlate across service restarts. The native UI may map known workspace IDs to already-authorized folder names.

All statistical timestamps are wall-clock UTC for display. Expiration and the rolling window use a monotonic clock. A long-running active conversation remains in both active_sessions and recent_sessions. Exceptions, cancellation and audit failures end active invocation counts in a finally block. Unknown metadata does not reject the actual tool call.

### Unavailable and incomplete states

If no managed backend is running, the support directory is isolated, the older backend does not implement this operation, or IPC fails, the parent snapshot still succeeds. activity.available is false; reason is one of not_running, isolated, unsupported, unavailable or invalid_response. message is a fixed ordinary explanation, without backend exception text.

When unavailable, all six integer count fields, all statistical timestamps, instance_id, metadata_seen, coverage_complete, capacity_limited, last_operation and last_workspace are null. window_seconds remains 600, sessions is empty, and sessions_truncated is false. Missing activity from an older bridge is also unavailable. The UI must show no-data placeholders and must not substitute zero or retain earlier numbers as if they were live.

Unknown sources are never merged into one invented session. If unknown calls exist but no session is recognized, present “来源未识别” rather than “0 个会话已连接”. With recognized and unknown sources together, label the recognized count as partial. There is no chat-close or online heartbeat event: none of these metrics may be called exact online chat counts.

### Memory and privacy bounds

At most 256 recognized session records, 4096 unknown-start timestamps, and 32 visible detail rows are kept. Expired idle records are pruned; current invocations take priority over idle records. If all retained sessions are active, excess identified calls contribute to active_calls and mark capacity_limited, without being mislabeled as unknown metadata. Capacity loss stays flagged while the lost activity could affect the current window or active counts.

Incoming openai/session values are accepted only as nonempty strings of at most 1024 UTF-8 bytes without control characters. Each valid value is immediately reduced to an HMAC using a fresh secret held only in this process. Only the digest is retained in memory; neither raw values, digests nor the secret are returned in IPC, GUI data or logs. No activity file or durable history is written. Metadata is for display only, never authorization or workspace/task ownership.

Real tunnel preservation of the metadata is a separate integration check after installation and reconnect. Unit and stdio tests use invented identifiers only. A locally ready tunnel alone cannot prove real ChatGPT metadata coverage.
