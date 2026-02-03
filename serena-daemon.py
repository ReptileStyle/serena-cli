#!/usr/bin/env python3
"""
Serena MCP daemon + client.

Usage:
  serena-daemon.py start <project_path>       - Start daemon (foreground)
  serena-daemon.py call <project_path> <tool> '<json_args>'  - Call tool (auto-starts daemon)
  serena-daemon.py stop <project_path>        - Stop daemon
  serena-daemon.py status <project_path>      - Check daemon status
"""

import asyncio
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

SERENA_CMD = [
    "uvx", "--from", "git+https://github.com/oraios/serena",
    "serena", "start-mcp-server", "--context", "ide-assistant",
]

SOCKET_DIR = "/tmp"
CALL_TIMEOUT = 120  # seconds for a single tool call
STARTUP_TIMEOUT = 90  # seconds to wait for daemon startup
RECV_BUF = 65536


def _hash(project_path: str) -> str:
    return hashlib.md5(project_path.encode()).hexdigest()[:12]


def socket_path(project_path: str) -> str:
    return os.path.join(SOCKET_DIR, f"serena-{_hash(project_path)}.sock")


def pid_path(project_path: str) -> str:
    return os.path.join(SOCKET_DIR, f"serena-{_hash(project_path)}.pid")


def log_path(project_path: str) -> str:
    return os.path.join(SOCKET_DIR, f"serena-{_hash(project_path)}.log")


def is_daemon_running(project_path: str) -> bool:
    pp = pid_path(project_path)
    sp = socket_path(project_path)
    if not os.path.exists(pp) or not os.path.exists(sp):
        return False
    try:
        with open(pp) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

