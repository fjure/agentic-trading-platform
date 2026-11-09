"""Isolated agent runs and append-only audit, with no financial grants."""

from alembic import op

revision = "0037_agent_research_runs"
down_revision = "0036_quant_validation_attempts"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='atl_agent_research') THEN
        CREATE ROLE atl_agent_research NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
      END IF;
    END $$;
    CREATE SCHEMA agent_research;
    REVOKE ALL ON SCHEMA agent_research FROM PUBLIC;
    CREATE TABLE agent_research.runs (
      run_id uuid PRIMARY KEY,
      actor text NOT NULL CHECK (length(actor) BETWEEN 1 AND 128),
      request_key_hash text NOT NULL CHECK (request_key_hash ~ '^[0-9a-f]{64}$'),
      request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
      kind text NOT NULL CHECK (kind IN ('DIALOGUE','SCHEDULED','REEVALUATION')),
      input_ref text NOT NULL CHECK (input_ref ~ '^[A-Za-z0-9._:-]{1,128}$'),
      trace_id text NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$'),
      agent_binding jsonb NOT NULL CHECK (jsonb_typeof(agent_binding)='object'),
      state text NOT NULL CHECK (state IN ('QUEUED','RUNNING','WAITING_FOR_INPUT',
        'WAITING_FOR_PROCESSING_DECISION','WAITING_FOR_APPROVAL','WAITING_FOR_BATCH',
        'COMPLETED','BLOCKED','FAILED','CANCELLED','TIMED_OUT','BUDGET_EXHAUSTED')),
      version bigint NOT NULL DEFAULT 1 CHECK (version>0),
      fence bigint NOT NULL DEFAULT 0 CHECK (fence>=0),
      owner uuid,
      claimed_at timestamptz,
      lease_until timestamptz,
      created_at timestamptz NOT NULL,
      updated_at timestamptz NOT NULL,
      deadline_at timestamptz NOT NULL CHECK (deadline_at>created_at),
      checkpoint_ref text CHECK (checkpoint_ref ~ '^[A-Za-z0-9._:-]{1,128}$'),
      step_limit integer NOT NULL CHECK (step_limit BETWEEN 1 AND 1000),
      steps integer NOT NULL DEFAULT 0 CHECK (steps BETWEEN 0 AND step_limit),
      tool_limit integer NOT NULL CHECK (tool_limit BETWEEN 1 AND 1000),
      tool_calls integer NOT NULL DEFAULT 0 CHECK (tool_calls BETWEEN 0 AND tool_limit),
      active_limit_ms integer NOT NULL CHECK (active_limit_ms BETWEEN 1 AND 86400000),
      active_ms integer NOT NULL DEFAULT 0 CHECK (active_ms BETWEEN 0 AND active_limit_ms),
      CHECK ((state='RUNNING')=(owner IS NOT NULL AND lease_until IS NOT NULL)),
      CHECK ((owner IS NULL)=(lease_until IS NULL)),
      CHECK ((owner IS NULL)=(claimed_at IS NULL)),
      UNIQUE (actor,kind,request_key_hash)
    );
    CREATE INDEX agent_run_ready ON agent_research.runs (created_at,run_id)
      WHERE state IN ('QUEUED','RUNNING');
    CREATE INDEX agent_run_deadline ON agent_research.runs (deadline_at)
      WHERE state IN ('QUEUED','RUNNING','WAITING_FOR_INPUT','WAITING_FOR_PROCESSING_DECISION',
        'WAITING_FOR_APPROVAL','WAITING_FOR_BATCH');
    CREATE TABLE agent_research.run_events (
      run_id uuid NOT NULL REFERENCES agent_research.runs (run_id),
      version bigint NOT NULL CHECK (version>0),
      fence bigint NOT NULL CHECK (fence>=0),
      state text NOT NULL,
      occurred_at timestamptz NOT NULL,
      PRIMARY KEY (run_id,version)
    );
    CREATE TABLE agent_research.tool_receipts (
      run_id uuid NOT NULL REFERENCES agent_research.runs (run_id),
      step_id text NOT NULL CHECK (step_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      tool_id text NOT NULL CHECK (tool_id IN ('RESEARCH_READ','QUANT_READ','IDEA_MANAGE',
        'QUANT_PREPARE')),
      tool_version integer NOT NULL CHECK (tool_version>0),
      input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
      result_ref text NOT NULL CHECK (result_ref ~ '^[A-Za-z0-9._:-]{1,128}$'),
      fence bigint NOT NULL CHECK (fence>0),
      occurred_at timestamptz NOT NULL,
      PRIMARY KEY (run_id,step_id,tool_id,tool_version)
    );
    CREATE TABLE agent_research.tool_denials (
      run_id uuid NOT NULL REFERENCES agent_research.runs (run_id),
      fence bigint NOT NULL CHECK (fence>0),
      step_id text NOT NULL CHECK (step_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      code text NOT NULL CHECK (code IN ('AGENT_POLICY_DENIED','IDEMPOTENCY_CONFLICT',
        'BUDGET_EXHAUSTED','BUDGET_POLICY_UNAVAILABLE','INVALID_TOOL_RESULT')),
      occurred_at timestamptz NOT NULL,
      PRIMARY KEY (run_id,fence,step_id,code)
    );
    REVOKE ALL ON ALL TABLES IN SCHEMA agent_research FROM PUBLIC;
    GRANT USAGE ON SCHEMA agent_research TO atl_agent_research;
    GRANT SELECT,INSERT ON agent_research.runs,agent_research.run_events,
      agent_research.tool_receipts,agent_research.tool_denials TO atl_agent_research;
    GRANT UPDATE (state,version,fence,owner,claimed_at,lease_until,updated_at,checkpoint_ref,
      steps,tool_calls,active_ms)
      ON agent_research.runs TO atl_agent_research;
    """)


def downgrade():
    op.execute("""
    DROP TABLE agent_research.tool_denials;
    DROP TABLE agent_research.tool_receipts;
    DROP TABLE agent_research.run_events;
    DROP TABLE agent_research.runs;
    DROP SCHEMA agent_research;
    """)
