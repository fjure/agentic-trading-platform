"""Store redacted, actor-scoped processing operation markers and local decisions."""

from alembic import op

revision = "0049_processing_operations"
down_revision = "0048_quant_report_reader"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.processing_operations (
      actor text NOT NULL CHECK (length(actor) BETWEEN 1 AND 128),
      dialogue_id text NOT NULL,
      turn_id text NOT NULL,
      operation text NOT NULL CHECK (operation IN ('PREVIEW','DECISION')),
      key_hash text NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
      request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
      decision jsonb CHECK (jsonb_typeof(decision)='object'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY (actor,operation,key_hash),
      FOREIGN KEY (dialogue_id,turn_id)
        REFERENCES agent_research.companion_originals (dialogue_id,turn_id),
      CHECK ((operation='PREVIEW')=(decision IS NULL))
    );
    CREATE INDEX processing_operations_turn
      ON agent_research.processing_operations (actor,dialogue_id,turn_id,created_at DESC)
      WHERE operation='DECISION';
    REVOKE ALL ON agent_research.processing_operations FROM PUBLIC;
    GRANT SELECT,INSERT ON agent_research.processing_operations TO atl_agent_research;
    """)


def downgrade():
    op.execute("DROP TABLE agent_research.processing_operations")
