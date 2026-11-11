from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from packages.agent_research.discovery.evidence import (
    EvaluationBinding,
    EvidenceAccess,
    EvidenceGroup,
    EvidenceKind,
    EvidencePosition,
    EvidenceRevision,
    Readiness,
    Rights,
    TriggerPolicy,
    TriggerReason,
    decision_fingerprint,
    evaluate_evidence,
    evaluation_fingerprint,
    group_evidence,
)

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)
READY = EvidenceAccess(Readiness.READY, Rights.ALLOWED, Rights.ALLOWED)
POLICY = TriggerPolicy("rules-v1", "allow-v1", frozenset({"instrument-1"}), timedelta(days=2))
EVIDENCE = EvidenceRevision(
    EvidencePosition(10, EvidenceKind.MARKET_REVISION, "revision-1"),
    1,
    "instrument-1",
    NOW - timedelta(minutes=10),
    NOW - timedelta(hours=2),
    NOW - timedelta(hours=1),
    "1" * 64,
    True,
)
BINDING = EvaluationBinding(
    "rules-v1",
    "allow-v1",
    "quant-research-agent-v1",
    1,
    "prompt-v1",
    "OPENAI",
    "profile-1",
    1,
    "model-1",
    1,
    "discovery-output-v1",
)


def evaluate(evidence=EVIDENCE, access=READY, *, previous=None, policy=POLICY, now=NOW):
    return evaluate_evidence(evidence, access, policy, now=now, previous=previous)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"retracted": True}, TriggerReason.RETRACTED_EVIDENCE),
        ({"rights": Rights.UNKNOWN}, TriggerReason.RIGHTS_UNAVAILABLE),
        ({"provider_rights": Rights.DENIED}, TriggerReason.PROVIDER_RIGHTS_UNAVAILABLE),
        ({"readiness": Readiness.UNKNOWN}, TriggerReason.EVIDENCE_NOT_READY),
        ({"readiness": Readiness.BLOCKED}, TriggerReason.EVIDENCE_NOT_READY),
    ],
)
def test_gate_changes_fail_closed(change, reason):
    decision = evaluate(access=replace(READY, **change))
    assert decision.reason == reason
    assert not decision.eligible


def test_source_kind_scope_freshness_and_window():
    assert evaluate().reason == TriggerReason.NEW_EVIDENCE
    assert evaluate().eligible
    assert evaluate(replace(EVIDENCE, instrument_id="other")).reason == (
        TriggerReason.SOURCE_OUT_OF_SCOPE
    )
    assert evaluate(
        policy=replace(POLICY, kinds=frozenset({EvidenceKind.QUANT_RESULT}))
    ).reason == (TriggerReason.SOURCE_OUT_OF_SCOPE)
    assert evaluate(now=NOW + timedelta(days=3)).reason == TriggerReason.STALE_EVIDENCE
    assert (
        evaluate(replace(EVIDENCE, observed_at=None)).reason == TriggerReason.EVIDENCE_TIME_UNCLEAR
    )
    assert evaluate(replace(EVIDENCE, window_end=NOW)).reason == TriggerReason.EVIDENCE_TIME_UNCLEAR
    with pytest.raises(ValueError, match="unregistered"):
        EvidencePosition(11, "NEWS", "headline-1")


def test_new_commit_does_not_make_identical_content_material():
    newer = replace(EVIDENCE, position=replace(EVIDENCE.position, commit_version=11))
    assert evaluate(newer, previous=EVIDENCE).reason == TriggerReason.UNCHANGED_EVIDENCE
    changed = replace(newer, revision=2, content_hash="2" * 64)
    assert evaluate(changed, previous=EVIDENCE).reason == TriggerReason.CHANGED_EVIDENCE
    assert evaluate(replace(changed, material_change=False), previous=EVIDENCE).reason == (
        TriggerReason.NON_MATERIAL_CHANGE
    )
    assert evaluate(changed, previous=EVIDENCE).eligible
    with pytest.raises(ValueError, match="prior evidence"):
        evaluate(changed, previous=replace(EVIDENCE, instrument_id="other"))


def test_only_persisted_findings_can_be_contradictions():
    with pytest.raises(ValueError, match="persisted findings"):
        replace(EVIDENCE, contradicts_prior=True)
    old = replace(EVIDENCE, position=replace(EVIDENCE.position, kind=EvidenceKind.QUANT_RESULT))
    new = replace(
        old,
        position=replace(old.position, commit_version=11),
        revision=2,
        content_hash="2" * 64,
        contradicts_prior=True,
    )
    assert evaluate(new, previous=old).reason == TriggerReason.CONTRADICTORY_FINDING


def test_group_boundaries_use_sealed_positions_not_input_order_or_page_size():
    values = [
        replace(EVIDENCE, position=replace(EVIDENCE.position, source_id=f"rev-{n}"))
        for n in range(5)
    ]
    pages = (values[:1], values[1:4], values[4:])
    forward = group_evidence([item for page in pages for item in page], max_items=2)
    backwards = group_evidence(values[::-1], max_items=2)
    assert [len(group.items) for group in forward] == [2, 2, 1]
    assert forward == backwards
    assert [group.fingerprint for group in forward] == [group.fingerprint for group in backwards]
    with pytest.raises(ValueError, match="repeated positions"):
        group_evidence([values[0], values[0]])
    with pytest.raises(ValueError, match="one commit"):
        group_evidence(
            [values[0], replace(EVIDENCE, position=replace(EVIDENCE.position, commit_version=11))]
        )


def test_fingerprint_binds_revisions_but_not_commit_reingestion_or_model_configuration():
    original = EvidenceGroup((EVIDENCE,))
    replay = EvidenceGroup(
        (replace(EVIDENCE, position=replace(EVIDENCE.position, commit_version=11)),)
    )
    assert original.fingerprint == replay.fingerprint
    new_version = EvidenceGroup((replace(EVIDENCE, revision=2),))
    assert new_version.fingerprint != original.fingerprint
    assert EvidenceGroup.from_record(original.record()) == original
    assert EvidenceRevision.from_record(EVIDENCE.record()) == EVIDENCE
    with pytest.raises(ValueError, match="noncanonical"):
        EvidenceRevision.from_record(EVIDENCE.record() | {"untrustedExtra": "value"})
    decision = evaluate()
    assert decision_fingerprint(EVIDENCE, decision, POLICY) != decision_fingerprint(
        EVIDENCE,
        decision,
        replace(POLICY, rule_version="rules-v2"),
    )


def test_reanalysis_requires_distinct_authorization_and_model_or_prompt_change():
    original = EvidenceGroup((EVIDENCE,))
    model_changed = replace(BINDING, model_generation=2)
    authorized = replace(model_changed, reanalysis_id="authorized-1")
    assert evaluation_fingerprint(original, BINDING) != evaluation_fingerprint(
        original, model_changed
    )
    assert not model_changed.permits_reanalysis(BINDING)
    assert not replace(BINDING, reanalysis_id="authorized-1").permits_reanalysis(BINDING)
    assert authorized.permits_reanalysis(BINDING)
    assert not authorized.permits_reanalysis(authorized)
