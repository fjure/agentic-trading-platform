"""Expose only a consumed Quant approval's durable decision timestamp."""

from alembic import op

revision = "0051_quant_started_receipt"
down_revision = "0050_agent_control_commands"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION agent_research.quant_started_receipt(p_actor text,p_approval uuid,p_batch uuid)
      RETURNS timestamptz LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $$
      SELECT consumed_at FROM agent_research.quant_approvals
        WHERE p_actor='local-operator' AND actor=p_actor AND approval_id=p_approval
          AND batch_id=p_batch AND consumed_at IS NOT NULL
    $$;
    REVOKE ALL ON FUNCTION agent_research.quant_started_receipt(text,uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.quant_started_receipt(text,uuid,uuid)
      TO atl_agent_quant_admission;
    """)


def downgrade():
    op.execute("DROP FUNCTION agent_research.quant_started_receipt(text,uuid,uuid)")
