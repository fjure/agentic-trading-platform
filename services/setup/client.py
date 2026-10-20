"""Fixed internal adapter; no public endpoint, credentials, retries or environment proxies."""

import json

import httpx

from .store import MAX_BODY, SetupUnavailable, validate


class FixedSetupClient:
    _base = "http://provider-setup:8011"

    def _request(self, method: str, path: str, value=None):
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=5.0) as client:
                with client.stream(method, self._base + path, json=value,
                                   headers={"Accept": "application/json"}) as response:
                    if response.status_code != 200:
                        raise SetupUnavailable()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if len(body) + len(chunk) > MAX_BODY:
                            raise SetupUnavailable()
                        body.extend(chunk)
                    return json.loads(body)
        except Exception:
            raise SetupUnavailable() from None

    def read_integrations(self) -> dict:
        value = self._request("GET", "/integrations")
        try:
            if set(value) != {"alpaca", "jev"}:
                raise SetupUnavailable()
            for entry in value.values():
                if (set(entry) != {"status", "restartRequired"}
                        or entry["status"] not in {"CONFIGURED", "UNCONFIGURED"}
                        or type(entry["restartRequired"]) is not bool):
                    raise SetupUnavailable()
            return value
        except Exception:
            raise SetupUnavailable() from None

    def _save(self, provider, value):
        validate(provider, value)
        result = self._request("PUT", "/integrations/" + provider, value)
        expected = {"status": "CONFIGURED", "restartRequired": True}
        if result != expected:
            raise SetupUnavailable()
        # Never forward untrusted internal response strings to the public API.
        return expected

    def save_alpaca(self, value: dict) -> dict:
        return self._save("alpaca", value)

    def save_jev(self, value: dict) -> dict:
        return self._save("jev", value)
