"""Append-only isolated research identities and commit-serialized revisions."""

from alembic import op

revision = "0021_research_foundation"
down_revision = "0020_paper_read_models"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA research")
    op.execute("REVOKE ALL ON SCHEMA research FROM PUBLIC")
    op.execute("""
    CREATE TABLE research.version_state (
      singleton boolean PRIMARY KEY CHECK(singleton),
      version bigint NOT NULL CHECK(version BETWEEN 0 AND 9007199254740991));
    INSERT INTO research.version_state VALUES(true,0);
    CREATE FUNCTION research.reserve_version() RETURNS bigint
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research,pg_temp AS $$
    DECLARE result bigint;
    BEGIN
      PERFORM set_config('lock_timeout','1s',true);
      UPDATE research.version_state SET version=version+1 WHERE singleton
        RETURNING version INTO result;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION research.reserve_version() FROM PUBLIC;
    CREATE FUNCTION research.stamp() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research,pg_temp AS $$
    BEGIN
      NEW.commit_version:=research.reserve_version();
      NEW.ingested_at:=clock_timestamp();
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION research.stamp() FROM PUBLIC;
    CREATE FUNCTION research.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'immutable research record'; END $$;
    REVOKE ALL ON FUNCTION research.reject_mutation() FROM PUBLIC;
    CREATE TABLE research.instruments (
      instrument_id uuid PRIMARY KEY, asset_class text NOT NULL
        CHECK(asset_class IN ('EQUITY','ETF','CRYPTO','FOREX','DERIVATIVE')),
      mic text NOT NULL CHECK(mic ~ '^[A-Z0-9]{4}$'),
      currency text NOT NULL CHECK(currency ~ '^[A-Z]{3}$'),
      base_unit text NOT NULL CHECK(length(base_unit) BETWEEN 1 AND 16),
      quote_unit text NOT NULL CHECK(length(quote_unit) BETWEEN 1 AND 16),
      commit_version bigint NOT NULL, ingested_at timestamptz NOT NULL);
    CREATE TABLE research.symbol_mappings (
      mapping_id uuid PRIMARY KEY, instrument_id uuid NOT NULL REFERENCES research.instruments,
      provider text NOT NULL CHECK(provider ~ '^[A-Za-z0-9_-]{1,64}$'),
      symbol text NOT NULL CHECK(symbol ~ '^[A-Z][A-Z0-9./-]{0,15}$'),
      valid_from timestamptz NOT NULL, valid_to timestamptz,
      commit_version bigint NOT NULL, ingested_at timestamptz NOT NULL,
      CHECK(valid_to IS NULL OR valid_to>valid_from));
    CREATE FUNCTION research.check_mapping() RETURNS trigger LANGUAGE plpgsql
      SET search_path=pg_catalog,research,pg_temp AS $$
    BEGIN
      -- stamp holds the global versions row lock until commit, including concurrent inserts.
      IF EXISTS(SELECT 1 FROM research.symbol_mappings m WHERE m.provider=NEW.provider
        AND (m.instrument_id=NEW.instrument_id OR m.symbol=NEW.symbol)
        AND tstzrange(m.valid_from,m.valid_to,'[)') &&
            tstzrange(NEW.valid_from,NEW.valid_to,'[)')) THEN
        RAISE EXCEPTION 'overlapping research symbol mapping';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION research.check_mapping() FROM PUBLIC;
    CREATE TABLE research.bar_revisions (
      revision_id uuid PRIMARY KEY, instrument_id uuid NOT NULL REFERENCES research.instruments,
      provider text NOT NULL, provider_symbol text NOT NULL
        CHECK(provider_symbol ~ '^[A-Z][A-Z0-9./-]{0,15}$'),
      feed text NOT NULL CHECK(feed='iex'),
      interval text NOT NULL CHECK(interval IN ('15Min','1Day')),
      bar_start timestamptz NOT NULL, bar_end timestamptz NOT NULL,
      adjustment text NOT NULL CHECK(adjustment='raw'),
      open numeric(38,18) NOT NULL CHECK(open>0),
      high numeric(38,18) NOT NULL CHECK(high>0),
      low numeric(38,18) NOT NULL CHECK(low>0),
      close numeric(38,18) NOT NULL CHECK(close>0),
      volume numeric(38,18) NOT NULL CHECK(volume>=0),
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      provider_revision text, local_revision integer NOT NULL CHECK(local_revision>0),
      predecessor_id uuid REFERENCES research.bar_revisions,
      published_at timestamptz CHECK(published_at IS NULL),
      quality text NOT NULL CHECK(quality IN
        ('VALID','MISSING_EXPECTED','NO_OBSERVATION','UNKNOWN_CALENDAR','CONFLICT','INVALID')),
      commit_version bigint NOT NULL, ingested_at timestamptz NOT NULL,
      CHECK(bar_end>bar_start),
      CHECK(interval<>'15Min' OR bar_end-bar_start=interval '15 minutes'),
      CHECK(high>=greatest(open,close,low) AND low<=least(open,close)),
      CHECK((local_revision=1)=(predecessor_id IS NULL)),
      UNIQUE(instrument_id,provider,feed,interval,bar_start,bar_end,adjustment,
        local_revision));
    CREATE INDEX research_bars_pit ON research.bar_revisions
      (instrument_id,feed,interval,bar_start,commit_version);
    CREATE TABLE research.evidence (
      evidence_id uuid PRIMARY KEY,
      revision_id uuid NOT NULL UNIQUE REFERENCES research.bar_revisions,
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      source_reference text NOT NULL
        CHECK(source_reference ~ '^[A-Za-z0-9][A-Za-z0-9:._/-]{0,255}$'
          AND source_reference NOT LIKE '%://%'),
      commit_version bigint NOT NULL, ingested_at timestamptz NOT NULL);
    CREATE TABLE research.calendar_versions (
      session_id uuid PRIMARY KEY, session_date date NOT NULL,
      opens_at timestamptz NOT NULL, closes_at timestamptz NOT NULL,
      version text NOT NULL CHECK(version ~ '^[0-9a-f]{64}$'),
      source text NOT NULL CHECK(source='alpaca-v2-calendar'),
      commit_version bigint NOT NULL, ingested_at timestamptz NOT NULL,
      CHECK(closes_at>opens_at), UNIQUE(version,session_date));
    CREATE FUNCTION research.check_bar_mapping() RETURNS trigger LANGUAGE plpgsql
      SET search_path=pg_catalog,research,pg_temp AS $$
    DECLARE head research.bar_revisions;
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM research.symbol_mappings m
        WHERE m.instrument_id=NEW.instrument_id AND m.provider=NEW.provider
          AND m.symbol=NEW.provider_symbol AND m.valid_from<=NEW.bar_start
          AND (m.valid_to IS NULL OR m.valid_to>=NEW.bar_end)) THEN
        RAISE EXCEPTION 'bar requires a valid source symbol mapping';
      END IF;
      SELECT * INTO head FROM research.bar_revisions b
        WHERE b.instrument_id=NEW.instrument_id AND b.provider=NEW.provider
          AND b.feed=NEW.feed AND b.interval=NEW.interval AND b.bar_start=NEW.bar_start
          AND b.bar_end=NEW.bar_end AND b.adjustment=NEW.adjustment
        ORDER BY b.local_revision DESC LIMIT 1;
      IF FOUND THEN
        IF NEW.local_revision<>head.local_revision+1
          OR NEW.predecessor_id IS DISTINCT FROM head.revision_id
          OR NEW.fingerprint=head.fingerprint THEN
          RAISE EXCEPTION 'invalid research revision chain';
        END IF;
      ELSIF NEW.local_revision<>1 OR NEW.predecessor_id IS NOT NULL THEN
        RAISE EXCEPTION 'invalid first research revision';
      END IF;
      IF NEW.provider_revision IS NOT NULL AND EXISTS (
        SELECT 1 FROM research.bar_revisions b WHERE b.instrument_id=NEW.instrument_id
          AND b.provider=NEW.provider AND b.feed=NEW.feed AND b.interval=NEW.interval
          AND b.bar_start=NEW.bar_start AND b.bar_end=NEW.bar_end
          AND b.adjustment=NEW.adjustment AND b.provider_revision=NEW.provider_revision
          AND b.fingerprint<>NEW.fingerprint) THEN
        RAISE EXCEPTION 'conflicting research provider revision';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION research.check_bar_mapping() FROM PUBLIC;
    """)
    for table in (
        "instruments",
        "symbol_mappings",
        "bar_revisions",
        "evidence",
        "calendar_versions",
    ):
        op.execute(
            f"CREATE TRIGGER a_stamp BEFORE INSERT ON research.{table} "
            "FOR EACH ROW EXECUTE FUNCTION research.stamp()"
        )
        op.execute(
            f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON research.{table} "
            "FOR EACH ROW EXECUTE FUNCTION research.reject_mutation()"
        )
    op.execute(
        "CREATE TRIGGER b_mapping BEFORE INSERT ON research.symbol_mappings "
        "FOR EACH ROW EXECUTE FUNCTION research.check_mapping()"
    )
    op.execute(
        "CREATE TRIGGER b_bar_mapping BEFORE INSERT ON research.bar_revisions "
        "FOR EACH ROW EXECUTE FUNCTION research.check_bar_mapping()"
    )
    op.execute("""
    REVOKE ALL ON ALL TABLES IN SCHEMA research FROM PUBLIC;
    GRANT USAGE ON SCHEMA research TO atl_execution,atl_worker,atl_control_api;
    GRANT SELECT ON ALL TABLES IN SCHEMA research TO atl_execution,atl_worker,atl_control_api;
    GRANT INSERT ON research.instruments,research.symbol_mappings,
      research.bar_revisions,research.evidence,research.calendar_versions TO atl_execution;
    GRANT EXECUTE ON FUNCTION research.reserve_version() TO atl_execution;
    """)


def downgrade():
    raise RuntimeError("Research rollback disables dispatch; immutable research data is preserved")
