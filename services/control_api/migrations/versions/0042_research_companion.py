"""Persist immutable research ideas and dialogue revisions."""

from alembic import op

revision = "0042_research_companion"
down_revision = "0041_agent_provider_probe"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_research.companion_ideas (
      idea_id text PRIMARY KEY CHECK (idea_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      actor text NOT NULL CHECK (actor ~ '^[A-Za-z0-9._:-]{1,128}$'),
      command_key text NOT NULL CHECK (command_key ~ '^[0-9a-f]{64}$'),
      command_hash text NOT NULL CHECK (command_hash ~ '^[0-9a-f]{64}$'),
      UNIQUE (actor,command_key)
    );
    CREATE INDEX companion_ideas_actor ON agent_research.companion_ideas (actor,idea_id);
    CREATE TABLE agent_research.companion_idea_versions (
      idea_id text NOT NULL REFERENCES agent_research.companion_ideas (idea_id),
      version bigint NOT NULL CHECK (version>0),
      previous_version bigint,
      hypothesis_signature text CHECK (hypothesis_signature ~ '^[0-9a-f]{64}$'),
      document jsonb NOT NULL CHECK (jsonb_typeof(document)='object'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY (idea_id,version),
      FOREIGN KEY (idea_id,previous_version)
        REFERENCES agent_research.companion_idea_versions (idea_id,version),
      CHECK ((version=1 AND previous_version IS NULL)
        OR (version>1 AND previous_version IS NOT NULL AND previous_version=version-1))
    );
    CREATE INDEX companion_exact_hypothesis
      ON agent_research.companion_idea_versions (hypothesis_signature,idea_id,version DESC)
      WHERE hypothesis_signature IS NOT NULL;
    CREATE TABLE agent_research.companion_dialogues (
      dialogue_id text PRIMARY KEY CHECK (dialogue_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      idea_id text NOT NULL REFERENCES agent_research.companion_ideas (idea_id),
      UNIQUE (dialogue_id,idea_id)
    );
    CREATE TABLE agent_research.companion_dialogue_versions (
      dialogue_id text NOT NULL,
      idea_id text NOT NULL,
      version bigint NOT NULL CHECK (version>0),
      previous_version bigint,
      idea_version bigint NOT NULL,
      document jsonb NOT NULL CHECK (jsonb_typeof(document)='object'),
      command_key text CHECK (command_key ~ '^[0-9a-f]{64}$'),
      command_hash text CHECK (command_hash ~ '^[0-9a-f]{64}$'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY (dialogue_id,version),
      UNIQUE (dialogue_id,command_key),
      FOREIGN KEY (dialogue_id,idea_id)
        REFERENCES agent_research.companion_dialogues (dialogue_id,idea_id),
      FOREIGN KEY (idea_id,idea_version)
        REFERENCES agent_research.companion_idea_versions (idea_id,version),
      FOREIGN KEY (dialogue_id,previous_version)
        REFERENCES agent_research.companion_dialogue_versions (dialogue_id,version),
      CHECK ((version=1 AND previous_version IS NULL AND command_key IS NULL)
        OR (version>1 AND previous_version IS NOT NULL AND previous_version=version-1
          AND command_key IS NOT NULL)),
      CHECK ((command_key IS NULL)=(command_hash IS NULL))
    );
    CREATE TABLE agent_research.companion_originals (
      dialogue_id text NOT NULL,
      version bigint NOT NULL,
      original_id text NOT NULL CHECK (original_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      turn_id text NOT NULL CHECK (turn_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
      original_text text NOT NULL CHECK (length(original_text) BETWEEN 1 AND 4096),
      PRIMARY KEY (dialogue_id,original_id),
      UNIQUE (dialogue_id,turn_id),
      FOREIGN KEY (dialogue_id,version)
        REFERENCES agent_research.companion_dialogue_versions (dialogue_id,version)
    );
    CREATE TABLE agent_research.companion_events (
      dialogue_id text NOT NULL,
      version bigint NOT NULL,
      code text NOT NULL CHECK (code IN ('ORIGINAL_ACCEPTED','LOCAL_CLARIFICATION',
        'INTERPRETATION_ACCEPTED','INTERPRETATION_ACTION_DENIED','HYPOTHESIS_CONFIRMED',
        'CHANGES_REQUESTED','IDEA_REVISED')),
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY (dialogue_id,version),
      FOREIGN KEY (dialogue_id,version)
        REFERENCES agent_research.companion_dialogue_versions (dialogue_id,version)
    );
    REVOKE ALL ON agent_research.companion_ideas,agent_research.companion_idea_versions,
      agent_research.companion_dialogues,agent_research.companion_dialogue_versions,
      agent_research.companion_originals,agent_research.companion_events FROM PUBLIC;
    GRANT SELECT,INSERT ON agent_research.companion_ideas,agent_research.companion_idea_versions,
      agent_research.companion_dialogues,agent_research.companion_dialogue_versions,
      agent_research.companion_originals,agent_research.companion_events
      TO atl_agent_research;
    """)


def downgrade():
    op.execute("""
    DROP TABLE agent_research.companion_events,agent_research.companion_originals;
    DROP TABLE agent_research.companion_dialogue_versions,agent_research.companion_dialogues,
      agent_research.companion_idea_versions,agent_research.companion_ideas;
    """)
