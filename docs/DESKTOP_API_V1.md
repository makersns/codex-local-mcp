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
