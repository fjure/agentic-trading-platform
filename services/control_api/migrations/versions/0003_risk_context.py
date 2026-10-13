"""Versioned account risk context and explicit ownership of economic exposure.

Revision ID: 0003_risk_context
Revises: 0002_jobs_outbox
"""

from alembic import op

revision = "0003_risk_context"
down_revision = "0002_jobs_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE UNIQUE INDEX accounts_single_paper_idx ON public.accounts ((true))")
    op.execute("""
    CREATE FUNCTION public.risk_policy_account_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    BEGIN
      PERFORM id FROM public.accounts ORDER BY id FOR UPDATE;
      RETURN NEW;
    END $$
    """)
    op.execute("""
    CREATE TRIGGER risk_policy_account_gate BEFORE INSERT ON public.risk_policies
    FOR EACH ROW EXECUTE FUNCTION public.risk_policy_account_gate()
    """)
    # Old rows remain unassigned and therefore block risk; migration never guesses ownership.
    for table in ("positions", "risk_reservations", "order_intents"):
        op.execute(
            f"ALTER TABLE public.{table} ADD COLUMN account_id varchar(255) "
            "REFERENCES public.accounts(id) ON DELETE RESTRICT"
        )
        op.execute(f"CREATE INDEX {table}_account_idx ON public.{table} (account_id)")
    op.execute("""
    CREATE FUNCTION public.risk_ownership_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
    BEGIN
      IF OLD.account_id IS NOT NULL AND NEW.account_id IS DISTINCT FROM OLD.account_id THEN
        RAISE EXCEPTION 'economic ownership is immutable';
      END IF;
      RETURN NEW;
    END $$
    """)
    for table in ("positions", "risk_reservations", "order_intents"):
        op.execute(
            f"CREATE TRIGGER {table}_ownership BEFORE UPDATE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.risk_ownership_guard()"
        )
    op.execute("REVOKE ALL ON FUNCTION public.risk_ownership_guard() FROM PUBLIC")
    op.execute("""
    CREATE TABLE public.risk_account_context (
      account_id varchar(255) PRIMARY KEY REFERENCES public.accounts(id) ON DELETE RESTRICT,
      version integer NOT NULL CHECK (version > 0),
      portfolio_snapshot_id varchar(255) NOT NULL
        REFERENCES public.portfolio_snapshots(id) ON DELETE RESTRICT,
      portfolio_version integer NOT NULL CHECK (portfolio_version > 0),
      risk_config_version varchar(255) NOT NULL CHECK (length(risk_config_version) > 0),
      day_start_at timestamptz NOT NULL,
      day_start_equity numeric(38,18) NOT NULL CHECK (day_start_equity > 0),
      day_start_realized_pnl numeric(38,18) NOT NULL,
      mode varchar(20) NOT NULL CHECK (mode IN ('DISABLED','REPLAY','SHADOW','PAPER')),
      safety_state varchar(20) NOT NULL CHECK (safety_state IN ('RUNNING','PAUSE_NEW','HALTED')),
      reconciliation_current boolean NOT NULL DEFAULT false,
      valid_until timestamptz NOT NULL,
      actor_id varchar(255) NOT NULL CHECK (length(actor_id) > 0),
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$')
    )
    """)
    op.execute("""
    CREATE TABLE public.risk_instrument_context (
      account_id varchar(255) NOT NULL REFERENCES public.accounts(id) ON DELETE RESTRICT,
      instrument_id varchar(255) NOT NULL REFERENCES public.instruments(id) ON DELETE RESTRICT,
      version integer NOT NULL CHECK (version > 0),
      capability_version integer NOT NULL CHECK (capability_version > 0),
      broker_id varchar(255) NOT NULL,
      paper_confirmed boolean NOT NULL DEFAULT false,
      regular_session_confirmed boolean NOT NULL DEFAULT false,
      market_order_confirmed boolean NOT NULL DEFAULT false,
      limit_order_confirmed boolean NOT NULL DEFAULT false,
      fractional_confirmed boolean NOT NULL DEFAULT false,
      quantity_step numeric(38,18) NOT NULL CHECK (quantity_step > 0),
      price_tick numeric(38,18) CHECK (price_tick > 0),
      session_open boolean NOT NULL DEFAULT false,
      session_version varchar(255) NOT NULL CHECK (length(session_version) > 0),
      session_valid_until timestamptz NOT NULL,
      valid_until timestamptz NOT NULL,
      actor_id varchar(255) NOT NULL CHECK (length(actor_id) > 0),
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$'),
      PRIMARY KEY (account_id,instrument_id)
    )
    """)
    op.execute("""
    CREATE FUNCTION public.risk_context_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
    BEGIN
      IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'risk context version or identity violation';
      END IF;
      IF TG_RELID = 'public.risk_account_context'::regclass THEN
        IF NEW.account_id IS DISTINCT FROM OLD.account_id THEN
          RAISE EXCEPTION 'risk account identity violation';
        END IF;
      ELSIF TG_RELID = 'public.risk_instrument_context'::regclass THEN
        IF (NEW.account_id,NEW.instrument_id) IS DISTINCT FROM
           (OLD.account_id,OLD.instrument_id) THEN
          RAISE EXCEPTION 'risk instrument identity violation';
        END IF;
      ELSE
        RAISE EXCEPTION 'invalid risk context source';
      END IF;
      RETURN NEW;
    END $$
    """)
    op.execute("""
    CREATE FUNCTION public.risk_context_require_audit() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE affected_id text;
    BEGIN
      IF TG_RELID = 'public.risk_account_context'::regclass THEN
        affected_id := NEW.account_id;
      ELSIF TG_RELID = 'public.risk_instrument_context'::regclass THEN
        affected_id := NEW.account_id || ':' || NEW.instrument_id;
      ELSE
        RAISE EXCEPTION 'invalid risk context audit source';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM public.audit_events
        WHERE entity_id=affected_id AND transaction_id=txid_current()
          AND entity_version=NEW.version AND actor_id=NEW.actor_id
          AND trace_id=NEW.trace_id) THEN
        RAISE EXCEPTION 'risk context mutation requires matching same-transaction audit';
      END IF;
      RETURN NULL;
    END $$
    """)
    for table in ("risk_account_context", "risk_instrument_context"):
        op.execute(
            f"CREATE TRIGGER {table}_guard BEFORE UPDATE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.risk_context_guard()"
        )
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {table}_audit AFTER INSERT OR UPDATE ON public.{table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION public.risk_context_require_audit()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE OR TRUNCATE ON public.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.ledger_reject_mutation()"
        )
    op.execute(
        "REVOKE ALL ON public.risk_account_context, public.risk_instrument_context FROM PUBLIC"
    )
    op.execute("GRANT UPDATE (account_id) ON public.order_intents TO atl_execution")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON public.risk_account_context, "
        "public.risk_instrument_context TO atl_execution"
    )
    op.execute(
        "GRANT SELECT ON public.risk_account_context, "
        "public.risk_instrument_context TO atl_control_api, atl_watchdog"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.risk_context_guard(), "
        "public.risk_context_require_audit() FROM PUBLIC"
    )
    op.execute("REVOKE ALL ON FUNCTION public.risk_policy_account_gate() FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError("risk context downgrade would discard ownership and safety evidence")
