"""Versioned, scoped research step and tool counters."""

from alembic import op

revision = "0040_agent_research_work_budget"
down_revision = "0039_agent_research_processing"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.work_budget_policies (
      scope text PRIMARY KEY CHECK (scope ~ '^[A-Za-z0-9._:-]{1,160}$'),
      version bigint NOT NULL CHECK (version>0),
      max_steps bigint NOT NULL CHECK (max_steps>=0),
      max_tool_calls bigint NOT NULL CHECK (max_tool_calls>=0),
      enabled boolean NOT NULL
    );
    CREATE TABLE agent_research.work_budget_usage (
      scope text NOT NULL REFERENCES agent_research.work_budget_policies(scope),
      period date NOT NULL,
      steps bigint NOT NULL CHECK (steps>=0),
      tool_calls bigint NOT NULL CHECK (tool_calls>=0),
      policy_version bigint NOT NULL CHECK (policy_version>0),
      PRIMARY KEY (scope,period)
    );
    -- Finite v1 technical ceilings. Schedule-specific policy is required before activation.
    INSERT INTO agent_research.work_budget_policies
      (scope,version,max_steps,max_tool_calls,enabled) VALUES
      ('global',1,2400,1600,true),
      ('agent:quant-research-agent-v1',1,2400,1600,true),
      ('agent_version:quant-research-agent-v1:1',1,2400,1600,true),
      ('work:DIALOGUE',1,1920,1280,true),
      ('work:SCHEDULED',1,240,160,true),
      ('work:REEVALUATION',1,240,160,true);

    CREATE FUNCTION agent_research.require_work_policy_revision() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $fn$
    BEGIN
      IF OLD.scope IS DISTINCT FROM NEW.scope OR NEW.version <= OLD.version THEN
        RAISE EXCEPTION 'research work budget policy version must increase';
      END IF;
      RETURN NEW;
    END $fn$;
    REVOKE ALL ON FUNCTION agent_research.require_work_policy_revision() FROM PUBLIC;
    CREATE TRIGGER work_policy_revision BEFORE UPDATE ON agent_research.work_budget_policies
      FOR EACH ROW EXECUTE FUNCTION agent_research.require_work_policy_revision();

    CREATE FUNCTION agent_research.charge_work(p_run_id uuid, p_owner uuid, p_fence bigint,
      p_version bigint, p_dimension text) RETURNS text
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,agent_research,pg_temp AS $fn$
    DECLARE
      run agent_research.runs%ROWTYPE;
      policy agent_research.work_budget_policies%ROWTYPE;
      scopes text[];
      run_period date;
      seen integer := 0;
      used bigint;
    BEGIN
      IF p_dimension IS NULL OR p_dimension NOT IN ('steps','tool_calls') THEN
        RETURN 'INVALID_BUDGET';
      END IF;
      IF current_setting('transaction_isolation') <> 'read committed' THEN
        RETURN 'BUDGET_POLICY_UNAVAILABLE';
      END IF;
      SELECT * INTO run FROM agent_research.runs WHERE run_id=p_run_id FOR UPDATE;
      IF NOT FOUND OR run.owner IS DISTINCT FROM p_owner OR run.fence IS DISTINCT FROM p_fence
        OR run.version IS DISTINCT FROM p_version
        OR run.state<>'RUNNING' OR run.deadline_at<=clock_timestamp()
        OR run.lease_until<=clock_timestamp() THEN
        RETURN 'LEASE_LOST';
      END IF;

      run_period := (run.created_at AT TIME ZONE 'UTC')::date;
      scopes := ARRAY[
        'global',
        'agent:' || (run.agent_binding->>'agent_id'),
        'agent_version:' || (run.agent_binding->>'agent_id') || ':' ||
          (run.agent_binding->>'version'),
        'work:' || run.kind
      ];
      IF run.kind='SCHEDULED' THEN
        scopes := array_append(scopes, 'schedule:' || run.input_ref);
      END IF;

      FOR policy IN
        SELECT * FROM agent_research.work_budget_policies
        WHERE scope=ANY(scopes) ORDER BY scope FOR UPDATE
      LOOP
        seen := seen+1;
        IF NOT policy.enabled THEN
          RETURN 'BUDGET_POLICY_UNAVAILABLE';
        END IF;
        SELECT CASE WHEN p_dimension='steps' THEN steps ELSE tool_calls END INTO used
        FROM agent_research.work_budget_usage WHERE scope=policy.scope AND period=run_period;
        IF coalesce(used,0) >= (CASE WHEN p_dimension='steps' THEN policy.max_steps
                                    ELSE policy.max_tool_calls END) THEN
          RETURN 'BUDGET_EXHAUSTED';
        END IF;
      END LOOP;
      IF seen<>cardinality(scopes) THEN
        RETURN 'BUDGET_POLICY_UNAVAILABLE';
      END IF;
      IF run.deadline_at<=clock_timestamp() OR run.lease_until<=clock_timestamp() THEN
        RETURN 'LEASE_LOST';
      END IF;

      INSERT INTO agent_research.work_budget_usage AS ledger
        (scope,period,steps,tool_calls,policy_version)
      SELECT scope,run_period,(p_dimension='steps')::integer,
        (p_dimension='tool_calls')::integer,version
      FROM agent_research.work_budget_policies WHERE scope=ANY(scopes) ORDER BY scope
      ON CONFLICT (scope,period) DO UPDATE SET steps=ledger.steps+excluded.steps,
        tool_calls=ledger.tool_calls+excluded.tool_calls,policy_version=excluded.policy_version;
      RETURN NULL;
    END $fn$;
    REVOKE ALL ON agent_research.work_budget_policies,agent_research.work_budget_usage FROM PUBLIC;
    REVOKE ALL ON FUNCTION agent_research.charge_work(uuid,uuid,bigint,bigint,text) FROM PUBLIC;
    GRANT SELECT ON agent_research.work_budget_policies,agent_research.work_budget_usage
      TO atl_agent_research;
    GRANT EXECUTE ON FUNCTION agent_research.charge_work(uuid,uuid,bigint,bigint,text)
      TO atl_agent_research;
    """)


def downgrade():
    op.execute("""
    DROP FUNCTION agent_research.charge_work(uuid,uuid,bigint,bigint,text);
    DROP TABLE agent_research.work_budget_usage;
    DROP TABLE agent_research.work_budget_policies;
    DROP FUNCTION agent_research.require_work_policy_revision();
    """)
