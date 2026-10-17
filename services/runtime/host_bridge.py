"""Fixed loopback/STDIO fallback for internal-network Docker Desktop publishing.

No target options, shell evaluation, payload logging, credentials or new container
network are involved. The host launcher owns the hidden helper's lifecycle.
"""

from __future__ import annotations

import argparse
import os
import socket
import socketserver
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from threading import BoundedSemaphore, Thread
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
LIMIT = BoundedSemaphore(16)
IDLE_SECONDS = 60


def command() -> list[str]:
    return [
        "docker", "compose", "-f", str(ROOT / "infra/compose/compose.yaml"),
        "--profile", "lab", "exec", "-T", "control-api", "python", "-u",
        "-m", "services.runtime.host_bridge", "guest",
    ]


def guest():
    """The child can reach only the frontend in its own network namespace."""
    with socket.create_connection(("127.0.0.1", 8000), timeout=5) as upstream:
        upstream.settimeout(IDLE_SECONDS)

        def upload():
            try:
                while data := sys.stdin.buffer.read1(16384):
                    upstream.sendall(data)
            except Exception:
                pass
            finally:
                with suppress(OSError):
                    upstream.shutdown(socket.SHUT_WR)

        Thread(target=upload, daemon=True).start()
        while data := upstream.recv(16384):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if not LIMIT.acquire(blocking=False):
            return
        child = None
        try:
            self.request.settimeout(1)
            last_activity = [monotonic()]
            child = subprocess.Popen(
                command(), cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            def upload():
                try:
                    while True:
                        try:
                            data = self.request.recv(16384)
                        except TimeoutError:
                            if monotonic() - last_activity[0] < IDLE_SECONDS:
                                continue
                            child.terminate()
                            break
                        if not data:
                            break
                        last_activity[0] = monotonic()
                        child.stdin.write(data)
                        child.stdin.flush()
                except Exception:
                    pass
                finally:
                    with suppress(Exception):
                        child.stdin.close()

            Thread(target=upload, daemon=True).start()
            while data := child.stdout.read(16384):
                self.request.sendall(data)
                last_activity[0] = monotonic()
        except Exception:
            pass  # No HTTP bytes or subprocess diagnostics enter a log.
        finally:
            if child is not None:
                with suppress(Exception):
                    child.terminate()
                    child.wait(timeout=5)
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                for pipe in (child.stdin, child.stdout):
                    with suppress(Exception):
                        pipe.close()
            LIMIT.release()


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        pass  # Socket/server errors contain no request bytes in diagnostics.


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["host", "guest"])
    args = parser.parse_args(argv)
    try:
        if args.role == "guest":
            guest()
        else:
            if os.name != "nt" or not (ROOT / "AGENTS.md").is_file():
                raise OSError("LOCAL_WINDOWS_LAUNCHER_REQUIRED")
            with Server(("127.0.0.1", 8000), Handler) as server:
                server.serve_forever(poll_interval=0.5)
    except (Exception, KeyboardInterrupt):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
