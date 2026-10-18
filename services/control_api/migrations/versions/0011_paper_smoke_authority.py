"""Execution-only smoke receipts with a narrow Worker-readable projection."""

from alembic import op

revision = "0011_paper_smoke_authority"
down_revision = "0010_runtime_market_grants"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION public.paper_smoke_audit_authority() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
        BEGIN
          IF NEW.action LIKE 'paper_smoke.%' AND current_user <> 'atl_execution' THEN
            RAISE EXCEPTION 'Execution role required for Paper smoke authority';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER paper_smoke_audit_authority BEFORE INSERT ON public.audit_events
        FOR EACH ROW EXECUTE FUNCTION public.paper_smoke_audit_authority()
    """)
    op.execute("REVOKE ALL ON FUNCTION public.paper_smoke_audit_authority() FROM PUBLIC")
    op.execute("""
        CREATE VIEW public.paper_smoke_authorizations WITH (security_barrier=true) AS
        SELECT id,occurred_at,actor_id,action,reason_codes,entity_id,trace_id,outcome
        FROM public.audit_events WHERE action='paper_smoke.authorize'
    """)
    op.execute("GRANT SELECT ON public.paper_smoke_authorizations TO atl_worker")


def downgrade():
    raise RuntimeError("Paper smoke authority is forward-only")
