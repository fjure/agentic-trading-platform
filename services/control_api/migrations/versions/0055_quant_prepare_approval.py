"""Return proven Quant preparations to Research and prepare operator approval."""

from alembic import op

revision = "0055_quant_prepare_approval"
down_revision = "0054_quant_prepare_handoff"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION agent_research.ready_prepared_quant_runs() RETURNS integer
      LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      WITH eligible AS (
        SELECT r.run_id FROM agent_research.runs r
        JOIN agent_research.quant_prepare_results p USING(run_id)
        WHERE r.state='WAITING_FOR_INPUT' AND r.checkpoint_ref='QUANT_VALIDATION_READY'
          AND r.deadline_at>clock_timestamp() AND p.validation_id IS NOT NULL
        ORDER BY r.run_id LIMIT 16 FOR UPDATE OF r SKIP LOCKED
      ), advanced AS (
        UPDATE agent_research.runs r SET state='QUEUED',version=version+1,
          updated_at=clock_timestamp()
        FROM eligible WHERE r.run_id=eligible.run_id
        RETURNING r.run_id,r.version,r.fence,r.state,r.updated_at
      ), audited AS (
        INSERT INTO agent_research.run_events(run_id,version,fence,state,occurred_at)
          SELECT run_id,version,fence,state,updated_at FROM advanced RETURNING 1
      ) SELECT count(*)::integer FROM audited
    $$;

    CREATE FUNCTION agent_research.claimed_prepared_quant(
      p_run uuid,p_owner uuid,p_fence bigint,p_version bigint
    ) RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT p.definition_id FROM agent_research.runs r
      JOIN agent_research.quant_prepare_results p USING(run_id)
      JOIN agent_research.quant_prepare_intents intent USING(run_id)
      JOIN backtest.experiment_validations v ON v.definition_id=p.definition_id
        AND v.validation_id=p.validation_id
      WHERE r.run_id=p_run AND r.owner=p_owner AND r.fence=p_fence AND r.version=p_version
        AND r.state='RUNNING' AND r.checkpoint_ref='QUANT_VALIDATION_READY'
        AND r.lease_until>statement_timestamp() AND r.deadline_at>statement_timestamp()
        AND v.body->'agentDataVerified'='true'::jsonb
        AND v.body->'agentValidationContext'=
          backtest.agent_validation_context((v.body->'agentValidationContext'->>
            'snapshotVersion')::bigint)
        AND NOT EXISTS (
          SELECT FROM agent_research.companion_idea_versions newer
          WHERE newer.idea_id=intent.idea_id AND newer.version>intent.idea_version
        )
        AND NOT EXISTS (
          SELECT FROM agent_research.companion_dialogue_versions newer
          WHERE newer.dialogue_id=intent.dialogue_id AND newer.version>intent.dialogue_version
        )
        AND 2=(SELECT count(*) FROM agent_research.quant_start_policies s
          WHERE s.enabled AND s.policy_id IN ('global','agent:' ||
            (r.agent_binding->>'agent_id') || ':' || (r.agent_binding->>'version')))
    $$;

    CREATE FUNCTION agent_research.prepare_result_approval(p_run uuid)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE binding jsonb; approval uuid;
    BEGIN
      SELECT a.approval_id INTO approval FROM agent_research.quant_approvals a
        WHERE a.run_id=p_run;
      IF FOUND THEN RETURN approval; END IF;
      SELECT jsonb_build_object(
        'runId',r.run_id,'runVersion',r.version,'operatorId',r.actor,
        'ideaId',i.idea_id,'ideaVersion',i.version,'hypothesisVersion',i.version,
        'hypothesisHash',encode(sha256(convert_to(
          backtest.quant_canonical(i.document->'hypothesis'),'UTF8')),'hex'),
        'approximation',NULL,'experimentId',d.definition_id,
        'definitionVersion',d.definition_version,'definitionHash',d.fingerprint,
        'validationId',v.validation_id,'validationHash',v.fingerprint,
        'researchMode',d.body->>'researchMode',
        'resourceBudget',jsonb_build_object(
          'schemaVersion','quant-lab-v1','candidateCount',d.candidate_count,
          'reservedQueueSlots',d.candidate_count+2,'snapshotRows',v.body->'snapshotRows',
          'simulationBarSteps',v.body->'simulationBarSteps',
          'publicationBytes',v.body->'publicationBytes','calendarConfirmed',true,
          'withinLimits',true),
        'activePolicies',(SELECT jsonb_agg(jsonb_build_object(
          'policyId',policy_id,'revision',revision,'hash',fingerprint) ORDER BY policy_id)
          FROM (
            SELECT policy_id,revision,fingerprint FROM agent_research.quant_start_policies
            WHERE enabled AND policy_id IN ('global','agent:' ||
              (r.agent_binding->>'agent_id') || ':' || (r.agent_binding->>'version'))
            UNION ALL SELECT 'selection',1,d.policy_hash
          ) policies),
        'expiresAt',to_char(r.deadline_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
      ) INTO binding
      FROM agent_research.runs r
      JOIN agent_research.quant_prepare_intents intent USING(run_id)
      JOIN agent_research.quant_prepare_results p USING(run_id)
      JOIN backtest.experiment_definitions d ON d.definition_id=p.definition_id
      JOIN backtest.experiment_validations v ON v.definition_id=d.definition_id
        AND v.validation_id=p.validation_id
      JOIN agent_research.companion_idea_versions i
        ON i.idea_id=intent.idea_id AND i.version=intent.idea_version+1
      WHERE r.run_id=p_run AND r.state='WAITING_FOR_APPROVAL'
        AND r.checkpoint_ref='QUANT_VALIDATION_READY'
        AND v.body->'agentDataVerified'='true'::jsonb;
      IF binding IS NULL THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      RETURN agent_research.prepare_quant_approval(
        gen_random_uuid(),binding,backtest.quant_canonical(binding),'result-approval:' || p_run
      );
    END $$;
    REVOKE ALL ON FUNCTION agent_research.ready_prepared_quant_runs(),
      agent_research.claimed_prepared_quant(uuid,uuid,bigint,bigint),
      agent_research.prepare_result_approval(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.ready_prepared_quant_runs(),
      agent_research.claimed_prepared_quant(uuid,uuid,bigint,bigint),
      agent_research.prepare_result_approval(uuid) TO atl_agent_research;
    """)


def downgrade():
    raise RuntimeError("Stop and drain Quant preparation before migrating")
