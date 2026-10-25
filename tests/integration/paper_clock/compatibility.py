"""Process-clock compatibility probe; synthetic disposable database only."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import psycopg
from runtime import LIBRARY_SHA256

STAGE = "INITIAL"
PASSED = []
ELAPSED = {}


def measured(name, action, minimum=0.12, maximum=1.5):
    global STAGE
    STAGE = name
    start = time.monotonic()
    action()
    elapsed = time.monotonic() - start
    ELAPSED[name] = round(elapsed, 6)
    if not minimum <= elapsed < maximum:
        print(f"CLOCK_METRIC:{name}:{elapsed:.3f}", flush=True)
        raise AssertionError("elapsed outside bound")
    PASSED.append(name)


def main():
    global STAGE
    offset = float(os.environ["FAKETIME"])
    virtual = datetime.now(UTC)
    assert virtual.weekday() == 0 and virtual.hour == 14 and virtual.minute == 30
    assert abs(virtual.timestamp() - time.time()) < 0.05
    STAGE = "UNMODIFIED_CLOCK_DOMAINS"
    raw_env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "LD_PRELOAD",
            "FAKETIME",
            "FAKETIME_DONT_FAKE_MONOTONIC",
            "NO_FAKE_STAT",
        }
    }
    code = "import json,time; print(json.dumps([time.time(),time.monotonic()]))"
    real_wall, real_monotonic = json.loads(
        subprocess.check_output(
            [sys.executable, "-c", code],
            env=raw_env,
            timeout=5,
        )
    )
    assert abs(time.time() - real_wall - offset) < 0.5
    assert abs(time.monotonic() - real_monotonic) < 0.5
    PASSED.append(STAGE)

    STAGE = "REAL_STAT_AND_UTIME"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "synthetic"
        path.write_text("synthetic")
        assert abs(path.stat().st_mtime - (time.time() - offset)) < 1
        os.utime(path, (1234567890, 1234567890))
        assert path.stat().st_mtime == 1234567890
    PASSED.append(STAGE)

    STAGE = "NEW_PYTHON_PROCESS"
    child_wall, child_monotonic = json.loads(
        subprocess.check_output(
            [sys.executable, "-c", code],
            timeout=5,
        )
    )
    assert abs(time.time() - child_wall) < 0.5
    assert abs(time.monotonic() - child_monotonic) < 0.5
    PASSED.append(STAGE)

    STAGE = "THREAD_CLOCKS"
    values = []
    threads = [threading.Thread(target=lambda: values.append(time.time())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert len(values) == 8 and max(values) - min(values) < 0.5
    assert abs(time.time() - min(values)) < 0.5
    PASSED.append(STAGE)
    wall, monotonic = time.time(), time.monotonic()
    measured("PYTHON_SLEEP", lambda: time.sleep(0.2))
    assert abs((time.time() - wall) - (time.monotonic() - monotonic)) < 0.05
    PASSED.append("MOVING_UTC_1X")
    measured("THREAD_EVENT_WAIT", lambda: threading.Event().wait(0.2))

    async def async_timeout():
        try:
            await asyncio.wait_for(asyncio.sleep(2), timeout=0.2)
        except TimeoutError:
            return
        raise AssertionError("async timeout did not expire")

    measured("ASYNCIO_TIMEOUT", lambda: asyncio.run(async_timeout()))

    class Slow(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            time.sleep(0.6)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"synthetic")
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def http_timeout():
        with httpx.Client(timeout=0.2, trust_env=False) as client:
            try:
                client.get(f"http://127.0.0.1:{server.server_port}/")
            except httpx.ReadTimeout:
                return
        raise AssertionError("HTTP read timeout did not expire")

    try:
        measured("HTTP_READ_TIMEOUT", http_timeout)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    STAGE = "PG_PYTHON_ALIGNMENT"

    def connect():
        return psycopg.connect(host="postgres", user="clock_probe", dbname="clock_probe")

    with connect() as first, connect() as second:
        a = first.execute("SELECT clock_timestamp(),pg_backend_pid()").fetchone()
        b = second.execute("SELECT clock_timestamp(),pg_backend_pid()").fetchone()
        assert a[1] != b[1]
        assert abs((a[0] - b[0]).total_seconds()) < 0.5
        assert abs(datetime.now(UTC).timestamp() - b[0].timestamp()) < 0.5
        PASSED.extend([STAGE, "NEW_PG_BACKENDS"])
        initial = first.execute("SELECT clock_timestamp()").fetchone()[0]
        measured("PG_SLEEP", lambda: first.execute("SELECT pg_sleep(0.2)"))
        later = first.execute("SELECT clock_timestamp()").fetchone()[0]
        assert 0.12 <= (later - initial).total_seconds() < 1.5
        PASSED.append("PG_MOVING_UTC_1X")
        first.commit()

        def statement_timeout():
            first.execute("SET statement_timeout='200ms'")
            try:
                first.execute("SELECT pg_sleep(2)")
            except psycopg.errors.QueryCanceled:
                first.rollback()
                return
            print("CLOCK_METRIC:PG_STATEMENT_TIMEOUT_NOT_FIRED:1", flush=True)
            raise AssertionError("statement timeout did not expire")

        measured("PG_STATEMENT_TIMEOUT", statement_timeout)
        first.execute("CREATE TABLE clock_probe_row(id integer PRIMARY KEY,value integer)")
        first.execute("INSERT INTO clock_probe_row VALUES(1,0)")
        first.commit()
        first.execute("UPDATE clock_probe_row SET value=1 WHERE id=1")

        def lock_timeout():
            second.execute("SET lock_timeout='200ms'")
            try:
                second.execute("UPDATE clock_probe_row SET value=2 WHERE id=1")
            except psycopg.errors.LockNotAvailable:
                second.rollback()
                return
            raise AssertionError("lock timeout did not expire")

        measured("PG_LOCK_TIMEOUT", lock_timeout)
        first.rollback()
        assert second.execute("SELECT value FROM clock_probe_row WHERE id=1").fetchone()[0] == 0

    STAGE = "ARTIFACT_INTEGRITY"
    artifacts = {}
    for name in ("libfaketime.so.1", "libfaketimeMT.so.1"):
        artifacts[name] = hashlib.sha256((Path("/opt/test-clock") / name).read_bytes()).hexdigest()
    assert artifacts == LIBRARY_SHA256
    assert Path("/opt/test-clock/source-version.txt").read_text().splitlines() == [
        "0.9.13",
        "86b37fde2fed7336ea2d0c17928e3015a55d9b4a",
    ]
    PASSED.append(STAGE)
    print(
        json.dumps(
            {
                "passed": PASSED,
                "realElapsedSeconds": ELAPSED,
                "librarySha256": artifacts,
                "virtualObservedAt": datetime.now(UTC).isoformat(),
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"CLOCK_FAIL:{STAGE}:{type(error).__name__}")
        raise SystemExit(1) from None
