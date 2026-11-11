"""Transactional idempotency for redacted Research operator commands."""

from alembic import op

revision = "0050_agent_control_commands"
down_revision = "0049_processing_operations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE agent_research.companion_events DROP CONSTRAINT companion_events_code_check;
    ALTER TABLE agent_research.companion_events ADD CONSTRAINT companion_events_code_check
      CHECK(code IN ('ORIGINAL_ACCEPTED','LOCAL_CLARIFICATION','INTERPRETATION_ACCEPTED',
        'INTERPRETATION_ACTION_DENIED','HYPOTHESIS_CONFIRMED','CHANGES_REQUESTED',
        'IDEA_REVISED','EXPERT_BOUND'));

    CREATE FUNCTION agent_research.expert_definition_symbol(
      p_actor text,p_id uuid,p_version integer,p_hash text
    ) RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT body->>'symbol' FROM backtest.experiment_definitions
      WHERE definition_id=p_id AND definition_version=p_version AND actor=p_actor
        AND fingerprint=p_hash
    $$;
    REVOKE ALL ON FUNCTION agent_research.expert_definition_symbol(text,uuid,integer,text)
      FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.expert_definition_symbol(text,uuid,integer,text)
      TO atl_agent_research;

    ALTER TABLE agent_research.quant_approximation_decisions ADD COLUMN recorded_at timestamptz
      NOT NULL DEFAULT clock_timestamp();
    CREATE FUNCTION agent_research.record_control_approximation(
      p_actor text,p_idea text,p_idea_version bigint,p_dialogue text,p_dialogue_version bigint,
      p_semantic_id text,p_semantic_hash text,p_decision text,p_approximation text,p_hash text
    ) RETURNS timestamptz
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE current_version bigint; current_idea bigint; proposal jsonb;
    BEGIN
      PERFORM pg_advisory_xact_lock(hashtextextended('companion-idea:' || p_idea,0));
      SELECT v.version,v.idea_version,(v.document->>'proposal_json')::jsonb
        INTO current_version,current_idea,proposal
      FROM agent_research.companion_dialogue_versions v
      JOIN agent_research.companion_ideas i ON i.idea_id=v.idea_id
      WHERE v.idea_id=p_idea AND v.dialogue_id=p_dialogue AND i.actor=p_actor
      ORDER BY v.version DESC LIMIT 1;
      IF current_version IS DISTINCT FROM p_dialogue_version
        OR current_idea IS DISTINCT FROM p_idea_version
        OR proposal->>'proposalId' IS DISTINCT FROM p_semantic_id
        OR proposal->>'proposalHash' IS DISTINCT FROM p_semantic_hash THEN RETURN NULL; END IF;
      PERFORM agent_research.record_quant_approximation(
        p_actor,p_idea,p_idea_version,p_decision,1,p_approximation,p_hash);
      RETURN (SELECT recorded_at FROM agent_research.quant_approximation_decisions
        WHERE decision_id=p_decision AND actor=p_actor);
    END $$;
    REVOKE ALL ON FUNCTION agent_research.record_control_approximation(
      text,text,bigint,text,bigint,text,text,text,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.record_control_approximation(
      text,text,bigint,text,bigint,text,text,text,text,text) TO atl_agent_research;

    CREATE TABLE agent_research.control_commands (
      actor text NOT NULL CHECK (actor='local-operator'),
      operation text NOT NULL CHECK (operation IN (
        'cancelAgentRun','updateAgentSchedule','commandAgentSchedule','decideAgentQuantApproval',
        'decideAgentHypothesis')),
      key_hash text NOT NULL CHECK (key_hash ~ '^[a-f0-9]{64}$'),
      payload_hash text NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
      response jsonb NOT NULL CHECK (jsonb_typeof(response)='object'),
      PRIMARY KEY (actor,operation,key_hash)
    );
    REVOKE ALL ON agent_research.control_commands FROM PUBLIC;
    GRANT SELECT,INSERT ON agent_research.control_commands TO atl_agent_research;
    GRANT UPDATE(version,epoch,state,configuration,active_since)
      ON agent_research.discovery_schedules TO atl_agent_research;
    GRANT INSERT ON agent_research.discovery_schedule_events TO atl_agent_research;
    CREATE FUNCTION agent_research.initialize_daily_schedule(p_configuration jsonb) RETURNS void
      LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      WITH inserted AS (
        INSERT INTO agent_research.discovery_schedules
          (schedule_id,version,epoch,state,configuration)
        SELECT 'daily-research',1,1,'DISABLED',p_configuration
        WHERE jsonb_typeof(p_configuration)='object'
          AND p_configuration->'limits'->>'maxModelCalls'='0'
          AND p_configuration->'limits'->>'maxIdeas'='0'
        ON CONFLICT (schedule_id) DO NOTHING RETURNING *
      ) INSERT INTO agent_research.discovery_schedule_events
        (schedule_id,version,epoch,state,configuration,action)
        SELECT schedule_id,version,epoch,state,configuration,'CREATE' FROM inserted
    $$;
    REVOKE ALL ON FUNCTION agent_research.initialize_daily_schedule(jsonb) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.initialize_daily_schedule(jsonb) TO atl_agent_research;
    CREATE TABLE agent_research.control_approval_rejections (
      approval_id uuid PRIMARY KEY REFERENCES agent_research.quant_approvals,
      decided_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    REVOKE ALL ON agent_research.control_approval_rejections FROM PUBLIC;
    CREATE FUNCTION agent_research.agent_run_started(p_run uuid,p_actor text) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT EXISTS (SELECT FROM agent_research.quant_approvals
        WHERE run_id=p_run AND actor=p_actor AND consumed_at IS NOT NULL)
    $$;
    REVOKE ALL ON FUNCTION agent_research.agent_run_started(uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.agent_run_started(uuid,text) TO atl_agent_research;

    CREATE FUNCTION agent_research.agent_approval_page(
      p_actor text,p_id uuid,p_after uuid,p_limit integer
    ) RETURNS TABLE(approval_id uuid, binding jsonb, binding_hash text, batch_id uuid,
      decided_at timestamptz, requested_at timestamptz, state text, idea jsonb, definition jsonb)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT a.approval_id,a.binding,a.binding_hash,a.batch_id,coalesce(a.consumed_at,x.decided_at),
        coalesce(e.occurred_at,r.created_at),
        CASE WHEN a.consumed_at IS NOT NULL THEN 'STARTED'
             WHEN x.decided_at IS NOT NULL THEN 'REJECTED'
             WHEN r.state<>'WAITING_FOR_APPROVAL' OR r.version<>(a.binding->>'runVersion')::bigint
               THEN 'INVALIDATED'
             WHEN a.expires_at<=statement_timestamp() THEN 'EXPIRED'
             ELSE 'PENDING' END,
        i.document,d.body
      FROM agent_research.quant_approvals a
      JOIN agent_research.runs r ON r.run_id=a.run_id AND r.actor=a.actor
      JOIN agent_research.companion_ideas c ON c.idea_id=a.binding->>'ideaId' AND c.actor=a.actor
      JOIN agent_research.companion_idea_versions i ON i.idea_id=c.idea_id
        AND i.version=(a.binding->>'ideaVersion')::bigint
      JOIN backtest.experiment_definitions d ON d.definition_id=a.definition_id
      LEFT JOIN agent_research.run_events e ON e.run_id=r.run_id
        AND e.version=(a.binding->>'runVersion')::bigint
      LEFT JOIN agent_research.control_approval_rejections x ON x.approval_id=a.approval_id
      WHERE a.actor=p_actor AND (p_id IS NULL OR a.approval_id=p_id)
        AND (p_after IS NULL OR a.approval_id>p_after)
      ORDER BY a.approval_id LIMIT least(greatest(coalesce(p_limit,1),1),201)
    $$;
    REVOKE ALL ON FUNCTION agent_research.agent_approval_page(text,uuid,uuid,integer) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.agent_approval_page(text,uuid,uuid,integer)
      TO atl_agent_research;

    CREATE FUNCTION agent_research.record_approval_rejection(
      p_actor text,p_id uuid,p_hash text
    ) RETURNS timestamptz
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a agent_research.quant_approvals; r agent_research.runs; decided timestamptz;
    BEGIN
      SELECT * INTO a FROM agent_research.quant_approvals
        WHERE approval_id=p_id AND actor=p_actor;
      IF NOT FOUND THEN RETURN NULL; END IF;
      SELECT * INTO r FROM agent_research.runs WHERE run_id=a.run_id AND actor=p_actor FOR UPDATE;
      IF NOT FOUND OR r.state<>'WAITING_FOR_APPROVAL'
        OR r.version<>(a.binding->>'runVersion')::bigint
        OR a.expires_at<=clock_timestamp() OR a.binding_hash<>p_hash THEN RETURN NULL; END IF;
      SELECT * INTO a FROM agent_research.quant_approvals
        WHERE approval_id=p_id AND actor=p_actor FOR UPDATE;
      IF a.consumed_at IS NOT NULL THEN RETURN NULL; END IF;
      INSERT INTO agent_research.control_approval_rejections(approval_id) VALUES(p_id)
        ON CONFLICT DO NOTHING RETURNING decided_at INTO decided;
      IF decided IS NULL THEN RETURN NULL; END IF;
      UPDATE agent_research.runs SET state='CANCELLED',version=version+1,fence=fence+1,
        updated_at=decided WHERE run_id=r.run_id;
      INSERT INTO agent_research.run_events(run_id,version,fence,state,occurred_at)
        VALUES(r.run_id,r.version+1,r.fence+1,'CANCELLED',decided);
      RETURN decided;
    END $$;
    REVOKE ALL ON FUNCTION agent_research.record_approval_rejection(text,uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.record_approval_rejection(text,uuid,text)
      TO atl_agent_research;

    CREATE FUNCTION agent_research.ready_quant_runs() RETURNS integer
      LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      WITH eligible AS (
        SELECT r.run_id FROM agent_research.runs r
        JOIN agent_research.quant_approvals a USING(run_id)
        JOIN backtest.experiment_batches b ON b.batch_id=a.batch_id
        WHERE r.state='WAITING_FOR_BATCH' AND r.deadline_at>clock_timestamp()
          AND a.consumed_at IS NOT NULL AND b.status IN ('SUCCEEDED','BLOCKED','FAILED')
        ORDER BY r.run_id FOR UPDATE OF r SKIP LOCKED LIMIT 16
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
    CREATE FUNCTION agent_research.claimed_quant_result(p_run uuid,p_owner uuid,p_fence bigint)
      RETURNS TABLE(batch_id uuid,actor text,status text)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT b.batch_id,r.actor,b.status FROM agent_research.runs r
      JOIN agent_research.quant_approvals a USING(run_id)
      JOIN backtest.experiment_batches b ON b.batch_id=a.batch_id
      WHERE r.run_id=p_run AND r.owner=p_owner AND r.fence=p_fence AND r.state='RUNNING'
        AND r.lease_until>statement_timestamp() AND r.deadline_at>statement_timestamp()
        AND a.consumed_at IS NOT NULL
    $$;
    REVOKE ALL ON FUNCTION agent_research.ready_quant_runs(),
      agent_research.claimed_quant_result(uuid,uuid,bigint) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION agent_research.ready_quant_runs(),
      agent_research.claimed_quant_result(uuid,uuid,bigint) TO atl_agent_research;
    """)


def downgrade():
    raise RuntimeError("Research control commands preserve idempotency history")
