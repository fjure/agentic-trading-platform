"""Synthetic TypeSafe wire contract and shadow-only safety tests."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from packages.decisions.baseline import _canonical
from packages.decisions.jev import JevDecisionEngine, load_config
from packages.decisions.replay import CachedResponse, ReplayCase, replay
from packages.domain.models import Choice, NoulAnswer
from services.worker.planner import PlannerConfig, plan_proposal
from tests.unit.test_decision import NOW, FixedClock, SeededIds, state

_KEY = "synthetic-credential-never-real"


def _response(*, direction: bool = True) -> dict:
    labels = list(load_config().questions["evidence_quality"]["criteria"])
    quality_probabilities = {str(index): 0 for index in range(10)}
    quality_probabilities["0"] = 0.1
    quality_probabilities["9"] = 0.9
    answers = {
        "regime": {
            "type": "choice",
            "choice": "TREND",
            "probabilities": {"TREND": 0.8, "RANGE": 0.1, "UNCERTAIN": 0.1},
            "confidence": 0.8,
        },
        "evidence_quality": {
            "type": "score",
            "score": 8.1,
            "legend": {str(index): label for index, label in enumerate(labels)},
            "probabilities": quality_probabilities,
            "confidence": 0.9,
        },
        "signal_conflict": {"type": "noul", "noul": 0.1},
    }
    if direction:
        answers["shadow_direction"] = {
            "type": "choice",
            "choice": "LONG",
            "probabilities": {"LONG": 0.8, "FLAT": 0.1, "ABSTAIN": 0.1},
            "confidence": 0.8,
        }
    return {
        "model": "jev-1.13.0",
        "answers": answers,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }


def _engine(handler=None, *, enabled: bool = True, monotonic_ns=None, config=None, clock=None):
    config = config or replace(load_config(), enabled=enabled)
    handler = handler or (lambda _request: httpx.Response(200, json=_response()))
    kwargs = {
        "clock": clock or FixedClock(),
        "ids": SeededIds(),
        "api_key": _KEY,
        "config": config,
        "transport": httpx.MockTransport(handler),
    }
    if monotonic_ns is not None:
        kwargs["monotonic_ns"] = monotonic_ns
    return JevDecisionEngine(**kwargs)


def test_disabled_default_and_fixed_shadow_mode() -> None:
    assert load_config().enabled is False
    assert (
        JevDecisionEngine(clock=FixedClock(), ids=SeededIds())
        .decide(state(), "active.v1")
        .reason_code
        == "ABSTAIN_UNAVAILABLE"
    )
    with pytest.raises(ValueError, match="unsafe Jev configuration"):
        replace(load_config(), mode="PAPER")
    with pytest.raises(ValueError, match="unsafe Jev configuration"):
        replace(load_config(), model="jev-latest")
    for change in (
        {"max_state_age_seconds": 121},
        {"minimum_confidence": Decimal("0.69")},
        {"minimum_evidence_quality": Decimal("0.69")},
        {"maximum_signal_conflict": Decimal("0.26")},
    ):
        with pytest.raises(ValueError, match="unsafe Jev"):
            replace(load_config(), **change)
    with pytest.raises(ValueError, match="MockTransport"):
        JevDecisionEngine(clock=FixedClock(), ids=SeededIds(), transport=httpx.HTTPTransport())


def test_official_request_and_typed_shadow_response_replay() -> None:
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == f"Bearer {_KEY}"
        sent = json.loads(request.content)
        assert set(sent) == {"model", "state", "questions"}
        assert sent["model"] == "jev-1.13.0"
        assert set(sent["questions"]) == {
            "regime",
            "evidence_quality",
            "signal_conflict",
            "shadow_direction",
        }
        assert len(sent["questions"]["evidence_quality"]["criteria"]) == 10
        assert sent["state"]["features"]["last_close"] == "101"
        return httpx.Response(200, json=_response())

    source = state()
    engine = _engine(handler)
    result = engine.decide(source, "active.v1")
    assert len(seen) == 1
    assert result.choice is Choice.ABSTAIN
    assert result.reason_code == "JEV_SHADOW"
    assert result.shadow_choice is Choice.LONG
    assert result.typed_answers is not None
    assert result.typed_answers.regime.choice == "TREND"
    assert result.typed_answers.evidence_quality.score == Decimal("8.1")
    assert result.evidence_quality == Decimal("0.900000000000000000")
    assert result.api_units == Decimal("100")
    assert (result.input_tokens, result.output_tokens) == (100, 10)
    assert result.estimated_cost is None
    assert engine.usage_for(result.id).output_tokens == 10
    assert result.config_version == engine.config.replay_config("active.v1").version
    assert (
        plan_proposal(
            decision=result,
            market_state=source,
            config=PlannerConfig("fixed.v1", Decimal("2")),
            clock=FixedClock(),
            ids=SeededIds(),
        )
        is None
    )
    case = ReplayCase("jev-case", source, NOW)
    cached = replay(
        (case,),
        engine.config.replay_config("active.v1"),
        source="cached",
        cached={case.case_id: CachedResponse(result)},
    )
    assert cached.records[0].result == result
    assert cached.metrics.reported_input_tokens == 100
    assert cached.metrics.reported_output_tokens == 10


def test_replay_rejects_forged_healthy_shadow_thresholds_and_stale_frame() -> None:
    engine = _engine()
    source = state()
    result = engine.decide(source, "active.v1")
    assert result.reason_code == "JEV_SHADOW"
    config = engine.config.replay_config("active.v1")
    assert config.shadow_minimum_evidence_quality == engine.config.minimum_evidence_quality
    assert config.shadow_maximum_signal_conflict == engine.config.maximum_signal_conflict
    case = ReplayCase("forged-shadow", source, NOW)

    def attempt(forged, replay_case=case):
        return replay(
            (replay_case,),
            config,
            source="cached",
            cached={"forged-shadow": CachedResponse(forged)},
        )

    with pytest.raises(ValueError, match="shadow response fails bound eligibility"):
        attempt(replace(result, evidence_quality=Decimal("0")))
    conflict = replace(
        result.typed_answers,
        signal_conflict=NoulAnswer(config.rules.question_version, Decimal("1")),
    )
    with pytest.raises(ValueError, match="shadow response fails bound eligibility"):
        attempt(replace(result, typed_answers=conflict, signal_conflict=Decimal("1")))
    stale_at = NOW + timedelta(seconds=121)
    stale = ReplayCase("forged-shadow", source, stale_at)
    with pytest.raises(ValueError, match="admitted replay input"):
        attempt(replace(result, valid_until=stale_at + timedelta(seconds=1)), stale)


def test_optional_direction_still_abstains() -> None:
    config = replace(
        load_config(),
        enabled=True,
        questions={
            key: value
            for key, value in load_config().questions.items()
            if key != "shadow_direction"
        },
    )
    engine = _engine(
        lambda _request: httpx.Response(200, json=_response(direction=False)), config=config
    )
    result = engine.decide(state(), "active.v1")
    assert result.choice is Choice.ABSTAIN
    assert result.typed_answers.shadow_direction is None
    assert result.shadow_choice is None
    assert result.distribution["ABSTAIN"] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["answers"].pop("regime"),
        lambda body: body["answers"].pop("shadow_direction"),
        lambda body: body["answers"]["regime"].update(choice="SHORT"),
        lambda body: body["answers"]["shadow_direction"].update(choice="FLAT"),
        lambda body: body["answers"]["regime"].update(
            probabilities={"TREND": 0.7, "RANGE": 0.1, "UNCERTAIN": 0.1}
        ),
        lambda body: body["answers"]["evidence_quality"].update(score=4.0),
        lambda body: body["answers"]["evidence_quality"]["legend"].update({"9": "changed"}),
        lambda body: body["answers"]["shadow_direction"].update(quantity="1000"),
        lambda body: body.update(order={"side": "BUY"}),
        lambda body: body["usage"].update(input_tokens=-1),
        lambda body: body["usage"].update(output_tokens=9_007_199_254_740_992),
    ],
)
def test_invalid_provider_fields_abstain_without_typed_answers(mutate) -> None:
    body = copy.deepcopy(_response())
    mutate(body)
    result = _engine(lambda _request: httpx.Response(200, json=body)).decide(state(), "active.v1")
    assert result.choice is Choice.ABSTAIN
    assert result.reason_code == "ABSTAIN_INVALID_RESPONSE"
    assert result.typed_answers is result.model_id is result.confidence is None
    assert result.input_tokens is result.output_tokens is None
    assert result.shadow_choice is None


@pytest.mark.parametrize(
    "body",
    [
        b'{"model":"jev-1.13.0","model":"other"}',
        b'{"model":"jev-1.13.0","answers":{},"usage":{"input_tokens":NaN}}',
    ],
)
def test_duplicate_and_nonfinite_json_abstain(body) -> None:
    result = _engine(lambda _request: httpx.Response(200, content=body)).decide(
        state(), "active.v1"
    )
    assert result.reason_code == "ABSTAIN_INVALID_RESPONSE"


def test_failure_status_redirect_and_raw_error_are_private() -> None:
    raw = f"provider says {_KEY} and secret-bearing trace"
    for status in (302, 401, 422, 429, 529):
        engine = _engine(
            lambda _request, status=status: httpx.Response(
                status, text=raw, headers={"Location": "https://other.invalid/"}
            )
        )
        result = engine.decide(state(), "active.v1")
        assert result.reason_code == "ABSTAIN_UNAVAILABLE"
        assert raw not in repr(result)
        assert engine.usage_for(result.id) is None


def test_late_response_is_discarded_and_bad_inputs_never_call_provider() -> None:
    calls = []
    ticks = iter((0, 5_000_000_001, 5_000_000_002))
    engine = _engine(
        lambda request: (calls.append(request), httpx.Response(200, json=_response()))[1],
        monotonic_ns=lambda: next(ticks),
    )
    assert engine.decide(state(), "active.v1").reason_code == "ABSTAIN_UNAVAILABLE"
    assert len(calls) == 1
    invalid = replace(state(), features={"last_close": Decimal("101")})
    safe = _engine(lambda request: pytest.fail("provider called for invalid state"))
    assert safe.decide(invalid, "active.v1").reason_code == "ABSTAIN_MISSING_EVIDENCE"


def test_final_clock_expiry_discards_fully_parsed_response() -> None:
    clock = FixedClock()

    def handler(_request):
        clock.value = NOW + timedelta(seconds=6)
        return httpx.Response(200, json=_response())

    result = _engine(handler, clock=clock).decide(state(), "active.v1")
    assert result.reason_code == "ABSTAIN_UNAVAILABLE"
    assert result.typed_answers is None


def test_final_monotonic_deadline_discards_constructed_result() -> None:
    ticks = iter((0, 1, 2, 5_000_000_001, 5_000_000_002))
    result = _engine(monotonic_ns=lambda: next(ticks)).decide(state(), "active.v1")
    assert result.reason_code == "ABSTAIN_UNAVAILABLE"
    assert result.typed_answers is None


def test_http_timeout_abstains_without_provider_body() -> None:
    result = _engine(
        lambda _request: (_ for _ in ()).throw(httpx.ReadTimeout("synthetic-private-error"))
    ).decide(state(), "active.v1")
    assert result.reason_code == "ABSTAIN_UNAVAILABLE"
    assert result.typed_answers is None


def test_low_evidence_quality_abstains_with_typed_answer() -> None:
    body = _response()
    probabilities = body["answers"]["evidence_quality"]["probabilities"]
    probabilities["0"], probabilities["9"] = 0.8, 0.2
    body["answers"]["evidence_quality"]["score"] = 1.8
    result = _engine(lambda _request: httpx.Response(200, json=body)).decide(state(), "active.v1")
    assert result.choice is Choice.ABSTAIN
    assert result.reason_code == "ABSTAIN_LOW_EVIDENCE_QUALITY"
    assert result.typed_answers is not None


def test_low_confidence_conflict_and_uncertain_never_create_direction() -> None:
    for alteration, reason in (
        (
            lambda b: b["answers"]["shadow_direction"].update(confidence=0.1),
            "ABSTAIN_LOW_CONFIDENCE",
        ),
        (lambda b: b["answers"]["signal_conflict"].update(noul=0.9), "ABSTAIN_SIGNAL_CONFLICT"),
        (
            lambda b: b["answers"]["regime"].update(
                choice="UNCERTAIN", probabilities={"TREND": 0.1, "RANGE": 0.1, "UNCERTAIN": 0.8}
            ),
            "ABSTAIN_UNCERTAIN_REGIME",
        ),
    ):
        body = _response()
        alteration(body)
        result = _engine(lambda _request, body=body: httpx.Response(200, json=body)).decide(
            state(), "active.v1"
        )
        assert result.choice is Choice.ABSTAIN
        assert result.reason_code == reason
        assert result.typed_answers is not None


def test_question_or_state_change_changes_replay_binding() -> None:
    config = replace(load_config(), enabled=True)
    changed = _canonical(config.questions)
    changed["regime"]["instructions"] += " Revised."
    revised = replace(config, questions=changed)
    assert revised.digest != config.digest
    first = _engine().decide(state(), "active.v1")
    second = JevDecisionEngine(
        clock=FixedClock(),
        ids=SeededIds(),
        api_key=_KEY,
        config=revised,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=_response())),
    ).decide(state(), "active.v1")
    assert first.input_hash != second.input_hash
    assert first.config_version != second.config_version
