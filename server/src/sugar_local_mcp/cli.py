"""User-facing setup, diagnostics and local service launcher."""

from __future__ import annotations

import argparse
import getpass
import importlib
import importlib.metadata
import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import load_config


def _mcp_command(config_path: Path) -> str:
    argv = [sys.executable, "-m", "sugar_local_mcp", "serve", "--config", str(config_path)]
    # A universal macOS Python may otherwise choose the architecture of the
    # tunnel process rather than the interpreter whose dependencies we checked.
    architecture = platform.machine()
    if os.name != "nt" and platform.system() == "Darwin" and architecture in {"arm64", "x86_64"}:
        argv = ["/usr/bin/arch", f"-{architecture}", *argv]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _runtime_import_hint(error: ImportError) -> str:
    detail = str(error).lower()
    if any(marker in detail for marker in ("incompatible architecture", "wrong architecture", "bad cpu type")):
        return (
            "A native dependency does not match this Python process's architecture. "
            "Use the same architecture when installing dependencies and starting the MCP Python. "
            "On macOS, verify the appropriate /usr/bin/arch -arm64 or -x86_64 Python command first; "
            "use mcp_command only after doctor reports runtime_import_ok: true."
        )
    return (
        "The MCP runtime or one of its dependencies could not be imported. "
        "Install this project's pinned dependencies in the Python environment shown above, "
        "then rerun doctor with that same interpreter. The original import error is included for diagnosis."
    )


async def _run_stdio(server) -> None:
    """Let SIGTERM/interrupt cancel the SDK scope and close managed jobs."""
    import anyio
    from .stdio_transport import run_stdio
    if os.name != "posix":
        await run_stdio(server)
        return
    async with anyio.create_task_group() as tasks:
        async def watch_signals() -> None:
            with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
                async for _ in signals:
                    tasks.cancel_scope.cancel()
                    return

        async def run_protocol() -> None:
            try:
                await run_stdio(server)
            finally:
                tasks.cancel_scope.cancel()

        tasks.start_soon(watch_signals)
        tasks.start_soon(run_protocol)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Single-user local project MCP bridge")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a project config; read-only by default")
    init.add_argument("--project", required=True, type=Path)
    init.add_argument("--name", default="project")
    init.add_argument("--config", default=Path.home() / ".config" / "sugar-local-mcp" / "workspaces.json", type=Path)
    init.add_argument("--state-dir", type=Path)
    init.add_argument("--allow-write", action="store_true")
    init.add_argument("--allow-exec", action="store_true",
                      help="Grant commands the service OS user's permissions; NOT a sandbox")
    doctor = commands.add_parser("doctor", help="Validate config and print local diagnostics")
    doctor.add_argument("--config", type=Path, required=True)
    serve = commands.add_parser("serve", help="Serve stdio (default) or private loopback HTTP")
    serve.add_argument("--config", type=Path, required=True)
    serve.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    serve.add_argument("--port", type=int, default=8765)
    tunnel = commands.add_parser("tunnel", help="Run an already-configured official tunnel profile")
    tunnel.add_argument("--profile", default="sugar-local")
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            project = args.project.expanduser().resolve(strict=True)
            if not project.is_dir():
                raise ValueError("Project must be an existing directory")
            target = args.config.expanduser().absolute()
            target.parent.mkdir(parents=True, exist_ok=True)
            data = {"workspaces": [{"id": args.name, "root": str(project),
                                   "allow_write": args.allow_write, "allow_exec": args.allow_exec}]}
            if args.state_dir is not None:
                data["state_dir"] = str(args.state_dir.expanduser().absolute())
            # Exclusive create: never silently replace a user's existing config.
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            try:
                load_config(target)
            except Exception:
                target.unlink()
                raise
            print(json.dumps({"config": str(target), "mcp_command": _mcp_command(target),
                              "allow_write": args.allow_write, "allow_exec": args.allow_exec},
                             ensure_ascii=False, indent=2))
            return 0

        if args.command == "tunnel":
            executable = shutil.which("tunnel-client")
            if not executable:
                raise ValueError("Install the official openai/tunnel-client, then retry")
            environment = dict(os.environ)
            if not environment.get("CONTROL_PLANE_API_KEY"):
                if not sys.stdin.isatty():
                    raise ValueError("Set CONTROL_PLANE_API_KEY locally or run this command in a terminal")
                secret = getpass.getpass("Tunnel runtime key (hidden; kept in memory): ")
                if not secret.strip():
                    raise ValueError("A tunnel runtime key is required")
                environment["CONTROL_PLANE_API_KEY"] = secret.strip()
            # The key is not placed in arguments, a file, or the console.
            return subprocess.call([executable, "run", "--profile", args.profile], env=environment)

        config_path = args.config.expanduser().resolve(strict=True)
        config = load_config(config_path)
        if args.command == "doctor":
            runtime_import_ok = True
            runtime_import_error = None
            runtime_import_hint = None
            try:
                # Import the actual server adapter, including MCP/Pydantic native
                # dependencies. Do not build a server or start any managed jobs.
                importlib.import_module(".server", __package__)
            except ImportError as error:
                runtime_import_ok = False
                runtime_import_error = f"{type(error).__name__}: {error}"
                runtime_import_hint = _runtime_import_hint(error)
            try:
                sdk_version = importlib.metadata.version("mcp")
            except importlib.metadata.PackageNotFoundError:
                sdk_version = None
            print(json.dumps({
                "version": __version__, "python": platform.python_version(),
                "platform": platform.system(), "sdk_version": sdk_version,
                "python_executable": sys.executable,
                "process_architecture": platform.machine(),
                "runtime_import_ok": runtime_import_ok,
                "runtime_import_error": runtime_import_error,
                "runtime_import_hint": runtime_import_hint,
                "config": str(config_path), "state_dir": str(config.state_dir),
                "mcp_command": _mcp_command(config_path),
                "workspaces": [{"id": w.id, "root": str(w.root), "allow_write": w.allow_write,
                                "allow_exec": w.allow_exec} for w in config.workspaces.values()],
                "tunnel_client_installed": shutil.which("tunnel-client") is not None,
                "account_access_verified": False,
                "execution_note": "Host execution is not an OS sandbox. Use an isolated container or VM if needed.",
            }, ensure_ascii=False, indent=2))
            return 0 if runtime_import_ok else 2

        if not 1 <= args.port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        # Do not incidentally forward the tunnel control-plane key to jobs.
        # This is hygiene only, not isolation from same-user arbitrary code.
        os.environ.pop("CONTROL_PLANE_API_KEY", None)
        try:
            from .server import build_server, http_security
        except ImportError as error:
            print(f"Error: MCP runtime import failed using {sys.executable} "
                  f"(process architecture: {platform.machine()}).", file=sys.stderr)
            print(f"{type(error).__name__}: {error}", file=sys.stderr)
            print(_runtime_import_hint(error), file=sys.stderr)
            return 2
        server = build_server(config)
        if config.any_exec:
            print("Execution enabled: commands have this service's OS-user permissions.", file=sys.stderr)
        if args.transport == "stdio":
            import anyio
            anyio.run(_run_stdio, server)
        else:
            print(f"Private MCP listener: http://127.0.0.1:{args.port}/mcp", file=sys.stderr)
            server.run(transport="streamable-http", host="127.0.0.1", port=args.port,
                       json_response=True, stateless_http=True,
                       max_request_body_size=2_097_152,
                       transport_security=http_security(args.port))
        return 0
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
