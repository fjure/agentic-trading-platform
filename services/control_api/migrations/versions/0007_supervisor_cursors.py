"""Durable opaque supervisor cursor receipts.

Revision ID: 0007_supervisor_cursors
Revises: 0006_control_http
"""

from alembic import op

revision = "0007_supervisor_cursors"
down_revision = "0006_control_http"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing runs predate role/start evidence. New generic worker runs acquire
    # both fields at their first durable insert; old rows remain unknown.
    op.execute("ALTER TABLE public.agent_runs ADD COLUMN role varchar(32)")
    op.execute("ALTER TABLE public.agent_runs ADD COLUMN started_at timestamptz")
    op.execute("ALTER TABLE public.agent_runs ALTER COLUMN role SET DEFAULT 'WORKER'")
    op.execute(
        "ALTER TABLE public.agent_runs ALTER COLUMN started_at SET DEFAULT clock_timestamp()"
    )
    op.execute("""
    CREATE FUNCTION public.supervisor_run_metadata_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
    BEGIN
      IF (NEW.role,NEW.started_at) IS DISTINCT FROM (OLD.role,OLD.started_at) THEN
        RAISE EXCEPTION 'run provenance is immutable';
      END IF;
      RETURN NEW;
    END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.supervisor_run_metadata_guard() FROM PUBLIC")
    op.execute("""
    CREATE TRIGGER agent_runs_supervisor_provenance BEFORE UPDATE ON public.agent_runs
      FOR EACH ROW EXECUTE FUNCTION public.supervisor_run_metadata_guard()
    """)
    op.execute("""
    CREATE TABLE public.supervisor_entity_owners (
      entity_type varchar(40) NOT NULL,
      entity_id varchar(255) NOT NULL,
      account_id varchar(255) NOT NULL REFERENCES public.accounts(id) ON DELETE RESTRICT,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      created_txid bigint NOT NULL DEFAULT txid_current(),
      PRIMARY KEY (entity_type,entity_id)
    )
    """)
    op.execute("""
    CREATE TRIGGER supervisor_entity_owners_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
      ON public.supervisor_entity_owners FOR EACH STATEMENT
      EXECUTE FUNCTION public.ledger_reject_mutation()
    """)
    op.execute("""
    CREATE FUNCTION public.supervisor_assign_owner() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE p_account varchar(255);
    BEGIN
      IF TG_OP NOT IN ('INSERT','UPDATE') OR TG_TABLE_SCHEMA <> 'public' OR
         TG_TABLE_NAME NOT IN ('market_states','decisions','trade_proposals',
                               'risk_decisions','agent_runs','audit_events','order_intents') THEN
        RAISE EXCEPTION 'invalid supervisor ownership source';
      END IF;
      IF TG_TABLE_NAME='order_intents' THEN
        p_account := NEW.account_id;
      ELSIF (SELECT count(*) FROM public.accounts) = 1 THEN
        SELECT id INTO p_account FROM public.accounts;
      END IF;
      IF p_account IS NOT NULL THEN
        INSERT INTO public.supervisor_entity_owners(entity_type,entity_id,account_id)
        VALUES (TG_TABLE_NAME,NEW.id,p_account) ON CONFLICT DO NOTHING;
      END IF;
      RETURN NEW;
    END $$
    """)
    for table in (
        "market_states",
        "decisions",
        "trade_proposals",
        "risk_decisions",
        "agent_runs",
        "audit_events",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_supervisor_owner AFTER INSERT ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.supervisor_assign_owner()"
        )
    op.execute("REVOKE ALL ON FUNCTION public.supervisor_assign_owner() FROM PUBLIC")
    op.execute("""
    CREATE TRIGGER order_intents_supervisor_owner AFTER INSERT OR UPDATE OF account_id
      ON public.order_intents FOR EACH ROW EXECUTE FUNCTION public.supervisor_assign_owner()
    """)
    # Existing explicit intent ownership is evidence; NULL ownership is not guessed.
    op.execute("""
    INSERT INTO public.supervisor_entity_owners(entity_type,entity_id,account_id)
      SELECT 'order_intents',id,account_id FROM public.order_intents
      WHERE account_id IS NOT NULL
    """)
    op.execute("REVOKE ALL ON public.supervisor_entity_owners FROM PUBLIC")
    op.execute("GRANT SELECT ON public.supervisor_entity_owners TO atl_control_api")

    op.execute("""
    CREATE TABLE public.supervisor_cursors (
      token varchar(45) PRIMARY KEY CHECK (token ~ '^c_[A-Za-z0-9_-]{43}$'),
      account_id varchar(255) NOT NULL REFERENCES public.accounts(id) ON DELETE RESTRICT,
      kind varchar(12) NOT NULL CHECK (kind IN ('events','page')),
      position bigint NOT NULL CHECK (position >= 0),
      context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(context)='object'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      expires_at timestamptz NOT NULL DEFAULT (clock_timestamp() + interval '7 days'),
      CHECK (expires_at > created_at)
    )
    """)
    op.execute("""
    CREATE TRIGGER supervisor_cursors_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
      ON public.supervisor_cursors FOR EACH STATEMENT
      EXECUTE FUNCTION public.ledger_reject_mutation()
    """)
    op.execute(
        "CREATE INDEX supervisor_cursors_expiry_idx ON public.supervisor_cursors (expires_at)"
    )
    op.execute("REVOKE ALL ON public.supervisor_cursors FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT ON public.supervisor_cursors TO atl_control_api")


def downgrade() -> None:
    raise RuntimeError("supervisor cursor downgrade would destroy replay receipts")
