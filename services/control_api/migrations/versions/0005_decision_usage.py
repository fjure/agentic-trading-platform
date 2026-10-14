"""Persist normalized provider token counts with immutable decisions."""

import sqlalchemy as sa
from alembic import op

revision = "0005_decision_usage"
down_revision = "0004_safety_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "decisions", sa.Column("input_tokens", sa.BigInteger(), nullable=True), schema="public"
    )
    op.add_column(
        "decisions", sa.Column("output_tokens", sa.BigInteger(), nullable=True), schema="public"
    )
    op.create_check_constraint(
        "ck_decision_token_pair",
        "decisions",
        "(input_tokens IS NULL AND output_tokens IS NULL) OR "
        "(input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND "
        "input_tokens BETWEEN 0 AND 9007199254740991 AND "
        "output_tokens BETWEEN 0 AND 9007199254740991)",
        schema="public",
    )


def downgrade() -> None:
    raise RuntimeError("decision usage downgrade would discard provider evidence")
