"""Private, normalized provider evidence. Never place this in a worker DTO."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class BindingProof:
    provider_account_id: str
    provider_asset_id: str
    instrument_id: str
    session_start: datetime
    session_end: datetime
    observed_at: datetime
    provider_time: datetime
    capability_version: int

    def __post_init__(self) -> None:
        for value in (self.provider_account_id, self.provider_asset_id):
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise ValueError("invalid provider identity")
        if (not self.instrument_id or type(self.capability_version) is not int
                or self.capability_version <= 0):
            raise ValueError("invalid binding proof")
        for value in (self.session_start, self.session_end, self.observed_at, self.provider_time):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("binding proof requires aware time")
            if value.utcoffset().total_seconds() != 0:
                raise ValueError("binding proof requires UTC")
        if not self.session_start < self.session_end:
            raise ValueError("invalid binding session")
