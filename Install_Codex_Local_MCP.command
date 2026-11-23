#!/bin/bash
# Optional source installer. Most users can open the prebuilt app directly.
set -euo pipefail
umask 077
installer_directory=$(cd "$(dirname "$0")" && pwd -P)
source_directory="$installer_directory/Source"
if [ ! -d "$source_directory/server/src/sugar_local_mcp" ]; then source_directory="$installer_directory"; fi
temporary_directory=''
installer_done=0
cleanup_installer() {
  installer_status=$?
  if [ -n "$temporary_directory" ] && [ -d "$temporary_directory" ]; then /bin/rm -rf "$temporary_directory"; fi
  if [ "$installer_done" != 1 ]; then
    printf '\n%s\n' '安装尚未完成。原有文件夹权限、连接设置和钥匙串会保留。'
    if [ -t 0 ]; then printf '%s' '按回车关闭窗口…'; IFS= read -r installer_reply || true; fi
  fi
  exit "$installer_status"
}
trap cleanup_installer EXIT
[ "$(/usr/bin/uname -s)" = Darwin ] || { printf '%s\n' '此安装器只支持 macOS。' >&2; exit 2; }
if ! /usr/bin/xcrun --find swiftc >/dev/null 2>&1; then
  printf '%s\n' '从源码安装需要 Apple Command Line Tools。你也可以直接打开已构建的 Codex Local MCP.app。' >&2
  exit 2
fi
[ -f "$source_directory/build-macos.sh" ] && [ -d "$source_directory/macos" ] || {
  printf '%s\n' '源码包不完整，请保持 Source 文件夹与安装器在一起。' >&2; exit 2;
}
printf '%s\n' 'Codex Local MCP 0.6.0 · by Sugar' '正在构建原生应用…'
temporary_directory=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/codex-local-mcp-install.XXXXXXXX")
new_app="$temporary_directory/Codex Local MCP.app"
/bin/bash "$source_directory/build-macos.sh" "$new_app"
installed_app=$(/bin/bash "$source_directory/install-app.sh" "$new_app")
/usr/bin/codesign --verify --deep --strict "$installed_app"
printf '%s\n' '安装完成，正在打开 Codex Local MCP。'
/usr/bin/open -n "$installed_app"
installer_done=1
printf '%s\n' '以后可从 Applications 或桌面打开；关闭窗口后连接仍可在后台运行。'
