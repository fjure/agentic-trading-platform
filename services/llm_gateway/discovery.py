"""Purpose-specific synthetic discovery dispatch with an independent one-shot journal."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from threading import Lock
from uuid import UUID

from .dispatch_journal import DispatchJournal, DispatchOutcome, JournalDenied
from .offline_gateway import AuthorizedDispatch, DispatchRequest
from .provider_http import Capability, OfflineProviderHttp
from .provider_wire import ProviderResult, ResultState


class ProviderDiscoveryDispatch:
    """Release a scheduled projection only through signed, one-use admission."""

    def __init__(self, gateway, capability: Capability):
        self._gateway = gateway
        self.capability = capability
        self._lock = Lock()
        self._pending = {}

    def send(self, attempt_id, commitment, deadline, payload, *, authorize):
        capability = self.capability
        request = DispatchRequest(
            attempt_id, commitment, sha256(payload).hexdigest(), capability.provider,
            capability.model, capability.profile_id, capability.profile_generation,
            capability.model_generation, capability.credential_generation,
            capability.envelope_version, deadline, purpose="DISCOVERY",
        )
        with self._lock:
            if self._pending:
                return ProviderResult(ResultState.DISPATCH_DENIED)
            self._pending[attempt_id] = (request, payload, authorize)
        try:
            return self._gateway.dispatch(request)
        finally:
            with self._lock:
                self._pending.pop(attempt_id, None)

    def consume(self, request):
        with self._lock:
            pending = self._pending.get(request.attempt_id)
            if pending is None or pending[0] != request or request.expires_at <= datetime.now(UTC):
                return None
            _, payload, authorize = self._pending.pop(request.attempt_id)
        authorize()
        return AuthorizedDispatch(request, self.capability, payload)


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
