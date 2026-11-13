"""Join an injected supervisor authority to one offline HTTP dispatch."""

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
from threading import Event, Lock
from uuid import UUID

from services.setup.llm_credentials import (
    CredentialBinding,
    CredentialDenied,
    SyntheticCredentialStore,
)

from .content_policy import valid_projection
from .dispatch_journal import DispatchJournal, DispatchOutcome, JournalDenied
from .provider_http import Capability, OfflineProviderHttp, ProviderHttp
from .provider_wire import Provider, ProviderResult, ResultState

_HASH = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    attempt_id: UUID
    request_hash: str
    projection_sha256: str
    provider: Provider
    model: str
    profile_id: str
    profile_generation: int
    model_generation: int
    credential_generation: int
    envelope_version: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthorizedDispatch:
    request: DispatchRequest
    capability: Capability
    projection: bytes


class OfflineGateway:
    def __init__(
        self, journal: DispatchJournal, transport: OfflineProviderHttp,
        authorize: Callable[[DispatchRequest], AuthorizedDispatch | None],
        *, credentials: SyntheticCredentialStore, capabilities: tuple[Capability, ...],
    ):
        self._capabilities = {
            self._capability_key(capability): capability for capability in capabilities
        }
        if len(self._capabilities) != len(capabilities):
            raise ValueError("duplicate gateway capability")
        self._journal = journal
        self._transport = transport
        self._authorize = authorize
        self._credentials = credentials
        self._active: dict[UUID, tuple[str, Event]] = {}
        self._active_lock = Lock()

    def cancel(self, attempt_id: UUID, request_hash: str) -> None:
        """Stop forwarding a locally active attempt at the next transport checkpoint."""
        with self._active_lock:
            active = self._active.get(attempt_id)
            if active is not None and compare_digest(active[0], request_hash):
                active[1].set()

    def dispatch(self, request: DispatchRequest) -> ProviderResult:
        """Spend the gateway attempt before consulting the current supervisor authority."""
        if not isinstance(request.projection_sha256, str) or not _HASH.fullmatch(
            request.projection_sha256
        ):
            return ProviderResult(ResultState.DISPATCH_DENIED)
        try:
            self._journal.claim(request.attempt_id, request.request_hash, request.expires_at)
        except JournalDenied:
            try:
                status = self._journal.status(request.attempt_id, request.request_hash)
                if status is DispatchOutcome.UNSENT:
                    return ProviderResult(ResultState.DISPATCH_DENIED)
            except JournalDenied:
                pass
            return ProviderResult(ResultState.OUTCOME_UNKNOWN)
        cancelled = Event()
        with self._active_lock:
            self._active[request.attempt_id] = (request.request_hash, cancelled)
        try:
            return self._dispatch_claimed(request, cancelled)
        finally:
            with self._active_lock:
                self._active.pop(request.attempt_id)

    def _dispatch_claimed(self, request: DispatchRequest, cancelled: Event) -> ProviderResult:
        try:
            authorized = self._authorize(request)
        except Exception:
            return self._finish(request, DispatchOutcome.UNSENT, ResultState.DISPATCH_DENIED)
        if not self._matches(request, authorized):
            return self._finish(request, DispatchOutcome.UNSENT, ResultState.DISPATCH_DENIED)
        if request.expires_at <= datetime.now(UTC):
            return self._finish(request, DispatchOutcome.UNSENT, ResultState.DISPATCH_DENIED)
        if cancelled.is_set():
            return self._finish(request, DispatchOutcome.UNSENT, ResultState.DISPATCH_CANCELLED)
        binding = CredentialBinding(
            request.provider, request.credential_generation, request.profile_id,
            request.profile_generation, request.model_generation,
        )
        try:
            credential = self._credentials.read_active(binding)
        except CredentialDenied:
            return self._finish(request, DispatchOutcome.UNSENT, ResultState.DISPATCH_DENIED)

        def stopped() -> bool:
            if cancelled.is_set() or request.expires_at <= datetime.now(UTC):
                return True
            try:
                return not compare_digest(credential, self._credentials.read_active(binding))
            except CredentialDenied:
                return True

        remaining = (request.expires_at - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return self._finish(request, DispatchOutcome.UNSENT, ResultState.DISPATCH_CANCELLED)
        if not 0 < authorized.capability.timeout_seconds <= 60:
            return self._finish(request, DispatchOutcome.UNSENT, ResultState.CAPABILITY_MISMATCH)
        capability = replace(
            authorized.capability,
            timeout_seconds=min(authorized.capability.timeout_seconds, remaining),
        )
        result = self._send(capability, authorized.projection, stopped, credential)
        if stopped():
            result = ProviderResult(ResultState.DISPATCH_CANCELLED)
        elif result.usage is None and result.state in {
            ResultState.ACCEPTED, ResultState.INCOMPLETE, ResultState.INVALID_OUTPUT,
            ResultState.REFUSED,
        }:
            result = ProviderResult(ResultState.OUTCOME_UNKNOWN)
        if result.state is ResultState.ACCEPTED:
            outcome = DispatchOutcome.COMPLETED
        elif result.state in {ResultState.OUTCOME_UNKNOWN, ResultState.DISPATCH_CANCELLED} or (
            result.state is ResultState.MODEL_MISMATCH and result.usage is None
        ):
            outcome = DispatchOutcome.OUTCOME_UNKNOWN
        elif result.state is ResultState.CAPABILITY_MISMATCH:
            outcome = DispatchOutcome.UNSENT
        else:
            outcome = DispatchOutcome.RESULT_UNAVAILABLE
        try:
            self._journal.finish(request.attempt_id, outcome)
        except JournalDenied:
            return ProviderResult(ResultState.OUTCOME_UNKNOWN)
        return result

    def _send(self, capability, projection, stopped, credential):
        return self._transport.send(
            capability, projection, cancelled=stopped, synthetic_credential=credential,
        )

    def _finish(
        self, request: DispatchRequest, outcome: DispatchOutcome, state: ResultState,
    ) -> ProviderResult:
        try:
            self._journal.finish(request.attempt_id, outcome)
        except JournalDenied:
            return ProviderResult(ResultState.OUTCOME_UNKNOWN)
        return ProviderResult(state)

    @staticmethod
    def _capability_key(capability: Capability) -> tuple:
        return (
            capability.provider, capability.profile_id, capability.profile_generation,
            capability.model, capability.model_generation,
        )

    def _matches(self, request: DispatchRequest, authorized: AuthorizedDispatch | None) -> bool:
        if not isinstance(authorized, AuthorizedDispatch) or authorized.request != request:
            return False
        profile = authorized.capability
        return (
            isinstance(profile, Capability)
            and self._capabilities.get(self._capability_key(profile)) == profile
            and profile.provider is request.provider and profile.model == request.model
            and profile.profile_id == request.profile_id
            and profile.profile_generation == request.profile_generation
            and profile.model_generation == request.model_generation
            and profile.credential_generation == request.credential_generation
            and profile.envelope_version == request.envelope_version
            and type(authorized.projection) is bytes
            and compare_digest(sha256(authorized.projection).hexdigest(), request.projection_sha256)
            and valid_projection(authorized.projection)
        )


class ProviderGateway(OfflineGateway):
    """Reuse journal and cancellation while fixing the real provider transport."""

    def __init__(self, journal, authorize, *, credentials, capabilities):
        if not capabilities or type(journal) is not DispatchJournal:
            raise ValueError("incomplete provider gateway composition")
        super().__init__(
            journal, ProviderHttp(), authorize, credentials=credentials, capabilities=capabilities,
        )

    def _send(self, capability, projection, stopped, credential):
        return self._transport.send(
            capability, projection, cancelled=stopped, credential=credential,
        )
