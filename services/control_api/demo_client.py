"""Narrow internal demo adapter, without database credentials or provider access."""

from __future__ import annotations

import json

import httpx

from services.control_api.auth import AuthDenied


class DemoClient:
    def _call(self, operation, **values):
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
                with client.stream(
                    "POST",
                    "http://demo:8010/internal/demo",
                    json={"operation": operation, **values},
                ) as response:
                    if response.status_code not in (200, 201, 202):
                        status = response.status_code
                        raise AuthDenied(
                            status if status in (400, 404, 409) else 503,
                            "INVALID_REQUEST" if status == 400 else "TEMPORARILY_UNAVAILABLE",
                        )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 1048576:
                            raise ValueError
                    return response.status_code, json.loads(body)
        except AuthDenied:
            raise
        except Exception:
            raise AuthDenied(503, "TEMPORARILY_UNAVAILABLE") from None

    def start(self, payload, key, actor, trace):
        status, value = self._call("start", payload=payload, key=key, actor=actor, trace=trace)
        return status, value, {}

    def list_runs(self):
        return self._call("list_runs")[1]

    def get_run(self, run_id):
        return self._call("get_run", run_id=run_id)[1]

    def snapshot(self, run_id, trace):
        return self._call("snapshot", run_id=run_id, trace=trace)[1]

    def history(self, run_id, resource, **kwargs):
        return self._call("history", run_id=run_id, resource=resource, **kwargs)[1]

    def control(self, run_id, action, actor, trace):
        return self._call("control", run_id=run_id, action=action, actor=actor, trace=trace)[1]
