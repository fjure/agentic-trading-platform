"""Atomically consume an operator approval in the canonical Quant admission."""

from alembic import op

revision = "0044_quant_operator_approval"
down_revision = "0043_quant_draft_provenance"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='atl_agent_quant_admission') THEN
        CREATE ROLE atl_agent_quant_admission NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
      END IF;
    END $$;
    GRANT USAGE ON SCHEMA agent_research TO atl_agent_quant_admission;
    CREATE TABLE agent_research.quant_start_policies (
      policy_id text PRIMARY KEY CHECK (policy_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      revision bigint NOT NULL CHECK (revision>0),
      fingerprint text NOT NULL CHECK (fingerprint ~ '^[a-f0-9]{64}$'),
      max_daily integer NOT NULL CHECK (max_daily BETWEEN 0 AND 1000),
      enabled boolean NOT NULL
    );
    CREATE TABLE agent_research.quant_approvals (
      approval_id uuid PRIMARY KEY,
      run_id uuid NOT NULL REFERENCES agent_research.runs,
      definition_id uuid NOT NULL REFERENCES backtest.experiment_definitions,
      actor text NOT NULL CHECK (actor='local-operator'),
      agent_scope text NOT NULL,
      binding jsonb NOT NULL CHECK (jsonb_typeof(binding)='object'),
      binding_hash text NOT NULL CHECK (binding_hash ~ '^[a-f0-9]{64}$'),
      prepare_key text NOT NULL UNIQUE CHECK (prepare_key ~ '^[a-f0-9]{64}$'),
      expires_at timestamptz NOT NULL,
      batch_id uuid UNIQUE REFERENCES backtest.experiment_batches,
      consume_key text CHECK (consume_key ~ '^[a-f0-9]{64}$'),
      consumed_at timestamptz,
      CHECK ((batch_id IS NULL)=(consume_key IS NULL)),
      CHECK ((batch_id IS NULL)=(consumed_at IS NULL)),
      UNIQUE (actor,consume_key),
      UNIQUE (run_id,binding_hash)
    );
    CREATE INDEX quant_approval_charge ON agent_research.quant_approvals (consumed_at,agent_scope)
      WHERE consumed_at IS NOT NULL;
    CREATE TABLE agent_research.quant_approximation_proposals (
      proposal_id text PRIMARY KEY CHECK (proposal_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      idea_id text NOT NULL,
      idea_version bigint NOT NULL,
      definition_id uuid NOT NULL REFERENCES backtest.experiment_definitions,
      fingerprint text NOT NULL CHECK (fingerprint ~ '^[a-f0-9]{64}$'),
      description text NOT NULL CHECK (length(description) BETWEEN 1 AND 1000),
      FOREIGN KEY (idea_id,idea_version)
        REFERENCES agent_research.companion_idea_versions(idea_id,version)
    );
    CREATE TABLE agent_research.quant_approximation_decisions (
      decision_id text PRIMARY KEY CHECK (decision_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      idea_id text NOT NULL,
      idea_version bigint NOT NULL,
      actor text NOT NULL CHECK (actor='local-operator'),
      revision bigint NOT NULL CHECK (revision>0),
      fingerprint text NOT NULL CHECK (fingerprint ~ '^[a-f0-9]{64}$'),
      description text NOT NULL CHECK (length(description) BETWEEN 1 AND 1000),
      proposal_id text NOT NULL REFERENCES agent_research.quant_approximation_proposals,
      FOREIGN KEY (idea_id,idea_version)
        REFERENCES agent_research.companion_idea_versions(idea_id,version)
    );
    CREATE TABLE agent_research.quant_approval_denials (
      approval_id uuid NOT NULL REFERENCES agent_research.quant_approvals,
      trace_id text NOT NULL CHECK (trace_id ~ '^[a-f0-9]{32}$'),
      code text NOT NULL CHECK (code IN ('APPROVAL_STALE','QUANT_CAPACITY_EXHAUSTED',
        'BUDGET_EXHAUSTED','IDEMPOTENCY_CONFLICT')),
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY (approval_id,trace_id,code)
    );
    REVOKE ALL ON agent_research.quant_start_policies,agent_research.quant_approvals,
      agent_research.quant_approximation_proposals,agent_research.quant_approximation_decisions,
      agent_research.quant_approval_denials
      FROM PUBLIC;
    """)
    _binding()
    _ports()
    _execution_budget()


def _binding():
    op.execute("""
    CREATE FUNCTION agent_research.check_quant_approval_binding(p_run agent_research.runs,
      p_binding jsonb,p_now timestamptz) RETURNS text LANGUAGE plpgsql
      SET search_path=pg_catalog,pg_temp AS $$
    DECLARE d backtest.experiment_definitions; v backtest.experiment_validations;
      i agent_research.companion_idea_versions; origin backtest.experiment_agent_provenance;
      agent_scope text; expected_policies jsonb; received_policies jsonb; budget jsonb;
      approximation jsonb;
    BEGIN
      IF p_run.state<>'WAITING_FOR_APPROVAL' OR p_run.kind<>'DIALOGUE'
        OR p_run.actor IS DISTINCT FROM p_binding->>'operatorId'
        OR p_run.run_id IS DISTINCT FROM (p_binding->>'runId')::uuid
        OR p_run.version IS DISTINCT FROM (p_binding->>'runVersion')::bigint
        OR NOT coalesce((p_binding->>'expiresAt')::timestamptz>p_now AND
          (p_binding->>'expiresAt')::timestamptz<=p_run.deadline_at,false)
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended(
        'companion-idea:' || (p_binding->>'ideaId'),0));
      SELECT iv.* INTO i FROM agent_research.companion_idea_versions iv
        JOIN agent_research.companion_ideas owners USING (idea_id)
        WHERE iv.idea_id=p_binding->>'ideaId' AND owners.actor=p_run.actor
        ORDER BY iv.version DESC LIMIT 1;
      IF NOT FOUND OR i.version IS DISTINCT FROM (p_binding->>'ideaVersion')::bigint
        OR i.version IS DISTINCT FROM (p_binding->>'hypothesisVersion')::bigint
        OR i.document->>'status' IS DISTINCT FROM 'EXPERIMENT_DRAFTED'
        OR jsonb_typeof(i.document->'hypothesis') IS DISTINCT FROM 'object'
        OR encode(sha256(convert_to(backtest.quant_canonical(i.document->'hypothesis'),
            'UTF8')),'hex') IS DISTINCT FROM p_binding->>'hypothesisHash'
        OR NOT coalesce(i.document->'experimentDraftIds' ? (p_binding->>'experimentId'),false)
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      SELECT * INTO d FROM backtest.experiment_definitions
        WHERE definition_id=(p_binding->>'experimentId')::uuid;
      SELECT * INTO origin FROM backtest.experiment_agent_provenance
        WHERE definition_id=d.definition_id;
      IF NOT FOUND OR origin.run_id<>p_run.run_id OR origin.idea_id<>i.idea_id
        OR origin.idea_version>i.version
        OR NOT EXISTS (SELECT FROM agent_research.companion_idea_versions base
          WHERE base.idea_id=i.idea_id AND base.version=origin.idea_version
            AND base.document->'hypothesis'=i.document->'hypothesis')
        OR d.definition_version IS DISTINCT FROM (p_binding->>'definitionVersion')::integer
        OR d.fingerprint IS DISTINCT FROM p_binding->>'definitionHash'
        OR d.body->>'researchMode' IS DISTINCT FROM p_binding->>'researchMode'
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      SELECT * INTO v FROM backtest.experiment_validations WHERE definition_id=d.definition_id;
      IF NOT FOUND OR v.validation_id IS DISTINCT FROM (p_binding->>'validationId')::uuid
        OR v.fingerprint IS DISTINCT FROM p_binding->>'validationHash'
        OR v.body->'calendarVerified' IS DISTINCT FROM 'true'::jsonb
        OR v.body->'agentValidationContext' IS DISTINCT FROM
          backtest.agent_validation_context((v.body->'agentValidationContext'->>
            'snapshotVersion')::bigint)
        OR v.body->'agentValidationContext' IS NULL
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      budget := jsonb_build_object(
        'schemaVersion','quant-lab-v1','candidateCount',d.candidate_count,
        'reservedQueueSlots',d.candidate_count+2,'snapshotRows',v.body->'snapshotRows',
        'simulationBarSteps',v.body->'simulationBarSteps','publicationBytes',
        v.body->'publicationBytes','calendarConfirmed',true,'withinLimits',true);
      IF budget IS DISTINCT FROM p_binding->'resourceBudget'
        OR v.body->>'snapshotRows' IS NULL OR v.body->>'simulationBarSteps' IS NULL
        OR v.body->>'publicationBytes' IS NULL
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      approximation := p_binding->'approximation';
      IF i.document->'testability'->>'classification'='TESTABLE_WITH_DECLARED_APPROXIMATION'
        THEN
          IF NOT EXISTS (SELECT FROM agent_research.quant_approximation_decisions a
            JOIN agent_research.quant_approximation_proposals proposal USING(proposal_id)
            WHERE a.decision_id=approximation->>'decisionId' AND a.idea_id=i.idea_id
              AND a.idea_version=i.version AND a.actor=p_run.actor
              AND a.revision=(approximation->>'revision')::bigint
              AND a.fingerprint=approximation->>'hash'
              AND a.description=approximation->>'description'
              AND proposal.definition_id=d.definition_id)
          THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      ELSIF i.document->'testability'->>'classification' IS DISTINCT FROM 'EXACTLY_TESTABLE'
        OR approximation IS DISTINCT FROM 'null'::jsonb
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      agent_scope := 'agent:' || (p_run.agent_binding->>'agent_id') || ':' ||
        (p_run.agent_binding->>'version');
      PERFORM 1 FROM agent_research.quant_start_policies
        WHERE policy_id IN ('global',agent_scope) ORDER BY policy_id FOR UPDATE;
      SELECT jsonb_agg(jsonb_build_object('policyId',policy_id,'revision',revision,
        'hash',fingerprint) ORDER BY policy_id) INTO expected_policies FROM (
        SELECT policy_id,revision,fingerprint FROM agent_research.quant_start_policies
          WHERE policy_id IN ('global',agent_scope) AND enabled
        UNION ALL SELECT 'selection',1,d.policy_hash
      ) policies;
      SELECT jsonb_agg(policy ORDER BY policy->>'policyId') INTO received_policies
        FROM jsonb_array_elements(p_binding->'activePolicies') AS policy;
      IF jsonb_array_length(expected_policies)<>3
        OR expected_policies IS DISTINCT FROM received_policies
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      RETURN agent_scope;
    END $$;
    REVOKE ALL ON FUNCTION agent_research.check_quant_approval_binding(
      agent_research.runs,jsonb,timestamptz) FROM PUBLIC;
    """)


def _ports():
    op.execute("""
    CREATE FUNCTION agent_research.register_quant_approximation(p_idea text,p_version bigint,
      p_definition uuid,p_proposal text,p_hash text,p_description text) RETURNS void
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF current_setting('transaction_isolation')<>'read committed'
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('companion-idea:' || p_idea,0));
      IF NOT EXISTS (SELECT FROM agent_research.companion_idea_versions i
        JOIN backtest.experiment_agent_provenance d ON d.idea_id=i.idea_id
        WHERE i.idea_id=p_idea AND i.version=p_version AND d.definition_id=p_definition
          AND i.version=(SELECT max(v.version) FROM agent_research.companion_idea_versions v
            WHERE v.idea_id=p_idea)
          AND i.document->'testability'->>'classification'='TESTABLE_WITH_DECLARED_APPROXIMATION'
          AND i.document->'experimentDraftIds' ? p_definition::text)
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      INSERT INTO agent_research.quant_approximation_proposals
        (proposal_id,idea_id,idea_version,definition_id,fingerprint,description)
        VALUES (p_proposal,p_idea,p_version,p_definition,p_hash,p_description)
        ON CONFLICT (proposal_id) DO NOTHING;
      IF NOT EXISTS (SELECT FROM agent_research.quant_approximation_proposals p
        WHERE p.proposal_id=p_proposal AND p.idea_id=p_idea AND p.idea_version=p_version
          AND p.definition_id=p_definition AND p.fingerprint=p_hash
          AND p.description=p_description)
        THEN RAISE EXCEPTION 'backtest idempotency conflict'; END IF;
    END $$;

    CREATE FUNCTION agent_research.record_quant_approximation(p_actor text,p_idea text,
      p_version bigint,p_decision text,p_revision bigint,p_proposal text,p_expected text)
      RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE i agent_research.companion_idea_versions;
      candidate agent_research.quant_approximation_proposals;
    BEGIN
      IF p_actor IS DISTINCT FROM 'local-operator'
        OR current_setting('transaction_isolation')<>'read committed'
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('companion-idea:' || p_idea,0));
      SELECT v.* INTO i FROM agent_research.companion_idea_versions v
        JOIN agent_research.companion_ideas a USING(idea_id)
        WHERE v.idea_id=p_idea AND a.actor=p_actor ORDER BY v.version DESC LIMIT 1;
      IF NOT FOUND OR i.version IS DISTINCT FROM p_version
        OR i.document->'testability'->>'classification' IS DISTINCT FROM
          'TESTABLE_WITH_DECLARED_APPROXIMATION'
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      SELECT * INTO candidate FROM agent_research.quant_approximation_proposals
        WHERE proposal_id=p_proposal AND idea_id=p_idea AND idea_version=p_version
          AND fingerprint=p_expected;
      IF NOT FOUND THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      INSERT INTO agent_research.quant_approximation_decisions
        (decision_id,idea_id,idea_version,actor,revision,fingerprint,description,proposal_id)
        VALUES (p_decision,p_idea,p_version,p_actor,p_revision,candidate.fingerprint,
          candidate.description,p_proposal)
        ON CONFLICT (decision_id) DO NOTHING;
      IF NOT EXISTS (SELECT FROM agent_research.quant_approximation_decisions a
        WHERE a.decision_id=p_decision AND a.proposal_id=p_proposal AND a.revision=p_revision)
        THEN RAISE EXCEPTION 'backtest idempotency conflict'; END IF;
      RETURN candidate.fingerprint;
    END $$;

    CREATE FUNCTION agent_research.record_quant_approval_denial(p_id uuid,p_actor text,
      p_trace text,p_code text) RETURNS void LANGUAGE sql SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $$
      INSERT INTO agent_research.quant_approval_denials (approval_id,trace_id,code)
      SELECT approval_id,p_trace,p_code FROM agent_research.quant_approvals
        WHERE approval_id=p_id AND actor=p_actor
      ON CONFLICT DO NOTHING
    $$;

    CREATE FUNCTION agent_research.prepare_quant_approval(p_id uuid,p_binding jsonb,p_text text,
      p_key text)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE r agent_research.runs; scope text; h text; key_hash text;
      previous agent_research.quant_approvals;
    BEGIN
      IF current_setting('transaction_isolation')<>'read committed'
        OR p_text::jsonb IS DISTINCT FROM p_binding OR octet_length(p_text)>16384
        OR p_key IS NULL OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$'
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      h := encode(sha256(convert_to(p_text,'UTF8')),'hex');
      key_hash := encode(sha256(convert_to(p_key,'UTF8')),'hex');
      PERFORM pg_advisory_xact_lock(hashtextextended('quant-prepare:' || key_hash,0));
      SELECT * INTO previous FROM agent_research.quant_approvals WHERE prepare_key=key_hash;
      IF FOUND THEN
        IF previous.binding_hash<>h THEN RAISE EXCEPTION 'backtest idempotency conflict'; END IF;
        RETURN previous.approval_id;
      END IF;
      SELECT * INTO r FROM agent_research.runs WHERE run_id=(p_binding->>'runId')::uuid FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      scope := agent_research.check_quant_approval_binding(r,p_binding,clock_timestamp());
      INSERT INTO agent_research.quant_approvals
        (approval_id,run_id,definition_id,actor,agent_scope,binding,binding_hash,prepare_key,
          expires_at)
        VALUES (p_id,r.run_id,(p_binding->>'experimentId')::uuid,r.actor,scope,p_binding,h,
          key_hash,(p_binding->>'expiresAt')::timestamptz);
      RETURN p_id;
    EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION 'backtest idempotency conflict';
    END $$;

    CREATE FUNCTION agent_research.consume_quant_approval(p_id uuid,p_hash text,p_actor text,
      p_key text,p_trace text,p_code text,p_batch uuid) RETURNS uuid LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a agent_research.quant_approvals; r agent_research.runs; scope text;
      charged_at timestamptz; utc_day timestamptz; accepted uuid; key_hash text;
    BEGIN
      IF current_setting('transaction_isolation')<>'read committed'
        OR p_actor IS DISTINCT FROM 'local-operator' OR p_key IS NULL
        OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$'
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      SELECT run_id INTO a.run_id FROM agent_research.quant_approvals WHERE approval_id=p_id;
      SELECT * INTO r FROM agent_research.runs WHERE run_id=a.run_id FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      SELECT * INTO a FROM agent_research.quant_approvals WHERE approval_id=p_id FOR UPDATE;
      IF NOT FOUND OR a.actor IS DISTINCT FROM p_actor OR a.binding_hash IS DISTINCT FROM p_hash
        THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      key_hash := encode(sha256(convert_to(p_key,'UTF8')),'hex');
      IF a.batch_id IS NOT NULL THEN
        IF a.consume_key IS DISTINCT FROM key_hash
          THEN RAISE EXCEPTION 'backtest idempotency conflict'; END IF;
        RETURN a.batch_id;
      END IF;
      IF EXISTS (SELECT FROM agent_research.quant_approvals
        WHERE actor=p_actor AND consume_key=key_hash)
        THEN RAISE EXCEPTION 'backtest idempotency conflict'; END IF;
      charged_at := clock_timestamp();
      scope := agent_research.check_quant_approval_binding(r,a.binding,charged_at);
      PERFORM pg_advisory_xact_lock(894217345);
      charged_at := clock_timestamp();
      utc_day := date_trunc('day',charged_at,'UTC');
      IF EXISTS (SELECT FROM agent_research.quant_start_policies q
        WHERE q.policy_id IN ('global',scope) AND q.max_daily <= (
          SELECT count(*) FROM agent_research.quant_approvals c
            WHERE c.consumed_at>=utc_day AND (q.policy_id='global' OR c.agent_scope=scope)
        )) OR coalesce((r.agent_binding->'limits'->>'max_experiments')::integer,0) <= (
          SELECT count(*) FROM agent_research.quant_approvals c
            WHERE c.run_id=r.run_id AND c.consumed_at IS NOT NULL)
        THEN RAISE EXCEPTION 'quant budget exhausted'; END IF;
      accepted := backtest.request_approved_experiment_batch(p_batch,a.definition_id,
        (a.binding->>'definitionVersion')::integer,a.binding->>'definitionHash',
        (a.binding->>'validationId')::uuid,a.binding->>'validationHash',
        p_actor,encode(sha256(convert_to('apf-quant:' || p_id::text || ':' || key_hash,
          'UTF8')),'hex'),p_code,p_trace);
      IF NOT EXISTS (
        SELECT FROM backtest.experiment_batches b
          JOIN backtest.experiment_source_receipts s USING (batch_id)
        WHERE b.batch_id=accepted
          AND s.license_body->>'status' IN ('CONFIRMED','DECLARED_CONFIRMED')
          AND b.publication_bytes<=(a.binding->'resourceBudget'->>'publicationBytes')::bigint
          AND jsonb_build_object('snapshotVersion',b.snapshot_version,
            'trustVersion',s.trust_body->'version','licenseHash',s.license_fingerprint)
              =(SELECT v.body->'agentValidationContext' FROM backtest.experiment_validations v
                WHERE v.validation_id=b.validation_id)
      ) THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      IF clock_timestamp()>=a.expires_at THEN RAISE EXCEPTION 'quant approval conflict'; END IF;
      UPDATE agent_research.quant_approvals SET batch_id=accepted,consume_key=key_hash,
        consumed_at=charged_at WHERE approval_id=p_id;
      UPDATE agent_research.runs SET state='WAITING_FOR_BATCH',version=version+1,
        checkpoint_ref=accepted::text,updated_at=clock_timestamp() WHERE run_id=r.run_id
        RETURNING * INTO r;
      INSERT INTO agent_research.run_events (run_id,version,fence,state,occurred_at)
        VALUES (r.run_id,r.version,r.fence,r.state,r.updated_at);
      RETURN accepted;
    EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION 'backtest idempotency conflict';
    END $$;
    REVOKE ALL ON FUNCTION agent_research.prepare_quant_approval(uuid,jsonb,text,text),
      agent_research.consume_quant_approval(uuid,text,text,text,text,text,uuid),
      agent_research.register_quant_approximation(text,bigint,uuid,text,text,text),
      agent_research.record_quant_approximation(text,text,bigint,text,bigint,text,text),
      agent_research.record_quant_approval_denial(uuid,text,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.prepare_quant_approval(uuid,jsonb,text,text),
      agent_research.consume_quant_approval(uuid,text,text,text,text,text,uuid),
      agent_research.record_quant_approximation(text,text,bigint,text,bigint,text,text),
      agent_research.record_quant_approval_denial(uuid,text,text,text)
      TO atl_agent_quant_admission;
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        GRANT USAGE ON SCHEMA agent_research TO atl_paper_control_api;
        GRANT EXECUTE ON FUNCTION
          agent_research.register_quant_approximation(text,bigint,uuid,text,text,text)
          TO atl_paper_control_api;
      ELSE
        GRANT USAGE ON SCHEMA agent_research TO atl_control_api;
        GRANT EXECUTE ON FUNCTION
          agent_research.register_quant_approximation(text,bigint,uuid,text,text,text)
          TO atl_control_api;
      END IF;
    END $$;
    """)


def _execution_budget():
    op.execute("""
    CREATE FUNCTION backtest.agent_validation_context(p_snapshot bigint) RETURNS jsonb
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT jsonb_build_object('snapshotVersion',v.version,'trustVersion',t.version,
        'licenseHash',encode(sha256(convert_to(backtest.quant_canonical(to_jsonb(l)),
          'UTF8')),'hex'))
      FROM research.version_state v CROSS JOIN research.trust_version_state t
        CROSS JOIN backtest.source_license_status l
      WHERE v.singleton AND t.singleton AND v.version=p_snapshot
        AND l.status IN ('CONFIRMED','DECLARED_CONFIRMED')
    $$;
    REVOKE ALL ON FUNCTION backtest.agent_validation_context(bigint) FROM PUBLIC;
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        GRANT EXECUTE ON FUNCTION backtest.agent_validation_context(bigint)
          TO atl_paper_control_api;
        GRANT SELECT ON backtest.experiment_agent_provenance TO atl_paper_control_api;
      ELSE
        GRANT EXECUTE ON FUNCTION backtest.agent_validation_context(bigint) TO atl_control_api;
        GRANT SELECT ON backtest.experiment_agent_provenance TO atl_control_api;
      END IF;
    END $$;
    CREATE FUNCTION backtest.enforce_agent_validation_context() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE context jsonb;
    BEGIN
      IF EXISTS (SELECT FROM backtest.experiment_agent_provenance
        WHERE definition_id=NEW.definition_id) THEN
        context := NEW.body->'agentValidationContext';
        IF context IS NULL OR context IS DISTINCT FROM
          backtest.agent_validation_context((context->>'snapshotVersion')::bigint)
          THEN RAISE EXCEPTION 'APPROVAL_REQUIRED'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION backtest.enforce_agent_validation_context() FROM PUBLIC;
    CREATE TRIGGER enforce_agent_validation_context
      BEFORE INSERT ON backtest.experiment_validations
      FOR EACH ROW EXECUTE FUNCTION backtest.enforce_agent_validation_context();
    CREATE FUNCTION backtest.agent_batch_budget(p_batch uuid) RETURNS jsonb
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT binding->'resourceBudget' FROM agent_research.quant_approvals
      WHERE batch_id=p_batch
    $$;
    REVOKE ALL ON FUNCTION backtest.agent_batch_budget(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION backtest.agent_batch_budget(uuid) TO atl_backtest_worker;
    ALTER FUNCTION backtest.current_quant_gate(uuid) RENAME TO current_quant_gate_private;
    REVOKE ALL ON FUNCTION backtest.current_quant_gate_private(uuid)
      FROM PUBLIC,atl_backtest_worker,atl_control_api;
    DO $$ BEGIN
      IF EXISTS (SELECT FROM pg_roles WHERE rolname='atl_paper_control_api') THEN
        REVOKE ALL ON FUNCTION backtest.current_quant_gate_private(uuid) FROM atl_paper_control_api;
      END IF;
    END $$;
    CREATE FUNCTION backtest.current_quant_gate(p_assessment uuid) RETURNS text
      LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT CASE WHEN backtest.agent_batch_budget(p_assessment) IS NOT NULL
        AND NOT EXISTS (SELECT FROM backtest.source_license_status
          WHERE status IN ('CONFIRMED','DECLARED_CONFIRMED'))
        THEN CASE WHEN EXISTS (SELECT FROM backtest.source_license_status WHERE status='DENIED')
          THEN 'LICENSE_DENIED' ELSE 'LICENSE_UNKNOWN' END
        ELSE backtest.current_quant_gate_private(p_assessment) END
    $$;
    REVOKE ALL ON FUNCTION backtest.current_quant_gate(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION backtest.current_quant_gate(uuid) TO atl_backtest_worker;
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        GRANT EXECUTE ON FUNCTION backtest.current_quant_gate(uuid) TO atl_paper_control_api;
      ELSE
        GRANT EXECUTE ON FUNCTION backtest.current_quant_gate(uuid) TO atl_control_api;
      END IF;
    END $$;
    CREATE FUNCTION backtest.enforce_agent_batch_budget() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE budget jsonb;
    BEGIN
      budget := backtest.agent_batch_budget(NEW.batch_id);
      IF budget IS NOT NULL AND (
        NEW.bar_steps>(budget->>'simulationBarSteps')::bigint
        OR NEW.publication_bytes>(budget->>'publicationBytes')::bigint
        OR NEW.slots>(budget->>'reservedQueueSlots')::integer
      ) THEN RAISE EXCEPTION 'quant budget exhausted'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION backtest.enforce_agent_batch_budget() FROM PUBLIC;
    CREATE TRIGGER enforce_agent_batch_budget
      BEFORE UPDATE OF bar_steps,publication_bytes,slots ON backtest.experiment_batches
      FOR EACH ROW EXECUTE FUNCTION backtest.enforce_agent_batch_budget();
    """)


def downgrade():
    raise RuntimeError("Quant approvals retain immutable operator and admission evidence")
