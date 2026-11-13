"""Bound the Control-side claim and result of native Quant preparation."""

from alembic import op

revision = "0054_quant_prepare_handoff"
down_revision = "0053_quant_prepare_intent"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.quant_prepare_results (
      run_id uuid PRIMARY KEY REFERENCES agent_research.quant_prepare_intents(run_id),
      definition_id uuid REFERENCES backtest.experiment_definitions(definition_id),
      validation_id uuid,
      retry_after timestamptz,
      recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK ((definition_id IS NOT NULL AND validation_id IS NOT NULL AND retry_after IS NULL)
        OR (definition_id IS NULL AND validation_id IS NULL AND retry_after IS NOT NULL))
    );
    REVOKE ALL ON agent_research.quant_prepare_results FROM PUBLIC;

    CREATE FUNCTION agent_research.claim_quant_preparation(p_run uuid) RETURNS jsonb
      LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT jsonb_build_object(
        'runId',r.run_id,'traceId',r.trace_id,'ideaId',p.idea_id,'ideaVersion',p.idea_version,
        'deadlineAt',r.deadline_at,
        'definition',q.body || jsonb_build_object(
          'hypothesis',i.document#>>'{hypothesis,statement}'
        )
      )
      FROM agent_research.quant_prepare_intents p
      JOIN agent_research.runs r USING(run_id)
      JOIN agent_research.companion_idea_versions i
        ON i.idea_id=p.idea_id AND i.version=p.idea_version
      JOIN agent_research.companion_dialogue_versions d
        ON d.dialogue_id=p.dialogue_id AND d.version=p.dialogue_version
       AND d.idea_id=p.idea_id AND d.idea_version=p.idea_version
      JOIN backtest.experiment_definitions q ON q.definition_id=p.template_id
       AND q.definition_version=p.template_version AND q.fingerprint=p.template_hash
       AND q.actor=r.actor
      JOIN agent_research.tool_receipts t ON t.run_id=p.run_id AND t.step_id='quant-template'
       AND t.tool_id='QUANT_READ' AND t.tool_version=1 AND t.result_ref=q.definition_id::text
      WHERE (p_run IS NULL OR p.run_id=p_run)
        AND r.actor='local-operator' AND r.input_ref=p.dialogue_id AND r.kind='DIALOGUE'
        AND r.state='WAITING_FOR_INPUT' AND r.checkpoint_ref='QUANT_PREPARATION_READY'
        AND r.deadline_at>clock_timestamp() AND i.document->>'status'='REVIEWED'
        AND i.document#>>'{hypothesis,statement}' IS NOT NULL
        AND NOT EXISTS (
          SELECT FROM agent_research.quant_prepare_results done WHERE done.run_id=p.run_id
            AND (done.retry_after IS NULL OR done.retry_after>clock_timestamp())
        )
        AND NOT EXISTS (
          SELECT FROM agent_research.companion_idea_versions newer
          WHERE newer.idea_id=p.idea_id AND newer.version>p.idea_version
        )
        AND NOT EXISTS (
          SELECT FROM agent_research.companion_dialogue_versions newer
          WHERE newer.dialogue_id=p.dialogue_id AND newer.version>p.dialogue_version
        )
      ORDER BY p.created_at,p.run_id LIMIT 1 FOR UPDATE OF r SKIP LOCKED
    $$;

    CREATE FUNCTION agent_research.record_quant_preparation(
      p_run uuid,p_definition uuid,p_validation uuid
    ) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE validated boolean; reason text;
    BEGIN
      IF p_run IS NULL OR agent_research.claim_quant_preparation(p_run) IS NULL THEN
        RAISE EXCEPTION 'quant preparation conflict';
      END IF;
      IF p_definition IS NULL AND p_validation IS NULL THEN
        INSERT INTO agent_research.quant_prepare_results(run_id,retry_after)
        VALUES (p_run,clock_timestamp()+interval '30 seconds')
        ON CONFLICT (run_id) DO UPDATE SET retry_after=excluded.retry_after
          WHERE quant_prepare_results.retry_after IS NOT NULL;
        RETURN;
      END IF;
      SELECT v.valid,CASE
        WHEN v.body->'reasons' ? 'BUDGET_EXCEEDED' THEN 'QUANT_VALIDATION_BUDGET'
        ELSE 'QUANT_VALIDATION_EVIDENCE'
      END INTO validated,reason
        FROM agent_research.quant_prepare_intents p
        JOIN backtest.experiment_agent_provenance origin
          ON origin.run_id=p.run_id AND origin.idea_id=p.idea_id
         AND origin.idea_version=p.idea_version
        JOIN backtest.experiment_validation_requests v ON v.definition_id=origin.definition_id
        WHERE p.run_id=p_run AND origin.definition_id=p_definition
          AND v.validation_id=p_validation
          AND (NOT v.valid OR v.body->'agentDataVerified'='true'::jsonb);
      IF NOT FOUND THEN RAISE EXCEPTION 'quant preparation conflict'; END IF;
      INSERT INTO agent_research.quant_prepare_results(run_id,definition_id,validation_id)
      VALUES (p_run,p_definition,p_validation)
      ON CONFLICT (run_id) DO UPDATE SET definition_id=excluded.definition_id,
        validation_id=excluded.validation_id,retry_after=NULL,recorded_at=clock_timestamp()
      WHERE quant_prepare_results.retry_after IS NOT NULL;
      WITH advanced AS (
        UPDATE agent_research.runs
        SET state=CASE WHEN validated THEN 'WAITING_FOR_INPUT' ELSE 'BLOCKED' END,
            checkpoint_ref=CASE WHEN validated THEN 'QUANT_VALIDATION_READY' ELSE reason END,
            version=version+1,updated_at=clock_timestamp()
        WHERE run_id=p_run
        RETURNING run_id,version,fence,state,updated_at
      )
      INSERT INTO agent_research.run_events(run_id,version,fence,state,occurred_at)
        SELECT run_id,version,fence,state,updated_at FROM advanced;
    END $$;
    REVOKE ALL ON FUNCTION agent_research.claim_quant_preparation(uuid) FROM PUBLIC;
    REVOKE ALL ON FUNCTION agent_research.record_quant_preparation(uuid,uuid,uuid) FROM PUBLIC;
    DO $$ DECLARE control_role text;
    BEGIN
      control_role:=CASE current_database() WHEN 'lab_paper_runtime' THEN 'atl_paper_control_api'
        ELSE 'atl_control_api' END;
      EXECUTE format('GRANT EXECUTE ON FUNCTION agent_research.claim_quant_preparation(uuid)
        TO %I',control_role);
      EXECUTE format('GRANT EXECUTE ON FUNCTION
        agent_research.record_quant_preparation(uuid,uuid,uuid) TO %I',control_role);
    END $$;
    """)


def downgrade():
    raise RuntimeError("Stop and drain Quant preparation before migrating")
