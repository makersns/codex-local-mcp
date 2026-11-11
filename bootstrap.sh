#!/bin/bash
# Sugar Local MCP Desktop 0.5.0: private, native macOS runtime bootstrap.
# Contract: bootstrap.sh ABSOLUTE_RESOURCES_DIRECTORY ABSOLUTE_SUPPORT_DIRECTORY
# Success: the only stdout line is the absolute venv Python path.
# Diagnostics: stderr and SUPPORT/logs/bootstrap.log. No runtime keys are read.
set -eu
set -o pipefail
umask 077

die() { printf '安装失败：%s\n' "$*" >&2; exit 1; }
[ "$#" -eq 2 ] || die '需要 Resources 和 Application Support 两个绝对路径。'
case "$1:$2" in *$'\n'*|*$'\r'*) die '安装路径不能包含换行。';; esac
case "$1" in /*) ;; *) die 'Resources 必须使用绝对路径。';; esac
case "$2" in /*) ;; *) die 'Application Support 必须使用绝对路径。';; esac
[ "$2" != / ] || die 'Application Support 不能是根目录。'
[ "$(/usr/bin/uname -s)" = Darwin ] || die '此安装器只支持 macOS。'
[ -d "$1/server/src/sugar_local_mcp" ] || die '安装包缺少 server/src/sugar_local_mcp，请重新下载完整安装包。'
resources_root=$(cd "$1" && pwd -P)
server_root="${resources_root}/server"
constraints="${server_root}/constraints.txt"
[ -f "${constraints}" ] || constraints="${resources_root}/constraints.txt"
[ -f "${constraints}" ] || die '安装包缺少 constraints.txt。'
[ -f "${server_root}/pyproject.toml" ] || die '安装包缺少 server/pyproject.toml。'

/bin/mkdir -p "$2"
support_root=$(cd "$2" && pwd -P)
[ "${support_root}" != / ] || die 'Application Support 不能是根目录。'
[ "$(/usr/bin/stat -f '%u' "${support_root}")" = "$(/usr/bin/id -u)" ] || die 'Application Support 目录不属于当前用户，请使用自己的用户目录。'
/bin/chmod 700 "${support_root}"
/bin/mkdir -p "${support_root}/logs" "${support_root}/tools" "${support_root}/runtimes" "${support_root}/tmp"
log_file="${support_root}/logs/bootstrap.log"
exec 3>&1 4>&2
exec >>"${log_file}" 2>&1
log() { printf '%s %s\n' "$(/bin/date '+%Y-%m-%d %H:%M:%S')" "$*"; printf '%s\n' "$*" >&4; }
die() { log "安装失败：$*"; log "安装日志：${log_file}"; exit 1; }

lock_dir="${support_root}/.bootstrap.lock"
lock_owned=0
stage_dir=''
bootstrap_succeeded=0
cleanup() {
  local exit_status=${1:-1}
  trap - EXIT
  # Bash 3.2 can report status 0 in EXIT after a nounset expansion failure.
  # Success is earned only after runtime-info.json and stdout are complete.
  if [ "${bootstrap_succeeded}" != 1 ] && [ "${exit_status}" -eq 0 ]; then exit_status=1; fi
  if [ -n "${stage_dir}" ] && [ -d "${stage_dir}" ]; then /bin/rm -rf "${stage_dir}"; fi
  if [ "${lock_owned}" = 1 ]; then /bin/rm -f "${lock_dir}/pid"; /bin/rmdir "${lock_dir}" 2>/dev/null || true; fi
  if [ "${exit_status}" -ne 0 ]; then printf '安装未完成；可重新点击连接重试。日志：%s\n' "${log_file}" >&4; fi
  exit "${exit_status}"
}
trap 'cleanup "$?"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
if ! /bin/mkdir "${lock_dir}" 2>/dev/null; then
  old_pid=''
  [ ! -f "${lock_dir}/pid" ] || IFS= read -r old_pid <"${lock_dir}/pid" || true
  case "${old_pid}" in
    ''|*[!0-9]*) die '另一安装进程可能正在初始化，请稍后重试。';;
    *) if kill -0 "${old_pid}" 2>/dev/null; then die '另一安装进程正在运行，请等待它完成。'; fi;;
  esac
  # Only this installer's stale pid file and empty lock directory are removed.
  /bin/rm -f "${lock_dir}/pid"
  /bin/rmdir "${lock_dir}" || die '安装锁目录异常，请检查安装日志。'
  /bin/mkdir "${lock_dir}" || die '另一安装进程已经启动，请稍后重试。'
fi
lock_owned=1
printf '%s\n' "$$" >"${lock_dir}/pid"
stage_dir=$(/usr/bin/mktemp -d "${support_root}/tmp/bootstrap.XXXXXXXX")

# hw.optional.arm64 reflects the hardware even when this script was launched
# by an Intel process under Rosetta. Never inherit the launcher's architecture.
if [ "$(/usr/sbin/sysctl -in hw.optional.arm64 2>/dev/null || true)" = 1 ]; then
  native_arch=arm64
  uv_arch=aarch64
  tunnel_arch=arm64
  uv_sha=81b811b57e4624df7ed3aac148e0359100d9962f3d9dce00aec533dc2e086731
  tunnel_sha=f4689e75a835f69f1bbb9c59de9c4debb8e9c9cbbdd2721aa93378b4b048fd33
  native_brew=/opt/homebrew/bin/brew
else
  case "$(/usr/bin/uname -m)" in x86_64|i386) ;; *) die '无法识别此 Mac 的硬件架构。';; esac
  native_arch=x86_64
  uv_arch=x86_64
  tunnel_arch=amd64
  uv_sha=20c47e4c1f630b8abaff8c291fa9b9aa300fef92c4fbff95adf198b55287cda7
  tunnel_sha=a8933a472c18409d137fbdece70673dbd23446754fea88eb9e5653d3244b93eb
  native_brew=/usr/local/bin/brew
fi
uv_version=0.13.7
tunnel_version=0.0.20
uv_dir="${support_root}/tools/uv-${uv_version}-${native_arch}"
uv_bin="${uv_dir}/uv-${uv_arch}-apple-darwin/uv"
tunnel_dir="${support_root}/tools/tunnel-client-${tunnel_version}-${native_arch}"
log "正在准备 ${native_arch} 本地运行环境。"

# Source identity is independent of the application's location. Existing
# environments are never overwritten, so an old running session can continue.
source_digest=$(
  {
    (cd "${server_root}" && /usr/bin/find src -type f -name '*.py' -print0 | /usr/bin/xargs -0 /usr/bin/shasum -a 256 | LC_ALL=C /usr/bin/sort)
    /usr/bin/shasum -a 256 "${server_root}/pyproject.toml" | /usr/bin/awk '{print $1}'
    /usr/bin/shasum -a 256 "${constraints}" | /usr/bin/awk '{print $1}'
    printf 'bootstrap=1;uv=%s;python=3.12;arch=%s\n' "${uv_version}" "${native_arch}"
  } | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}'
)
runtime_dir="${support_root}/runtimes/${source_digest:0:24}-${native_arch}"
python_bin="${runtime_dir}/venv/bin/python"
run_python() { /usr/bin/arch "-${native_arch}" "${python_bin}" -I -B "$@"; }
verify_python() {
  [ -f "${runtime_dir}/complete.json" ] && [ -x "${python_bin}" ] || return 1
  run_python - "${native_arch}" "${source_digest}" "${runtime_dir}/complete.json" "${constraints}" <<'PY'
import importlib.metadata, json, platform, sys
from pathlib import Path
import pydantic_core
from mcp.server.mcpserver import MCPServer
from sugar_local_mcp.server import build_server
assert platform.machine() == sys.argv[1], 'Python architecture does not match hardware'
assert sys.version_info[:2] == (3, 12), 'Python 3.12 is required'
record = json.loads(Path(sys.argv[3]).read_text())
assert record['source_digest'] == sys.argv[2]
assert importlib.metadata.version('sugar-local-mcp') == record['service_version']
for raw in Path(sys.argv[4]).read_text().splitlines():
    line = raw.partition('#')[0].strip()
    if not line:
        continue
    package, expected = line.split('==', 1)
    actual = importlib.metadata.version(package)
    assert actual == expected, f'{package}: installed {actual}, expected {expected}'
PY
}

download_verified() {
  download_url=$1
  download_sha=$2
  download_target=$3
  /usr/bin/curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --connect-timeout 20 --max-time 600 --retry 2 --retry-delay 2 \
    --output "${download_target}" "${download_url}" || die '官方下载失败，请检查网络后重试。'
  actual_sha=$(/usr/bin/shasum -a 256 "${download_target}" | /usr/bin/awk '{print $1}')
  [ "${actual_sha}" = "${download_sha}" ] || die '下载文件的 SHA-256 与安装包记录不一致，已停止安装。'
}

if verify_python; then
  log '已有 Python 和 MCP 依赖验证通过，直接复用。'
else
  if [ ! -x "${uv_bin}" ]; then
    log "首次安装：下载已固定版本的 uv ${uv_version}。"
    archive="${stage_dir}/uv.tar.gz"
    download_verified "https://releases.astral.sh/github/uv/releases/download/${uv_version}/uv-${uv_arch}-apple-darwin.tar.gz" "${uv_sha}" "${archive}"
    # The archive is authenticated by its pinned digest before extraction.
    /bin/mkdir "${stage_dir}/uv"
    /usr/bin/tar -xzf "${archive}" -C "${stage_dir}/uv"
    [ -x "${stage_dir}/uv/uv-${uv_arch}-apple-darwin/uv" ] || die 'uv 压缩包结构与预期不符。'
    [ ! -e "${uv_dir}" ] || die '已有 uv 安装不完整，请从安装日志中确认需要修复的目录。'
    /bin/mv "${stage_dir}/uv" "${uv_dir}"
  fi
  uv_actual=$(/usr/bin/arch "-${native_arch}" "${uv_bin}" --version) || die 'macOS 无法运行下载的 uv。请检查系统提示；本安装器不会更改系统安全设置。'
  case "${uv_actual}" in "uv ${uv_version}"|"uv ${uv_version} "*) ;; *) die '私有 uv 的版本与安装清单不符，请检查安装日志。';; esac
  # Isolate uv from unrelated project/configuration settings and third-party
  # indexes. Preserve ordinary proxy/certificate environment settings.
  unset PYTHONHOME PYTHONPATH VIRTUAL_ENV
  for uv_env_name in ${!UV_@}; do unset "${uv_env_name}"; done
  export UV_NO_CONFIG=1 UV_MANAGED_PYTHON=1 UV_PYTHON_DOWNLOADS=automatic
  export UV_PYTHON_ARCH="${uv_arch}" UV_PYTHON_INSTALL_DIR="${support_root}/pythons-${native_arch}"
  export UV_PYTHON_BIN_DIR="${support_root}/python-bin-${native_arch}" UV_PYTHON_INSTALL_BIN=false
  export UV_CACHE_DIR="${support_root}/cache/uv" UV_NO_PROGRESS=1
  run_uv() { /usr/bin/arch "-${native_arch}" "${uv_bin}" --no-config "$@"; }
  if [ -e "${runtime_dir}" ]; then
    # Retain a failed/corrupt runtime for diagnostics instead of deleting it.
    /bin/mv "${runtime_dir}" "${runtime_dir}.incomplete.$(/bin/date '+%s').$$"
  fi
  /bin/mkdir -p "${runtime_dir}"
  log '首次安装可能需要几分钟：下载原生 Python 3.12 和 MCP 依赖。'
  run_uv venv --managed-python --python "cpython-3.12-macos-${uv_arch}-none" "${runtime_dir}/venv" || die '无法安装原生 Python 3.12，请检查安装日志后重试。'
  run_uv pip install --python "${python_bin}" --default-index https://pypi.org/simple \
    --only-binary :all: --requirement "${constraints}" || die '无法安装已固定版本的依赖。此 Mac 可能缺少对应系统版本的预编译依赖；详见安装日志。'
  # Setuptools may write egg-info/build beside the source. Build a private copy
  # so installation never changes the signed .app bundle's Resources directory.
  /bin/cp -R "${server_root}" "${stage_dir}/server"
  run_uv pip install --python "${python_bin}" --default-index https://pypi.org/simple \
    --no-deps "${stage_dir}/server" || die 'Sugar Local MCP 安装失败，请查看安装日志。'
  run_python - "${runtime_dir}/complete.json" "${source_digest}" <<'PY'
import importlib.metadata, json, platform, sys
from pathlib import Path
data = {'source_digest': sys.argv[2], 'python_version': platform.python_version(),
        'arch': platform.machine(), 'service_version': importlib.metadata.version('sugar-local-mcp')}
Path(sys.argv[1]).write_text(json.dumps(data, indent=2) + '\n')
PY
  verify_python || die 'Python 或 MCP 的架构/导入验证失败，请查看安装日志。'
fi

# Probe with a timeout and read only --version. Do not start a tunnel here.
probe_tunnel() {
  [ -n "$1" ] && [ -x "$1" ] || return 1
  run_python - "$1" <<'PY'
import re, subprocess, sys
try:
    p = subprocess.run([sys.argv[1], '--version'], text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=15)
    match = re.search(r'\b(\d+)\.(\d+)\.(\d+)(?:\+[^\s]+)?', p.stdout)
    if p.returncode or not match or tuple(map(int, match.groups())) < (0, 0, 20):
        raise ValueError('a working tunnel-client >= 0.0.20 is required')
    print(match.group(0))
except (OSError, subprocess.SubprocessError, ValueError) as exc:
    print(f'tunnel-client probe failed: {type(exc).__name__}', file=sys.stderr)
    sys.exit(1)
PY
}
tunnel_bin=''
tunnel_actual_version=''
tunnel_source=''
path_candidate=$(command -v tunnel-client || true)
for candidate in "${path_candidate}" /opt/homebrew/bin/tunnel-client /usr/local/bin/tunnel-client; do
  case "${candidate}" in /*) ;; *) continue;; esac
  if tunnel_actual_version=$(probe_tunnel "${candidate}"); then
    tunnel_bin="${candidate}"
    tunnel_source=existing
    break
  fi
done

# A previously verified portable bundle is also reusable without networking.
if [ -z "${tunnel_bin}" ] && [ -f "${tunnel_dir}/client-path.txt" ]; then
  IFS= read -r relative_client <"${tunnel_dir}/client-path.txt"
  case "${relative_client}" in /*|*../*|../*|'') die '本地隧道安装记录无效。';; esac
  candidate="${tunnel_dir}/${relative_client}"
  if tunnel_actual_version=$(probe_tunnel "${candidate}"); then
    tunnel_bin="${candidate}"
    tunnel_source=portable
  elif [ ! -x "${native_brew}" ]; then
    die 'macOS 拒绝运行现有隧道客户端，或该安装已损坏。请使用官方 Homebrew 安装 tunnel-client 后重试；本安装器不会绕过 Gatekeeper。'
  fi
fi

if [ -z "${tunnel_bin}" ] && [ -x "${native_brew}" ]; then
  log '正在通过本机原生 Homebrew 安装官方 tunnel-client。'
  HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 /usr/bin/arch "-${native_arch}" \
    "${native_brew}" install openai/tools/tunnel-client || die '官方 Homebrew 安装未完成。请查看日志并在终端完成 brew install openai/tools/tunnel-client，然后重新连接。'
  brew_prefix=$(HOMEBREW_NO_AUTO_UPDATE=1 "${native_brew}" --prefix openai/tools/tunnel-client)
  candidate="${brew_prefix}/bin/tunnel-client"
  tunnel_actual_version=$(probe_tunnel "${candidate}") || die 'Homebrew 中的 tunnel-client 无法运行或低于 0.0.20；请更新该官方 formula 后重试。'
  tunnel_bin="${candidate}"
  tunnel_source=homebrew
fi

if [ -z "${tunnel_bin}" ]; then
  log "未找到 Homebrew；尝试安装 SHA-256 已固定的官方 tunnel-client ${tunnel_version} 便携包。"
  archive="${stage_dir}/tunnel-client.zip"
  download_verified "https://github.com/openai/tunnel-client/releases/download/v${tunnel_version}/tunnel-client-v${tunnel_version}-darwin-${tunnel_arch}.zip" "${tunnel_sha}" "${archive}"
  /bin/mkdir "${stage_dir}/tunnel"
  run_python - "${archive}" "${stage_dir}/tunnel" <<'PY'
import os, stat, sys, zipfile
from pathlib import Path, PurePosixPath
destination = Path(sys.argv[2])
with zipfile.ZipFile(sys.argv[1]) as archive:
    members = archive.infolist()
    if sum(x.file_size for x in members) > 1024 * 1024 * 1024:
        raise ValueError('release archive is unexpectedly large')
    for member in members:
        name = PurePosixPath(member.filename)
        mode = member.external_attr >> 16
        if name.is_absolute() or '..' in name.parts or stat.S_ISLNK(mode):
            raise ValueError('unsafe archive member')
        archive.extract(member, destination)
        path = destination.joinpath(*name.parts)
        if path.is_file():
            path.chmod(0o700 if mode & 0o111 else 0o600)
        elif path.is_dir():
            path.chmod(0o700)
clients = [p for p in destination.rglob('tunnel-client') if p.is_file()]
if len(clients) != 1:
    raise ValueError('expected exactly one tunnel-client in official release')
clients[0].chmod(0o700)
companions = [p for p in destination.rglob('cloudflared') if p.is_file()]
if not companions:
    raise ValueError('official tunnel release has no cloudflared companion')
for companion in companions:
    companion.chmod(0o700)
(destination / 'client-path.txt').write_text(str(clients[0].relative_to(destination)) + '\n')
PY
  [ ! -e "${tunnel_dir}" ] || die '隧道便携包目录已存在但不完整。请使用官方 Homebrew 安装后重试。'
  /bin/mv "${stage_dir}/tunnel" "${tunnel_dir}"
  IFS= read -r relative_client <"${tunnel_dir}/client-path.txt"
  candidate="${tunnel_dir}/${relative_client}"
  tunnel_actual_version=$(probe_tunnel "${candidate}") || die 'macOS 无法运行官方隧道便携包。请使用官方 Homebrew 安装 tunnel-client 后重试；本安装器不会删除 quarantine 或更改 Gatekeeper。'
  tunnel_bin="${candidate}"
  tunnel_source=portable
fi

run_python - "${support_root}/runtime-info.json" "${python_bin}" "${tunnel_bin}" "${native_arch}" \
  "${source_digest}" "${tunnel_actual_version}" "${tunnel_source}" "${uv_version}" <<'PY'
import datetime, importlib.metadata, json, os, platform, sys, tempfile
from pathlib import Path
target = Path(sys.argv[1])
data = {'schema_version': 1, 'python': sys.argv[2], 'tunnel_client': sys.argv[3],
        'arch': sys.argv[4], 'machine': sys.argv[4], 'source_digest': sys.argv[5],
        'tunnel_version': sys.argv[6], 'tunnel_source': sys.argv[7], 'uv_version': sys.argv[8],
        'python_version': platform.python_version(),
        'service_version': importlib.metadata.version('sugar-local-mcp'),
        'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
fd, temporary = tempfile.mkstemp(prefix='.runtime-info.', suffix='.json', dir=target.parent)
try:
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
log '运行环境已就绪。'
printf '%s\n' "${python_bin}" >&3
bootstrap_succeeded=1
