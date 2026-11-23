#!/bin/bash
# Install the owned native app. Never modify connection settings or stop a service.
# stdout: exactly one absolute installed .app path on success.
set -euo pipefail
umask 077
fail() { printf '%s\n' "$*" >&2; exit 2; }
[ "$#" -ge 1 ] || fail '请选择要安装的 Codex Local MCP.app。'
source_app="${1%/}"
shift
install_directory="$HOME/Applications"
make_shortcut=1
custom_destination=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --destination)
      [ "$#" -ge 2 ] || fail '--destination 需要一个绝对目录。'
      install_directory="${2%/}"; custom_destination=1; make_shortcut=0; shift 2;;
    --no-shortcut) make_shortcut=0; shift;;
    *) fail '安装参数无法识别。';;
  esac
done
[ "$(/usr/bin/uname -s)" = Darwin ] || fail '此安装器只支持 macOS。'
case "$source_app" in /*.app) ;; *) fail '安装来源必须是绝对 .app 路径。';; esac
case "$install_directory" in /*) ;; *) fail '安装位置必须是绝对目录。';; esac
[ -n "$install_directory" ] && [ "$install_directory" != / ] || fail '不能安装到根目录。'
[ -d "$source_app" ] && [ ! -L "$source_app" ] || fail '安装来源不是有效的应用目录。'
[ ! -L "$install_directory" ] || fail '安装目录不能是符号链接。'
source_app=$(cd "$source_app" && pwd -P)
expected_id='com.sugar.local-mcp.desktop'
plist_value() { /usr/libexec/PlistBuddy -c "Print :$2" "$1/Contents/Info.plist" 2>/dev/null; }
[ "$(plist_value "$source_app" CFBundleIdentifier)" = "$expected_id" ] || fail '应用标识不匹配，已停止安装。'
[ "$(plist_value "$source_app" CFBundleExecutable)" = CodexLocalMCP ] || fail '请使用 Codex Local MCP 的原生安装包。'
/usr/bin/codesign --verify --deep --strict "$source_app" >/dev/null 2>&1 || fail '应用完整性验证失败，请重新下载完整安装包。'
source_version=$(plist_value "$source_app" CFBundleVersion)
source_digest=$(plist_value "$source_app" CodexBuildIdentifier || true)
[[ "$source_version" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]] || fail '安装包版本号无效。'
[ -n "$source_digest" ] || fail '安装包缺少构建标识。'
/bin/mkdir -p "$install_directory"
install_directory=$(cd "$install_directory" && pwd -P)
[ "$(/usr/bin/stat -f '%u' "$install_directory")" = "$(/usr/bin/id -u)" ] || fail '请安装到当前用户拥有的目录。'
target_app="$install_directory/Codex Local MCP.app"
if [ "$source_app" = "$target_app" ]; then
  printf '%s\n' "$target_app"
  exit 0
fi
install_lock="$install_directory/.Codex-Local-MCP-install.lock"
stage_app=''
backup_app=''
lock_acquired=0
installation_complete=0
cleanup_install() {
  if [ "$installation_complete" != 1 ] && [ -n "$backup_app" ] && [ -d "$backup_app" ] &&
     [ ! -e "$target_app" ] && [ ! -L "$target_app" ]; then
    /bin/mv "$backup_app" "$target_app" || true
  fi
  if [ -n "$stage_app" ] && [ -d "$stage_app" ]; then /bin/rm -rf "$stage_app"; fi
  if [ "$lock_acquired" = 1 ]; then
    /bin/rm -f "$install_lock/pid"
    /bin/rmdir "$install_lock" 2>/dev/null || true
  fi
}
trap cleanup_install EXIT
if ! /bin/mkdir "$install_lock" 2>/dev/null; then
  [ -d "$install_lock" ] && [ ! -L "$install_lock" ] ||
    fail '安装锁位置被其他文件占用，请检查安装目录。'
  [ "$(/usr/bin/stat -f '%u' "$install_lock")" = "$(/usr/bin/id -u)" ] ||
    fail '安装锁不属于当前用户，请检查安装目录。'
  [ ! -L "$install_lock/pid" ] || fail '安装锁记录异常，请检查安装目录。'
  old_pid=''
  [ ! -f "$install_lock/pid" ] || IFS= read -r old_pid <"$install_lock/pid" || true
  case "$old_pid" in
    ''|*[!0-9]*) ;;
    *) if ! kill -0 "$old_pid" 2>/dev/null; then
         /bin/rm -f "$install_lock/pid"
         /bin/rmdir "$install_lock" 2>/dev/null || true
       fi;;
  esac
  /bin/mkdir "$install_lock" 2>/dev/null || fail '另一个安装正在进行，请稍后重新打开应用。'
fi
lock_acquired=1
printf '%s\n' "$$" >"$install_lock/pid"
# Compare numeric release versions without requiring a Python installation.
version_is_newer() {
  local left="$1" right="$2" index lvalue rvalue
  local left_parts right_parts
  IFS=. read -r -a left_parts <<<"$left"
  IFS=. read -r -a right_parts <<<"$right"
  for index in 0 1 2; do
    lvalue="${left_parts[$index]:-0}"; rvalue="${right_parts[$index]:-0}"
    if (( 10#$lvalue > 10#$rvalue )); then return 0; fi
    if (( 10#$lvalue < 10#$rvalue )); then return 1; fi
  done
  return 1
}
if [ -e "$target_app" ] || [ -L "$target_app" ]; then
  [ -d "$target_app" ] && [ ! -L "$target_app" ] || fail '安装位置已有其他文件，未覆盖。'
  [ "$(plist_value "$target_app" CFBundleIdentifier || true)" = "$expected_id" ] || fail '安装位置已有其他应用，未覆盖。'
  existing_version=$(plist_value "$target_app" CFBundleVersion || true)
  [[ "$existing_version" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]] || fail '已有应用版本无法识别，请先在 Finder 中检查。'
  if version_is_newer "$existing_version" "$source_version"; then
    fail '这台 Mac 已安装更新版本，已阻止旧安装包覆盖。'
  fi
  existing_digest=$(plist_value "$target_app" CodexBuildIdentifier || true)
  if [ "$existing_version" = "$source_version" ] && [ "$existing_digest" = "$source_digest" ] &&
     /usr/bin/codesign --verify --deep --strict "$target_app" >/dev/null 2>&1; then
    installation_complete=1
    printf '%s\n' "$target_app"
    exit 0
  fi
fi
stage_app="$install_directory/.Codex-Local-MCP-install-$$.app"
[ ! -e "$stage_app" ] && [ ! -L "$stage_app" ] || fail '临时安装位置已被占用，请稍后重试。'
/usr/bin/ditto "$source_app" "$stage_app"
/usr/bin/codesign --verify --deep --strict "$stage_app" >/dev/null 2>&1 || fail '安装副本完整性验证失败。'
if [ -e "$target_app" ]; then
  backup_app="$install_directory/Codex Local MCP.previous.$(/bin/date +%Y%m%d%H%M%S).$$.app"
  [ ! -e "$backup_app" ] || fail '旧版本备份位置已存在，请稍后重试。'
  /bin/mv "$target_app" "$backup_app"
fi
/bin/mv "$stage_app" "$target_app"
stage_app=''
installation_complete=1
if [ "$make_shortcut" = 1 ] && [ -d "$HOME/Desktop" ]; then
  desktop_app="$HOME/Desktop/Codex Local MCP.app"
  if [ ! -e "$desktop_app" ] && [ ! -L "$desktop_app" ]; then
    /bin/ln -s "$target_app" "$desktop_app"
  fi
fi
if [ "$custom_destination" = 0 ]; then
  register='/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister'
  if [ -x "$register" ]; then "$register" -f "$target_app" >/dev/null 2>&1 || true; fi
fi
printf '%s\n' "$target_app"
