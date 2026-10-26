"""Offline immutable corporate-action coverage; no runtime write permission."""

from alembic import op

revision = "0028_backtest_coverage"
down_revision = "0027_research_quality"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE SCHEMA backtest;
    REVOKE ALL ON SCHEMA backtest FROM PUBLIC;
    CREATE TABLE backtest.corporate_action_coverage (
      coverage_id uuid PRIMARY KEY,
      instrument_id uuid NOT NULL REFERENCES research.instruments,
      start_at timestamptz NOT NULL, end_at timestamptz NOT NULL CHECK(end_at>start_at),
      certified_at timestamptz NOT NULL,
      source_reference text NOT NULL CHECK(source_reference ~ '^[A-Za-z0-9_.:/-]{1,120}$'
        AND source_reference NOT LIKE '%://%'),
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      no_actions boolean NOT NULL,
      commit_version bigint NOT NULL, ingested_at timestamptz NOT NULL,
      CHECK(certified_at<=ingested_at));
    CREATE INDEX backtest_coverage_pit ON backtest.corporate_action_coverage
      (instrument_id,start_at,end_at,commit_version);
    CREATE TRIGGER a_stamp BEFORE INSERT ON backtest.corporate_action_coverage
      FOR EACH ROW EXECUTE FUNCTION research.stamp();
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.corporate_action_coverage
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    REVOKE ALL ON backtest.corporate_action_coverage FROM PUBLIC;
    GRANT USAGE ON SCHEMA backtest TO atl_worker,atl_control_api;
    GRANT SELECT ON backtest.corporate_action_coverage TO atl_worker,atl_control_api;
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        REVOKE ALL ON SCHEMA backtest FROM atl_worker,atl_control_api;
        REVOKE ALL ON backtest.corporate_action_coverage FROM atl_worker,atl_control_api;
        GRANT USAGE ON SCHEMA backtest TO atl_paper_worker,atl_paper_control_api;
        GRANT SELECT ON backtest.corporate_action_coverage
          TO atl_paper_worker,atl_paper_control_api;
      END IF;
    END $$;
    """)


def downgrade():
    raise RuntimeError("Backtest rollback preserves immutable coverage evidence")
