"""Execution may append normalized read-only market evidence.

Revision ID: 0010_runtime_market_grants
Revises: 0009_paper_reconciliation
"""

from alembic import op

revision = "0010_runtime_market_grants"
down_revision = "0009_paper_reconciliation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "GRANT INSERT ON public.market_bars, public.evidence_bundles, "
        "public.market_states TO atl_execution"
    )


def downgrade():
    raise RuntimeError("runtime market evidence grants are forward-only")
