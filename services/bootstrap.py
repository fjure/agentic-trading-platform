"""Build smoke only. Does not start workers or authorize trading."""

import importlib.metadata
import json
import os
import sys


def main() -> int:
    if os.environ.get("TRADING_MODE", "DISABLED") != "DISABLED":
        print("bootstrap rejected: mode must be DISABLED", file=sys.stderr)
        return 2
    if os.environ.get("BROKER_PROVIDER", "fake") != "fake":
        print("bootstrap rejected: provider must be fake", file=sys.stderr)
        return 2
    # Inspect names only; never read or log credential values.
    if any(name.startswith(("ALPACA_", "APCA_", "JEV_")) for name in os.environ):
        print("bootstrap rejected: external credentials/configuration forbidden", file=sys.stderr)
        return 2
    print(json.dumps({
        "check": "build-smoke",
        "python": sys.version.split()[0],
        "mode": "DISABLED",
        "provider": "fake",
        "fastapi": importlib.metadata.version("fastapi"),
        "psycopg": importlib.metadata.version("psycopg"),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
