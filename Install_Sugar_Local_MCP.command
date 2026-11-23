#!/bin/bash
# Compatibility filename for older downloaded source instructions.
set -euo pipefail
installer_directory=$(cd "$(dirname "$0")" && pwd -P)
exec /bin/bash "$installer_directory/Install_Codex_Local_MCP.command" "$@"
