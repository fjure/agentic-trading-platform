"""Connect an operator-owned evaluation supervisor to the existing signed gateway.

The supervisor must issue the same durable dialogue grant used by Research. This
adapter carries only opaque attempt commitments to the gateway and never reads
provider credentials or creates admission authority of its own.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256

from services.llm_gateway.app import GatewayDispatchClient
from services.llm_gateway.content_policy import valid_projection
from services.llm_gateway.offline_gateway import DispatchRequest
from services.llm_gateway.provider_wire import ProviderResult, ResultState

from .harness import Approval, AttemptOutcome, EvaluationInput
from .operator_store import OperatorStore


@dataclass(frozen=True, slots=True)
class PreparedCase:
    request: DispatchRequest
    approved_projection: bytes
    case_id: str
    repetition: int


class GatewaySupervisor:
    def __init__(
        self,
        gateway: GatewayDispatchClient,
        store: OperatorStore,
        prepare: Callable[[Approval, int, EvaluationInput], PreparedCase],
        settle: Callable[[Approval, int, str, DispatchRequest, ProviderResult], Decimal | None],
    ):
        self._gateway = gateway
        self._store = store
        self.consume = store.consume
        self._prepare = prepare
        self._settle = settle

    def execute(
        self, approval: Approval, repetition: int, case: EvaluationInput,
    ) -> AttemptOutcome:
        if self._store.reserve(approval, repetition, case.case_id) is not True:
            raise ValueError("EVALUATION_RESERVATION_DENIED")
        prepared = self._prepare(approval, repetition, case)
        request, projection = prepared.request, prepared.approved_projection
        expected_model = (approval.binding.profile, approval.binding.model)
        if (
            (prepared.case_id, prepared.repetition) != (case.case_id, repetition)
            or request.purpose != "DIALOGUE"
            or (request.profile_id, request.model) != expected_model
            or not datetime.now(UTC) < request.expires_at <= approval.expires_at
            or not valid_projection(projection)
            or json.loads(projection)["excerpt"] != case.text
            or (
                json.loads(projection)["confirmedContext"]
                != case.dialogue_state.get("confirmed", {})
            )
            or sha256(projection).hexdigest() != request.projection_sha256
        ):
            raise ValueError("EVALUATION_DISPATCH_BINDING_MISMATCH")
        if self._store.authorize(approval, repetition, case.case_id) is not True:
            raise ValueError("EVALUATION_RESERVATION_DENIED")
        result = self._gateway.dispatch(request)
        cost = self._settle(approval, repetition, case.case_id, request, result)
        status = {
            ResultState.ACCEPTED: "answered",
            ResultState.REFUSED: "refused",
            ResultState.OUTCOME_UNKNOWN: "timeout",
        }.get(result.state, "provider_blocked")
        cost = cost if result.usage is not None else None
        self._store.settle(approval, repetition, case.case_id, status, cost, result.output)
        return AttemptOutcome(status, cost)
