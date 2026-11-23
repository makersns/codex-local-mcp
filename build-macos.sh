#!/bin/bash
# Build a signed native Codex Local MCP.app without changing installed apps or services.
set -euo pipefail
if [ "$#" -ne 1 ]; then
  printf '%s\n' '用法：/bin/bash build-macos.sh /绝对路径/Codex-Local-MCP.app' >&2
  exit 2
fi
output_app="${1%/}"
case "$output_app" in /*.app) ;; *) printf '%s\n' '请提供尚不存在的绝对 .app 路径。' >&2; exit 2;; esac
if [ -e "$output_app" ] || [ -L "$output_app" ]; then
  printf '%s\n' '输出位置已经存在，不会覆盖。' >&2
  exit 2
fi
source_root=$(cd "$(dirname "$0")" && pwd -P)
[ "$(/usr/bin/uname -s)" = Darwin ] || { printf '%s\n' '原生应用需要在 macOS 构建。' >&2; exit 2; }
for required in desktop_api.py desktop.py security_store.py workspace_config.py bootstrap.sh dependency-manifest.json tools/generate-app-icon.swift macos/Localizations.json; do
  [ -f "$source_root/$required" ] || { printf '缺少构建文件：%s\n' "$required" >&2; exit 2; }
done
swift_sources=("$source_root"/macos/*.swift)
[ -f "${swift_sources[0]}" ] || { printf '%s\n' '缺少 macos/ 中的原生界面源码。' >&2; exit 2; }
sdk=$(/usr/bin/xcrun --sdk macosx --show-sdk-path)
version=$(/usr/bin/plutil -extract app_version raw -o - "$source_root/dependency-manifest.json")
case "$version" in ''|*[!0-9.]*) printf '%s\n' '版本清单无效。' >&2; exit 2;; esac
output_parent=$(dirname "$output_app")
/bin/mkdir -p "$output_parent"
stage=$(/usr/bin/mktemp -d "$output_parent/.codex-build.XXXXXXXX")
cleanup() { /bin/rm -rf "$stage"; }
trap cleanup EXIT
app="$stage/Codex Local MCP.app"
resources="$app/Contents/Resources"
/bin/mkdir -p "$app/Contents/MacOS" "$resources" "$stage/module-cache"
architectures=(${CODEX_MCP_BUILD_ARCHS:-arm64 x86_64})
[ "${#architectures[@]}" -gt 0 ] || exit 2
binaries=()
for architecture in "${architectures[@]}"; do
  case "$architecture" in arm64|x86_64) ;; *) printf '不支持的架构：%s\n' "$architecture" >&2; exit 2;; esac
  binary="$stage/CodexLocalMCP-$architecture"
  printf '正在构建 %s 原生界面…\n' "$architecture" >&2
  /usr/bin/xcrun --sdk macosx swiftc -parse-as-library -swift-version 5 -O \
    -target "$architecture-apple-macosx13.0" -sdk "$sdk" \
    -module-cache-path "$stage/module-cache" -module-name CodexLocalMCP \
    -framework SwiftUI -framework AppKit "${swift_sources[@]}" -o "$binary"
  binaries+=("$binary")
done
if [ "${#binaries[@]}" -eq 1 ]; then
  /bin/cp "${binaries[0]}" "$app/Contents/MacOS/CodexLocalMCP"
else
  /usr/bin/lipo -create "${binaries[@]}" -output "$app/Contents/MacOS/CodexLocalMCP"
fi
/bin/chmod 755 "$app/Contents/MacOS/CodexLocalMCP"
source_entries=(desktop_api.py desktop.py workspace_config.py permission_guide.py environment_guide.py security_store.py
  bootstrap.sh install-app.sh build-macos.sh Install_Codex_Local_MCP.command
  dependency-manifest.json server macos tools)
for optional in README.md INSTALL.html RELEASE_NOTES.md VALIDATION.md docs_BOOTSTRAP.md docs tests; do
  [ ! -e "$source_root/$optional" ] || source_entries+=("$optional")
done
# Current validation documents link to these preserved root-level histories.
for history in "$source_root"/VALIDATION_*_HISTORY.md; do
  [ ! -f "$history" ] || source_entries+=("${history##*/}")
