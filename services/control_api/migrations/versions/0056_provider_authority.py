"""Research provider authority, immutable evidence and purpose epochs."""

from alembic import op

revision = "0056_provider_authority"
down_revision = "0055_quant_prepare_approval"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.provider_credential_authority (
      provider text PRIMARY KEY CHECK (provider IN ('OPENAI','ANTHROPIC')),
      version bigint NOT NULL DEFAULT 1 CHECK (version>0),
      generation bigint CHECK (generation>0),
      digest text CHECK (digest ~ '^[0-9a-f]{64}$'),
      blocked boolean NOT NULL DEFAULT true,
      CHECK ((version=1)=(generation IS NULL)),
      CHECK (digest IS NULL OR generation IS NOT NULL)
    );
    INSERT INTO agent_research.provider_credential_authority (provider)
      VALUES ('OPENAI'),('ANTHROPIC');
    CREATE TABLE agent_research.provider_cost_evidence (
      provider text NOT NULL REFERENCES agent_research.provider_credential_authority(provider),
      profile_id text NOT NULL CHECK (profile_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      revision bigint NOT NULL CHECK (revision>0),
      binding_digest text NOT NULL CHECK (binding_digest ~ '^[0-9a-f]{64}$'),
      valid_until timestamptz NOT NULL,
      PRIMARY KEY (provider,profile_id)
    );
    REVOKE ALL ON agent_research.provider_cost_evidence FROM PUBLIC;
    GRANT SELECT ON agent_research.provider_cost_evidence TO atl_agent_research;
    CREATE FUNCTION agent_research.lock_provider_evidence_write() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
      IF TG_OP = 'UPDATE' AND (NEW.provider,NEW.profile_id) <> (OLD.provider,OLD.profile_id) THEN
        RAISE EXCEPTION 'provider evidence identity is immutable';
      END IF;
      IF TG_OP = 'DELETE' THEN
        PERFORM 1 FROM agent_research.provider_credential_authority
          WHERE provider=OLD.provider FOR UPDATE;
        RETURN OLD;
      END IF;
      PERFORM 1 FROM agent_research.provider_credential_authority
        WHERE provider=NEW.provider FOR UPDATE;
      RETURN NEW;
    END
    $$;
    REVOKE ALL ON FUNCTION agent_research.lock_provider_evidence_write() FROM PUBLIC;
    CREATE TRIGGER provider_evidence_write BEFORE INSERT OR UPDATE OR DELETE
      ON agent_research.provider_cost_evidence FOR EACH ROW
      EXECUTE FUNCTION agent_research.lock_provider_evidence_write();
    CREATE TABLE agent_research.provider_bindings (
      provider text NOT NULL REFERENCES agent_research.provider_credential_authority(provider),
      profile_id text NOT NULL CHECK (profile_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      generation bigint NOT NULL CHECK (generation>0),
      digest text NOT NULL UNIQUE CHECK (digest ~ '^[0-9a-f]{64}$'),
      binding jsonb NOT NULL CHECK (jsonb_typeof(binding)='object'),
      PRIMARY KEY (provider,profile_id,generation)
    );
    CREATE TABLE agent_research.provider_authority (
      provider text NOT NULL,
      profile_id text NOT NULL,
      generation bigint NOT NULL,
      epoch bigint NOT NULL CHECK (epoch>0),
      state text NOT NULL CHECK (state IN ('STAGED','VERIFIED','ACTIVE','DISABLED','REVOKED')),
      dialogue boolean NOT NULL DEFAULT false,
      discovery boolean NOT NULL DEFAULT false,
      smoke_attempt_id uuid REFERENCES agent_research.model_attempts(attempt_id),
      CHECK ((state='ACTIVE')=(dialogue OR discovery)),
      CHECK (state NOT IN ('VERIFIED','ACTIVE') OR smoke_attempt_id IS NOT NULL),
      PRIMARY KEY (provider,profile_id),
      FOREIGN KEY (provider,profile_id,generation)
        REFERENCES agent_research.provider_bindings(provider,profile_id,generation)
    );
    REVOKE ALL ON agent_research.provider_credential_authority,agent_research.provider_bindings,
      agent_research.provider_authority FROM PUBLIC;
    GRANT SELECT ON agent_research.provider_credential_authority,agent_research.provider_bindings,
      agent_research.provider_authority TO atl_agent_research;
    GRANT UPDATE (version,generation,digest,blocked) ON agent_research.provider_credential_authority
      TO atl_agent_research;
    GRANT INSERT ON agent_research.provider_bindings,agent_research.provider_authority
      TO atl_agent_research;
    GRANT UPDATE (generation,epoch,state,dialogue,discovery,smoke_attempt_id)
      ON agent_research.provider_authority TO atl_agent_research;
    """)


def downgrade():
    op.execute("""
    DROP TABLE agent_research.provider_authority;
    DROP TABLE agent_research.provider_bindings;
    DROP TABLE agent_research.provider_cost_evidence;
    DROP FUNCTION agent_research.lock_provider_evidence_write();
    DROP TABLE agent_research.provider_credential_authority;
    """)
