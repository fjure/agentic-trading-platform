"""Forward-only explicit-error guard for unmaterialized human commands."""

from alembic import op

revision = "0018_paper_human_finish_guard"
down_revision = "0017_paper_human_orders"
branch_labels = None
depends_on = None


def upgrade():
    # CREATE OR REPLACE preserves the existing, exact Execution-only ACL.
    # Keep this definition self-contained: migration17 may already be applied.
    op.execute("""
    CREATE OR REPLACE FUNCTION public.paper_finish(
      p_id uuid,p_owner uuid,p_fence bigint,p_public jsonb,p_private uuid,p_error text
    ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,public,pg_temp AS $$
    BEGIN
      IF p_error IS NULL AND EXISTS(SELECT 1 FROM public.paper_commands WHERE id=p_id
        AND operation_type='PLACE_LIMIT_ORDER') THEN RETURN false; END IF;
      IF EXISTS(SELECT 1 FROM public.paper_human_requests WHERE operation_id=p_id)
        THEN RETURN false; END IF;
      RETURN public.paper_finish_v16(p_id,p_owner,p_fence,p_public,p_private,p_error);
    END;
    $$;
    REVOKE ALL ON FUNCTION public.paper_finish(uuid,uuid,bigint,jsonb,uuid,text) FROM PUBLIC;
    """)
