"""Private immutable Alpaca provider pins and audited capability provenance.

Revision ID: 0008_broker_binding
Revises: 0007_supervisor_cursors
"""

from alembic import op

revision = "0008_broker_binding"
down_revision = "0007_supervisor_cursors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE public.risk_instrument_context
      ADD COLUMN continuous_session_confirmed boolean NOT NULL DEFAULT false
    """)
    op.execute("""
    CREATE TABLE public.broker_account_bindings (
      account_id varchar(255) PRIMARY KEY REFERENCES public.accounts(id) ON DELETE RESTRICT,
      provider_account_id uuid NOT NULL,
      actor_id varchar(255) NOT NULL,
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )
    """)
    op.execute("""
    CREATE TABLE public.broker_asset_bindings (
      account_id varchar(255) NOT NULL REFERENCES public.broker_account_bindings(account_id)
        ON DELETE RESTRICT,
      instrument_id varchar(255) NOT NULL REFERENCES public.instruments(id)
        ON DELETE RESTRICT,
      provider_asset_id uuid NOT NULL,
      actor_id varchar(255) NOT NULL,
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY (account_id,instrument_id)
    )
    """)
    op.execute("""
    CREATE TABLE public.broker_binding_proofs (
      account_id varchar(255) NOT NULL,
      instrument_id varchar(255) NOT NULL,
      context_version integer NOT NULL CHECK (context_version > 0),
      trading_capability_version integer NOT NULL CHECK (trading_capability_version > 0),
      source_capability_version integer NOT NULL CHECK (source_capability_version > 0),
      provider_account_id uuid NOT NULL,
      provider_asset_id uuid NOT NULL,
      session_start timestamptz NOT NULL,
      session_end timestamptz NOT NULL,
      market_observed_at timestamptz NOT NULL,
      trade_observed_at timestamptz NOT NULL,
      market_provider_time timestamptz NOT NULL,
      trade_provider_time timestamptz NOT NULL,
      valid_until timestamptz NOT NULL,
      actor_id varchar(255) NOT NULL,
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$'),
      PRIMARY KEY (account_id,instrument_id,context_version),
      FOREIGN KEY (account_id,instrument_id)
        REFERENCES public.broker_asset_bindings(account_id,instrument_id) ON DELETE RESTRICT,
      CHECK (session_start < session_end),
      CHECK (valid_until > market_observed_at AND valid_until > trade_observed_at),
      CHECK (valid_until <= session_end)
    )
    """)
    op.execute("""
    CREATE FUNCTION public.broker_binding_require_audit() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE affected_id text; affected_version integer;
    BEGIN
      IF TG_RELID = 'public.broker_account_bindings'::regclass THEN
        affected_id := NEW.account_id || ':' || 'provider-account'; affected_version := 1;
      ELSIF TG_RELID = 'public.broker_asset_bindings'::regclass THEN
        affected_id := NEW.account_id || ':' || NEW.instrument_id || ':' || 'provider-asset';
        affected_version := 1;
      ELSIF TG_RELID = 'public.broker_binding_proofs'::regclass THEN
        affected_id := NEW.account_id || ':' || NEW.instrument_id || ':' || 'proof';
        affected_version := NEW.context_version;
      ELSE
        RAISE EXCEPTION 'invalid binding audit source';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM public.audit_events
        WHERE entity_id=affected_id AND transaction_id=txid_current()
          AND entity_version=affected_version AND actor_id=NEW.actor_id
          AND trace_id=NEW.trace_id) THEN
        RAISE EXCEPTION 'binding mutation requires matching same-transaction audit';
      END IF;
      RETURN NULL;
    END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.broker_binding_require_audit() FROM PUBLIC")
    for table in ("broker_account_bindings", "broker_asset_bindings", "broker_binding_proofs"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
            f"ON public.{table} FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.ledger_reject_mutation()"
        )
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {table}_audit AFTER INSERT ON public.{table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION public.broker_binding_require_audit()"
        )
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")
        op.execute(f"GRANT SELECT,INSERT ON public.{table} TO atl_execution")


def downgrade() -> None:
    raise RuntimeError("broker binding downgrade would destroy immutable provenance")
