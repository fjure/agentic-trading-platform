"""Immutable normalized page rejection/completion metadata; no raw payloads."""

from alembic import op

revision = "0027_research_quality"
down_revision = "0026_research_observability"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE research.quality_observations (
      observation_id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES research.ingestion_jobs,
      instrument_id uuid NOT NULL REFERENCES research.instruments,
      start_at timestamptz NOT NULL, end_at timestamptz NOT NULL CHECK(end_at>start_at),
      interval text NOT NULL CHECK(interval IN ('15Min','1Day')),
      quality text NOT NULL CHECK(quality IN ('VALID','INVALID','CONFLICT')),
      rule_version text NOT NULL CHECK(rule_version='research-quality-v1'),
      code text NOT NULL CHECK(code IN
        ('NORMALIZED_JOB_COMPLETED','REJECTED_PROVIDER_RESPONSE','PROVIDER_REVISION_CONFLICT')),
      commit_version bigint NOT NULL, ingested_at timestamptz NOT NULL);
    CREATE INDEX research_quality_pit ON research.quality_observations
      (instrument_id,interval,start_at,end_at,commit_version);
    CREATE TRIGGER a_stamp BEFORE INSERT ON research.quality_observations
      FOR EACH ROW EXECUTE FUNCTION research.stamp();
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON research.quality_observations
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    REVOKE ALL ON research.quality_observations FROM PUBLIC;
    GRANT SELECT ON research.quality_observations TO atl_control_api,atl_worker,atl_execution;
    GRANT INSERT ON research.quality_observations TO atl_execution;
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        REVOKE ALL ON research.quality_observations FROM atl_control_api,atl_worker,atl_execution;
        GRANT SELECT ON research.quality_observations
          TO atl_paper_control_api,atl_paper_worker,atl_paper_execution;
        GRANT INSERT ON research.quality_observations TO atl_paper_execution;
      END IF;
    END $$;
    """)


def downgrade():
    raise RuntimeError("Research rollback preserves quality observations")
