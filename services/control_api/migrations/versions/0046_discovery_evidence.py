"""Commit-ordered discovery sources, pending work and publication identities."""

from alembic import op

revision = "0046_discovery_evidence"
down_revision = "0045_discovery_schedules"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.discovery_source_clock (
      singleton boolean PRIMARY KEY CHECK(singleton),
      position bigint NOT NULL CHECK(position>=0)
    );
    INSERT INTO agent_research.discovery_source_clock VALUES (true,0);
    CREATE TABLE agent_research.discovery_sources (
      position bigint PRIMARY KEY CHECK(position>0),
      kind text NOT NULL CHECK(kind IN
        ('MARKET_REVISION','DATA_QUALITY','RESEARCH_FINDING','QUANT_RESULT')),
      source_id uuid NOT NULL,
      source_commit bigint NOT NULL CHECK(source_commit>=0),
      revision bigint NOT NULL CHECK(revision>0),
      instrument_id uuid NOT NULL,
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      observed_at timestamptz NOT NULL,
      window_start timestamptz NOT NULL,
      window_end timestamptz NOT NULL CHECK(window_end>window_start),
      material_change boolean NOT NULL,
      UNIQUE(kind,source_id,revision)
    );
    CREATE TABLE agent_research.discovery_cursors (
      schedule_id text PRIMARY KEY REFERENCES agent_research.discovery_schedules,
      position bigint NOT NULL DEFAULT 0 CHECK(position>=0)
    );
    CREATE TABLE agent_research.discovery_scans (
      run_id uuid PRIMARY KEY REFERENCES agent_research.runs,
      schedule_id text NOT NULL REFERENCES agent_research.discovery_schedules,
      schedule_epoch bigint NOT NULL CHECK(schedule_epoch>0),
      upper_position bigint NOT NULL CHECK(upper_position>=0),
      rule_binding text NOT NULL CHECK(rule_binding ~ '^[0-9a-f]{64}$'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE agent_research.discovery_work (
      evaluation_id uuid PRIMARY KEY,
      schedule_id text NOT NULL REFERENCES agent_research.discovery_schedules,
      source_position bigint NOT NULL REFERENCES agent_research.discovery_sources,
      evidence_hash text NOT NULL CHECK(evidence_hash ~ '^[0-9a-f]{64}$'),
      evaluation_hash text NOT NULL CHECK(evaluation_hash ~ '^[0-9a-f]{64}$'),
      reanalysis_key text NOT NULL DEFAULT '',
      model_binding_hash text NOT NULL CHECK(model_binding_hash ~ '^[0-9a-f]{64}$'),
      evaluation_binding jsonb NOT NULL CHECK(jsonb_typeof(evaluation_binding)='object'),
      state text NOT NULL CHECK(state IN ('PENDING','DISPATCHING','READY','COMPLETED',
        'BLOCKED','OUTCOME_UNKNOWN')),
      reason text NOT NULL CHECK(reason ~ '^[A-Z0-9_]{1,64}$'),
      run_id uuid REFERENCES agent_research.runs,
      attempt_id uuid UNIQUE REFERENCES agent_research.model_attempts,
      safe_result jsonb CHECK(jsonb_typeof(safe_result)='array'),
      expires_at timestamptz NOT NULL,
      UNIQUE(evidence_hash,reanalysis_key),
      UNIQUE(evaluation_hash),
      CHECK((state IN ('DISPATCHING','READY','OUTCOME_UNKNOWN'))<=(attempt_id IS NOT NULL))
    );
    CREATE INDEX discovery_pending ON agent_research.discovery_work(schedule_id,source_position)
      WHERE state='PENDING';
    CREATE TABLE agent_research.discovery_reanalyses (
      evaluation_id uuid PRIMARY KEY REFERENCES agent_research.discovery_work,
      previous_id uuid NOT NULL REFERENCES agent_research.discovery_work,
      approval_id text NOT NULL UNIQUE CHECK(approval_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      actor text NOT NULL CHECK(actor ~ '^[A-Za-z0-9._:-]{1,128}$'),
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE agent_research.discovery_events (
      run_id uuid NOT NULL REFERENCES agent_research.runs,
      source_position bigint NOT NULL REFERENCES agent_research.discovery_sources,
      reason text NOT NULL CHECK(reason ~ '^[A-Z0-9_]{1,64}$'),
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(run_id,source_position,reason)
    );
    CREATE TABLE agent_research.discovery_publications (
      publication_key text PRIMARY KEY CHECK(publication_key ~ '^[0-9a-f]{64}$'),
      hypothesis_key text NOT NULL CHECK(hypothesis_key ~ '^[0-9a-f]{64}$'),
      comparison_key text NOT NULL CHECK(comparison_key ~ '^[0-9a-f]{64}$'),
      comparison_fields jsonb NOT NULL CHECK(jsonb_typeof(comparison_fields)='array'),
      run_id uuid NOT NULL REFERENCES agent_research.runs,
      evaluation_id uuid NOT NULL REFERENCES agent_research.discovery_work,
      idea_id text NOT NULL,
      idea_version bigint NOT NULL,
      FOREIGN KEY(idea_id,idea_version)
        REFERENCES agent_research.companion_idea_versions(idea_id,version)
    );
    CREATE INDEX discovery_hypothesis ON agent_research.discovery_publications(hypothesis_key);
    REVOKE ALL ON agent_research.discovery_source_clock,agent_research.discovery_sources,
      agent_research.discovery_cursors,agent_research.discovery_scans,agent_research.discovery_work,
      agent_research.discovery_events,agent_research.discovery_publications,
      agent_research.discovery_reanalyses FROM PUBLIC;
    GRANT SELECT ON agent_research.discovery_source_clock,agent_research.discovery_sources
      TO atl_agent_research;
    GRANT SELECT,INSERT ON agent_research.discovery_cursors,agent_research.discovery_scans,
      agent_research.discovery_work,agent_research.discovery_events,
      agent_research.discovery_publications,agent_research.discovery_reanalyses
      TO atl_agent_research;
    GRANT UPDATE(position) ON agent_research.discovery_cursors TO atl_agent_research;
    GRANT UPDATE(state,reason,run_id,attempt_id,safe_result) ON agent_research.discovery_work
      TO atl_agent_research;
    """)
    _source_triggers()


def _source_triggers():
    op.execute("""
    CREATE FUNCTION agent_research.queue_discovery_market() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE bar research.bar_revisions; p bigint; changed boolean;
    BEGIN
      SELECT * INTO bar FROM research.bar_revisions WHERE revision_id=NEW.revision_id;
      IF NOT FOUND THEN RETURN NEW; END IF;
      changed := NOT EXISTS(SELECT FROM research.bar_revisions prior
        WHERE prior.revision_id=bar.predecessor_id
          AND (prior.open,prior.high,prior.low,prior.close,prior.volume,prior.quality)
            IS NOT DISTINCT FROM (bar.open,bar.high,bar.low,bar.close,bar.volume,bar.quality));
      UPDATE agent_research.discovery_source_clock SET position=position+1 WHERE singleton
        RETURNING position INTO p;
      INSERT INTO agent_research.discovery_sources
        VALUES(p,'MARKET_REVISION',bar.revision_id,bar.commit_version,bar.local_revision,
          bar.instrument_id,bar.fingerprint,bar.bar_end,bar.bar_start,bar.bar_end,changed);
      RETURN NEW;
    END $$;
    CREATE TRIGGER queue_discovery_market AFTER INSERT ON research.evidence
      FOR EACH ROW EXECUTE FUNCTION agent_research.queue_discovery_market();

    CREATE FUNCTION agent_research.queue_discovery_quality() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE p bigint; changed boolean;
    BEGIN
      IF NEW.kind<>'QUALITY' THEN RETURN NEW; END IF;
      changed := NOT EXISTS(SELECT FROM research.evidence_revisions prior
        WHERE prior.evidence_id=NEW.predecessor_id AND prior.statement=NEW.statement);
      UPDATE agent_research.discovery_source_clock SET position=position+1 WHERE singleton
        RETURNING position INTO p;
      INSERT INTO agent_research.discovery_sources VALUES(p,'DATA_QUALITY',NEW.evidence_id,
        NEW.commit_version,NEW.revision,NEW.instrument_id,NEW.fingerprint,
        NEW.source_observed_at,NEW.start_at,NEW.end_at,changed);
      RETURN NEW;
    END $$;
    CREATE TRIGGER queue_discovery_quality AFTER INSERT ON research.evidence_revisions
      FOR EACH ROW EXECUTE FUNCTION agent_research.queue_discovery_quality();

    CREATE FUNCTION agent_research.queue_discovery_quant() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE d backtest.experiment_definitions; h text; s timestamptz; e timestamptz; p bigint;
    BEGIN
      IF NEW.status<>'SUCCEEDED' OR OLD.status=NEW.status THEN RETURN NEW; END IF;
      SELECT * INTO d FROM backtest.experiment_definitions WHERE definition_id=NEW.definition_id;
      SELECT fingerprint INTO h FROM backtest.experiment_reports WHERE batch_id=NEW.batch_id;
      SELECT min(start_at),max(end_at) INTO s,e FROM backtest.test_exposures
        WHERE source_kind='BATCH' AND source_id=NEW.batch_id;
      IF h IS NULL OR s IS NULL OR e IS NULL THEN RETURN NEW; END IF;
      UPDATE agent_research.discovery_source_clock SET position=position+1 WHERE singleton
        RETURNING position INTO p;
      INSERT INTO agent_research.discovery_sources
        VALUES(p,'QUANT_RESULT',NEW.batch_id,NEW.snapshot_version,1,
          (d.body->>'instrumentId')::uuid,h,NEW.updated_at,s,e,true);
      RETURN NEW;
    END $$;
    CREATE TRIGGER queue_discovery_quant AFTER UPDATE OF status ON backtest.experiment_batches
      FOR EACH ROW EXECUTE FUNCTION agent_research.queue_discovery_quant();

    CREATE FUNCTION agent_research.queue_discovery_finding() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a backtest.retrospective_assessments; r backtest.retrospective_reports;
      s timestamptz; e timestamptz; p bigint;
    BEGIN
      IF NEW.status<>'SUCCEEDED' OR OLD.status=NEW.status OR NEW.assessment_id IS NULL
        OR NEW.quant_batch_id IS NOT NULL THEN RETURN NEW; END IF;
      SELECT * INTO a FROM backtest.retrospective_assessments
        WHERE assessment_id=NEW.assessment_id;
      SELECT * INTO r FROM backtest.retrospective_reports WHERE run_id=NEW.run_id AND phase='FINAL';
      SELECT min(start_at),max(end_at) INTO s,e FROM backtest.test_exposures
        WHERE source_kind='RETROSPECTIVE' AND source_id=NEW.assessment_id;
      IF r.report_id IS NULL OR s IS NULL OR e IS NULL OR s>=e THEN RETURN NEW; END IF;
      UPDATE agent_research.discovery_source_clock SET position=position+1 WHERE singleton
        RETURNING position INTO p;
      INSERT INTO agent_research.discovery_sources
        VALUES(p,'RESEARCH_FINDING',r.report_id,a.snapshot_version,1,
          (a.request->>'instrumentId')::uuid,r.fingerprint,r.published_at,s,e,true);
      RETURN NEW;
    END $$;
    CREATE TRIGGER queue_discovery_finding AFTER UPDATE OF status ON backtest.runs
      FOR EACH ROW EXECUTE FUNCTION agent_research.queue_discovery_finding();

    CREATE FUNCTION agent_research.discovery_source_allowed(p_position bigint) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT coalesce((SELECT CASE s.kind WHEN 'MARKET_REVISION' THEN EXISTS(
        SELECT FROM research.bar_revisions b
        WHERE b.revision_id=s.source_id AND b.quality='VALID' AND NOT EXISTS(
          SELECT FROM research.bar_revisions n WHERE n.instrument_id=b.instrument_id
            AND n.provider=b.provider AND n.feed=b.feed AND n.interval=b.interval
            AND n.adjustment=b.adjustment AND n.bar_start=b.bar_start AND n.bar_end=b.bar_end
            AND n.local_revision>b.local_revision))
        WHEN 'DATA_QUALITY' THEN EXISTS(
          SELECT FROM research.evidence_revisions q CROSS JOIN research.trust_version_state t
          WHERE q.evidence_id=s.source_id AND t.singleton AND q.trust_version=t.version
            AND q.statement->>'quality'='VALID'
            AND q.trust->'sourceAuthenticity'->>'status'='VERIFIED'
            AND q.trust->'temporalAuthenticity'->>'status'='VERIFIED'
            AND NOT EXISTS(SELECT FROM research.evidence_revisions n
              WHERE n.source_id=q.source_id AND n.evidence_key=q.evidence_key
                AND n.revision>q.revision))
        WHEN 'RESEARCH_FINDING' THEN EXISTS(
          SELECT FROM backtest.retrospective_reports r JOIN backtest.runs b USING(run_id)
          WHERE r.report_id=s.source_id AND r.phase='FINAL' AND b.status='SUCCEEDED'
            AND backtest.current_retrospective_gate(r.assessment_id) IS NULL)
        WHEN 'QUANT_RESULT' THEN backtest.current_quant_gate(s.source_id) IS NULL ELSE false END
        FROM agent_research.discovery_sources s WHERE s.position=p_position),false)
        AND EXISTS (SELECT FROM research.source_status
          WHERE singleton AND license_status='CONFIRMED')
    $$;
    CREATE FUNCTION agent_research.lock_discovery_source_policy() RETURNS void
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      PERFORM 1 FROM research.version_state WHERE singleton FOR SHARE;
      PERFORM 1 FROM research.trust_version_state WHERE singleton FOR SHARE;
      PERFORM 1 FROM research.source_status WHERE singleton FOR SHARE;
    END $$;
    REVOKE ALL ON FUNCTION agent_research.queue_discovery_market(),
      agent_research.queue_discovery_quality(),agent_research.queue_discovery_quant(),
      agent_research.queue_discovery_finding(),
      agent_research.discovery_source_allowed(bigint),
      agent_research.lock_discovery_source_policy() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.discovery_source_allowed(bigint),
      agent_research.lock_discovery_source_policy() TO atl_agent_research;
    """)


def downgrade():
    raise RuntimeError("Discovery migration preserves committed source and publication history")
