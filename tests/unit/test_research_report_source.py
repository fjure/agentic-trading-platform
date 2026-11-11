from copy import deepcopy
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from packages.agent_research.reporting.source import (
    QuantReportSource,
    SourceIntegrityError,
    _result,
    _selection,
)
from packages.backtesting.experiment_storage import reliability_identity, result_identity
from packages.research.models import content_fingerprint

BATCH = UUID(int=1)
RUN = UUID(int=2)
RELIABILITY = UUID(int=3)
DEFINITION_HASH = "d" * 64
MANIFEST_HASH = "a" * 64
POLICY_HASH = "b" * 64


def _link(row):
    row["fingerprint"] = content_fingerprint(
        result_identity(
            row["body"],
            candidate_id=row["candidate_id"],
            role=row["role"],
            phase_key=row["phase_key"],
            status=row["status"],
        )
    )
    return row


def _published_result():
    request = dict(
        candidateId="candidate",
        role="SEARCH",
        phaseKey="0:validation",
        definitionHash=DEFINITION_HASH,
        strategySelectionPolicyHash=POLICY_HASH,
    )
    manifest = dict(request, batchId=str(BATCH), snapshotHash=MANIFEST_HASH)
    report = dict(
        batchId=str(BATCH),
        runId=str(RUN),
        reportId=str(RELIABILITY),
        candidateId="candidate",
        role="SEARCH_CANDIDATE",
        phase="validation",
        fold=0,
        phaseRole="PARAMETER_SELECTION",
        strategySelectionPolicyHash=POLICY_HASH,
        methods={"manifestHash": MANIFEST_HASH},
        findingsCount=0,
        revisionReferenceCount=0,
        overallStatus="ELIGIBLE",
        rating="PASS",
        evidenceStatus="COMPLETE",
    )
    envelope = dict(report=report, findings=[], revisionReferences=[])
    report_hash = content_fingerprint(reliability_identity(report, [], []))
    reliability = dict(
        runId=str(RUN),
        reportId=str(RELIABILITY),
        reportHash=report_hash,
        eligibility="ELIGIBLE",
        rating="PASS",
        evidenceStatus="COMPLETE",
    )
    values = dict(netReturn="-0.05")
    result = dict(
        manifest,
        metrics={"netReturn": "-0.05"},
        reliability=reliability,
        selectionValues=values,
        dataComplete=True,
        requiredMetricsValid=False,
    )
    result_hash = content_fingerprint(result)
    link = dict(
        definitionHash=DEFINITION_HASH,
        strategySelectionPolicyHash=POLICY_HASH,
        reliability=reliability,
        selectionValues=values,
        resultHash=result_hash,
        dataComplete=True,
        requiredMetricsValid=False,
        reliabilityStatus="COMPLETE",
    )
    return _link(
        dict(
            batch_id=BATCH,
            candidate_id="candidate",
            role="SEARCH",
            phase_key="0:validation",
            phase="validation",
            fold_index=0,
            phase_hash="c" * 64,
            status="COMPLETE",
            run_id=RUN,
            run_batch_id=BATCH,
            run_status="SUCCEEDED",
            request=request,
            request_hash=content_fingerprint(request),
            run_manifest=manifest,
            run_manifest_hash=content_fingerprint(manifest),
            result_body=result,
            result_hash=result_hash,
            reliability_report_id=RELIABILITY,
            reliability_batch_id=BATCH,
            reliability_phase_key="0:validation",
            reliability_run_id=RUN,
            reliability_body=report,
            findings=[],
            revision_references=[],
            reliability_fingerprint=content_fingerprint(envelope),
            reliability_hash=report_hash,
            body=link,
        )
    )


def _project(row):
    return _result(row, str(BATCH), MANIFEST_HASH, DEFINITION_HASH, POLICY_HASH)


def test_result_projects_exact_hashes_and_phase():
    row = _published_result()
    actual = _project(row)
    assert actual["resultHash"] == row["result_hash"]
    assert actual["linkHash"] == row["fingerprint"]
    assert actual["runId"] == str(RUN)
    assert actual["researchRole"] == "PARAMETER_SELECTION"
    assert actual["metrics"] == {"netReturn": "-0.05"}


