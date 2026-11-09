"""Durable consent and dispatch status without storing the sendable projection."""

from alembic import op

revision = "0039_agent_research_processing"
down_revision = "0038_agent_research_budget"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.processing_grants (
      receipt_id uuid PRIMARY KEY,
      run_id uuid NOT NULL REFERENCES agent_research.runs (run_id),
      preview_id uuid NOT NULL UNIQUE,
      actor text NOT NULL CHECK (length(actor) BETWEEN 1 AND 128),
      key_hash text NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
      request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
      commitment text NOT NULL CHECK (commitment ~ '^[0-9a-f]{64}$'),
      binding jsonb NOT NULL CHECK (jsonb_typeof(binding)='object'),
      created_at timestamptz NOT NULL,
      expires_at timestamptz NOT NULL CHECK (expires_at>created_at),
      revision bigint NOT NULL DEFAULT 1 CHECK (revision>0),
      state text NOT NULL CHECK (state IN ('AUTHORIZED','DISPATCHED','ACCEPTED','REVOKED',
        'UNAVAILABLE')),
      attempt_id uuid UNIQUE,
      dispatch_fence bigint CHECK (dispatch_fence>0),
      dispatched_at timestamptz,
      result_ref text CHECK (result_ref ~ '^[A-Za-z0-9._:-]{1,128}$'),
      UNIQUE (actor,run_id,key_hash),
      CHECK ((attempt_id IS NULL)=(dispatch_fence IS NULL)),
      CHECK ((attempt_id IS NULL)=(dispatched_at IS NULL)),
      CHECK (state NOT IN ('DISPATCHED','ACCEPTED') OR attempt_id IS NOT NULL),
      CHECK (state NOT IN ('AUTHORIZED','UNAVAILABLE') OR attempt_id IS NULL),
      CHECK (state<>'ACCEPTED' OR result_ref IS NOT NULL),
      CHECK (result_ref IS NULL OR state IN ('ACCEPTED','REVOKED'))
    );
    CREATE TABLE agent_research.processing_events (
      receipt_id uuid NOT NULL REFERENCES agent_research.processing_grants (receipt_id),
      revision bigint NOT NULL CHECK (revision>0),
      state text NOT NULL CHECK (state IN ('AUTHORIZED','DISPATCHED','ACCEPTED','REVOKED',
        'UNAVAILABLE')),
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY (receipt_id,revision)
    );
    REVOKE ALL ON agent_research.processing_grants,agent_research.processing_events FROM PUBLIC;
    GRANT SELECT,INSERT ON agent_research.processing_grants,agent_research.processing_events
      TO atl_agent_research;
    GRANT UPDATE (revision,state,attempt_id,dispatch_fence,dispatched_at,result_ref)
      ON agent_research.processing_grants TO atl_agent_research;
    """)


def downgrade():
    op.execute("""
    DROP TABLE agent_research.processing_events;
    DROP TABLE agent_research.processing_grants;
    """)
