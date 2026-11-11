#!/bin/bash
# Build the native app on the target Mac, preserving the installed configuration.
# This is an alternative one-time installer when no prebuilt .app is supplied.
set -euo pipefail
umask 077

installer_directory=$(cd "$(dirname "$0")" && pwd -P)
source_directory="${installer_directory}/Source"
if [ ! -d "${source_directory}/server/src/sugar_local_mcp" ]; then
  # The script is also included beside the editable source for rebuilding.
  source_directory="${installer_directory}"
fi
temporary_directory=''
installer_done=0
cleanup_installer() {
  installer_status=$?
  if [ -n "${temporary_directory}" ] && [ -d "${temporary_directory}" ]; then
    /bin/rm -rf "${temporary_directory}"
  fi
  if [ "${installer_done}" != 1 ]; then
    printf '\n%s\n' '安装尚未完成。请查看上面的具体错误；原有连接配置和钥匙串条目会保留。'
    if [ -t 0 ]; then
      printf '%s' '按回车关闭窗口…'
      IFS= read -r installer_reply || true
    fi
  fi
  exit "${installer_status}"
}
trap cleanup_installer EXIT

if [ "$(/usr/bin/uname -s)" != Darwin ]; then
  printf '%s\n' '此安装器只支持 macOS。' >&2
  exit 2
fi
if [ ! -x /usr/bin/osacompile ] || [ ! -x /usr/bin/codesign ]; then
  printf '%s\n' '未找到 macOS 自带的应用编译或签名工具，暂时无法构建应用。' >&2
  exit 2
fi
if [ ! -f "${source_directory}/build-macos.sh" ] || [ ! -f "${source_directory}/server/pyproject.toml" ]; then
  printf '%s\n' '安装包不完整。请完整解压后运行，保持 Source 文件夹与安装器在一起。' >&2
  exit 2
fi

printf '%s\n' 'Sugar Local MCP Desktop 0.5.0' '正在使用这台 Mac 的系统工具构建应用…'
temporary_directory=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/sugar-local-mcp-install.XXXXXXXX")
new_app="${temporary_directory}/Sugar Local MCP Desktop.app"
/bin/bash "${source_directory}/build-macos.sh" "${new_app}"
printf '%s\n' '应用构建完成，正在安装到当前用户的 Applications 目录…'
installed_app=$(/bin/bash "${source_directory}/install-app.sh" "${new_app}")
/usr/bin/codesign --verify --deep --strict "${installed_app}"
printf '%s\n' '正在打开应用。首次准备完成后，按窗口提示连接；已有项目和隧道设置会保留。'
/usr/bin/open "${installed_app}"
installer_done=1
printf '%s\n' '应用已打开。以后双击桌面或 Applications 中的 Sugar Local MCP Desktop 即可。'
