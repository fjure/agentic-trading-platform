"""Read scoped native Quant sources using the existing agent report role."""

from alembic import op

revision = "0048_quant_report_reader"
down_revision = "0047_research_reports"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION agent_research.report_quant_evidence(p_batch uuid,p_actor text)
      RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      WITH authorized AS MATERIALIZED (
        SELECT agent_research.report_scope(p_batch,p_actor) AS scope
      ), batch AS MATERIALIZED (
        SELECT b.batch_id,b.definition_id,b.status,b.snapshot_version,b.snapshot_at,
               b.code_version,b.registry_hash,b.registry_watermark,b.accepted_at,
               d.definition_version,d.body AS definition,d.fingerprint AS definition_hash,
               d.policy_canonical,d.policy_hash,predecessor.fingerprint AS predecessor_hash,
               s.body AS snapshot,s.canonical_text AS canonical_manifest,
               s.fingerprint AS manifest_hash,r.body AS report,r.fingerprint AS physical_hash
        FROM authorized
        JOIN backtest.experiment_batches b ON b.batch_id=p_batch
        JOIN backtest.experiment_definitions d USING(definition_id)
        LEFT JOIN backtest.experiment_definitions predecessor
          ON predecessor.definition_id=d.predecessor_id
        JOIN backtest.experiment_snapshots s USING(batch_id)
        JOIN backtest.experiment_reports r USING(batch_id)
        WHERE authorized.scope IS NOT NULL
      ), phases AS (
        SELECT phase_key,phase,fold_index,fingerprint,body,start_at,end_at,seed_ids
        FROM backtest.experiment_phase_bindings JOIN batch USING(batch_id)
      ), links AS (
        SELECT l.*,p.phase,p.fold_index,p.fingerprint AS phase_hash,
               u.quant_batch_id AS run_batch_id,u.status AS run_status,u.request,u.request_hash,
               m.body AS run_manifest,m.fingerprint AS run_manifest_hash,
               r.body AS result_body,r.fingerprint AS result_hash,
               t.quant_batch_id AS reliability_batch_id,t.quant_phase_key AS reliability_phase_key,
               t.run_id AS reliability_run_id,t.body AS reliability_body,t.findings,
               t.revision_references,t.fingerprint AS reliability_fingerprint,
               t.quant_report_hash AS reliability_hash,c.body AS configuration
        FROM backtest.experiment_result_links l
        JOIN batch b USING(batch_id)
        JOIN backtest.experiment_phase_bindings p USING(batch_id,phase_key)
        JOIN backtest.experiment_candidates c
          ON (c.definition_id,c.candidate_id,c.role)=(b.definition_id,l.candidate_id,l.role)
        LEFT JOIN backtest.runs u ON u.run_id=l.run_id
        LEFT JOIN backtest.manifests m ON m.run_id=l.run_id
        LEFT JOIN backtest.results r ON r.run_id=l.run_id
        LEFT JOIN backtest.retrospective_reports t ON t.report_id=l.reliability_report_id
      ), selections AS (
        SELECT scope,selected_candidate_id,s.status,s.body,s.fingerprint
        FROM backtest.experiment_selections s JOIN batch USING(batch_id)
      ), later AS (
        SELECT e.source_kind,e.source_id,min(e.observed_at) AS first_seen
        FROM batch b JOIN backtest.test_exposures e ON e.exposure_id>b.registry_watermark
        WHERE e.instrument_id=(b.definition->>'instrumentId')::uuid
          AND e.feed='iex' AND e.interval='15Min' AND e.adjustment='raw'
          AND NOT (e.source_kind='BATCH' AND e.source_id=b.batch_id)
          AND NOT EXISTS (SELECT 1 FROM backtest.test_exposures prior
            WHERE prior.exposure_id<=b.registry_watermark
              AND prior.source_kind=e.source_kind AND prior.source_id=e.source_id)
          AND EXISTS (SELECT 1 FROM backtest.test_exposures own
            WHERE own.source_kind='BATCH' AND own.source_id=b.batch_id
              AND own.knowledge_status='REQUESTED' AND own.status='ACCEPTED'
              AND e.start_at<own.end_at AND own.start_at<e.end_at)
        GROUP BY e.source_kind,e.source_id
        ORDER BY first_seen,e.source_kind,e.source_id LIMIT 31
      )
      SELECT jsonb_build_object(
        'batch',to_jsonb(batch),
        'phases',coalesce((SELECT jsonb_agg(to_jsonb(phases)) FROM phases),'[]'),
        'links',coalesce((SELECT jsonb_agg(to_jsonb(links) ORDER BY candidate_id,role,phase_key)
          FROM links),'[]'),
        'selections',coalesce((SELECT jsonb_agg(to_jsonb(selections) ORDER BY scope)
          FROM selections),'[]'),
        'gateObservedAt',CASE WHEN backtest.current_quant_gate(batch.batch_id) IS NOT NULL
          THEN clock_timestamp() END,
        'laterOverlaps',coalesce((SELECT jsonb_agg(to_jsonb(later)
          ORDER BY first_seen,source_kind,source_id) FROM later),'[]')
      ) FROM batch
    $$;
    REVOKE ALL ON FUNCTION agent_research.report_quant_evidence(uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.report_quant_evidence(uuid,text)
      TO atl_agent_research;
    """)


def downgrade():
    op.execute("DROP FUNCTION agent_research.report_quant_evidence(uuid,text)")
