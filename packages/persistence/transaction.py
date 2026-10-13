"""Statement-only view of a caller-owned database transaction."""

from __future__ import annotations

import re

from sqlalchemy.engine import Connection


class ScopedConnection:
    """Callbacks may issue one SQL statement at a time, never commit or rollback."""

    __slots__ = ("__connection",)

    def __init__(self, connection: Connection):
        self.__connection = connection

    @staticmethod
    def _check(statement: object) -> None:
        sql = str(statement).strip()
        if not re.match(r"^(SELECT|WITH|INSERT|UPDATE|DELETE|VALUES)\b", sql, re.I):
            raise ValueError("only scoped data statements are allowed")
        if ";" in sql.rstrip(";"):
            raise ValueError("multi-statement SQL is not allowed in this context")

    def execute(self, statement, parameters=None):
        self._check(statement)
        return self.__connection.execute(statement, parameters or {})

    def exec_driver_sql(self, statement: str, parameters=None):
        self._check(statement)
        return self.__connection.exec_driver_sql(statement, parameters or {})


class ReadOnlyScopedConnection(ScopedConnection):
    """Verification callbacks can inspect durable state only."""

    @staticmethod
    def _check(statement: object) -> None:
        ScopedConnection._check(statement)
        if not re.match(r"^(SELECT|VALUES)\b", str(statement).strip(), re.I):
            raise ValueError("recovery verification may only read persisted state")
