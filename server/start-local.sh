#!/usr/bin/env bash
set -euo pipefail
bridge_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
bridge_python="${bridge_dir}/.venv/bin/python"
if [[ ! -x "$bridge_python" ]]; then
  echo "Please complete the virtual-environment installation in README.md first." >&2
  exit 2
fi
exec "$bridge_python" -m sugar_local_mcp tunnel --profile "${1:-sugar-local}"