done
(
  cd "$source_root"
  /usr/bin/tar --exclude='.git' --exclude='.venv' --exclude='venv' --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='*.egg-info' --exclude='.pytest_cache' --exclude='build' \
    --exclude='dist' --exclude='logs' --exclude='*.log' --exclude='test-results.txt' \
    --exclude='.DS_Store' --exclude='._*' -cf - "${source_entries[@]}"
) | (cd "$resources" && /usr/bin/tar -xf -)
CLANG_MODULE_CACHE_PATH="$stage/module-cache" /usr/bin/xcrun swift \
  "$source_root/tools/generate-app-icon.swift" "$stage/AppIcon.iconset"
/usr/bin/iconutil -c icns "$stage/AppIcon.iconset" -o "$resources/AppIcon.icns"
digest=$(
  cd "$resources"
  /usr/bin/find . -type f -print0 | /usr/bin/xargs -0 /usr/bin/shasum -a 256 |
    LC_ALL=C /usr/bin/sort | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}'
)
revision=$(/usr/bin/git -C "$source_root" rev-parse HEAD 2>/dev/null || true)
case "$revision" in *[!a-f0-9]*|'') revision=source-archive;; esac
dirty=false
if /usr/bin/git -C "$source_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  [ -z "$(/usr/bin/git -C "$source_root" status --porcelain --untracked-files=normal)" ] || dirty=true
fi
plist="$app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Clear dict' "$plist" >/dev/null
plist_string() { /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$plist"; }
plist_string CFBundleIdentifier com.sugar.local-mcp.desktop
plist_string CFBundleExecutable CodexLocalMCP
plist_string CFBundleName 'Codex Local MCP'
plist_string CFBundleDisplayName 'Codex Local MCP'
plist_string CFBundleVersion "$version"
plist_string CFBundleShortVersionString "$version"
plist_string CFBundlePackageType APPL
plist_string CFBundleIconFile AppIcon.icns
plist_string CFBundleDevelopmentRegion en
/usr/libexec/PlistBuddy -c 'Add :CFBundleLocalizations array' "$plist"
for language in en zh-Hans zh-Hant ja; do
  /usr/libexec/PlistBuddy -c "Add :CFBundleLocalizations: string $language" "$plist"
done
plist_string LSMinimumSystemVersion 13.0
plist_string LSApplicationCategoryType public.app-category.developer-tools
plist_string NSPrincipalClass NSApplication
plist_string NSHumanReadableCopyright '© 2026 Sugar'
plist_string CodexBuildIdentifier "$digest"
/usr/libexec/PlistBuddy -c 'Add :NSHighResolutionCapable bool true' "$plist"
/usr/libexec/PlistBuddy -c 'Add :NSSupportsAutomaticTermination bool false' "$plist"
cat >"$resources/codex-build-info.json" <<JSON
{
  "product": "Codex Local MCP",
  "author": "Sugar",
  "version": "$version",
  "source_revision": "$revision",
  "source_modified": $dirty,
  "resource_sha256": "$digest",
  "minimum_macos": "13.0",
  "interface_languages": ["en", "zh-Hans", "zh-Hant", "ja"],
  "architectures": "$(/usr/bin/lipo -archs "$app/Contents/MacOS/CodexLocalMCP")"
}
JSON
/usr/bin/plutil -lint "$plist" >/dev/null
/usr/bin/codesign --force --sign - --timestamp=none "$app"
/usr/bin/codesign --verify --deep --strict "$app"
if [ -e "$output_app" ] || [ -L "$output_app" ]; then
  printf '%s\n' '构建期间输出位置已被使用，未覆盖。' >&2
  exit 2
fi
/bin/mv "$app" "$output_app"
printf '%s\n' "$output_app"
