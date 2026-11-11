#!/bin/bash
# Rebuild from Contents/Resources, where the complete server/ source is bundled.
set -euo pipefail
if [ "$#" -ne 1 ]; then
  printf '%s\n' '用法：/bin/bash build-macos.sh /绝对路径/Sugar-Local-MCP-Desktop.app' >&2
  exit 2
fi
output_app="$1"
case "${output_app}" in /*.app) ;; *) printf '%s\n' '请提供尚不存在的绝对 .app 路径。' >&2; exit 2;; esac
if [ -e "${output_app}" ]; then
  printf '%s\n' '输出位置已经存在，不会覆盖。' >&2
  exit 2
fi
source_root=$(cd "$(dirname "$0")" && pwd -P)
if [ "$(/usr/bin/uname -s)" != Darwin ] || [ ! -d "${source_root}/server/src/sugar_local_mcp" ]; then
  printf '%s\n' '请在 macOS 中使用包含 server/ 的完整源码包。' >&2
  exit 2
fi
/bin/mkdir -p "$(dirname "${output_app}")"
/usr/bin/osacompile -o "${output_app}" "${source_root}/App.applescript"
resources="${output_app}/Contents/Resources"
(
  cd "${source_root}"
  /usr/bin/tar --exclude='__pycache__' --exclude='*.pyc' --exclude='*.egg-info' \
    -cf - App.applescript desktop.py workspace_config.py permission_guide.py environment_guide.py security_store.py bootstrap.sh install-app.sh \
    build-macos.sh Install_Sugar_Local_MCP.command dependency-manifest.json docs_BOOTSTRAP.md README.md \
    INSTALL.html VALIDATION.md VALIDATION_0.3.0_HISTORY.md VALIDATION_0.4.0_HISTORY.md VALIDATION_0.4.1_HISTORY.md RELEASE_NOTES.md tests server
) | (cd "${resources}" && /usr/bin/tar -xf -)
plist="${output_app}/Contents/Info.plist"
plist_string() {
  if /usr/libexec/PlistBuddy -c "Print :$1" "${plist}" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Set :$1 $2" "${plist}"
  else
    /usr/libexec/PlistBuddy -c "Add :$1 string $2" "${plist}"
  fi
}
plist_string CFBundleIdentifier com.sugar.local-mcp.desktop
plist_string CFBundleName 'Sugar Local MCP Desktop'
plist_string CFBundleDisplayName 'Sugar Local MCP Desktop'
plist_string CFBundleVersion 0.5.0
plist_string CFBundleShortVersionString 0.5.0
plist_string LSApplicationCategoryType public.app-category.developer-tools
/usr/bin/codesign --force --sign - --timestamp=none "${output_app}"
/usr/bin/codesign --verify --deep --strict "${output_app}"
printf '%s\n' "${output_app}"
