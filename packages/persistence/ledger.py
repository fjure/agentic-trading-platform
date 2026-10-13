"""Transactional PostgreSQL implementation of the ledger port."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from sqlalchemy import Engine, create_engine, insert, select, update
from sqlalchemy.engine import Connection

from packages.domain import models
from packages.persistence.schema import REFERENCES, TABLE_NAMES, TABLES, accounts, position_versions


def _encode(value: object) -> object:
    if isinstance(value, Decimal):
        return models.decimal_string(value)
    if isinstance(value, datetime):
        return models.utc_time(value).isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {field.name: _encode(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    return value


def _decode(hint: object, value: object) -> object:
    if value is None:
        return None
    variants = get_args(hint) if get_origin(hint) in (UnionType, Union) else (hint,)
    concrete = next((part for part in variants if part is not type(None)), hint)
    if concrete is Decimal:
        return models.decimal_value(value)
    if concrete is datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.astimezone(UTC)
    if isinstance(concrete, type) and issubclass(concrete, StrEnum):
        return concrete(value)
    if isinstance(concrete, type) and is_dataclass(concrete):
        return concrete(
            **{
                key: _decode(type_hint, value[key])
                for key, type_hint in get_type_hints(concrete).items()
            }
        )
    origin = get_origin(concrete)
    if origin in (tuple, list):
        element_hint = get_args(concrete)[0]
        return tuple(_decode(element_hint, item) for item in value)
    if origin is not None and isinstance(value, dict):
        args = get_args(concrete)
        if len(args) == 2:
            return {key: _decode(args[1], item) for key, item in value.items()}
    return value


class LedgerUnit:
    def __init__(self, connection: Connection):
        self.connection = connection

    def insert(self, entity: object, *, adapter: str | None = None) -> None:
        model = type(entity)
        table = TABLES[model]
        values = {}
        for field in fields(entity):
            value = getattr(entity, field.name)
            if field.name in REFERENCES.get(table.name, {}) and not field.name.endswith("_id"):
                values[field.name + "_id"] = value.id
                if table.name == "market_states" and field.name == "position":
                    values["position_ledger_version"] = value.ledger_version
            elif (
                isinstance(value, (dict, tuple))
                or is_dataclass(value)
                or isinstance(value, Mapping)
            ):
                values[field.name] = _encode(value)
            else:
                values[field.name] = (
                    _encode(value) if isinstance(value, (Decimal, StrEnum)) else value
                )
        if table.name in ("broker_orders", "execution_reports"):
            if not adapter:
                raise ValueError("adapter identity required for broker ledger entries")
            values["adapter"] = adapter
        self.connection.execute(insert(table).values(**values))

    def get(self, model: type, entity_id: str) -> object | None:
        table = TABLES[model]
        row = (
            self.connection.execute(select(table).where(table.c.id == entity_id)).mappings().first()
        )
        if row is None:
            return None
        values = {}
        for name, hint in get_type_hints(model).items():
            if name in REFERENCES.get(table.name, {}) and not name.endswith("_id"):
                if table.name == "market_states" and name == "position":
                    version = (
                        self.connection.execute(
                            select(position_versions).where(
                                position_versions.c.position_id == row["position_id"],
                                position_versions.c.ledger_version
                                == row["position_ledger_version"],
                            )
                        )
                        .mappings()
                        .one()
                    )
                    values[name] = models.Position(
                        id=version["position_id"],
                        instrument_id=version["instrument_id"],
                        currency=version["currency"],
                        quantity=version["quantity"],
                        average_price=version["average_price"],
                        realized_pnl=version["realized_pnl"],
                        ledger_version=version["ledger_version"],
                    )
                    continue
                nested_model = next(
                    item
                    for item, table_name in TABLE_NAMES.items()
                    if table_name == REFERENCES[table.name][name]
                )
                values[name] = self.get(nested_model, row[name + "_id"])
            else:
                values[name] = _decode(hint, row[name])
        return model(**values)

    def replace(self, entity: object, *, expected_version: int | None = None) -> bool:
        model = type(entity)
        table = TABLES[model]
        if table.name not in ("positions", "risk_reservations", "broker_orders", "agent_runs"):
            raise ValueError("immutable ledger entity")
        values = {}
        for field in fields(entity):
            if field.name == "id":
                continue
            value = getattr(entity, field.name)
            if field.name in REFERENCES.get(table.name, {}) and not field.name.endswith("_id"):
                values[field.name + "_id"] = value.id
            else:
                values[field.name] = _encode(value) if not isinstance(value, datetime) else value
        statement = update(table).where(table.c.id == entity.id)
        if expected_version is not None:
            version_name = "version" if "version" in table.c else "ledger_version"
            statement = statement.where(table.c[version_name] == expected_version)
        return self.connection.execute(statement.values(**values)).rowcount == 1

    def lock_account(self, account_id: str) -> None:
        result = self.connection.execute(
            select(accounts.c.id).where(accounts.c.id == account_id).with_for_update()
        ).scalar_one_or_none()
        if result is None:
            raise LookupError("account does not exist")

    def lock_accounts(self, account_ids: Iterator[str]) -> None:
        for account_id in sorted(set(account_ids)):
            self.lock_account(account_id)


class PostgresLedger:
    """Caller supplies a runtime DSN; the migration role must never use this class."""

    def __init__(self, dsn: str | None = None, *, engine: Engine | None = None):
        if (dsn is None) == (engine is None):
            raise ValueError("provide exactly one of dsn or engine")
        self.engine = engine or create_engine(dsn, hide_parameters=True)

    @contextmanager
    def transaction(self) -> Iterator[LedgerUnit]:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            yield LedgerUnit(connection)
