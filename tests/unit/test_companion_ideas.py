"""APF-Q01 immutable idea history and server-owned lifecycle gates."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.agent_research.companion.ideas import (
    IdeaAction,
    IdeaDenied,
    IdeaLifecycle,
    IdeaOrigin,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/schemas/v1/agent-research.schema.json"
AT = datetime(2026, 11, 8, 12, tzinfo=UTC)
CONTENT = {"title": "Quarterly reversal", "originalText": "Quarterly earnings can overreact."}


def lifecycle(*, permits=lambda _: True, verifies=lambda *_: True):
    return IdeaLifecycle(CONTRACT, permits=permits, verifies=verifies)


def create(domain, content=CONTENT, origin=IdeaOrigin.USER):
    return domain.create(
        content, idea_id="idea-1", initiator_id="operator-1", origin=origin, created_at=AT,
    )


def revise(domain, current, action, changes=None):
    return domain.revise(
        current, expected_version=current.version, action=action,
        decision_id=f"decision-{current.version}",
        created_at=AT + timedelta(seconds=current.version),
        changes=changes,
    )


def test_original_hypothesis_and_version_lineage_remain_separate():
    domain = lifecycle()
    idea = create(domain)
    contract_sample = json.loads(
        (ROOT / "contracts/examples/v1/agent-research.valid.json").read_text(encoding="utf-8")
    )["items"][0]
    hypothesis = contract_sample["hypothesis"]
    confirmed = revise(domain, idea, IdeaAction.CONFIRM_HYPOTHESIS, {
        "hypothesis": hypothesis, "assumptions": [], "dialogueId": "dialogue-1",
    })
    hypothesis["trigger"] = "Caller mutation"
    document = confirmed.to_document()
    assert document["originalText"] == CONTENT["originalText"]
    assert document["hypothesis"]["trigger"] != "Caller mutation"
    assert document["previousVersion"] == 1
    assert document["decisionId"] == "decision-1"
    assert idea.to_document()["hypothesis"] is None
    assert domain.restore(document) == confirmed
    assert CONTENT["originalText"] not in repr(confirmed)


def test_agent_publication_requires_authoritative_validation():
    domain = lifecycle(verifies=lambda action, *_: action != IdeaAction.PUBLISH)
    agent_document = json.loads(
        (ROOT / "contracts/examples/v1/agent-research.valid.json").read_text(encoding="utf-8")
    )["items"][1]
    content = {
        name: agent_document[name] for name in ("title", "description", "sourceId", "discovery")
    }
    discovery = create(domain, content, IdeaOrigin.AGENT)
    assert discovery.status == "DISCOVERED"
    with pytest.raises(IdeaDenied, match="IDEA_REFERENCE_DENIED"):
        revise(domain, discovery, IdeaAction.PUBLISH)
    assert revise(lifecycle(), discovery, IdeaAction.PUBLISH).status == "PROPOSED"


def test_stale_and_unlisted_transitions_are_rejected_before_verification():
    domain = lifecycle(verifies=lambda action, *_: action == IdeaAction.CREATE)
    idea = create(domain)
    with pytest.raises(IdeaDenied, match="STALE_IDEA_VERSION"):
        domain.revise(
            idea, expected_version=0, action=IdeaAction.REVIEW, decision_id="decision-1",
            created_at=AT,
        )
    with pytest.raises(IdeaDenied, match="INVALID_IDEA_TRANSITION"):
        revise(domain, idea, IdeaAction.COMPLETE_REPORT)
    with pytest.raises(IdeaDenied, match="INVALID_IDEA_CHANGE"):
        revise(domain, idea, IdeaAction.REVISE, {"originalText": "Replacement"})


def test_tested_requires_linked_experiment_and_authoritative_completed_report():
    domain = lifecycle(verifies=lambda action, *_: action != IdeaAction.COMPLETE_REPORT)
    reviewed = revise(domain, create(domain), IdeaAction.REVIEW)
    with pytest.raises(IdeaDenied, match="VALIDATED_DRAFT_REQUIRED"):
        revise(domain, reviewed, IdeaAction.VALIDATE_DRAFT)
    draft = revise(domain, reviewed, IdeaAction.VALIDATE_DRAFT, {
        "experimentDraftIds": ["draft-1"],
        "testability": {
            "classification": "EXACTLY_TESTABLE", "catalogVersion": "catalog-1",
            "mappingVersion": "mapping-1", "reasons": ["Exact mapping"],
            "limitations": [], "options": [],
        },
    })
    with pytest.raises(IdeaDenied, match="LINKED_EXPERIMENT_REQUIRED"):
        revise(domain, draft, IdeaAction.COMPLETE_REPORT)
    linked = revise(domain, draft, IdeaAction.LINK_EXPERIMENT, {"experimentIds": ["experiment-1"]})
    with pytest.raises(IdeaDenied, match="IDEA_REFERENCE_DENIED"):
        revise(domain, linked, IdeaAction.COMPLETE_REPORT)
    tested = revise(lifecycle(), linked, IdeaAction.COMPLETE_REPORT)
    assert tested.status == "TESTED"
    assert revise(lifecycle(), tested, IdeaAction.RECHECK).to_document()["experimentIds"] == []
    assert tested.to_document()["experimentIds"] == ["experiment-1"]


def test_rejected_recommendation_requires_new_verified_evidence():
    domain = lifecycle()
    rejected = revise(domain, create(domain), IdeaAction.REJECT)
    with pytest.raises(IdeaDenied, match="NEW_EVIDENCE_REQUIRED"):
        revise(domain, rejected, IdeaAction.RECHECK, {"description": "Wording only"})
    evidence = [{
        "kind": "MARKET_REVISION", "evidenceId": "source-1", "revision": 1,
        "observedAt": "2026-11-08T12:00:00Z", "qualityFindings": [],
    }]
    next_idea = revise(domain, rejected, IdeaAction.RECHECK, {"evidence": evidence})
    assert next_idea.status == "PROPOSED"
    assert next_idea.to_document()["previousVersion"] == rejected.version


def test_policy_failure_is_redacted_and_precedes_reference_resolution():
    reached = []

    def refuse(_document):
        raise RuntimeError("Sensitive exception text")

    domain = lifecycle(permits=refuse, verifies=lambda *args: reached.append(args))
    with pytest.raises(IdeaDenied, match="^IDEA_POLICY_DENIED$"):
        create(domain)
    assert reached == []


@pytest.mark.parametrize("field", ["testability", "interpretation", "evidence", "assumptions"])
def test_user_intake_cannot_write_derived_or_authoritative_fields(field):
    with pytest.raises(IdeaDenied, match="INVALID_IDEA"):
        create(lifecycle(), CONTENT | {field: []})
