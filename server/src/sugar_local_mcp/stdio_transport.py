"""Official MCP stdio transport with cancellable POSIX pipe I/O.

MCP SDK 2.3.0 wraps stdin with AnyIO's AsyncFile. Its blocking readline runs
in a cancellation-shielded worker thread, so an open, idle client pipe can
prevent SIGTERM shutdown indefinitely. The same problem affects a full stdout
pipe. These small stream adapters change only how bytes enter/leave the SDK;
the SDK still owns JSON-RPC messages and session behavior.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import ExitStack
import os

import anyio
from mcp.server.stdio import stdio_server


MAX_FRAME_BYTES = 2 * 1024 * 1024


class _PosixInput:
    """Read UTF-8 newline-delimited frames without an uninterruptible thread."""

    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor
        self.pending = bytearray()
        self.eof = False

    async def readline(self) -> str:
        while True:
            newline = self.pending.find(b"\n")
            if newline >= 0:
                # The wire-frame limit includes its trailing newline.
                if newline + 1 > MAX_FRAME_BYTES:
                    raise ValueError("MCP stdio frame exceeds 2 MiB")
                data = bytes(self.pending[:newline + 1])
                del self.pending[:newline + 1]
                return data.decode("utf-8", errors="replace")
            if len(self.pending) > MAX_FRAME_BYTES:
                raise ValueError("MCP stdio frame exceeds 2 MiB")
            if self.eof:
                data = bytes(self.pending)
                self.pending.clear()
                return data.decode("utf-8", errors="replace")

            try:
                chunk = os.read(self.descriptor, 65536)
            except BlockingIOError:
                await anyio.wait_readable(self.descriptor)
                continue
            if chunk:
                self.pending.extend(chunk)
            else:
                self.eof = True

    async def __aiter__(self) -> AsyncIterator[str]:
        while line := await self.readline():
            yield line


class _PosixOutput:
    """Unbuffered, cancellable UTF-8 writes, including a client that stops reading."""

    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor

    async def write(self, text: str) -> int:
        data = text.encode("utf-8")
        if len(data) > MAX_FRAME_BYTES:
            raise ValueError("MCP stdio response frame exceeds 2 MiB")
        pending = memoryview(data)
        while pending:
            await anyio.lowlevel.checkpoint()
            try:
                written = os.write(self.descriptor, pending)
            except BlockingIOError:
                await anyio.wait_writable(self.descriptor)
                continue
            if not written:
                raise BrokenPipeError("MCP stdout is closed")
            pending = pending[written:]
        return len(text)

    async def flush(self) -> None:
        # os.write has no userspace buffering to flush.
        await anyio.lowlevel.checkpoint()


async def run_stdio(server) -> None:
    """Run the official SDK, preserving cancellability on POSIX pipes.

    Non-POSIX uses the SDK unchanged; no claim of SIGTERM/pipe-cancellation
    behavior is made on those platforms.
    """
    if os.name != "posix":
        await server.run_stdio_async()
        return

    import fcntl

    # Save both wire descriptors before diverting either. ExitStack runs every
    # restoration callback even if another fails, including partial setup.
    # Divert standard input/output as the SDK normally does, so input() and
    # stray print() calls cannot consume/corrupt the MCP wire.
    with ExitStack() as cleanup:
        input_descriptor = fcntl.fcntl(0, fcntl.F_DUPFD_CLOEXEC, 3)
        cleanup.callback(os.close, input_descriptor)
        output_descriptor = fcntl.fcntl(1, fcntl.F_DUPFD_CLOEXEC, 3)
        cleanup.callback(os.close, output_descriptor)
        input_was_blocking = os.get_blocking(input_descriptor)
        output_was_blocking = os.get_blocking(output_descriptor)
        cleanup.callback(os.dup2, output_descriptor, 1)
        cleanup.callback(os.dup2, input_descriptor, 0)
        cleanup.callback(os.set_blocking, output_descriptor, output_was_blocking)
        cleanup.callback(os.set_blocking, input_descriptor, input_was_blocking)
        null_descriptor = os.open(os.devnull, os.O_RDONLY)
        try:
            os.dup2(null_descriptor, 0)
        finally:
            os.close(null_descriptor)
        try:
            diverted_output = os.dup(2)
        except OSError:
            diverted_output = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(diverted_output, 1)
        finally:
            os.close(diverted_output)
        os.set_blocking(input_descriptor, False)
        os.set_blocking(output_descriptor, False)
        stdin = _PosixInput(input_descriptor)
        stdout = _PosixOutput(output_descriptor)
        async with stdio_server(stdin=stdin, stdout=stdout) as (read_stream, write_stream):
            # MCPServer.run_stdio_async() does not accept explicit streams in
            # the pinned SDK 2.3.0. Keep this one private-SDK access localized;
            # re-run real stdio/EOF/SIGTERM tests before upgrading that pin.
            lowlevel = server._lowlevel_server
            await lowlevel.run(
                read_stream, write_stream, lowlevel.create_initialization_options()
            )
