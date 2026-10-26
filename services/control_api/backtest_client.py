"""Fixed private API relay; bounded sanitized DTOs without ambient credentials."""

import json
import secrets
from time import monotonic

import httpx

from packages.backtesting.jobs import BacktestError
from services.control_api.backtest_dto import RESPONSE_MODELS, BacktestProblem
from services.control_api.backtest_routes import CURRENT_TRACE
from services.control_api.backtest_service import identity, pairs


class BacktestClient:
    def __init__(self, *, transport=None):
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("exact MockTransport required")
        self._transport = transport

    def _request(self, operation, path, *, params=None, body=None, key=None):
        failure = (503, "UNAVAILABLE")
        try:
            with httpx.Client(
                transport=self._transport, timeout=5, trust_env=False, follow_redirects=False
            ) as client:
                headers = {"Accept-Encoding": "identity"}
                if key:
                    headers["Idempotency-Key"] = key
                trace = CURRENT_TRACE.get()
                if trace:
                    headers["traceparent"] = "00-" + trace + "-" + secrets.token_hex(8) + "-00"
                with client.stream(
                    "POST" if operation == "create_run" else "GET",
                    "http://paper-api:8012/api/v1/research/backtests/" + path,
                    params=params,
                    json=body,
                    headers=headers,
                ) as response:
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError()
                    length = response.headers.get("content-length")
                    if length and (
                        not length.isascii()
                        or not length.isdigit()
                        or len(length) > 12
                        or int(length) > 16 * 1024 * 1024
                    ):
                        raise ValueError()
                    raw, deadline = bytearray(), monotonic() + 8
                    for chunk in response.iter_raw():
                        if len(raw) + len(chunk) > 16 * 1024 * 1024 or monotonic() >= deadline:
                            raise ValueError()
                        raw.extend(chunk)
                    value = json.loads(
                        raw,
                        object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                    )
                    if response.status_code == (202 if operation == "create_run" else 200):
                        return RESPONSE_MODELS[operation].model_validate(value).model_dump()
                    error = BacktestProblem.model_validate(value)
                    if error.status == response.status_code:
                        failure = error.status, error.code
        except Exception:
            failure = 503, "UNAVAILABLE"
        raise BacktestError(*failure)

    def read_strategies(self):
        return self._request("read_strategies", "strategies")

    def create_run(self, payload, actor, key, trace):
        if actor != "local-operator":
            raise BacktestError(403, "FORBIDDEN")
        return self._request("create_run", "runs", body=payload, key=key)

    def read_runs(self, values):
        return self._request("read_runs", "runs", params=values)

    def read_run(self, run_id):
        identity(run_id)
        return self._request("read_run", "runs/" + run_id)

    def read_results(self, run_id, values):
        identity(run_id)
        return self._request("read_results", "runs/" + run_id + "/results", params=values)
