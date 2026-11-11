"""Persist isolated discovery schedules, immutable revisions and daily claims."""

from alembic import op

revision = "0045_discovery_schedules"
down_revision = "0044_quant_operator_approval"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.discovery_schedules (
      schedule_id text PRIMARY KEY CHECK (schedule_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      version bigint NOT NULL CHECK (version>0),
      epoch bigint NOT NULL CHECK (epoch>0),
      state text NOT NULL CHECK (state IN ('DISABLED','ACTIVE','PAUSED')),
      configuration jsonb NOT NULL CHECK (jsonb_typeof(configuration)='object'),
      active_since timestamptz,
      CHECK ((state='ACTIVE')=(active_since IS NOT NULL))
    );
    CREATE TABLE agent_research.discovery_schedule_events (
      schedule_id text NOT NULL REFERENCES agent_research.discovery_schedules,
      version bigint NOT NULL CHECK (version>0),
      epoch bigint NOT NULL CHECK (epoch>0),
      state text NOT NULL CHECK (state IN ('DISABLED','ACTIVE','PAUSED')),
      configuration jsonb NOT NULL CHECK (jsonb_typeof(configuration)='object'),
      action text NOT NULL CHECK (action IN ('CREATE','UPDATE','PAUSE','RESUME')),
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY (schedule_id,version),
      UNIQUE (schedule_id,version,epoch)
    );
    CREATE TABLE agent_research.discovery_occurrences (
      schedule_id text NOT NULL REFERENCES agent_research.discovery_schedules,
      local_date date NOT NULL,
      scheduled_at timestamptz NOT NULL,
      schedule_version bigint NOT NULL,
      schedule_epoch bigint NOT NULL,
      resolution text NOT NULL CHECK (resolution IN ('EXACT','GAP_FORWARD','FOLD_EARLIER')),
      run_id uuid NOT NULL UNIQUE REFERENCES agent_research.runs,
      PRIMARY KEY (schedule_id,local_date),
      FOREIGN KEY (schedule_id,schedule_version,schedule_epoch)
        REFERENCES agent_research.discovery_schedule_events (schedule_id,version,epoch)
    );
    CREATE FUNCTION agent_research.lock_discovery_schedule(p_schedule_id text)
    RETURNS SETOF agent_research.discovery_schedules
    LANGUAGE sql VOLATILE SECURITY DEFINER
    SET search_path=pg_catalog,agent_research
    ROWS 1
    AS $$
      SELECT * FROM agent_research.discovery_schedules
      WHERE schedule_id=p_schedule_id FOR UPDATE
    $$;
    REVOKE ALL ON FUNCTION agent_research.lock_discovery_schedule(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.lock_discovery_schedule(text) TO atl_agent_research;
    GRANT SELECT ON agent_research.discovery_schedules,agent_research.discovery_schedule_events
      TO atl_agent_research;
    GRANT SELECT,INSERT ON agent_research.discovery_occurrences TO atl_agent_research;
    """)


def downgrade():
    op.execute("""
    DROP FUNCTION agent_research.lock_discovery_schedule(text);
    DROP TABLE agent_research.discovery_occurrences;
    DROP TABLE agent_research.discovery_schedule_events;
    DROP TABLE agent_research.discovery_schedules;
    """)
