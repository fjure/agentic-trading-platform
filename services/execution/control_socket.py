"""Private Unix control endpoint for the Linux Execution/container boundary."""

from __future__ import annotations

import argparse
import json
import os
import socket
import stat
import time
import uuid
from contextlib import AbstractContextManager
from pathlib import Path

MAX_FRAME = 4096


def _private_directory(path: Path) -> None:
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        raise OSError("SAFE_LOCAL_ENDPOINT_UNAVAILABLE")
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PermissionError("CONTROL_DIRECTORY_NOT_PRIVATE")


def _frame(connection) -> dict:
    deadline = time.monotonic() + 2.0
    data = bytearray()
    while len(data) <= MAX_FRAME:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("CONTROL_FRAME_TIMEOUT")
        connection.settimeout(remaining)
        part = connection.recv(min(1024, MAX_FRAME + 1 - len(data)))
        if not part:
            break
        data.extend(part)
        if b"\n" in data:
            break
    if not data.endswith(b"\n") or data.count(b"\n") != 1 or len(data) > MAX_FRAME:
        raise ValueError("INVALID_CONTROL_FRAME")
    value = json.loads(data)
    if type(value) is not dict:
        raise ValueError("INVALID_CONTROL_REQUEST")
    return value


def validate_request(value: dict) -> dict:
    if type(value) is not dict or value.get("command") not in {
        "pause",
        "kill",
        "resume",
        "activate-paper",
    }:
        raise ValueError("INVALID_CONTROL_REQUEST")
    policy_command = value["command"] in {"resume", "activate-paper"}
    expected = {"command", "expected_policy_version"} if policy_command else {"command"}
    if set(value) != expected:
        raise ValueError("INVALID_CONTROL_REQUEST")
    if policy_command and (
        type(value["expected_policy_version"]) is not int or value["expected_policy_version"] < 1
    ):
        raise ValueError("INVALID_CONTROL_POLICY")
    return value


def dispatcher(*, ledger, account_id: str, on_kill=None):
    """Control-role commands only; recovery verification is Execution-owned."""
    from services.control_api import control

    def dispatch(value):
        value = validate_request(value)
        command = value["command"]
        common = dict(
            ledger=ledger,
            account_id=account_id,
            actor_id="local-control",
            trace_id=uuid.uuid4().hex,
        )
        if command == "pause":
            version = control.pause(**common, reason_code="PAUSED_NEW")
            from sqlalchemy import text

            with ledger.transaction() as tx:
                status = tx.connection.execute(
                    text(
                        "SELECT safety_state FROM public.risk_account_context "
                        "WHERE account_id=:account"
                    ),
                    {"account": account_id},
                ).scalar_one()
            return {"status": status, "version": version}
        if command == "kill":
            version = control.kill(**common, reason_code="KILL_SWITCH_ACTIVE")
            cancellation = "NOT_CONFIGURED"
            if on_kill is not None:
                try:
                    on_kill()
                    cancellation = "REQUESTED"
                except Exception:
                    cancellation = "UNAVAILABLE"
            return {"status": "HALTED", "version": version, "cancellation": cancellation}
        operation = (
            control.request_resume if command == "resume" else control.request_paper_activation
        )
        request_id = operation(**common, expected_policy_version=value["expected_policy_version"])
        return {"status": "REQUESTED", "request_id": request_id}

    return dispatch


class LocalControlServer(AbstractContextManager):
    def __init__(self, path: Path, dispatch):
        self.path = Path(path)
        self.dispatch = dispatch
        self.socket = None
        self.identity = None

    def __enter__(self):
        _private_directory(self.path.parent)
        if os.path.lexists(self.path):
            raise FileExistsError("CONTROL_ENDPOINT_ALREADY_EXISTS")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            info = self.path.lstat()
            self.identity = (info.st_dev, info.st_ino)
            listener.listen(4)
            listener.settimeout(2.0)
            self.socket = listener
            return self
        except BaseException:
            listener.close()
            raise

    def serve_once(self):
        connection, _ = self.socket.accept()
        with connection:
            try:
                request = validate_request(_frame(connection))
                response = self.dispatch(request)
            except Exception:
                response = {"status": "DENIED", "code": "INVALID_OR_UNAVAILABLE"}
            connection.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode())

    def __exit__(self, *args):
        if self.socket is not None:
            self.socket.close()
        try:
            info = self.path.lstat()
            if (info.st_dev, info.st_ino) == self.identity and stat.S_ISSOCK(info.st_mode):
                self.path.unlink()
        except FileNotFoundError:
            pass


def send_control(path: Path, request: dict) -> dict:
    request = validate_request(request)
    _private_directory(Path(path).parent)
    info = Path(path).lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PermissionError("CONTROL_ENDPOINT_NOT_PRIVATE")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2.0)
        client.connect(str(path))
        client.sendall((json.dumps(request) + "\n").encode())
        return _frame(client)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("command", choices=["pause", "kill", "resume", "activate-paper"])
    parser.add_argument("--policy-version", type=int)
    args = parser.parse_args()
    request = {"command": args.command}
    if args.policy_version is not None:
        request["expected_policy_version"] = args.policy_version
    try:
        result = send_control(args.socket, request)
    except Exception:
        result = {"status": "DENIED", "code": "INVALID_OR_UNAVAILABLE"}
    print(json.dumps(result))
    if result.get("status") == "DENIED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
