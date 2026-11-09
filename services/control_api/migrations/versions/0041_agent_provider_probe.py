"""Give synthetic provider probes a distinct internal work class."""

from alembic import op

revision = "0041_agent_provider_probe"
down_revision = "0040_agent_research_work_budget"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE agent_research.runs DROP CONSTRAINT runs_kind_check;
    ALTER TABLE agent_research.runs ADD CONSTRAINT runs_kind_check
      CHECK (kind IN ('DIALOGUE','SCHEDULED','REEVALUATION','PROVIDER_PROBE'));
    INSERT INTO agent_research.work_budget_policies
      (scope,version,max_steps,max_tool_calls,enabled)
      VALUES ('work:PROVIDER_PROBE',1,20,0,true);
    """)


def downgrade():
    op.execute("""
    ALTER TABLE agent_research.runs DROP CONSTRAINT runs_kind_check;
    ALTER TABLE agent_research.runs ADD CONSTRAINT runs_kind_check
      CHECK (kind IN ('DIALOGUE','SCHEDULED','REEVALUATION'));
    DELETE FROM agent_research.work_budget_policies WHERE scope='work:PROVIDER_PROBE';
    """)
