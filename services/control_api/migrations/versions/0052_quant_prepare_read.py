"""Read the expert Quant definition attached to the active Research lease."""

from alembic import op

revision = "0052_quant_prepare_read"
down_revision = "0051_quant_started_receipt"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION agent_research.claimed_quant_template(
      p_run uuid,p_owner uuid,p_fence bigint,p_version bigint,
      p_idea_version bigint,p_dialogue_version bigint
    ) RETURNS jsonb LANGUAGE sql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $$
      SELECT jsonb_build_object(
        'id',q.definition_id,'version',q.definition_version,'hash',q.fingerprint,
        'ideaId',i.idea_id,'ideaVersion',v.version,'dataContext',(
          SELECT backtest.agent_validation_context(s.version)
          FROM research.version_state s WHERE s.singleton
        ),'parameters',(
          SELECT jsonb_object_agg(field.key,field.value) FROM jsonb_each(q.body) field
          WHERE field.key IN ('schemaVersion','predecessorDefinitionId','supersedesBatchId',
            'searchSpaces','protocol','strategySelectionPolicy','instrumentId','symbol',
            'currency','feed','interval','adjustment','researchMode','dataSelectionPolicyVersion',
            'startCash','feeUsd','slippageBps','quantityStep')
        )
      )
      FROM agent_research.runs r
      JOIN agent_research.companion_dialogues d ON d.dialogue_id=r.input_ref
      JOIN agent_research.companion_ideas i ON i.idea_id=d.idea_id AND i.actor=r.actor
      CROSS JOIN LATERAL (
        SELECT version,idea_version,document FROM agent_research.companion_dialogue_versions
        WHERE dialogue_id=d.dialogue_id ORDER BY version DESC LIMIT 1
      ) dialogue
      CROSS JOIN LATERAL (
        SELECT version,document FROM agent_research.companion_idea_versions
        WHERE idea_id=i.idea_id ORDER BY version DESC LIMIT 1
      ) v
      JOIN backtest.experiment_definitions q
        ON q.definition_id=CASE
          WHEN pg_input_is_valid(dialogue.document->'expert'->>'definition_id','uuid')
          THEN (dialogue.document->'expert'->>'definition_id')::uuid END
       AND q.definition_version::text=dialogue.document->'expert'->>'definition_version'
       AND q.fingerprint=dialogue.document->'expert'->>'definition_hash'
       AND q.actor=r.actor
      WHERE r.run_id=p_run AND r.owner=p_owner AND r.fence=p_fence AND r.version=p_version
        AND r.actor='local-operator' AND r.kind='DIALOGUE' AND r.state='RUNNING'
        AND r.lease_until>clock_timestamp() AND r.deadline_at>clock_timestamp()
        AND v.version=p_idea_version AND v.version=dialogue.idea_version
        AND dialogue.version=p_dialogue_version AND v.document->>'status'='REVIEWED'
        AND jsonb_typeof(v.document->'hypothesis')='object'
    $$;
    REVOKE ALL ON FUNCTION
      agent_research.claimed_quant_template(uuid,uuid,bigint,bigint,bigint,bigint)
      FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION
      agent_research.claimed_quant_template(uuid,uuid,bigint,bigint,bigint,bigint)
      TO atl_agent_research;
    """)


def downgrade():
    op.execute("""
      DROP FUNCTION agent_research.claimed_quant_template(uuid,uuid,bigint,bigint,bigint,bigint)
    """)
