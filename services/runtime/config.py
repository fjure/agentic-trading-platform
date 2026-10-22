"""Strict immutable public configuration and bounded private file readers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path("/run/config/runtime.json")
DATABASE_PATH = Path("/run/secrets/database")
ROLES = {
    "execution": "atl_execution",
    "worker": "atl_worker",
    "api": "atl_control_api",
    "watchdog": "atl_watchdog",
}
PAPER_ROLES = {role: group.replace("atl_", "atl_paper_", 1) for role, group in ROLES.items()}
DATABASES = {"lab": "lab_runtime", "paper": "lab_paper_runtime"}


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("INVALID_CONFIG")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    """Never include file contents, paths or parser diagnostics in exceptions."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
            raise ValueError
        value = json.loads(
            path.read_text("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if type(value) is not dict:
            raise ValueError
        return value
    except Exception:
        raise ValueError("INVALID_CONFIG") from None


def secret_fields(path: Path, fields: set[str]) -> dict[str, str]:
    value = read_json(path)
    if set(value) != fields or any(
        type(v) is not str or not v or len(v) > 2048 or any(ord(c) < 32 for c in v)
        for v in value.values()
    ):
        raise ValueError("INVALID_PRIVATE_CONFIGURATION")
    return value


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    mode: str = "DISABLED"
    broker_provider: str = "fake"
    decision_provider: str = "mock"
    account_id: str = "paper"
    instruments: tuple[str, ...] = ("SPY", "QQQ")
    origin: str = "http://127.0.0.1:8000"
    paper_enabled: bool = False
    reconciliation_fee_profile: str | None = None
    deployment: str = "lab"

    def __post_init__(self):
        if (
            self.deployment not in DATABASES
            or self.mode not in {"DISABLED", "REPLAY", "SHADOW", "PAPER"}
            or self.broker_provider not in {"fake", "alpaca-paper"}
            or self.decision_provider not in {"mock", "jev"}
            or self.account_id != "paper"
            or type(self.paper_enabled) is not bool
            or type(self.instruments) is not tuple
            or not self.instruments
            or len(set(self.instruments)) != len(self.instruments)
            or any(item not in {"SPY", "QQQ"} for item in self.instruments)
            or self.origin != "http://127.0.0.1:8000"
            or self.reconciliation_fee_profile
            not in {None, "alpaca-paper-us-retail-equity-zero-v1"}
            or (self.deployment == "lab" and self.broker_provider == "alpaca-paper"
                and not self.paper_enabled)
            or (self.mode == "PAPER" and not self.paper_enabled)
            or (self.deployment == "paper" and (
                self.mode != "DISABLED" or self.paper_enabled
                or self.broker_provider != "alpaca-paper" or self.decision_provider != "mock"
                or self.reconciliation_fee_profile is not None
            ))
        ):
            raise ValueError("UNSAFE_RUNTIME_CONFIG")

    @classmethod
    def load(cls, path: Path = CONFIG_PATH):
        value = read_json(path)
        if not set(value) <= set(cls.__dataclass_fields__):
            raise ValueError("INVALID_CONFIG")
        if "instruments" in value:
            if type(value["instruments"]) is not list:
                raise ValueError("INVALID_CONFIG")
            value["instruments"] = tuple(value["instruments"])
        return cls(**value)


def database_engine(role: str, path: Path = DATABASE_PATH, *, deployment: str = "lab"):
    """Fixed host/database; verified SET ROLE on every physical connection."""
    from sqlalchemy import URL, create_engine, event

    if role not in ROLES or deployment not in DATABASES:
        raise ValueError("INVALID_ROLE")
    expected = (PAPER_ROLES if deployment == "paper" else ROLES)[role]
    credentials = secret_fields(path, {"username", "password"})
    if not re.fullmatch(r"atl_[a-z_]+_login", credentials["username"]):
        raise ValueError("INVALID_DATABASE_IDENTITY")
    if (
        deployment == "paper" and credentials["username"] != expected + "_login"
        or deployment == "lab" and credentials["username"].startswith("atl_paper_")
    ):
        raise ValueError("INVALID_DATABASE_IDENTITY")
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=credentials["username"],
            password=credentials["password"],
            host="postgres",
            port=5432,
            database=DATABASES[deployment],
        ),
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def assign(connection, _record):
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SET ROLE {expected}")
                cursor.execute("SET TIME ZONE 'UTC'")
                cursor.execute("SELECT current_user,current_database(),session_user")
                identity = cursor.fetchone()
                if identity != (expected, DATABASES[deployment], credentials["username"]):
                    raise PermissionError("DATABASE_ROLE_MISMATCH")
            connection.commit()
        except Exception:
            connection.rollback()
            raise PermissionError("DATABASE_ROLE_UNAVAILABLE") from None

    return engine
