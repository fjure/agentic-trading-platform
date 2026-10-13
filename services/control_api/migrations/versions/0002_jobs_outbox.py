"""Durable leased jobs and transactional event delivery.

Revision ID: 0002_jobs_outbox
Revises: 0001_ledger
"""

from alembic import op

revision = "0002_jobs_outbox"
down_revision = "0001_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE public.jobs (
      id text PRIMARY KEY,
      type text NOT NULL,
      input_ref text NOT NULL,
      trace_id text NOT NULL,
      status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','LEASED','RETRY','RECONCILE','DONE','DEAD')),
      lease_owner text,
      lease_until timestamptz,
      fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
      attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
      max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 3),
      not_before timestamptz NOT NULL DEFAULT clock_timestamp(),
      heartbeat timestamptz,
      error_code text,
      completed_at timestamptz,
      CHECK ((status = 'LEASED') = (lease_owner IS NOT NULL AND lease_until IS NOT NULL))
    )
    """)
    op.execute(
        "CREATE INDEX jobs_claim_idx ON public.jobs (not_before, id) "
        "WHERE status IN ('PENDING','RETRY','LEASED')"
    )
    op.execute("""
    CREATE TABLE public.job_recovery_checks (
      job_id text NOT NULL REFERENCES public.jobs(id) ON DELETE RESTRICT,
      fencing_token bigint NOT NULL,
      checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      evidence_ref text NOT NULL CHECK (length(evidence_ref) > 0),
      PRIMARY KEY (job_id, fencing_token)
    )
    """)
    op.execute("""
    CREATE TABLE public.outbox_events (
      event_id text PRIMARY KEY,
      event_type text NOT NULL,
      occurred_at timestamptz NOT NULL,
      aggregate_id text NOT NULL,
      aggregate_version integer NOT NULL CHECK (aggregate_version > 0),
      trace_id text NOT NULL,
      schema_version integer NOT NULL CHECK (schema_version = 1),
      payload jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      published_at timestamptz
    )
    """)
    op.execute(
        "CREATE INDEX outbox_pending_idx ON public.outbox_events (created_at, event_id) "
        "WHERE published_at IS NULL"
    )
    op.execute("""
    CREATE TABLE public.consumer_receipts (
      consumer text NOT NULL,
      event_id text NOT NULL REFERENCES public.outbox_events(event_id) ON DELETE RESTRICT,
      consumed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY (consumer, event_id)
    )
    """)
    op.execute("""
    CREATE TABLE public.publish_cursor (
      singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
      next_cursor bigint NOT NULL CHECK (next_cursor > 0)
    )
    """)
    op.execute("INSERT INTO public.publish_cursor (singleton, next_cursor) VALUES (true, 1)")
    op.execute("""
    CREATE TABLE public.publish_log (
      cursor bigint PRIMARY KEY,
      event_id text NOT NULL UNIQUE REFERENCES public.outbox_events(event_id) ON DELETE RESTRICT,
      committed_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )
    """)
    op.execute("""
    CREATE FUNCTION public.outbox_guard_event() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
      IF TG_RELID <> 'public.outbox_events'::regclass OR TG_OP <> 'UPDATE'
         OR (NEW.event_id,NEW.event_type,NEW.occurred_at,NEW.aggregate_id,
             NEW.aggregate_version,NEW.trace_id,NEW.schema_version,NEW.payload,
             NEW.created_at) IS DISTINCT FROM
            (OLD.event_id,OLD.event_type,OLD.occurred_at,OLD.aggregate_id,
             OLD.aggregate_version,OLD.trace_id,OLD.schema_version,OLD.payload,
             OLD.created_at)
         OR OLD.published_at IS NOT NULL THEN
        RAISE EXCEPTION 'outbox event is immutable';
      END IF;
      RETURN NEW;
    END $$
    """)
    op.execute(
        "CREATE TRIGGER outbox_guard_event BEFORE UPDATE ON public.outbox_events "
        "FOR EACH ROW EXECUTE FUNCTION public.outbox_guard_event()"
    )
    op.execute(
        "CREATE TRIGGER outbox_no_delete BEFORE DELETE OR TRUNCATE ON public.outbox_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION public.ledger_reject_mutation()"
    )
    for table in ("job_recovery_checks", "consumer_receipts", "publish_log"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
            f"ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.ledger_reject_mutation()"
        )
    op.execute("""
    CREATE FUNCTION public.publish_cursor_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
      IF TG_RELID <> 'public.publish_cursor'::regclass OR TG_OP <> 'UPDATE'
         OR NEW.singleton IS DISTINCT FROM OLD.singleton
         OR NEW.next_cursor <> OLD.next_cursor + 1 THEN
        RAISE EXCEPTION 'invalid publish cursor allocation';
      END IF;
      RETURN NEW;
    END $$
    """)
    op.execute(
        "CREATE TRIGGER publish_cursor_guard BEFORE UPDATE ON public.publish_cursor "
        "FOR EACH ROW EXECUTE FUNCTION public.publish_cursor_guard()"
    )
    op.execute(
        "CREATE TRIGGER publish_cursor_no_delete "
        "BEFORE DELETE OR TRUNCATE ON public.publish_cursor "
        "FOR EACH STATEMENT EXECUTE FUNCTION public.ledger_reject_mutation()"
    )
    op.execute("""
    CREATE FUNCTION public.publish_consistency_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
    DECLARE allocated bigint;
    BEGIN
      IF TG_RELID NOT IN ('public.publish_cursor'::regclass,
                         'public.publish_log'::regclass,
                         'public.outbox_events'::regclass) THEN
        RAISE EXCEPTION 'invalid publish consistency trigger source';
      END IF;
      SELECT next_cursor INTO allocated FROM public.publish_cursor WHERE singleton=true
        FOR UPDATE;
      IF allocated IS DISTINCT FROM
         (SELECT COALESCE(max(cursor), 0) + 1 FROM public.publish_log)
         OR EXISTS (
           SELECT 1 FROM public.publish_log l
           JOIN public.outbox_events e USING (event_id)
           WHERE e.published_at IS NULL
         )
         OR EXISTS (
           SELECT 1 FROM public.outbox_events e
           WHERE e.published_at IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM public.publish_log l WHERE l.event_id=e.event_id)
         ) THEN
        RAISE EXCEPTION 'publish cursor, log and acknowledgement must commit together';
      END IF;
      RETURN NULL;
    END $$
    """)
    for table, operation in (
        ("publish_cursor", "UPDATE"),
        ("publish_log", "INSERT"),
        ("outbox_events", "UPDATE"),
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {table}_publish_consistency AFTER {operation} "
            f"ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION public.publish_consistency_guard()"
        )
    op.execute(
        "REVOKE ALL ON FUNCTION public.outbox_guard_event(), "
        "public.publish_cursor_guard(), public.publish_consistency_guard() FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON public.jobs, public.job_recovery_checks, public.outbox_events, "
        "public.consumer_receipts, public.publish_cursor, public.publish_log FROM PUBLIC"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON public.jobs TO atl_worker, atl_execution")
    op.execute("GRANT SELECT, INSERT ON public.job_recovery_checks TO atl_worker, atl_execution")
    op.execute("GRANT SELECT, INSERT ON public.outbox_events TO atl_worker, atl_execution")
    op.execute("GRANT UPDATE (published_at) ON public.outbox_events TO atl_worker, atl_execution")
    op.execute(
        "GRANT SELECT, INSERT ON public.consumer_receipts "
        "TO atl_worker, atl_execution, atl_control_api"
    )
    op.execute("GRANT SELECT ON public.publish_cursor TO atl_worker, atl_execution")
    op.execute("GRANT UPDATE (next_cursor) ON public.publish_cursor TO atl_worker, atl_execution")
    op.execute("GRANT SELECT, INSERT ON public.publish_log TO atl_worker, atl_execution")
    op.execute(
        "GRANT SELECT ON public.jobs, public.outbox_events, public.publish_log "
        "TO atl_control_api, atl_watchdog"
    )


def downgrade() -> None:
    raise RuntimeError("jobs/outbox downgrade would destroy durable delivery state")
