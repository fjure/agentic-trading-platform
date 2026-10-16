"""Opaque, account-scoped supervisor cursors stored in PostgreSQL."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

_TOKEN = re.compile(r"c_[A-Za-z0-9_-]{43}\Z")
_KINDS = frozenset({"events", "page"})


class CursorExpired(Exception):
    """The caller must fetch a fresh snapshot or first history page."""


@dataclass(frozen=True, slots=True)
class CursorRecord:
    position: int
    context: dict
    expires_at: datetime


class CursorStore:
    def __init__(self, account_id: str):
        if not account_id:
            raise ValueError("account_id required")
        self.account_id = account_id

    def mint(
        self,
        connection: Connection,
        position: int,
        kind: str = "events",
        context: dict | None = None,
    ) -> str:
        if type(position) is not int or position < 0:
            raise ValueError("cursor position must be a nonnegative integer")
        if kind not in _KINDS:
            raise ValueError("invalid cursor kind")
        if context is not None and not isinstance(context, dict):
            raise ValueError("cursor context must be an object")
        encoded_context = json.dumps(context or {}, allow_nan=False, separators=(",", ":"))
        if len(encoded_context.encode("utf-8")) > 4096:
            raise ValueError("cursor context too large")
        token = "c_" + secrets.token_urlsafe(32)
        connection.execute(
            text("""
            INSERT INTO public.supervisor_cursors
              (token,account_id,kind,position,context)
            VALUES (:token,:account,:kind,:position,CAST(:context AS jsonb))
            """),
            {
                "token": token,
                "account": self.account_id,
                "kind": kind,
                "position": position,
                "context": encoded_context,
            },
        )
        return token

    def resolve(
        self, connection: Connection, token: str | None, kind: str = "events"
    ) -> CursorRecord:
        if kind not in _KINDS:
            raise ValueError("invalid cursor kind")
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            raise CursorExpired("unknown cursor")
        row = (
            connection.execute(
                text("""
                SELECT position,context,expires_at,
                       expires_at > clock_timestamp() AS is_current
                FROM public.supervisor_cursors
                WHERE token=:token AND account_id=:account AND kind=:kind
                """),
                {"token": token, "account": self.account_id, "kind": kind},
            )
            .mappings()
            .first()
        )
        if row is None or not row["is_current"]:
            raise CursorExpired("unknown or expired cursor")
        return CursorRecord(row["position"], row["context"], row["expires_at"])
