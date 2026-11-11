"""Purpose-specific synthetic discovery dispatch with an independent one-shot journal."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from .dispatch_journal import DispatchJournal, DispatchOutcome, JournalDenied
from .provider_http import Capability, OfflineProviderHttp
from .provider_wire import ProviderResult, ResultState


class OfflineDiscoveryDispatch:
    def __init__(
        self, journal: DispatchJournal, transport: OfflineProviderHttp, capability: Capability,
    ):
        self._journal, self._transport, self.capability = journal, transport, capability

    def send(
        self, attempt_id: UUID, commitment: str, deadline: datetime, payload: bytes, *,
        authorize: Callable[[], None],
    ) -> ProviderResult:
        try:
            self._journal.claim(attempt_id, commitment, deadline)
        except JournalDenied:
            return ProviderResult(ResultState.OUTCOME_UNKNOWN)
        try:
            authorize()
        except Exception:
            try:
                self._journal.finish(attempt_id, DispatchOutcome.UNSENT)
            except JournalDenied:
                return ProviderResult(ResultState.OUTCOME_UNKNOWN)
            return ProviderResult(ResultState.DISPATCH_DENIED)
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            result = ProviderResult(ResultState.OUTCOME_UNKNOWN)
        else:
            capability = replace(
                self.capability, timeout_seconds=min(remaining, self.capability.timeout_seconds),
            )
            result = self._transport.send(capability, payload)
        if (
            result.usage is None or result.state is ResultState.OUTCOME_UNKNOWN
            or deadline <= datetime.now(UTC)
        ):
            result = ProviderResult(ResultState.OUTCOME_UNKNOWN)
        outcome = {
            ResultState.ACCEPTED: DispatchOutcome.COMPLETED,
            ResultState.OUTCOME_UNKNOWN: DispatchOutcome.OUTCOME_UNKNOWN,
        }.get(result.state, DispatchOutcome.RESULT_UNAVAILABLE)
        try:
            self._journal.finish(attempt_id, outcome)
        except JournalDenied:
            return ProviderResult(ResultState.OUTCOME_UNKNOWN)
        return result
