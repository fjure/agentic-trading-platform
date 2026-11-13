"""Record explicit local Quant admission limits using the database owner's libpq service."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.agent_research.registry import QUANT_RESEARCH_V1  # noqa: E402


def configure(connection, *, global_daily, agent_daily):
    agent = QUANT_RESEARCH_V1
    for policy, limit in (("global", global_daily), (f"agent:{agent.agent_id}:{agent.version}", agent_daily)):
        fingerprint = hashlib.sha256(f"{policy}:{limit}".encode()).hexdigest()
        connection.execute(text("""
            INSERT INTO agent_research.quant_start_policies AS current
                (policy_id,revision,fingerprint,max_daily,enabled)
            VALUES (:policy,1,:fingerprint,:limit,true)
            ON CONFLICT (policy_id) DO UPDATE SET revision=current.revision+1,
                fingerprint=excluded.fingerprint,max_daily=excluded.max_daily,enabled=true
            WHERE current.fingerprint<>excluded.fingerprint
                OR current.max_daily<>excluded.max_daily OR NOT current.enabled
        """), {"policy": policy, "fingerprint": fingerprint, "limit": limit})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True, help="Local database-owner libpq service")
    parser.add_argument("--global-daily", type=int, required=True)
    parser.add_argument("--agent-daily", type=int, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.service):
        parser.error("Provide a local libpq service name")
    if not 0 <= args.agent_daily <= args.global_daily <= 1000:
        parser.error("Require 0 <= agent-daily <= global-daily <= 1000")
    engine = create_engine(
        URL.create("postgresql+psycopg", query={"service": args.service}),
        connect_args={"host": "127.0.0.1", "hostaddr": "127.0.0.1", "connect_timeout": 5},
        hide_parameters=True,
    )
    try:
        with engine.begin() as connection:
            configure(connection, global_daily=args.global_daily, agent_daily=args.agent_daily)
    except Exception:
        print('{"status":"REJECTED","code":"QUANT_POLICY_WRITE_FAILED"}', file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(json.dumps({"status": "RECORDED", "globalDaily": args.global_daily, "agentDaily": args.agent_daily}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