@pytest.mark.parametrize(
    "tamper",
    [
        lambda row: row["result_body"]["metrics"].update(netReturn="0.05"),
        lambda row: row["run_manifest"].update(snapshotHash="e" * 64),
        lambda row: row.update(run_batch_id=UUID(int=4)),
        lambda row: row.update(reliability_phase_key="holdout"),
        lambda row: row.update(role="FLAT_REFERENCE"),
        lambda row: row.update(result_body=None),
    ],
)
def test_result_rejects_tampering(tamper):
    row = _published_result()
    tamper(row)
    with pytest.raises(SourceIntegrityError):
        _project(row)


def test_unselected_holdout_retains_reason_and_has_no_metrics():
    row = _link(
        dict(
            body={"reasonCodes": ["NOT_EVALUATED_NO_SELECTION"]},
            role="SEARCH",
            candidate_id="candidate",
            phase_key="holdout",
            phase="holdout",
            fold_index=None,
            phase_hash="c" * 64,
            status="NOT_EVALUATED_NO_SELECTION",
            result_body=None,
            run_id=None,
            reliability_report_id=None,
            result_hash=None,
        )
    )
    actual = _project(row)
    assert actual["researchRole"] == "FINAL_HOLDOUT"
    assert actual["reasonCodes"] == ["NOT_EVALUATED_NO_SELECTION"]
    assert actual["metrics"] is actual["reliability"] is None


def test_selection_is_bound_to_exact_validation_link_hashes_and_candidate():
    validation = _project(_published_result())
    inputs = [validation["linkHash"]]
    payload = dict(
        scope="FOLD",
        fold=0,
        selectedCandidateId="candidate",
        status="SELECTED",
        validationInputHash=content_fingerprint(inputs),
    )
    policy = ["policy"]
    fingerprint = content_fingerprint(("quant-selection-v1", policy, "fold:0", inputs, payload))
    row = dict(
        scope="0",
        body=payload | {"selectionHash": fingerprint},
        fingerprint=fingerprint,
        status="SELECTED",
        selected_candidate_id="candidate",
    )
    assert _selection(row, (validation,), policy)["selectedCandidateId"] == "candidate"

    wrong_phase = validation | {"phase": "holdout"}
    with pytest.raises(SourceIntegrityError):
        _selection(row, (wrong_phase,), policy)

    tampered = deepcopy(row)
    tampered["body"]["selectedCandidateId"] = "alternate"
    with pytest.raises(SourceIntegrityError):
        _selection(tampered, (validation,), policy)


def test_idea_reference_resolves_exact_outbox_revision_and_time():
    engine = MagicMock()
    query = engine.connect.return_value.__enter__.return_value.execute
    found = dict(
        source_id=BATCH,
        revision=2,
        fingerprint=DEFINITION_HASH,
        observed_at="2026-01-01T00:00:00+00:00",
    )
    query.return_value.mappings.return_value.one_or_none.side_effect = [found, found, None]
    reference = dict(
        evidenceId=str(BATCH), revision=2, kind="MARKET_REVISION", observedAt="2026-01-01T00:00:00Z"
    )
    result = QuantReportSource(engine).idea_references(
        {
            "evidence": [
                reference,
                reference | {"observedAt": "2026-01-02T00:00:00Z"},
                reference | {"revision": 3},
                reference | {"kind": "DATA_QUALITY"},
            ]
        }
    )
    assert result == (
        dict(evidenceId=str(BATCH), revision=2, hash=DEFINITION_HASH, sourceType="MARKET_REVISION"),
    )
    assert query.call_count == 3
    assert query.call_args.args[1] == {"kind": "MARKET_REVISION", "source": BATCH, "revision": 3}


def test_missing_scope_rejects_source_before_reading_evidence():
    engine = MagicMock()
    query = engine.connect.return_value.__enter__.return_value.execute
    query.return_value.scalar_one.return_value = None
    with pytest.raises(SourceIntegrityError):
        QuantReportSource(engine).load(str(BATCH), actor="another-operator")
    sql, parameters = query.call_args.args
    assert str(sql) == "SELECT agent_research.report_quant_evidence(:batch,:actor)"
    assert parameters == {"batch": BATCH, "actor": "another-operator"}
