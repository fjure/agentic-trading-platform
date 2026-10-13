"""PostgreSQL ledger implementation. Schema changes are applied only by Alembic."""

from packages.persistence.ledger import PostgresLedger

__all__ = ["PostgresLedger"]
