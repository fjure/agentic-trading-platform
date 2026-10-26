"""Real PostgreSQL + authenticated HTTP persistence/cursor/partial result evidence."""

import sys
from pathlib import Path

from tests.integration.test_backtest_dataset import guest_engine, host


def guest():
    import json
    from uuid import uuid4

    from sqlalchemy import text

    from packages.backtesting.jobs import BacktestError
    from packages.research.registry import definitions, register
    from services.control_api.backtest_service import BacktestService
    from tests.security.test_backtest_access import BASE, ORIGIN, client

    root = Path("/opt/lab")

    def fixture(name):
        return json.loads((root / f"contracts/examples/v1/backtest-{name}.valid.json").read_text())

    engine = guest_engine()
    with engine.begin() as connection:
        register(connection)
    # Persistent signing material survives service recreation.
    service = BacktestService(engine, code_version="a" * 40)
    http = client(service)
    payload = fixture("request")
    payload["instrumentId"] = str(definitions()[0][0].instrument_id)
    headers = {"Origin": ORIGIN, "Idempotency-Key": "backtest-api-idem-001"}
    response = http.post(BASE + "/runs", json=payload, headers=headers)
    assert response.status_code == 202, (response.status_code, response.json())
    run_id = response.json()["runId"]
    repeated = http.post(BASE + "/runs", json=payload, headers=headers)
    assert repeated.status_code == 202 and repeated.json()["runId"] == run_id
    conflict = http.post(BASE + "/runs", json={**payload, "feeUsd": "2"}, headers=headers)
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    detail = http.get(BASE + "/runs/" + run_id)
    assert detail.status_code == 200 and detail.json()["run"]["status"] == "ACCEPTED"
    assert detail.json()["manifest"] is None
    assert http.get(BASE + "/runs/" + str(uuid4())).status_code == 404
    assert http.get(BASE + "/runs/" + run_id + "/results").json()["items"] == []
    for index in range(2):
        more = http.post(
            BASE + "/runs",
            json=payload,
            headers={**headers, "Idempotency-Key": f"backtest-api-other-00{index}"},
        )
        assert more.status_code == 202
    first = http.get(BASE + "/runs?limit=1").json()
    token = first["nextCursor"]
    restarted = client(BacktestService(engine, code_version="a" * 40))
    second = restarted.get(BASE + "/runs", params={"limit": 1, "cursor": token})
    assert second.status_code == 200
    assert second.json()["items"][0]["runId"] != first["items"][0]["runId"]
    assert (
        restarted.get(BASE + "/runs", params={"cursor": token, "status": "RUNNING"}).status_code
        == 409
    )
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert restarted.get(BASE + "/runs", params={"cursor": tampered}).status_code == 400
    invalid = {**payload, "folds": [dict(payload["folds"][0], trainingEnd="2026-10-24T00:00:00Z")]}
    assert http.post(BASE + "/runs", json=invalid, headers=headers).status_code == 400
    lease = service.jobs.claim(uuid4())
    assert str(lease.run_id) == run_id
    summary = fixture("summary-page")["items"][0]
    summary["window"] = payload["folds"][0]
    service.jobs.bind_manifest(lease, {"domain": {"syntheticFixture": True}, "public": None})
    service.jobs.checkpoint(lease, 0, {"summary": summary, "equity": [], "fills": []})
    service.jobs.publish(
        lease,
        {"summary": [], "equity": [], "fills": []},
        status="BLOCKED",
        error_code="BLOCKED_QUALITY",
    )
    blocked = http.get(BASE + "/runs/" + run_id).json()
    assert blocked["run"]["status"] == "BLOCKED"
    assert blocked["run"]["blockers"][0]["code"] == "BLOCKED_QUALITY_UNKNOWN"
    assert blocked["run"]["manifestHash"] and blocked["manifest"] is None
    partial = http.get(BASE + "/runs/" + run_id + "/results").json()
    assert partial["items"] == [] and partial["resultHash"] is None
    assert partial["completedFolds"] == [{"foldIndex": 0, "resultHash": summary["resultHash"]}]
    # Successful published rows use the actual persisted result fingerprint and public manifest hash.
    lease = service.jobs.claim(uuid4())
    manifest = fixture("manifest")
    manifest["request"] = payload
    service.jobs.bind_manifest(lease, {"domain": {"syntheticFixture": True}, "public": manifest})
    equity = fixture("equity-page")["items"][0]
    service.jobs.checkpoint(lease, 0, {"summary": summary, "equity": [equity] * 3, "fills": []})
    service.jobs.publish(lease, {"summary": [summary], "equity": [equity] * 3, "fills": []})
    successful_id = str(lease.run_id)
    page = http.get(
        BASE + "/runs/" + successful_id + "/results", params={"resultType": "equity", "limit": 1}
    )
    assert page.status_code == 200, page.text
    result_cursor = page.json()["nextCursor"]
    next_page = restarted.get(
        BASE + "/runs/" + successful_id + "/results",
        params={"resultType": "equity", "limit": 1, "cursor": result_cursor},
    )
    assert next_page.status_code == 200
    wrong_kind = restarted.get(
        BASE + "/runs/" + successful_id + "/results",
        params={"resultType": "fills", "cursor": result_cursor},
    )
    assert wrong_kind.status_code == 409
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM backtest.runs")).scalar_one() == 3
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.audit WHERE event='ACCEPTED'")
            ).scalar_one()
            == 3
        )
    try:
        service._verify(result_cursor, "wrong-binding")
        raise AssertionError("foreign cursor accepted")
    except BacktestError as error:
        assert error.code == "SNAPSHOT_CONFLICT"
    print("PASS backtest real PG/HTTP idempotency/reload/cursor/partial/publication/redaction")


def test_backtest_api_real_postgresql():
    assert host("tests.integration.test_backtest_api").startswith("PASS backtest real PG/HTTP")


if __name__ == "__main__":
    if sys.argv[1:] == ["guest"]:
        try:
            guest()
        except Exception as error:
            import traceback

            frames = traceback.extract_tb(error.__traceback__)
            location = ",".join(
                f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames[-4:]
            )
            print("FAIL " + type(error).__name__ + " " + location)
            raise SystemExit(1) from None
    else:
        print(host("tests.integration.test_backtest_api"))
