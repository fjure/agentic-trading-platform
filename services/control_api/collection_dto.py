"""Geschlossene Sammlungsprojektionen; keine frei eingebbaren Providerprogramme."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from services.control_api.readiness_dto import (
    Code,
    Id,
    Number,
    ReadinessDTO,
    ReadinessInterval,
    ReadinessProviderRights,
    ReadinessSafeReference,
    ReadinessTimelinessMetrics,
    ReadinessWindows,
    Utc,
)


class CollectionPlanUpdate(ReadinessDTO):
    enabled: bool
    expectedVersion: Number


class CollectionGap(ReadinessDTO):
    period: ReadinessInterval
    reason: Literal[
        "CALENDAR_UNKNOWN",
        "BAR_MISSING",
        "OBSERVED_TOO_LATE",
        "OUTSIDE_RECOVERY_WINDOW",
        "DAILY_BUDGET_EXHAUSTED",
        "UNKNOWN_DISPATCH",
    ]
    references: Annotated[list[ReadinessSafeReference], Field(max_length=200)]


class CollectionSchedule(ReadinessDTO):
    calendarRefreshUtc: Literal["00:05:00Z"]
    calendarLookaheadDays: Literal[7]
    firstBarDelaySeconds: Literal[5]
    reobserveDelaySeconds: Literal[240]
    tickSeconds: Literal[5]
    recoveryHours: Literal[24]
    overlapMinutes: Literal[30]
    maxDuePerUtcDay: Literal[100]
    maxOpenJobsTotal: Literal[2]
    maxConcurrentHttpGets: Literal[1]


class CollectionPlan(ReadinessDTO):
    instrumentId: Id
    symbol: Literal["SPY", "QQQ"]
    provider: Literal["alpaca"]
    feed: Literal["iex"]
    interval: Literal["15Min"]
    adjustment: Literal["raw"]
    enabled: bool
    version: Number
    generation: Number
    status: Literal["DISABLED", "ACTIVE", "BLOCKED", "PAUSED"]
    reasons: Annotated[list[Code], Field(max_length=32)]
    providerRights: ReadinessProviderRights
    globalEnabled: bool
    nextDueAt: Utc | None
    lastAttemptAt: Utc | None
    lastJobId: Id | None
    outstandingStartedDispatches: Annotated[int, Field(ge=0, le=2)]
    gaps: Annotated[list[CollectionGap], Field(max_length=200)]
    gapsComplete: bool
    metrics: Annotated[list[ReadinessTimelinessMetrics], Field(max_length=2)]
    windows: ReadinessWindows
    schedule: CollectionSchedule
    updatedAt: Utc | None

    @model_validator(mode="after")
    def state_agrees(self):
        if self.status == "ACTIVE" and (not self.enabled or not self.globalEnabled):
            raise ValueError("active plan requires persisted wish and global gate")
        if self.status in {"DISABLED", "PAUSED"} and self.enabled:
            raise ValueError("disabled plan cannot be enabled")
        return self


class CollectionPlanPage(ReadinessDTO):
    items: Annotated[list[CollectionPlan], Field(max_length=2)]
    observedAt: Utc

    @model_validator(mode="after")
    def unique_plans(self):
        if len({item.instrumentId for item in self.items}) != len(self.items) or (
            len({item.symbol for item in self.items}) != len(self.items)
        ):
            raise ValueError("unique registered plans required")
        return self


RESPONSE_MODELS = {"read_plans": CollectionPlanPage, "update_plan": CollectionPlan}
