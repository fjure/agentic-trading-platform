"""Bind provider reservations to evidence and the UTC send period."""

from alembic import op

revision = "0057_model_budget_periods"
down_revision = "0056_provider_authority"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE agent_research.model_budget_policies
      ADD COLUMN max_parallelism bigint CHECK (max_parallelism>=0);
    ALTER TABLE agent_research.model_attempts
      DROP CONSTRAINT model_attempts_scopes_check,
      ADD COLUMN evidence_revision bigint CHECK (evidence_revision>0),
      ADD COLUMN authority_hash text CHECK (authority_hash ~ '^[0-9a-f]{64}$'),
      ADD COLUMN send_period date,
      ADD CONSTRAINT model_attempts_scopes_check
        CHECK (cardinality(scopes) BETWEEN 6 AND 10),
      ADD CONSTRAINT model_attempts_authority_pair
        CHECK ((evidence_revision IS NULL)=(authority_hash IS NULL)),
      ADD CONSTRAINT model_attempts_send_period
        CHECK (send_period IS NULL OR (evidence_revision IS NOT NULL AND send_period=period));
    GRANT UPDATE (send_period) ON agent_research.model_attempts TO atl_agent_research;
    CREATE FUNCTION agent_research.pin_model_send_period() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $fn$
    BEGIN
      IF OLD.send_period IS DISTINCT FROM NEW.send_period AND NOT (
        OLD.send_period IS NULL AND NEW.send_period IS NOT NULL
        AND OLD.status='RESERVED' AND NEW.status='DISPATCH_AUTHORIZED'
      ) THEN
        RAISE EXCEPTION 'immutable research model send period';
      END IF;
      RETURN NEW;
    END $fn$;
    REVOKE ALL ON FUNCTION agent_research.pin_model_send_period() FROM PUBLIC;
    CREATE TRIGGER immutable_model_send_period BEFORE UPDATE OF send_period
      ON agent_research.model_attempts
      FOR EACH ROW EXECUTE FUNCTION agent_research.pin_model_send_period();
    """)


def downgrade():
    op.execute("""
    DO $fn$
    BEGIN
      IF EXISTS (
        SELECT FROM agent_research.model_attempts WHERE evidence_revision IS NOT NULL
      ) THEN
        RAISE EXCEPTION 'cannot discard production model attempt evidence';
      END IF;
    END $fn$;
    DROP TRIGGER immutable_model_send_period ON agent_research.model_attempts;
    DROP FUNCTION agent_research.pin_model_send_period();
    REVOKE UPDATE (send_period) ON agent_research.model_attempts FROM atl_agent_research;
    ALTER TABLE agent_research.model_attempts
      DROP CONSTRAINT model_attempts_send_period,
      DROP CONSTRAINT model_attempts_authority_pair,
      DROP CONSTRAINT model_attempts_scopes_check,
      DROP COLUMN send_period,
      DROP COLUMN authority_hash,
      DROP COLUMN evidence_revision,
      ADD CONSTRAINT model_attempts_scopes_check CHECK (cardinality(scopes) BETWEEN 6 AND 7);
    ALTER TABLE agent_research.model_budget_policies DROP COLUMN max_parallelism;
    """)
