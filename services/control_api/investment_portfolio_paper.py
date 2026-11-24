"""Read-only portfolio port for the isolated Paper ledger."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from .paper_dto import DTO, ReadNonnegative, ReadPositive, Stamp, Symbol, Version, read_stamp

SnapshotId = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Reason = Literal[
    "LEDGER_NOT_PREPARED", "SNAPSHOT_INCONSISTENT", "STALE_EVIDENCE",
    "VALUATION_NOT_AVAILABLE", "RECONCILIATION_REQUIRED",
]


class PaperPortfolioPosition(DTO):
    instrument: Symbol
    revision: Version
    quantity: ReadPositive
    average_price: ReadPositive | None
    cost_basis: ReadNonnegative | None
    realized_pnl: None


class PaperPortfolioProjection(DTO):
    snapshot_id: SnapshotId | None
    revision: Version | None
    generation: Version | None
    observed_at: Stamp
    as_of: Stamp | None
    reconciliation_at: Stamp | None
    quality: Literal["CURRENT", "STALE", "UNAVAILABLE", "INCONSISTENT"]
    reconciliation: Literal["CURRENT", "REQUIRED", "UNKNOWN"]
    currency: Literal["USD"]
    cash: ReadNonnegative | None
    equity: ReadNonnegative | None
    exposure: ReadNonnegative | None
    positions: Annotated[list[PaperPortfolioPosition], Field(max_length=2)] | None
    blockers: Annotated[list[Reason], Field(max_length=2)]

    _timestamps = field_validator("observed_at", "as_of", "reconciliation_at")(read_stamp)


def read_paper_portfolio(paper, trace_id):
    return paper.read_investment_portfolio(trace_id)
