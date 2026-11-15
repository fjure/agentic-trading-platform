"""Immutable prepared and consumed operator approvals for a synthetic smoke attempt."""

from alembic import op

revision = "0058_provider_smoke"
down_revision = "0057_model_budget_periods"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.provider_smoke_grants (
      grant_id uuid PRIMARY KEY,
      actor text NOT NULL CHECK (actor ~ '^[A-Za-z0-9._:-]{1,128}$'),
      binding_digest text NOT NULL REFERENCES agent_research.provider_bindings(digest),
      epoch bigint NOT NULL CHECK (epoch > 0),
      policy_sha256 text NOT NULL CHECK (policy_sha256 ~ '^[a-f0-9]{64}$'),
      wire_sha256 text NOT NULL CHECK (wire_sha256 ~ '^[a-f0-9]{64}$'),
      budget_binding_sha256 text NOT NULL CHECK (budget_binding_sha256 ~ '^[a-f0-9]{64}$'),
      commitment text NOT NULL CHECK (commitment ~ '^[a-f0-9]{64}$'),
      maximum_cost numeric(30,18) NOT NULL CHECK (maximum_cost >= 0),
      operator_limit numeric(30,18) NOT NULL CHECK (operator_limit >= 0),
      tariff_revision bigint NOT NULL CHECK (tariff_revision > 0),
      currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
      input_bound_kind text CHECK (input_bound_kind IN ('REQUEST','MODEL')),
      expires_at timestamptz NOT NULL,
      run_id uuid UNIQUE REFERENCES agent_research.runs(run_id),
      consumed boolean NOT NULL DEFAULT false,
      CHECK (NOT consumed OR run_id IS NOT NULL)
    );
    REVOKE ALL ON agent_research.provider_smoke_grants FROM PUBLIC;
    GRANT SELECT, INSERT ON agent_research.provider_smoke_grants TO atl_agent_research;
    GRANT UPDATE (run_id,consumed) ON agent_research.provider_smoke_grants TO atl_agent_research;
    """)


def downgrade():
    op.execute("DROP TABLE agent_research.provider_smoke_grants")