class SerenaDaemon:
    def __init__(self, project_path: str):
        self.project_path = os.path.abspath(project_path)
        self.sock_path = socket_path(self.project_path)
        self.pid_file = pid_path(self.project_path)
        self.proc = None
        self.request_id = 0
        self.lock = asyncio.Lock()
        self.pending: dict[int, asyncio.Future] = {}
        self._reader_task = None

    # -- MCP communication --------------------------------------------------

    async def _send(self, msg: dict):
        data = json.dumps(msg, ensure_ascii=False) + "\n"
        self.proc.stdin.write(data.encode("utf-8"))
        await self.proc.stdin.drain()

    async def _read_loop(self):
        """Read JSON-RPC responses from Serena stdout, dispatch to pending futures."""
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = msg.get("id")
            if rid is not None and rid in self.pending:
                self.pending[rid].set_result(msg)
            # Notifications (no id) are silently ignored

    async def _request(self, method: str, params: dict | None = None) -> dict:
        self.request_id += 1
        rid = self.request_id
        msg: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params

        future = asyncio.get_event_loop().create_future()
        self.pending[rid] = future
        await self._send(msg)
        try:
            result = await asyncio.wait_for(future, timeout=CALL_TIMEOUT)
        finally:
            self.pending.pop(rid, None)
        return result

    async def _notify(self, method: str, params: dict | None = None):
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self._send(msg)

    # -- Lifecycle ----------------------------------------------------------

    async def start_serena(self):
        cmd = SERENA_CMD + ["--project", self.project_path]
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

        # MCP handshake
        resp = await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "serena-cli", "version": "1.0.0"},
        })
        await self._notify("notifications/initialized")
        server_info = resp.get("result", {}).get("serverInfo", {})
        print(f"[daemon] Serena initialized: {server_info}", file=sys.stderr)

    async def call_tool(self, tool_name: str, args: dict) -> dict:
        """Call a Serena tool. Returns {"text": "...", "isError": bool}."""
        async with self.lock:
            resp = await self._request("tools/call", {
                "name": tool_name,
                "arguments": args,
            })

        # Extract content
        if "error" in resp:
            return {"text": resp["error"].get("message", str(resp["error"])), "isError": True}

        result = resp.get("result", {})
        contents = result.get("content", [])
        text_parts = [c.get("text", "") for c in contents if c.get("type") == "text"]
        return {
            "text": "\n".join(text_parts),
            "isError": result.get("isError", False),
        }

    # -- Socket server ------------------------------------------------------

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            raw = await asyncio.wait_for(reader.read(-1), timeout=10)
            request = json.loads(raw.decode("utf-8"))
            tool = request["tool"]
            args = request.get("args", {})
            result = await self.call_tool(tool, args)
            writer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
            await writer.drain()
        except Exception as e:
            err = json.dumps({"text": f"Daemon error: {e}", "isError": True})
            writer.write(err.encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def run(self):
        # Cleanup stale socket
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)

        # PID file
        with open(self.pid_file, "w") as f:
            f.write(str(os.getpid()))

        # Start Serena MCP server
        await self.start_serena()

        # Unix socket server
        server = await asyncio.start_unix_server(self.handle_client, path=self.sock_path)
        os.chmod(self.sock_path, 0o600)
        print(f"[daemon] Listening on {self.sock_path}", file=sys.stderr)

        try:
            async with server:
                await server.serve_forever()
        finally:
            self.cleanup()

    def cleanup(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
        for p in (self.sock_path, self.pid_file):
            if os.path.exists(p):
                os.unlink(p)


def run_daemon(project_path: str):
    daemon = SerenaDaemon(project_path)

    def handle_signal(sig, frame):
        daemon.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    asyncio.run(daemon.run())


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def ensure_daemon(project_path: str):
    """Start daemon if not running, wait for socket."""
    if is_daemon_running(project_path):
        return

    log = log_path(project_path)
    script = os.path.abspath(__file__)
    # Fork daemon in background
    with open(log, "w") as logf:
        subprocess.Popen(
            [sys.executable, script, "start", project_path],
            stdout=logf,
            stderr=logf,
            start_new_session=True,
        )

    # Wait for socket to appear
    sp = socket_path(project_path)
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if os.path.exists(sp):
            # Give it a moment to start accepting connections
            time.sleep(0.5)
            return
        time.sleep(1)

    print(f"ERROR: Daemon failed to start within {STARTUP_TIMEOUT}s", file=sys.stderr)
    print(f"Check log: {log}", file=sys.stderr)
    sys.exit(1)


def call_tool(project_path: str, tool_name: str, args_json: str) -> int:
    """Connect to daemon, call tool, print result. Returns exit code."""
    ensure_daemon(project_path)

    try:
        args = json.loads(args_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON args: {e}", file=sys.stderr)
        return 1

    sp = socket_path(project_path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(sp)
        sock.settimeout(CALL_TIMEOUT + 10)
        payload = json.dumps({"tool": tool_name, "args": args}, ensure_ascii=False)
        sock.sendall(payload.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)

        data = b""
        while True:
            chunk = sock.recv(RECV_BUF)
            if not chunk:
                break
            data += chunk
    except ConnectionRefusedError:
        print("ERROR: Daemon not accepting connections. Try: serena-cli --stop, then retry.", file=sys.stderr)
        return 1
    finally:
        sock.close()

    try:
        result = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        print(data.decode("utf-8", errors="replace"))
        return 1

    text = result.get("text", "")
    is_error = result.get("isError", False)

    print(text)
    return 1 if is_error else 0


def stop_daemon(project_path: str):
    pp = pid_path(project_path)
    if not os.path.exists(pp):
        print("Daemon is not running.")
        return
    try:
        with open(pp) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to daemon (PID {pid}).")
    except (OSError, ValueError) as e:
        print(f"Could not stop daemon: {e}")
    # Cleanup stale files
    for p in (socket_path(project_path), pp):
        if os.path.exists(p):
            os.unlink(p)


def status_daemon(project_path: str):
    if is_daemon_running(project_path):
        with open(pid_path(project_path)) as f:
            pid = f.read().strip()
        print(f"Daemon is running (PID {pid}), socket: {socket_path(project_path)}")
    else:
        print("Daemon is not running.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "start":
        if len(sys.argv) < 3:
            print("Usage: serena-daemon.py start <project_path>", file=sys.stderr)
            sys.exit(1)
        run_daemon(sys.argv[2])

    elif cmd == "call":
        if len(sys.argv) < 4:
            print("Usage: serena-daemon.py call <project_path> <tool_name> [json_args]", file=sys.stderr)
            sys.exit(1)
        project = sys.argv[2]
        tool = sys.argv[3]
        args = sys.argv[4] if len(sys.argv) > 4 else "{}"
        exit_code = call_tool(project, tool, args)
        sys.exit(exit_code)

    elif cmd == "stop":
        if len(sys.argv) < 3:
            print("Usage: serena-daemon.py stop <project_path>", file=sys.stderr)
            sys.exit(1)
        stop_daemon(sys.argv[2])

    elif cmd == "status":
        if len(sys.argv) < 3:
            print("Usage: serena-daemon.py status <project_path>", file=sys.stderr)
            sys.exit(1)
        status_daemon(sys.argv[2])

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
