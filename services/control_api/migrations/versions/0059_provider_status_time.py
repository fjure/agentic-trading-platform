"""Record the redacted provider credential observation timestamp."""

from alembic import op

revision = "0059_provider_status_time"
down_revision = "0058_provider_smoke"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE agent_research.provider_credential_authority
      ADD COLUMN updated_at timestamptz NOT NULL DEFAULT clock_timestamp();
    CREATE FUNCTION agent_research.touch_provider_credential_status() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
      IF (OLD.version,OLD.generation,OLD.digest,OLD.blocked)
          IS DISTINCT FROM (NEW.version,NEW.generation,NEW.digest,NEW.blocked) THEN
        NEW.updated_at := clock_timestamp();
      END IF;
      RETURN NEW;
    END
    $$;
    REVOKE ALL ON FUNCTION agent_research.touch_provider_credential_status() FROM PUBLIC;
    CREATE TRIGGER provider_credential_status_time BEFORE UPDATE
      ON agent_research.provider_credential_authority FOR EACH ROW
      EXECUTE FUNCTION agent_research.touch_provider_credential_status();
    """)


def downgrade():
    op.execute("""
    DROP TRIGGER provider_credential_status_time ON agent_research.provider_credential_authority;
    DROP FUNCTION agent_research.touch_provider_credential_status();
    ALTER TABLE agent_research.provider_credential_authority DROP COLUMN updated_at;
    """)
