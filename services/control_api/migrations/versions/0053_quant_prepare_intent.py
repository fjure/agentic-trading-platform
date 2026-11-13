"""Persist the version binding for operator-reviewed Quant preparation."""

from alembic import op

revision = "0053_quant_prepare_intent"
down_revision = "0052_quant_prepare_read"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.quant_prepare_intents (
      run_id uuid NOT NULL UNIQUE REFERENCES agent_research.runs(run_id),
      dialogue_id text NOT NULL,
      dialogue_version bigint NOT NULL,
      idea_id text NOT NULL,
      idea_version bigint NOT NULL,
      template_id uuid NOT NULL REFERENCES backtest.experiment_definitions(definition_id),
      template_version integer NOT NULL CHECK(template_version>0),
      template_hash text NOT NULL CHECK(template_hash ~ '^[a-f0-9]{64}$'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(idea_id,idea_version),
      FOREIGN KEY(dialogue_id,idea_id)
        REFERENCES agent_research.companion_dialogues(dialogue_id,idea_id),
      FOREIGN KEY(dialogue_id,dialogue_version)
        REFERENCES agent_research.companion_dialogue_versions(dialogue_id,version),
      FOREIGN KEY(idea_id,idea_version)
        REFERENCES agent_research.companion_idea_versions(idea_id,version)
    );
    REVOKE ALL ON agent_research.quant_prepare_intents FROM PUBLIC;
    GRANT SELECT,INSERT ON agent_research.quant_prepare_intents TO atl_agent_research;
    """)


def downgrade():
    raise RuntimeError("Stop and drain Quant preparation before migrating")
