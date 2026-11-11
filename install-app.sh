#!/bin/bash
# Install only this owned app in the current user's Applications directory.
set -euo pipefail
umask 077
source_app="${1%/}"
target_app="$HOME/Applications/Sugar Local MCP Desktop.app"
expected_id="com.sugar.local-mcp.desktop"
mkdir -p "$HOME/Applications"
if [ "$source_app" = "$target_app" ]; then
  printf '%s\n' "$target_app"
  exit 0
fi
source_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$source_app/Contents/Info.plist")
if [ "$source_id" != "$expected_id" ]; then
  printf '%s\n' '安装应用的标识不匹配，已停止。' >&2
  exit 2
fi
# The downloaded and installed copies can be opened at the same time. Keep the
# check-and-move sequence atomic with respect to another copy of this installer.
install_lock="$HOME/Applications/.Sugar-Local-MCP-Desktop-install.lock"
stage_app=''
lock_acquired=0
cleanup_install() {
  if [ -n "${stage_app}" ] && [ -d "${stage_app}" ]; then /bin/rm -rf "${stage_app}"; fi
  if [ "${lock_acquired}" = 1 ]; then
    /bin/rm -f "${install_lock}/pid"
    /bin/rmdir "${install_lock}" 2>/dev/null || true
  fi
}
trap cleanup_install EXIT
if ! /bin/mkdir "${install_lock}" 2>/dev/null; then
  old_pid=''
  [ ! -f "${install_lock}/pid" ] || IFS= read -r old_pid <"${install_lock}/pid" || true
  case "${old_pid}" in
    ''|*[!0-9]*) ;;
    *)
      if ! kill -0 "${old_pid}" 2>/dev/null; then
        /bin/rm -f "${install_lock}/pid"
        /bin/rmdir "${install_lock}" 2>/dev/null || true
      fi
      ;;
  esac
  if ! /bin/mkdir "${install_lock}" 2>/dev/null; then
    printf '%s\n' '另一个应用安装正在进行，请稍后重新打开。' >&2
    exit 2
  fi
fi
lock_acquired=1
printf '%s\n' "$$" >"${install_lock}/pid"
if [ -e "$target_app" ]; then
  existing_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$target_app/Contents/Info.plist" 2>/dev/null || true)
  if [ "$existing_id" != "$expected_id" ]; then
    printf '%s\n' '目标位置已有其他应用，未覆盖。请先在 Finder 中检查。' >&2
    exit 2
  fi
  source_version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$source_app/Contents/Info.plist")
  existing_version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$target_app/Contents/Info.plist" 2>/dev/null || true)
  if [ "$source_version" = "$existing_version" ] && /usr/bin/codesign --verify "$target_app" >/dev/null 2>&1; then
    printf '%s\n' "$target_app"
    exit 0
  fi
fi
stage_app="$HOME/Applications/.Sugar-Local-MCP-Desktop-install-$$.app"
/usr/bin/ditto "$source_app" "$stage_app"
/usr/bin/codesign --verify "$stage_app"
if [ -e "$target_app" ]; then
  backup_app="$HOME/Applications/Sugar Local MCP Desktop.previous.$(/bin/date +%Y%m%d%H%M%S).app"
  /bin/mv "$target_app" "$backup_app"
fi
/bin/mv "$stage_app" "$target_app"
desktop_app="$HOME/Desktop/Sugar Local MCP Desktop.app"
if [ -d "$HOME/Desktop" ] && [ ! -e "$desktop_app" ] && [ ! -L "$desktop_app" ]; then
  /bin/ln -s "$target_app" "$desktop_app"
fi
register='/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister'
if [ -x "$register" ]; then
  "$register" -f "$target_app" >/dev/null 2>&1 || true
fi
printf '%s\n' "$target_app"
