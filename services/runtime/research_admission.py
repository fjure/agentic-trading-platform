"""Supervisor-side bridge to transactional consent and model dispatch admission."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from hmac import compare_digest
from uuid import UUID

from packages.agent_research.processing import ProcessingBinding, ProcessingReceipt, ProcessingStore
from packages.agent_research.runs import Claim
from packages.persistence.transaction import ReadOnlyScopedConnection
from services.llm_gateway.content_policy import valid_projection
from services.llm_gateway.offline_gateway import AuthorizedDispatch, DispatchRequest
from services.llm_gateway.provider_http import (
    Capability,
    conservative_input_bound,
    production_input_bound,
    provider_request_bytes,
)


@dataclass(frozen=True, slots=True)
class PendingDispatch:
    request: DispatchRequest
    claim: Claim
    receipt_id: UUID
    binding: ProcessingBinding
    capability: Capability
    billable_input_bound: int | None = None


class ResearchDispatchAdmission:
    """Resolve attempts server-side and release projection bytes only after commit."""

    def __init__(
        self, processing: ProcessingStore, lookup: Callable[[UUID], PendingDispatch | None],
        check_policy: Callable[[ReadOnlyScopedConnection, ProcessingBinding], None],
    ):
        self._processing = processing
        self._lookup = lookup
        self._check_policy = check_policy

    def consume(self, request: DispatchRequest) -> AuthorizedDispatch | None:
        intent = self._lookup(request.attempt_id)
        if intent is None or intent.request != request:
            return None
        capability, binding = intent.capability, intent.binding
        if (
            capability.provider is not request.provider or capability.model != request.model
            or capability.profile_id != request.profile_id
            or capability.profile_generation != request.profile_generation
            or capability.model_generation != request.model_generation
            or capability.credential_generation != request.credential_generation
            or capability.envelope_version != request.envelope_version
            or binding.provider != request.provider or binding.model_id != request.model
            or binding.profile_id != request.profile_id
            or binding.profile_generation != request.profile_generation
            or binding.model_generation != request.model_generation
            or binding.credential_generation != request.credential_generation
            or binding.envelope_version != request.envelope_version
        ):
            return None

        def validate_dispatch(
            commitment: str, content: bytes, input_bound: int, output_bound: int,
            expires_at: datetime,
        ) -> bool:
            if binding.evidence_revision is None:
                if intent.billable_input_bound is not None:
                    return False
                required_input = conservative_input_bound(capability, content)
            else:
                if (
                    type(intent.billable_input_bound) is not int
                    or intent.billable_input_bound < 1
                    or binding.provider_request_sha256 is None
                ):
                    return False
                try:
                    wire = provider_request_bytes(capability, content)
                    digest = sha256(wire).hexdigest()
                except Exception:
                    return False
                if not compare_digest(digest, binding.provider_request_sha256):
                    return False
                required_input = production_input_bound(wire)
                if required_input > intent.billable_input_bound:
                    return False
            return (
                compare_digest(commitment, request.request_hash)
                and compare_digest(sha256(content).hexdigest(), request.projection_sha256)
                and valid_projection(content, binding.data_classes)
                and 1 <= capability.max_output_tokens <= output_bound
                and request.expires_at <= expires_at
                and required_input <= input_bound
            )

        approved = self._processing.authorize(
            intent.claim, intent.receipt_id, request.attempt_id, binding,
            check_policy=self._check_policy, validate_dispatch=validate_dispatch,
        )
        if (
            approved.attempt_id != request.attempt_id
            or not compare_digest(approved.commitment, request.request_hash)
            or not compare_digest(sha256(approved.content).hexdigest(), request.projection_sha256)
        ):
            return None
        return AuthorizedDispatch(request, capability, approved.content)

    def revoke(
        self, receipt_id: UUID, *, actor: str, expected_revision: int,
        notify: Callable[[UUID, str], bool],
    ) -> ProcessingReceipt:
        """Commit revocation first. Gateway notification is strictly best effort."""
        receipt = self._processing.revoke(
            receipt_id, actor=actor, expected_revision=expected_revision,
        )
        if receipt.attempt_id is not None:
            try:
                intent = self._lookup(receipt.attempt_id)
                if intent is not None and intent.receipt_id == receipt_id:
                    notify(receipt.attempt_id, intent.request.request_hash)
            except Exception:
                pass
        return receipt
