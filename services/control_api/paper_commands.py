"""Narrow SQL ports for public Paper commands and Execution-only private completion."""

from __future__ import annotations

import json

from sqlalchemy import text

from . import paper_dto as dto

ERROR_CODES = frozenset(
    {
        "INVALID_REQUEST",
        "AUTHENTICATION_REQUIRED",
        "ORIGIN_REJECTED",
        "IDEMPOTENCY_CONFLICT",
        "OPERATION_NOT_FOUND",
        "ORDER_NOT_FOUND",
        "VERSION_CONFLICT",
        "CREDENTIALS_MISSING",
        "CREDENTIALS_INVALID",
        "ACCOUNT_IDENTITY_MISMATCH",
        "ACCOUNT_BLOCKED",
        "CURRENCY_UNSUPPORTED",
        "MARKET_DATA_UNAVAILABLE",
        "OPEN_ORDERS_PRESENT",
        "UNSUPPORTED_POSITION",
        "COST_BASIS_MISSING",
        "SNAPSHOT_CHANGED",
        "LEDGER_ALREADY_BOUND",
        "LEDGER_NOT_PREPARED",
        "CONNECTION_NOT_VERIFIED",
        "APPROVAL_EXPIRED",
        "ENTRY_PAUSED",
        "KILL_SWITCH_ACTIVE",
        "SESSION_CLOSED",
        "STALE_QUOTE",
        "CAPABILITY_UNCONFIRMED",
        "BUDGET_EXCEEDED",
        "INSUFFICIENT_POSITION",
        "MINIMUM_QUANTITY",
        "POLICY_MISMATCH",
        "RECONCILIATION_REQUIRED",
        "BROKER_STATUS_UNKNOWN",
        "EXTERNAL_ACTIVITY",
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_REJECTED",
        "TEMPORARILY_UNAVAILABLE",
    }
)
REQUEST_TYPES = {
    "VERIFY_CONNECTION": dto.Verify,
    "PREPARE_LEDGER": dto.Prepare,
    "START": dto.Start,
    "PAUSE": dto.Control,
    "STOP": dto.Control,
    "KILL": dto.Control,
    "PLACE_LIMIT_ORDER": dto.Limit,
    "CANCEL_ORDER": dto.Cancel,
}


class PaperCommandError(ValueError):
    def __init__(self, status, code):
        self.code = code if code in ERROR_CODES else "TEMPORARILY_UNAVAILABLE"
        self.status = status
        super().__init__(self.code)


