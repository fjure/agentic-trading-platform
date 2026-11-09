"""Research-only model attempt and quota ledger."""

from alembic import op

revision = "0038_agent_research_budget"
down_revision = "0037_agent_research_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.model_budget_policies (
      scope text PRIMARY KEY CHECK (scope ~ '^[a-z_]+:[0-9a-f]{64}$'),
      version bigint NOT NULL CHECK (version>0),
      currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
      max_cost numeric(38,18) NOT NULL CHECK (max_cost>=0 AND max_cost<'Infinity'::numeric),
      max_input_tokens bigint NOT NULL CHECK (max_input_tokens>=0),
      max_output_tokens bigint NOT NULL CHECK (max_output_tokens>=0),
      max_calls bigint NOT NULL CHECK (max_calls>=0),
      enabled boolean NOT NULL
    );
    CREATE TABLE agent_research.model_prices (
      model_binding jsonb PRIMARY KEY CHECK (jsonb_typeof(model_binding)='object'),
      version bigint NOT NULL CHECK (version>0),
      currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
      input_price numeric(38,18) NOT NULL CHECK (input_price>=0 AND input_price<'Infinity'),
      output_price numeric(38,18) NOT NULL CHECK (output_price>=0 AND output_price<'Infinity'),
      bounds_unconfirmed_cost boolean NOT NULL
    );
    CREATE TABLE agent_research.model_attempts (
      attempt_id uuid PRIMARY KEY,
      run_id uuid NOT NULL REFERENCES agent_research.runs(run_id),
      step_id text NOT NULL CHECK (step_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
      fence bigint NOT NULL CHECK (fence>0),
      run_version bigint NOT NULL CHECK (run_version>0),
      deadline_at timestamptz NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      period date NOT NULL,
      scopes text[] NOT NULL CHECK (cardinality(scopes) BETWEEN 6 AND 7),
      policy_versions bigint[] NOT NULL CHECK (cardinality(policy_versions)=cardinality(scopes)),
      pricing_version bigint NOT NULL CHECK (pricing_version>0),
      currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
      input_price numeric(38,18) NOT NULL
        CHECK (input_price>=0 AND input_price<'Infinity'::numeric),
      output_price numeric(38,18) NOT NULL
        CHECK (output_price>=0 AND output_price<'Infinity'::numeric),
      price_bounds_cost boolean NOT NULL,
      input_bound bigint NOT NULL CHECK (input_bound>0),
      output_bound bigint NOT NULL CHECK (output_bound>0),
      cost_bound numeric(38,18) NOT NULL CHECK (cost_bound>=0 AND cost_bound<'Infinity'::numeric),
      held_cost numeric(38,18) NOT NULL CHECK (held_cost>=0 AND held_cost<'Infinity'::numeric),
      held_input bigint NOT NULL CHECK (held_input>=0),
      held_output bigint NOT NULL CHECK (held_output>=0),
      reported_input bigint CHECK (reported_input>=0),
      reported_output bigint CHECK (reported_output>=0),
      estimated_cost numeric(38,18)
        CHECK (estimated_cost>=0 AND estimated_cost<'Infinity'::numeric),
      confirmed_cost numeric(38,18)
        CHECK (confirmed_cost>=0 AND confirmed_cost<'Infinity'::numeric),
      exceeded_bound boolean NOT NULL DEFAULT false,
      status text NOT NULL CHECK (status IN ('RESERVED','DISPATCH_AUTHORIZED',
        'OUTCOME_UNKNOWN','RESULT_UNAVAILABLE','UNSENT','COMPLETED')),
      result_ref text CHECK (result_ref ~ '^[A-Za-z0-9._:-]{1,128}$'),
      unsent_evidence_ref text CHECK (unsent_evidence_ref ~ '^[A-Za-z0-9._:-]{1,128}$'),
      CHECK (unsent_evidence_ref IS NULL OR status='UNSENT'),
      CHECK ((status='COMPLETED')=(result_ref IS NOT NULL)),
      CHECK ((reported_input IS NULL)=(reported_output IS NULL)),
      CHECK (deadline_at>created_at)
    );
    CREATE INDEX agent_model_attempt_scope ON agent_research.model_attempts USING gin (scopes);
    CREATE INDEX agent_model_attempt_run_step ON agent_research.model_attempts (run_id,step_id);
    CREATE TABLE agent_research.model_attempt_events (
      attempt_id uuid NOT NULL REFERENCES agent_research.model_attempts(attempt_id),
      status text NOT NULL,
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      exceeded_bound boolean NOT NULL,
      PRIMARY KEY (attempt_id,status)
    );
    CREATE TABLE agent_research.model_accounting_receipts (
      attempt_id uuid PRIMARY KEY REFERENCES agent_research.model_attempts(attempt_id),
      evidence_ref text NOT NULL CHECK (evidence_ref ~ '^[A-Za-z0-9._:-]{1,128}$'),
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      confirmed_cost numeric(38,18) NOT NULL
        CHECK (confirmed_cost>=0 AND confirmed_cost<'Infinity'::numeric)
    );
    REVOKE ALL ON agent_research.model_budget_policies,agent_research.model_attempts,
      agent_research.model_attempt_events,agent_research.model_accounting_receipts FROM PUBLIC;
    GRANT SELECT ON agent_research.model_budget_policies TO atl_agent_research;
    REVOKE ALL ON agent_research.model_prices FROM PUBLIC;
    GRANT SELECT ON agent_research.model_prices TO atl_agent_research;
    GRANT UPDATE (model_binding) ON agent_research.model_prices TO atl_agent_research;
    CREATE TRIGGER immutable_model_binding BEFORE UPDATE OF model_binding
      ON agent_research.model_prices FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    -- Row locks require UPDATE privilege on at least one column. The key is immutable
    -- to this role through the trigger below, and no budget fields are writable.
    GRANT UPDATE (scope) ON agent_research.model_budget_policies TO atl_agent_research;
    CREATE FUNCTION agent_research.reject_budget_scope_change() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $fn$
    BEGIN
      IF OLD.scope IS DISTINCT FROM NEW.scope THEN
        RAISE EXCEPTION 'immutable research budget scope';
      END IF;
      RETURN NEW;
    END $fn$;
    REVOKE ALL ON FUNCTION agent_research.reject_budget_scope_change() FROM PUBLIC;
    CREATE TRIGGER immutable_scope BEFORE UPDATE OF scope ON agent_research.model_budget_policies
      FOR EACH ROW EXECUTE FUNCTION agent_research.reject_budget_scope_change();
    GRANT SELECT,INSERT ON agent_research.model_attempts,agent_research.model_attempt_events,
      agent_research.model_accounting_receipts TO atl_agent_research;
    GRANT UPDATE (held_cost,held_input,held_output,reported_input,reported_output,
      estimated_cost,confirmed_cost,exceeded_bound,status,result_ref,unsent_evidence_ref)
      ON agent_research.model_attempts TO atl_agent_research;
    """)


def downgrade():
    op.execute("""
    DROP TABLE agent_research.model_accounting_receipts;
    DROP TABLE agent_research.model_attempt_events;
    DROP TABLE agent_research.model_attempts;
    DROP TABLE agent_research.model_prices;
    DROP TABLE agent_research.model_budget_policies;
    DROP FUNCTION agent_research.reject_budget_scope_change();
    """)
