"""Private admission HTTP path using only synthetic authority and context."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, Mock
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from services.llm_gateway.dispatch_journal import DispatchJournal
from services.llm_gateway.offline_gateway import AuthorizedDispatch, OfflineGateway
from services.llm_gateway.provider_http import OfflineProviderHttp
from services.llm_gateway.provider_wire import ResultState
from services.llm_gateway.supervisor_rpc import (
    ADMISSION_PATH,
    SIGNATURE,
    AdmissionAuthentication,
    AdmissionClient,
    _request_body,
    create_admission_app,
)
from services.runtime.research_admission import PendingDispatch, ResearchDispatchAdmission
from services.setup.llm_credentials import SyntheticCredentialStore
from tests.unit.test_llm_offline_gateway import active_credentials, make_request
from tests.unit.test_llm_provider_http import PROFILE, PROJECTION
from tests.unit.test_llm_provider_wire import response


def test_private_admission_http_is_bound_to_original_message(tmp_path):
    auth = AdmissionAuthentication(bytes(range(32)))
    request = make_request()
    calls = []

    def consume(current):
        calls.append(current)
        return AuthorizedDispatch(current, PROFILE, PROJECTION) if current == request else None

    def provider(_request):
        return httpx.Response(200, json=response(
            PROFILE.provider, '{"hypothesis":"synthetic"}',
        ) | {"usage": {"input_tokens": 10, "output_tokens": 5}})

    with TestClient(create_admission_app(auth, consume)) as client:
        transport = OfflineProviderHttp(httpx.MockTransport(provider))
        journal = DispatchJournal.provision(tmp_path / "gateway.sqlite")
        gateway = OfflineGateway(
            journal, transport, AdmissionClient(client, auth).authorize,
            credentials=active_credentials()[0], capabilities=(PROFILE,),
        )
        assert gateway.dispatch(request).state is ResultState.ACCEPTED
        assert gateway.dispatch(request).state is ResultState.OUTCOME_UNKNOWN
        assert calls == [request]


def test_bad_signature_duplicate_header_and_expiry_never_reach_authority():
    auth = AdmissionAuthentication(bytes(range(32)))
    calls = []
    app = create_admission_app(auth, calls.append)
    body = _request_body(make_request())
    signed = auth.request_signature(body)
    expired = _request_body(replace(make_request(), expires_at=datetime.now(UTC) - timedelta(1)))
    with TestClient(app) as client:
        altered = client.post(ADMISSION_PATH, content=body + b" ", headers={SIGNATURE: signed})
        assert altered.status_code == 403
        duplicates = [(SIGNATURE, signed), (SIGNATURE, signed)]
        assert client.post(ADMISSION_PATH, content=body, headers=duplicates).status_code == 403
        headers = {SIGNATURE: auth.request_signature(expired)}
        assert client.post(ADMISSION_PATH, content=expired, headers=headers).status_code == 400
    assert calls == []


def test_signed_reply_cannot_be_replayed_for_another_attempt():
    auth = AdmissionAuthentication(bytes(range(32)))
    original = make_request()
    body = _request_body(original)
    with TestClient(create_admission_app(
        auth, lambda request: AuthorizedDispatch(request, PROFILE, PROJECTION),
    )) as client:
        reply = client.post(ADMISSION_PATH, content=body, headers={
            SIGNATURE: auth.request_signature(body),
        })
    assert reply.status_code == 200 and reply.headers["cache-control"] == "no-store"

    def replay(_request):
        return httpx.Response(200, content=reply.content, headers={
            SIGNATURE: reply.headers[SIGNATURE],
        })

    with httpx.Client(
        base_url="http://supervisor.invalid", transport=httpx.MockTransport(replay),
    ) as client:
        assert AdmissionClient(client, auth).authorize(make_request()) is None


def test_supervisor_bridge_resolves_binding_before_consuming_and_checks_committed_bytes():
    request = make_request()
    binding = SimpleNamespace(
        provider=request.provider, model_id=request.model,
        profile_id=request.profile_id, credential_generation=request.credential_generation,
        model_generation=request.model_generation, profile_generation=request.profile_generation,
        envelope_version=request.envelope_version,
        data_classes=("OPERATOR_RESEARCH_EXCERPT",),
    )
    pending = PendingDispatch(request, Mock(), uuid4(), binding, PROFILE)
    processing = Mock()
    check_policy = Mock()
    bridge = ResearchDispatchAdmission(processing, lambda _: pending, check_policy)
    assert bridge.consume(replace(request, model="wrong")) is None
    processing.authorize.assert_not_called()
    processing.authorize.return_value = SimpleNamespace(
        attempt_id=request.attempt_id, commitment=request.request_hash, content=PROJECTION,
    )
    assert bridge.consume(request) == AuthorizedDispatch(request, PROFILE, PROJECTION)
    processing.authorize.assert_called_once_with(
        pending.claim, pending.receipt_id, request.attempt_id, binding, check_policy=check_policy,
        validate_dispatch=ANY,
    )
    validate = processing.authorize.call_args.kwargs["validate_dispatch"]
    assert validate(request.request_hash, PROJECTION, 4096, PROFILE.max_output_tokens,
                    request.expires_at)
    assert not validate(request.request_hash, PROJECTION, 4096, PROFILE.max_output_tokens - 1,
                        request.expires_at)
    assert not validate(request.request_hash, PROJECTION, 1, PROFILE.max_output_tokens,
                        request.expires_at)
    assert not validate(request.request_hash, PROJECTION, 4096, PROFILE.max_output_tokens,
                        request.expires_at - timedelta(seconds=1))
    processing.authorize.return_value.content += b" "
    assert bridge.consume(request) is None


def test_signed_dispatch_uses_only_active_generation_and_rechecks_revocation(tmp_path):
    auth = AdmissionAuthentication(bytes(range(32)))
    store_path = tmp_path / "credentials.sqlite"
    store, binding = active_credentials(SyntheticCredentialStore.provision(store_path))
    expected = store.read_active(binding)
    calls = []

    def provider(request):
        assert request.headers["authorization"] == f"Bearer {expected}"
        assert expected.encode() not in request.content
        calls.append(request.url)
        store.revoke(binding.provider, expected_version=store.status(binding.provider).version,
                     generation=binding.credential_generation)
        return httpx.Response(200, json=response(PROFILE.provider, '{"hypothesis":"synthetic"}'))

    with TestClient(create_admission_app(
        auth, lambda request: AuthorizedDispatch(request, PROFILE, PROJECTION),
    )) as client:
        transport = OfflineProviderHttp(httpx.MockTransport(provider))
        journal = DispatchJournal.provision(tmp_path / "gateway.sqlite")
        gateway = OfflineGateway(
            journal, transport, AdmissionClient(client, auth).authorize,
            credentials=SyntheticCredentialStore(store_path), capabilities=(PROFILE,),
        )
        first = gateway.dispatch(make_request())
        assert first.state is ResultState.DISPATCH_CANCELLED and first.output is None
        assert gateway.dispatch(make_request()).state is ResultState.DISPATCH_DENIED
    assert len(calls) == 1


def test_supervisor_commits_revocation_before_notifying_gateway():
    request = make_request()
    intent = PendingDispatch(request, Mock(), uuid4(), Mock(), PROFILE)
    order = []
    receipt = SimpleNamespace(attempt_id=request.attempt_id)

    def commit(*_args, **_kwargs):
        order.append("committed")
        return receipt

    def notify(attempt_id, request_hash):
        assert (attempt_id, request_hash) == (request.attempt_id, request.request_hash)
        order.append("notified")
        raise RuntimeError("synthetic network error")

    processing = Mock()
    processing.revoke.side_effect = commit
    bridge = ResearchDispatchAdmission(processing, lambda _: intent, Mock())
    assert bridge.revoke(intent.receipt_id, actor="operator", expected_revision=2,
                         notify=notify) is receipt
    assert order == ["committed", "notified"]