class PaperCommands:
    """Injected role engine only; no credential files, broker ports or provider requests."""

    def __init__(self, engine):
        self.engine = engine

    def _call(self, statement, values):
        with self.engine.begin() as connection:
            result = connection.execute(text(statement), values).scalar_one_or_none()
        if isinstance(result, dict) and "error" in result:
            raise PaperCommandError(
                {"INVALID_REQUEST": 400, "ORDER_NOT_FOUND": 404,
                 "OPERATION_NOT_FOUND": 404}.get(result["error"], 409), result["error"]
            )
        return result

    def read_status(self, trace_id):
        return self._call("SELECT public.paper_status(:trace)", {"trace": trace_id})

    def read_profile(self, trace_id):
        return self._call("SELECT public.paper_trading_profile_get(:trace)", {"trace": trace_id})

    def read_snapshot(self, trace_id):
        dto.read_query([("trace", trace_id)], private=True)
        return self._call("SELECT public.paper_snapshot(:trace)", {"trace": trace_id})

    def _read_page(self, statement, limit, cursor, trace_id):
        values = dto.read_arguments(limit, cursor, trace_id)
        return self._call(statement, values)

    def read_orders(self, limit, cursor, trace_id):
        return self._read_page(
            "SELECT public.paper_orders(:limit,:cursor,:trace)", limit, cursor, trace_id,
        )

    def read_fills(self, limit, cursor, trace_id):
        return self._read_page(
            "SELECT public.paper_fills(:limit,:cursor,:trace)", limit, cursor, trace_id,
        )

    def read_operations(self, limit, cursor, trace_id):
        return self._read_page(
            "SELECT public.paper_operations(:limit,:cursor,:trace)", limit, cursor, trace_id,
        )

    def read_trace(self, trace_id):
        dto.read_query([("trace", trace_id)], private=True)
        return self._call("SELECT public.paper_trace(:trace)", {"trace": trace_id})

    def save_profile(self, payload, actor_id, key, trace_id):
        try:
            payload = dto.ProfileUpdate.model_validate(payload).model_dump()
        except (ValueError, TypeError):
            raise PaperCommandError(400, "INVALID_REQUEST") from None
        return self._call(
            "SELECT public.paper_trading_profile_put(:actor,:key,:trace,CAST(:body AS jsonb))",
            {"actor": actor_id, "key": key, "trace": trace_id, "body": json.dumps(payload)},
        )

    def read_operation(self, operation_id, trace_id=None):
        dto.identity(operation_id)
        result = self._call(
            "SELECT public.paper_operation(CAST(:id AS uuid))", {"id": operation_id}
        )
        if result is None:
            raise PaperCommandError(404, "OPERATION_NOT_FOUND")
        return result

    def enqueue(self, operation, payload, actor_id, key, trace_id, order_id=None):
        try:
            model = REQUEST_TYPES[operation]
            payload = model.model_validate(payload).model_dump()
            if operation in {"PAUSE", "STOP", "KILL"} and payload["action"] != operation:
                raise ValueError
            if operation == "CANCEL_ORDER":
                payload["orderId"] = dto.identity(order_id)
            elif order_id is not None:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise PaperCommandError(400, "INVALID_REQUEST") from None
        return self._call(
            "SELECT public.paper_enqueue(:kind,CAST(:body AS jsonb),:actor,:key,:trace)",
            {
                "kind": operation,
                "body": json.dumps(payload),
                "actor": actor_id,
                "key": key,
                "trace": trace_id,
            },
        )

    def rotation_begin(self, actor_id, key, trace_id):
        generation = self._call(
            "SELECT public.paper_rotation_begin(:actor,:key,:trace)",
            {"actor": actor_id, "key": key, "trace": trace_id},
        )
        if generation is None:
            raise PaperCommandError(409, "VERSION_CONFLICT")
        return generation

    def rotation_complete(self, generation):
        if type(generation) is not int or generation < 1:
            raise PaperCommandError(400, "INVALID_REQUEST")
        return self._call(
            "SELECT public.paper_rotation_complete(:generation)", {"generation": generation}
        )

    def claim(self, owner, lease_seconds=30):
        dto.identity(owner)
        return self._call(
            "SELECT public.paper_claim(CAST(:owner AS uuid),:seconds)",
            {"owner": owner, "seconds": lease_seconds},
        )

    def finish(
        self, operation_id, owner, fence, *, public=None, private_account_id=None, error_code=None
    ):
        dto.identity(operation_id)
        dto.identity(owner)
        if private_account_id is not None:
            dto.identity(private_account_id)
        if error_code is not None and error_code not in ERROR_CODES:
            raise PaperCommandError(400, "INVALID_REQUEST")
        public = dict(public or {})
        aliases = {
            "credential_generation": "credentialGeneration",
            "observed_at": "observedAt",
            "account_label": "accountLabel",
            "verified_at": "verifiedAt",
            "market_data_feed": "marketDataFeed",
            "provider_verification": "providerVerification",
        }
        public = {aliases.get(key, key): value for key, value in public.items()}
        allowed = {
            "credentialGeneration",
            "observedAt",
            "accountLabel",
            "currency",
            "cash",
            "verifiedAt",
            "marketDataFeed",
            "providerVerification",
            "blockers",
        }
        if not public.keys() <= allowed:
            raise PaperCommandError(400, "INVALID_REQUEST")
        # Serialize only allowlisted fields; private UUID uses its own protected argument.
        for key in ("observedAt", "verifiedAt"):
            if public.get(key) is not None and hasattr(public[key], "isoformat"):
                public[key] = public[key].isoformat().replace("+00:00", "Z")
        if public.get("cash") is not None:
            public["cash"] = str(public["cash"])
        return self._call(
            "SELECT public.paper_finish(CAST(:id AS uuid),CAST(:owner AS uuid),:fence,"
            "CAST(:public AS jsonb),CAST(:private AS uuid),:error)",
            {
                "id": operation_id,
                "owner": owner,
                "fence": fence,
                "public": json.dumps(public),
                "private": private_account_id,
                "error": error_code,
            },
        )
