"""Alembic environment. Migration credentials are supplied by the operator."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from packages.persistence.schema import metadata

config = context.config
target_metadata = metadata


def run_migrations_offline() -> None:
    raise RuntimeError("offline SQL output is disabled for privileged migrations")


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    section = config.get_section(config.config_ini_section) or {}
    if not section.get("sqlalchemy.url"):
        raise RuntimeError("migration URL must be supplied by the operator")
    engine = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool, hide_parameters=True
    )
    with engine.connect() as opened:
        context.configure(connection=opened, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
