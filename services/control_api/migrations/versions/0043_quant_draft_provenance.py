"""Keep agent-derived experiment definitions out of the manual admission path."""

from alembic import op

revision = "0043_quant_draft_provenance"
down_revision = "0042_research_companion"
branch_labels = None
depends_on = None

_ADMISSION_ARGS = "uuid,uuid,integer,text,uuid,text,text,text,text,text"
_DRAFT_ARGS = "uuid,text,jsonb,text,text,text,text,jsonb,uuid,text,bigint"


def _owner_only(signature):
    # Renaming retains the original ACL. Remove every non-owner grant, including
    # any role added since the original Quant migration.
    op.execute(f"""
    DO $$ DECLARE entry record;
    BEGIN
      REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;
      FOR entry IN
        SELECT DISTINCT r.rolname
        FROM pg_catalog.pg_proc fn
        CROSS JOIN LATERAL pg_catalog.aclexplode(fn.proacl) acl
        JOIN pg_catalog.pg_roles r ON r.oid=acl.grantee
        WHERE fn.oid='{signature}'::regprocedure AND acl.grantee<>fn.proowner
      LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION {signature} FROM %I CASCADE',entry.rolname);
      END LOOP;
    END $$;
    """)


def upgrade():
    op.execute("""
    CREATE TABLE backtest.experiment_agent_provenance (
      definition_id uuid PRIMARY KEY REFERENCES backtest.experiment_definitions (definition_id),
      origin_definition_id uuid NOT NULL REFERENCES backtest.experiment_definitions (definition_id),
      run_id uuid NOT NULL REFERENCES agent_research.runs (run_id),
      idea_id text NOT NULL,
      idea_version bigint NOT NULL,
      FOREIGN KEY (idea_id,idea_version)
        REFERENCES agent_research.companion_idea_versions (idea_id,version)
    );
    REVOKE ALL ON backtest.experiment_agent_provenance FROM PUBLIC;
    CREATE TRIGGER immutable_agent_provenance BEFORE UPDATE OR DELETE
      ON backtest.experiment_agent_provenance FOR EACH ROW
      EXECUTE FUNCTION research.reject_mutation();

    CREATE FUNCTION backtest.inherit_experiment_agent_provenance() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE inherited backtest.experiment_agent_provenance;
      superseded backtest.experiment_agent_provenance;
    BEGIN
      SELECT * INTO inherited FROM backtest.experiment_agent_provenance
        WHERE definition_id=NEW.predecessor_id;
      SELECT p.* INTO superseded FROM backtest.experiment_batches b
        JOIN backtest.experiment_agent_provenance p USING (definition_id)
        WHERE b.batch_id=NEW.supersedes_batch_id;
      IF inherited.definition_id IS NOT NULL AND superseded.definition_id IS NOT NULL
        AND inherited.origin_definition_id<>superseded.origin_definition_id THEN
        RAISE EXCEPTION 'quant provenance conflict';
      END IF;
      IF inherited.definition_id IS NULL THEN inherited:=superseded; END IF;
      IF inherited.definition_id IS NOT NULL THEN
        INSERT INTO backtest.experiment_agent_provenance
          (definition_id,origin_definition_id,run_id,idea_id,idea_version)
        VALUES (NEW.definition_id,inherited.origin_definition_id,inherited.run_id,
          inherited.idea_id,inherited.idea_version);
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER inherit_agent_provenance AFTER INSERT ON backtest.experiment_definitions
      FOR EACH ROW EXECUTE FUNCTION backtest.inherit_experiment_agent_provenance();

    CREATE FUNCTION backtest.request_agent_experiment_definition(p_id uuid,p_actor text,
      p_body jsonb,p_canonical text,p_hash text,p_policy_hash text,p_policy_canonical text,
      p_candidates jsonb,p_run uuid,p_idea text,p_idea_version bigint) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE created uuid; inherited backtest.experiment_agent_provenance;
    BEGIN
      IF current_setting('transaction_isolation')<>'read committed'
        OR p_actor IS DISTINCT FROM 'local-operator' THEN
        RAISE EXCEPTION 'quant provenance conflict';
      END IF;
      PERFORM 1 FROM agent_research.runs WHERE run_id=p_run AND actor=p_actor
        AND state IN ('RUNNING','WAITING_FOR_INPUT','WAITING_FOR_APPROVAL')
        AND deadline_at>clock_timestamp() FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'quant provenance conflict'; END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('companion-idea:' || p_idea,0));
      IF p_idea_version IS DISTINCT FROM (
        SELECT max(v.version) FROM agent_research.companion_idea_versions v
        JOIN agent_research.companion_ideas i USING (idea_id)
        WHERE v.idea_id=p_idea AND i.actor=p_actor
      ) OR p_idea_version IS NULL THEN RAISE EXCEPTION 'quant provenance conflict'; END IF;
      created:=backtest.request_experiment_definition(p_id,p_actor,p_body,p_canonical,p_hash,
        p_policy_hash,p_policy_canonical,p_candidates);
      SELECT * INTO inherited FROM backtest.experiment_agent_provenance
        WHERE definition_id=created;
      IF FOUND THEN
        IF (inherited.run_id,inherited.idea_id,inherited.idea_version)
          IS DISTINCT FROM (p_run,p_idea,p_idea_version) THEN
          RAISE EXCEPTION 'quant provenance conflict';
        END IF;
      ELSE
        INSERT INTO backtest.experiment_agent_provenance
          (definition_id,origin_definition_id,run_id,idea_id,idea_version)
        VALUES (created,created,p_run,p_idea,p_idea_version);
      END IF;
      RETURN created;
    END $$;
    """)

    op.execute(f"""
    ALTER FUNCTION backtest.request_experiment_batch({_ADMISSION_ARGS})
      RENAME TO request_experiment_batch_private;
    CREATE FUNCTION backtest.request_experiment_batch(p_id uuid,p_definition uuid,p_version integer,
      p_hash text,p_validation uuid,p_validation_hash text,p_actor text,p_key text,p_code text,
      p_trace text) RETURNS uuid LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE is_bound boolean;
    BEGIN
      -- Read definition existence and provenance in one snapshot. A draft that is
      -- uncommitted at this point must fail rather than reach the canonical function.
      SELECT p.definition_id IS NOT NULL INTO is_bound FROM backtest.experiment_definitions d
        LEFT JOIN backtest.experiment_agent_provenance p USING (definition_id)
        WHERE d.definition_id=p_definition;
      IF NOT FOUND THEN RAISE EXCEPTION 'quant definition conflict'; END IF;
      IF is_bound THEN RAISE EXCEPTION 'APPROVAL_REQUIRED'; END IF;
      RETURN backtest.request_experiment_batch_private(p_id,p_definition,p_version,p_hash,
        p_validation,p_validation_hash,p_actor,p_key,p_code,p_trace);
    END $$;

    CREATE FUNCTION backtest.request_approved_experiment_batch(p_id uuid,p_definition uuid,
      p_version integer,p_hash text,p_validation uuid,p_validation_hash text,p_actor text,
      p_key text,p_code text,p_trace text) RETURNS uuid LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    BEGIN
      IF NOT EXISTS(SELECT FROM backtest.experiment_agent_provenance
        WHERE definition_id=p_definition) THEN RAISE EXCEPTION 'quant provenance conflict'; END IF;
      RETURN backtest.request_experiment_batch_private(p_id,p_definition,p_version,p_hash,
        p_validation,p_validation_hash,p_actor,p_key,p_code,p_trace);
    END $$;
    """)

    for signature in (
        f"backtest.request_experiment_batch_private({_ADMISSION_ARGS})",
        f"backtest.request_approved_experiment_batch({_ADMISSION_ARGS})",
        f"backtest.request_experiment_batch({_ADMISSION_ARGS})",
        f"backtest.request_agent_experiment_definition({_DRAFT_ARGS})",
        "backtest.inherit_experiment_agent_provenance()",
    ):
        _owner_only(signature)

    op.execute(f"""
    DO $$ DECLARE control_role text;
    BEGIN
      control_role:=CASE current_database() WHEN 'lab_paper_runtime' THEN 'atl_paper_control_api'
        ELSE 'atl_control_api' END;
      EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',
        'backtest.request_experiment_batch({_ADMISSION_ARGS})',control_role);
      EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',
        'backtest.request_agent_experiment_definition({_DRAFT_ARGS})',control_role);
    END $$;
    """)


def downgrade():
    raise RuntimeError("Stop and drain research workers; preserve experiment provenance")
