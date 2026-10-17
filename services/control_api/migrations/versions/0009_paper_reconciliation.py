"""Append-only private Paper accounting evidence and recovery receipts.

Revision ID: 0009_paper_reconciliation
Revises: 0008_broker_binding
"""

from alembic import op

revision = "0009_paper_reconciliation"
down_revision = "0008_broker_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE public.paper_reconciliation_baselines (
      id varchar(255) UNIQUE NOT NULL,
      account_id varchar(255) PRIMARY KEY REFERENCES public.accounts(id),
      provider_account_id uuid NOT NULL,
      portfolio_snapshot_id varchar(255) NOT NULL REFERENCES public.portfolio_snapshots(id),
      portfolio_version integer NOT NULL CHECK (portfolio_version>0),
      baseline_at timestamptz NOT NULL,
      fee_authority_profile varchar(255),
      positions jsonb NOT NULL,
      actor_id varchar(255) NOT NULL,
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$')
    )
    """)
    op.execute("""
    CREATE TABLE public.paper_activity_evidence (
      id varchar(255) PRIMARY KEY,
      account_id varchar(255) NOT NULL REFERENCES public.paper_reconciliation_baselines(account_id),
      activity_id varchar(255) NOT NULL,
      activity_kind varchar(32) NOT NULL,
      evidence_hash varchar(64) NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
      evidence jsonb NOT NULL,
      order_id varchar(255) REFERENCES public.broker_orders(id),
      occurred_at timestamptz,
      activity_date date,
      cash_delta numeric(38,18) NOT NULL,
      instrument_id varchar(255),
      quantity_delta numeric(38,18) NOT NULL,
      actor_id varchar(255) NOT NULL,
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$'),
      UNIQUE(account_id,activity_id)
    )
    """)
    op.execute("""
    CREATE TABLE public.paper_reconciliation_receipts (
      id varchar(255) PRIMARY KEY,
      account_id varchar(255) NOT NULL REFERENCES public.paper_reconciliation_baselines(account_id),
      owner_id varchar(255) NOT NULL,
      fencing_token integer NOT NULL CHECK (fencing_token>0),
      portfolio_snapshot_id varchar(255) NOT NULL REFERENCES public.portfolio_snapshots(id),
      portfolio_version integer NOT NULL CHECK (portfolio_version>0),
      context_version integer NOT NULL CHECK (context_version>0),
      ready boolean NOT NULL,
      reason_code varchar(255) NOT NULL,
      observed_at timestamptz NOT NULL,
      actor_id varchar(255) NOT NULL,
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$')
    )
    """)
    op.execute("""
    CREATE TABLE public.paper_order_accounting_bases (
      id varchar(255) PRIMARY KEY,
      account_id varchar(255) NOT NULL REFERENCES public.paper_reconciliation_baselines(account_id),
      order_id varchar(255) UNIQUE NOT NULL REFERENCES public.broker_orders(id),
      reservation_id varchar(255) UNIQUE NOT NULL REFERENCES public.risk_reservations(id),
      original_notional numeric(38,18) NOT NULL CHECK (original_notional>=0),
      requested_quantity numeric(38,18) NOT NULL CHECK (requested_quantity>0),
      actor_id varchar(255) NOT NULL,
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$')
    )
    """)
    op.execute("""
    CREATE FUNCTION public.paper_reconciliation_require_audit() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM public.audit_events
        WHERE entity_id=NEW.id AND entity_version=1
          AND transaction_id=txid_current() AND actor_id=NEW.actor_id AND trace_id=NEW.trace_id)
      THEN RAISE EXCEPTION 'paper evidence requires same-transaction audit'; END IF;
      RETURN NULL;
    END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.paper_reconciliation_require_audit() FROM PUBLIC")
    for table in (
        "paper_reconciliation_baselines",
        "paper_activity_evidence",
        "paper_reconciliation_receipts",
        "paper_order_accounting_bases",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
            f"ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.ledger_reject_mutation()"
        )
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {table}_audit AFTER INSERT ON public.{table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION public.paper_reconciliation_require_audit()"
        )
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")
        op.execute(f"GRANT SELECT,INSERT ON public.{table} TO atl_execution")


def downgrade() -> None:
    raise RuntimeError("paper evidence downgrade would destroy immutable accounting evidence")
