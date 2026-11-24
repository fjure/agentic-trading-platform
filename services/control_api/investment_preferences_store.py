"""Actor-scoped display preferences; no financial or provider configuration ports."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from sqlalchemy import text

STEPS = ("EXPERIENCE", "GOALS", "HORIZON", "LOSS_TOLERANCE", "CONTEXT")
GOALS = {"LEARN", "UNDERSTAND_PORTFOLIO", "RESEARCH_STRATEGIES"}
_KEY = re.compile(r"[A-Za-z0-9._:-]{16,128}\Z")
_ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")


class PreferencesError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


def validate_update(payload: dict) -> dict:
    """Validate independently of HTTP; retain original input for exact retry hashing."""
    try:
        if not isinstance(payload, dict) or set(payload) != {
            "expectedVersion", "reset", "experienceLevel", "goals", "horizon",
            "lossTolerance", "contextReference", "onboardingSteps",
        }:
            raise ValueError
        if (type(payload["expectedVersion"]) is not int
                or not 0 <= payload["expectedVersion"] <= 9223372036854775806
                or type(payload["reset"]) is not bool):
            raise ValueError
        if (payload["experienceLevel"] not in {"UNSPECIFIED", "BEGINNER", "EXPERIENCED"}
                or payload["horizon"] not in {"UNSPECIFIED", "SHORT", "MEDIUM", "LONG"}
                or payload["lossTolerance"] not in {"UNSPECIFIED", "LOW", "MEDIUM", "HIGH"}):
            raise ValueError
        goals = payload["goals"]
        if (not isinstance(goals, list) or len(goals) > 3
                or any(not isinstance(goal, str) or goal not in GOALS for goal in goals)
                or len(set(goals)) != len(goals)):
            raise ValueError
        context = payload["contextReference"]
        if not isinstance(context, dict):
            raise ValueError
        if context.get("context") == "DEMO":
            if (set(context) != {"context", "namespace", "demoRunId"}
                    or context["namespace"] != "demo"
                    or not isinstance(context["demoRunId"], str)
                    or not _ID.fullmatch(context["demoRunId"])):
                raise ValueError
        elif (set(context) != {"context", "namespace"}
              or (context.get("context"), context.get("namespace")) not in {
                  ("FAKE", "fake"), ("ALPACA_PAPER", "alpaca-paper"),
              }):
            raise ValueError
        steps = payload["onboardingSteps"]
        if not isinstance(steps, list) or len(steps) > 5:
            raise ValueError
        seen = set()
        for step in steps:
            if (not isinstance(step, dict) or set(step) != {"step", "state", "updatedAt"}
                    or step["step"] not in STEPS or step["step"] in seen
                    or step["state"] not in {"COMPLETED", "SKIPPED", "UNSEEN"}):
                raise ValueError
            seen.add(step["step"])
            stamp = step["updatedAt"]
            if stamp is not None:
                if not isinstance(stamp, str) or not _STAMP.fullmatch(stamp):
                    raise ValueError
                datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return json.loads(json.dumps(payload, allow_nan=False))
    except (ValueError, TypeError, KeyError, OverflowError):
        raise PreferencesError(400, "INVALID_REQUEST") from None


class InvestmentPreferencesStore:
    def __init__(self, ledger):
        self.ledger = ledger

    @staticmethod
    def _actor(actor):
        if not isinstance(actor, str) or not _ACTOR.fullmatch(actor):
            raise PreferencesError(403, "FORBIDDEN")

    def read(self, actor: str) -> dict:
        self._actor(actor)
        try:
            with self.ledger.transaction() as tx:
                return tx.connection.execute(text(
                    "SELECT investment_experience.read_preferences(:actor)"
                ), {"actor": actor}).scalar_one()
        except Exception:
            raise PreferencesError(503, "UNAVAILABLE") from None

    def _request(self, actor: str, payload: dict, key: str) -> dict:
        self._actor(actor)
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise PreferencesError(400, "INVALID_REQUEST")
        payload = validate_update(payload)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        document = {k: v for k, v in payload.items() if k not in {"expectedVersion", "reset"}}
        return {
            "actor": actor, "key_hash": hashlib.sha256(key.encode()).hexdigest(),
            "request_hash": hashlib.sha256(encoded.encode()).hexdigest(),
            "expected": payload["expectedVersion"], "reset": payload["reset"],
            "document": json.dumps(document, separators=(",", ":")),
        }

    def replay(self, actor: str, payload: dict, key: str) -> dict | None:
        params = self._request(actor, payload, key)
        try:
            with self.ledger.transaction() as tx:
                result = tx.connection.execute(text("""
                    SELECT investment_experience.read_preference_receipt(
                        :actor,:key_hash,:request_hash)
                """), {k: params[k] for k in ("actor", "key_hash", "request_hash")}).scalar_one()
                if result is not None and "error" in result:
                    if result["error"] == "IDEMPOTENCY_CONFLICT":
                        raise PreferencesError(409, "IDEMPOTENCY_CONFLICT")
                    raise PreferencesError(503, "UNAVAILABLE")
                return result
        except PreferencesError:
            raise
        except Exception:
            raise PreferencesError(503, "UNAVAILABLE") from None

    def update(self, actor: str, payload: dict, key: str) -> dict:
        params = self._request(actor, payload, key)
        try:
            with self.ledger.transaction() as tx:
                result = tx.connection.execute(text("""
                    SELECT investment_experience.update_preferences(
                        :actor,:key_hash,:request_hash,:expected,:reset,CAST(:document AS jsonb))
                """), params).scalar_one()
                if "error" in result:
                    code = result["error"]
                    if code not in {"VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", "INVALID_REQUEST"}:
                        raise PreferencesError(503, "UNAVAILABLE")
                    raise PreferencesError(400 if code == "INVALID_REQUEST" else 409, code)
                return result
        except PreferencesError:
            raise
        except Exception:
            raise PreferencesError(503, "UNAVAILABLE") from None
