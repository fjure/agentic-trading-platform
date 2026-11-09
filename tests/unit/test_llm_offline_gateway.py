"""Offline end-to-end exercise of authority, journal and HTTP protocol."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from services.llm_gateway.dispatch_journal import DispatchJournal, DispatchOutcome
from services.llm_gateway.offline_gateway import AuthorizedDispatch, DispatchRequest, OfflineGateway
from services.llm_gateway.provider_http import OfflineProviderHttp
from services.llm_gateway.provider_wire import ResultState
from services.llm_gateway.supervisor_rpc import (
    CANCELLATION_PATH,
    SIGNATURE,
    AdmissionAuthentication,
    CancellationClient,
    _request_body,
    create_cancellation_app,
)
from services.setup.llm_credentials import CredentialBinding, SyntheticCredentialStore
from tests.unit.test_llm_provider_http import PROFILE, PROJECTION
from tests.unit.test_llm_provider_wire import response


def make_request():
    return DispatchRequest(
        uuid4(), "a" * 64, sha256(PROJECTION).hexdigest(), PROFILE.provider, PROFILE.model,
        PROFILE.profile_id, PROFILE.profile_generation, PROFILE.model_generation,
        PROFILE.credential_generation, PROFILE.envelope_version,
        datetime.now(UTC) + timedelta(seconds=20),
    )


def active_credentials(store=None):
    store = SyntheticCredentialStore() if store is None else store
    binding = CredentialBinding(PROFILE.provider, 1, PROFILE.profile_id, 1, 1)
    stored = store.save(binding.provider, expected_version=1)
    probe = store.begin_probe(binding, expected_version=stored.version)
    store.take_probe(probe)
    verified = store.record_probe(probe, passed=True)
    store.activate(binding, expected_version=verified.version, verified_probe_id=probe.probe_id)
    return store, binding


def test_gateway_replay_after_restart_does_not_call_authority_or_http(tmp_path):
    path = tmp_path / "gateway.sqlite"
    journal = DispatchJournal.provision(path)
    calls = []

    def handler(request):
        calls.append("http")
        return httpx.Response(200, json=response(
            PROFILE.provider, '{"hypothesis":"synthetic"}',
        ) | {"usage": {"input_tokens": 10, "output_tokens": 5}})

    def authorize(request):
        calls.append("authority")
        return AuthorizedDispatch(request, PROFILE, PROJECTION)

    transport = OfflineProviderHttp(httpx.MockTransport(handler))
    request = make_request()
    credentials, _ = active_credentials()
    gateway = OfflineGateway(
        journal, transport, authorize, credentials=credentials, capabilities=(PROFILE,),
    )
    assert gateway.dispatch(request).state is ResultState.ACCEPTED
    reopened = DispatchJournal(path)
    restarted = OfflineGateway(
        reopened, transport, authorize, credentials=credentials, capabilities=(PROFILE,),
    )
    assert restarted.dispatch(request).state is ResultState.OUTCOME_UNKNOWN
    assert reopened.status(request.attempt_id, request.request_hash) is DispatchOutcome.COMPLETED
    assert calls == ["authority", "http"]


@pytest.mark.parametrize("change", ["revoked", "projection", "generation", "credential", "hull"])
def test_gateway_denies_revoked_or_mismatched_authorization(tmp_path, change):
    journal = DispatchJournal.provision(tmp_path / "gateway.sqlite")
    transport = OfflineProviderHttp(httpx.MockTransport(lambda _: pytest.fail("unexpected HTTP")))
    request = make_request()
    authorized = {
        "revoked": None,
        "projection": AuthorizedDispatch(request, PROFILE, PROJECTION + b" "),
        "generation": AuthorizedDispatch(request, replace(PROFILE, model_generation=2), PROJECTION),
        "credential": AuthorizedDispatch(
            request, replace(PROFILE, credential_generation=2), PROJECTION,
        ),
        "hull": AuthorizedDispatch(
            request, replace(PROFILE, instructions="modified envelope"), PROJECTION,
        ),
    }[change]
    credentials, _ = active_credentials()
    result = OfflineGateway(
        journal, transport, lambda _: authorized, credentials=credentials, capabilities=(PROFILE,),
    ).dispatch(request)
    assert result.state is ResultState.DISPATCH_DENIED
    assert journal.status(request.attempt_id, request.request_hash) is DispatchOutcome.UNSENT


def test_missing_usage_with_valid_output_remains_unknown(tmp_path):
    journal = DispatchJournal.provision(tmp_path / "gateway.sqlite")
    request = make_request()
    transport = OfflineProviderHttp(httpx.MockTransport(lambda _: httpx.Response(
        200, json=response(PROFILE.provider, '{"hypothesis":"synthetic"}'),
    )))
    gateway = OfflineGateway(
        journal, transport, lambda current: AuthorizedDispatch(current, PROFILE, PROJECTION),
        credentials=active_credentials()[0], capabilities=(PROFILE,),
    )
    result = gateway.dispatch(request)
    assert result.state is ResultState.OUTCOME_UNKNOWN and result.output is None
    assert journal.status(
        request.attempt_id, request.request_hash,
    ) is DispatchOutcome.OUTCOME_UNKNOWN


def test_in_flight_cancellation_discards_result_and_retains_unknown_outcome(tmp_path):
    journal = DispatchJournal.provision(tmp_path / "gateway.sqlite")
    request = make_request()
    entered, proceed = Event(), Event()

    def provider(_request):
        entered.set()
        assert proceed.wait(timeout=5)
        return httpx.Response(200, json=response(PROFILE.provider, '{"hypothesis":"synthetic"}'))

    gateway = OfflineGateway(
        journal, OfflineProviderHttp(httpx.MockTransport(provider)),
        lambda current: AuthorizedDispatch(current, PROFILE, PROJECTION),
        credentials=active_credentials()[0], capabilities=(PROFILE,),
    )
    auth = AdmissionAuthentication(bytes(range(32)))
    app = create_cancellation_app(auth, gateway.cancel)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        dispatch = pool.submit(gateway.dispatch, request)
        try:
            assert entered.wait(timeout=5)
            assert gateway.dispatch(request).state is ResultState.OUTCOME_UNKNOWN
            body = _request_body(request)
            forged = client.post(CANCELLATION_PATH, content=body, headers={
                SIGNATURE: auth.request_signature(body),
            })
            assert forged.status_code == 403
            cancellation = CancellationClient(client, auth)
            assert cancellation.cancel(request.attempt_id, request.request_hash)
        finally:
            proceed.set()
        result = dispatch.result(timeout=5)
    assert result.state is ResultState.DISPATCH_CANCELLED and result.output is None
    outcome = journal.status(request.attempt_id, request.request_hash)
    assert outcome is DispatchOutcome.OUTCOME_UNKNOWN
