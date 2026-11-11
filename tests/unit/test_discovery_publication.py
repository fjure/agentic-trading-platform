"""APF-D03 deterministic publication contract and evidence admission tests."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from jsonschema import Draft202012Validator

from packages.agent_research.companion.ideas import IdeaLifecycle, IdeaOrigin, IdeaStatus
from packages.agent_research.discovery.publication import (
    PUBLICATION_SCHEMA,
    EvidenceClaim,
    EvidenceKind,
    KnownPublication,
    PublicationAssessment,
    PublicationDenied,
    validate_publication,
)
from tests.unit.test_companion_interpretation import CONTRACT

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
START = NOW - timedelta(days=6)
END = NOW - timedelta(days=2)
_QUOTE = "Persisted market revision changed 12 rows."


def _source(**changes):
    return replace(EvidenceClaim(
        EvidenceKind.MARKET_REVISION, "source-a", 1, "a" * 64, "instrument-a",
        NOW - timedelta(hours=1), START, END, (_QUOTE,),
        market="Technology shares", rights_allowed=True, quality_ok=True,
        quality_findings=("Synthetic data quality receipt",),
    ), **changes)


def _idea(evidence=None):
    evidence = evidence or _source()
    return {
        "title": "Do technology shares reverse after a price decline?",
        "hypothesis": {
            "statement": "Does a reversal follow an initial decline?",
            "market": evidence.market, "trigger": "First selloff", "signal": "Negative close",
            "observationWindow": "Prior sessions", "target": "Rebound",
            "measurementHorizon": "Next session", "benchmark": "Broad equity market",
            "evaluationCriterion": "Relative change after initial decline",
        },
        "direction": "INCREASE", "instrumentId": evidence.instrument_id,
        "observationStart": evidence.observation_start.isoformat().replace("+00:00", "Z"),
        "observationEnd": evidence.observation_end.isoformat().replace("+00:00", "Z"),
        "proof": [{
            "kind": evidence.kind.value, "evidenceId": evidence.evidence_id,
            "revision": evidence.revision, "quote": evidence.quotes[0],
        }],
    }


def _validate(*ideas, evidence=None, **options):
    return validate_publication(
        json.dumps({"ideas": list(ideas)}).encode(),
        **({"evidence": evidence or (_source(),), "now": NOW,
            "priority_rule_version": "priority-v1", "max_ideas": 3,
            "permits": lambda _: True} | options),
    )


def test_closed_evidence_proposal_maps_to_exact_companion_contract():
    row = _idea()
    assert Draft202012Validator(PUBLICATION_SCHEMA).is_valid({"ideas": [row]})
    result = _validate(row)
    assert result.denials == () and len(result.candidates) == 1
    accepted = result.candidates[0]
    content = accepted.idea_content(run_id="research-run")
    assert _QUOTE not in json.dumps(content) and "proof" not in content
    assert content["evidence"][0]["qualityFindings"] == ["Synthetic data quality receipt"]
    assert content["discovery"]["freshness"] == "CURRENT"
    assert content["discovery"]["estimatedEffort"] == "UNKNOWN"
    lifecycle = IdeaLifecycle(CONTRACT, permits=lambda _: True, verifies=lambda *_: True)
    discovered = lifecycle.create(
        content, idea_id="idea", initiator_id="operator", origin=IdeaOrigin.AGENT, created_at=NOW,
    )
    assert discovered.status is IdeaStatus.DISCOVERED
    assert discovered.to_document()["experimentDraftIds"] == []


@pytest.mark.parametrize("change,code", [
    ({"tool": "START_QUANT"}, "INVALID_RESEARCH_IDEA"),
    ({"title": "Did returns rise by 90 percent?"}, "UNVERIFIED_RESEARCH_CLAIM"),
    ({"instrumentId": "foreign"}, "EVIDENCE_REFERENCE_DENIED"),
    ({"observationStart": "2026-10-01T00:00:00Z"}, "EVIDENCE_WINDOW_UNVERIFIED"),
    ({"proof": [{"kind": "MARKET_REVISION", "evidenceId": "source-a", "revision": 1,
                "quote": "Fabricated market revision changed 88 rows."}]},
     "EVIDENCE_QUOTE_UNVERIFIED"),
    ({"proof": [{"kind": "MARKET_REVISION", "evidenceId": "source-a", "revision": 2,
                "quote": _QUOTE}]}, "EVIDENCE_REFERENCE_DENIED"),
])
def test_unverified_claims_and_scope_fail_closed(change, code):
    denied = _validate(_idea() | change)
    assert denied.candidates == () and denied.denials == (code,)


@pytest.mark.parametrize("changes", [{"retracted": True}, {"rights_allowed": False},
                                     {"quality_ok": False}])
def test_current_evidence_cannot_be_published_when_retracted_or_restricted(changes):
    denied = _validate(_idea(), evidence=(_source(**changes),))
    assert denied.candidates == () and denied.denials == ("EVIDENCE_NO_LONGER_ALLOWED",)


def test_novel_evidence_required_for_previously_published_exact_hypothesis():
    previous = _validate(_idea()).candidates[0]
    known = KnownPublication(
        "known-idea", previous.hypothesis_key, previous.comparison_key,
        previous.observation_start, previous.observation_end, frozenset({_source().key}),
    )
    assert _validate(_idea(), known=(known,)).denials == ("DUPLICATE_RESEARCH_IDEA",)
    replacement = _source(revision=2, material_change=True)
    new = _validate(_idea(replacement), evidence=(replacement,), known=(known,)).candidates[0]
    assert new.hypothesis_key == previous.hypothesis_key
    assert new.publication_key != previous.publication_key
    assert new.idea_content(run_id="run")["relatedIdeaIds"] == ["known-idea"]


def test_overlapping_similar_ideas_are_referred_to_review():
    row = _idea()
    alternate = row | {"hypothesis": row["hypothesis"] | {"benchmark": "Alternative index"}}
    result = _validate(row, alternate)
    assert len(result.candidates) == 1
    assert result.denials == ("SIMILAR_IDEA_REVIEW_REQUIRED",)
    assert _validate(alternate, row).candidates == result.candidates


def test_freshness_priority_and_publication_limit_are_deterministic():
    fresh = _source()
    old = _source(evidence_id="source-b", instrument_id="instrument-b", market="Bond market",
                  observed_at=NOW - timedelta(days=10),
                  observation_start=START - timedelta(days=10),
                  observation_end=END - timedelta(days=10))
    def assess(_):
        return PublicationAssessment(True, "LOW", "Katalogabgleich liegt vor.")
    result = _validate(_idea(old), _idea(fresh), evidence=(old, fresh), assess=assess, max_ideas=1)
    assert result.denials == ("PUBLICATION_LIMIT_REACHED",)
    selected = result.candidates[0].idea_content(run_id="run")
    assert selected["instrumentIds"] == [fresh.instrument_id]
    assert selected["discovery"]["estimatedEffort"] == "LOW"


def test_unknown_policy_and_duplicate_json_do_not_produce_content():
    assert _validate(_idea(), permits=lambda _: None).denials == ("DISCOVERY_CONTENT_DENIED",)
    with pytest.raises(PublicationDenied, match="INVALID_DISCOVERY_OUTPUT"):
        validate_publication(
            b'{"ideas":[],"ideas":[]}', evidence=(), now=NOW,
            priority_rule_version="priority-v1", max_ideas=1, permits=lambda _: True,
        )
