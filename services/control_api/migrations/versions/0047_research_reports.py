"""Immutable AgentReportV1 versions and redacted publication outcomes."""

from alembic import op

revision = "0047_research_reports"
down_revision = "0046_discovery_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.report_versions (
      report_id text NOT NULL CHECK(report_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      version bigint NOT NULL CHECK(version>0),
      idea_id text NOT NULL,
      idea_version bigint NOT NULL,
      batch_id uuid NOT NULL REFERENCES backtest.experiment_batches,
      approval_id uuid NOT NULL REFERENCES agent_research.quant_approvals,
      actor text NOT NULL CHECK(actor='local-operator'),
      content_hash text NOT NULL CHECK(content_hash ~ '^[a-f0-9]{64}$'),
      canonical_text text NOT NULL,
      document jsonb NOT NULL,
      PRIMARY KEY(report_id,version),
      UNIQUE(batch_id,version),
      FOREIGN KEY(idea_id,idea_version)
        REFERENCES agent_research.companion_idea_versions(idea_id,version),
      CHECK(octet_length(canonical_text)<=12582912),
      CHECK(content_hash=encode(sha256(convert_to(canonical_text,'UTF8')),'hex')),
      CHECK(NOT document ? 'currentRightsWarnings'),
      CHECK(document #- '{summary,reportHash}'=canonical_text::jsonb),
      CHECK(document->'summary'->>'reportHash' IS NOT DISTINCT FROM content_hash),
      CHECK(document->'summary'->>'reportId' IS NOT DISTINCT FROM report_id),
      CHECK((document->'summary'->>'reportVersion')::bigint IS NOT DISTINCT FROM version),
      CHECK(document->'summary'->>'ideaId' IS NOT DISTINCT FROM idea_id),
      CHECK((document->'summary'->>'ideaVersion')::bigint IS NOT DISTINCT FROM idea_version),
      CHECK((document->'summary'->>'batchId')::uuid IS NOT DISTINCT FROM batch_id)
    );
    CREATE TABLE agent_research.report_rejections (
      report_id text NOT NULL,
      version bigint NOT NULL,
      code text NOT NULL CHECK(code IN (
        'REPORT_OUTPUT_SHAPE_INVALID','REPORT_OUTPUT_REFERENCE_INVALID',
        'REPORT_OUTPUT_NOT_COMPARABLE','REPORT_OUTPUT_PREDICATE_MISMATCH')),
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(report_id,version,code),
      FOREIGN KEY(report_id,version) REFERENCES agent_research.report_versions
    );

    CREATE FUNCTION agent_research.report_scope(p_batch uuid,p_actor text) RETURNS jsonb
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT jsonb_build_object('approvalId',a.approval_id,'binding',a.binding,
        'idea',iv.document,'createdAt',b.updated_at,
        'manifestHash',s.fingerprint,'definition',d.body,
        'resultReportId',r.body->'comparison'->>'id')
      FROM agent_research.quant_approvals a
      JOIN agent_research.companion_ideas i ON i.idea_id=a.binding->>'ideaId'
      JOIN agent_research.companion_idea_versions iv ON iv.idea_id=i.idea_id
        AND iv.version=(a.binding->>'ideaVersion')::bigint
      JOIN backtest.experiment_batches b ON b.batch_id=a.batch_id
        AND b.actor=a.actor AND b.definition_id=a.definition_id
      JOIN backtest.experiment_definitions d ON d.definition_id=b.definition_id
      JOIN backtest.experiment_snapshots s ON s.batch_id=b.batch_id
      JOIN backtest.experiment_reports r ON r.batch_id=b.batch_id
      WHERE a.batch_id=p_batch AND a.actor=p_actor AND i.actor=p_actor
        AND a.consumed_at IS NOT NULL AND b.status='SUCCEEDED'
    $$;
    CREATE FUNCTION agent_research.guard_report_scope() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE scope jsonb; binding jsonb;
    BEGIN
      scope := agent_research.report_scope(NEW.batch_id,NEW.actor);
      binding := scope->'binding';
      PERFORM pg_advisory_xact_lock(hashtextextended('report:' || NEW.report_id,0));
      PERFORM pg_advisory_xact_lock(hashtextextended('report-batch:' || NEW.batch_id::text,0));
      IF scope IS NULL OR (scope->>'approvalId')::uuid IS DISTINCT FROM NEW.approval_id
        OR binding->>'ideaId' IS DISTINCT FROM NEW.idea_id
        OR (binding->>'ideaVersion')::bigint IS DISTINCT FROM NEW.idea_version
        OR binding->>'experimentId' IS DISTINCT FROM NEW.document->>'experimentId'
        OR binding->'definitionVersion' IS DISTINCT FROM NEW.document->'definitionVersion'
        OR binding->'definitionHash' IS DISTINCT FROM NEW.document->'definitionHash'
        OR binding->'hypothesisVersion' IS DISTINCT FROM NEW.document->'hypothesisVersion'
        OR scope->>'manifestHash' IS DISTINCT FROM NEW.document->'manifest'->>'manifestHash'
        OR scope->'definition' IS DISTINCT FROM NEW.document->'definition'
        OR (scope->>'createdAt')::timestamptz IS DISTINCT FROM
          (NEW.document->'summary'->>'createdAt')::timestamptz
        OR EXISTS(SELECT FROM agent_research.report_versions v
          WHERE (v.report_id=NEW.report_id AND v.batch_id<>NEW.batch_id)
            OR (v.batch_id=NEW.batch_id AND v.report_id<>NEW.report_id))
      THEN RAISE EXCEPTION 'report scope conflict'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_report_scope BEFORE INSERT ON agent_research.report_versions
      FOR EACH ROW EXECUTE FUNCTION agent_research.guard_report_scope();

    CREATE FUNCTION agent_research.report_rights(p_batch uuid,p_actor text)
      RETURNS TABLE(evidence_id text,reason text)
      LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT scope->>'resultReportId',backtest.current_quant_gate(p_batch)
      FROM agent_research.report_scope(p_batch,p_actor) AS bound(scope) WHERE scope IS NOT NULL
    $$;

    CREATE TRIGGER immutable_report_version BEFORE UPDATE OR DELETE
      ON agent_research.report_versions FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    CREATE TRIGGER immutable_report_rejection BEFORE UPDATE OR DELETE
      ON agent_research.report_rejections FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();

    REVOKE ALL ON agent_research.report_versions,agent_research.report_rejections FROM PUBLIC;
    REVOKE ALL ON FUNCTION agent_research.report_scope(uuid,text),
      agent_research.guard_report_scope(),agent_research.report_rights(uuid,text) FROM PUBLIC;
    GRANT SELECT,INSERT ON agent_research.report_versions,agent_research.report_rejections
      TO atl_agent_research;
    GRANT EXECUTE ON FUNCTION agent_research.report_scope(uuid,text),
      agent_research.report_rights(uuid,text) TO atl_agent_research;
    """)


def downgrade():
    raise RuntimeError("Research reports preserve published versions and audit history")
