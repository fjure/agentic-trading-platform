"""Expose one fixed-role, stored-only experience read in the Paper database."""

from alembic import op

revision = "0061_investment_paper_read"
down_revision = "0060_investment_preferences"
branch_labels = None
depends_on = None

_READ = """
DECLARE
  w public.paper_workflow_state%ROWTYPE;
  c public.risk_account_context%ROWTYPE;
  s public.portfolio_snapshots%ROWTYPE;
  latest public.paper_reconciliation_receipts%ROWTYPE;
  n timestamptz := statement_timestamp();
  confirmed_at timestamptz;
  position_rows jsonb;
  quality text;
  reconciliation text := 'UNKNOWN';
  blockers jsonb := '[]'::jsonb;
  generation bigint;
  observed text := public.paper_read_time(n);
BEGIN
  IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_control_api_login'
    OR current_setting('role')<>'atl_paper_control_api' THEN RETURN NULL; END IF;
  IF p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$' THEN
    RETURN jsonb_build_object('error','INVALID_REQUEST');
  END IF;

  SELECT * INTO w FROM public.paper_workflow_state WHERE singleton;
  generation := nullif(w.account_generation,0);
  SELECT * INTO c FROM public.risk_account_context WHERE account_id='paper';
  SELECT * INTO s FROM public.portfolio_snapshots WHERE id=c.portfolio_snapshot_id;

  IF c.account_id IS NULL THEN
    quality := 'UNAVAILABLE';
    blockers := '["LEDGER_NOT_PREPARED"]';
  ELSIF s.id IS NULL OR s.as_of>n OR s.currency<>'USD'
    OR s.ledger_version<>c.portfolio_version OR generation IS NULL
    OR NOT EXISTS (
      SELECT 1 FROM public.accounts a
      JOIN public.broker_account_bindings b ON b.account_id=a.id
      WHERE a.id='paper' AND a.ledger_version=s.ledger_version
        AND b.provider_account_id=w.pinned_account_id
    )
    OR EXISTS (
      SELECT 1 FROM public.positions p
      LEFT JOIN public.position_versions v
        ON v.position_id=p.id AND v.ledger_version=p.ledger_version
      WHERE p.account_id='paper'
        AND (p.instrument_id NOT IN ('SPY','QQQ') OR p.currency<>s.currency
          OR v.position_id IS NULL
          OR (p.instrument_id,p.currency,p.quantity,p.average_price,p.realized_pnl)
            IS DISTINCT FROM (v.instrument_id,v.currency,v.quantity,v.average_price,v.realized_pnl))
    )
    OR EXISTS (
      SELECT 1 FROM public.positions p WHERE p.account_id='paper'
      GROUP BY p.instrument_id HAVING count(*)>1
    )
    OR s.quality='CONFLICT' THEN
    quality := 'INCONSISTENT';
    blockers := '["SNAPSHOT_INCONSISTENT"]';
  ELSIF s.quality IN ('GAP','INVALID') THEN
    quality := 'UNAVAILABLE';
    blockers := '["VALUATION_NOT_AVAILABLE"]';
  ELSE
    quality := CASE WHEN s.quality='STALE' OR n-s.as_of>interval '120 seconds'
      THEN 'STALE' ELSE 'CURRENT' END;
    IF quality='STALE' THEN blockers := blockers||'["STALE_EVIDENCE"]'::jsonb; END IF;

    -- Receipt time is evidence observation, preceding the booking timestamp
    -- of a changed portfolio. Snapshot identity and version bind the receipt.
    SELECT * INTO latest FROM public.paper_reconciliation_receipts r
      WHERE r.account_id='paper' AND r.portfolio_snapshot_id=s.id
        AND r.portfolio_version=s.ledger_version AND r.observed_at<=n
      ORDER BY r.observed_at DESC,r.id DESC LIMIT 1;
    SELECT max(r.observed_at) INTO confirmed_at FROM public.paper_reconciliation_receipts r
      WHERE r.account_id='paper' AND r.portfolio_snapshot_id=s.id
        AND r.portfolio_version=s.ledger_version AND r.observed_at<=n
        AND r.ready;
    IF latest.ready AND s.reconciliation='CURRENT' AND n-latest.observed_at<=interval '120 seconds'
    THEN
      reconciliation := 'CURRENT';
    ELSE
      reconciliation := 'REQUIRED';
      blockers := blockers||'["RECONCILIATION_REQUIRED"]'::jsonb;
    END IF;

    SELECT coalesce(jsonb_agg(jsonb_build_object(
      'instrument',p.instrument_id,'revision',p.ledger_version,
      'quantity',trim_scale(p.quantity)::text,'average_price',trim_scale(p.average_price)::text,
      'cost_basis',trim_scale(cb.cost_basis)::text,'realized_pnl',NULL
    ) ORDER BY p.instrument_id),'[]') INTO position_rows
      FROM public.positions p
      LEFT JOIN public.paper_position_cost_bases cb ON cb.position_id=p.id
        AND cb.ledger_version=p.ledger_version AND cb.quantity=p.quantity
      WHERE p.account_id='paper' AND p.quantity>0;
  END IF;
  RETURN jsonb_build_object(
    'snapshot_id',CASE WHEN s.id IS NOT NULL THEN encode(sha256(convert_to(s.id,'UTF8')),'hex') END,
    'revision',s.ledger_version,'generation',generation,'currency','USD',
    'observed_at',observed,'as_of',public.paper_read_time(s.as_of),
    'reconciliation_at',public.paper_read_time(confirmed_at),
    'quality',quality,'reconciliation',reconciliation,'blockers',blockers,
    'cash',CASE WHEN position_rows IS NOT NULL THEN trim_scale(s.cash)::text END,
    'equity',CASE WHEN position_rows IS NOT NULL THEN trim_scale(s.equity)::text END,
    'exposure',CASE WHEN position_rows IS NOT NULL THEN trim_scale(s.exposure)::text END,
    'positions',position_rows
  );
END;
"""


def upgrade():
    op.execute(f"""
    DO $migration$ BEGIN
      IF current_database()<>'lab_paper_runtime' THEN RETURN; END IF;
      CREATE FUNCTION public.paper_investment_portfolio(p_trace text) RETURNS jsonb
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp
      AS $paper_portfolio$ {_READ} $paper_portfolio$;
      REVOKE ALL ON FUNCTION public.paper_investment_portfolio(text) FROM PUBLIC;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_control_api') THEN
        GRANT EXECUTE ON FUNCTION public.paper_investment_portfolio(text) TO atl_paper_control_api;
      END IF;
    END $migration$;
    """)


def downgrade():
    op.execute("""
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        DROP FUNCTION public.paper_investment_portfolio(text);
      END IF;
    END $$;
    """)
