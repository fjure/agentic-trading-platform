"""Read and verify the immutable Quant Lab evidence behind a research report."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from packages.backtesting.comparison import (
    BatchComparisonReference,
    ComparisonBinding,
    compare_batches,
)
from packages.backtesting.experiment import (
    PHASE_ROLES,
    REFERENCE_CONFIGURATIONS,
    CandidateConfiguration,
    canonical_definition_preimage,
    canonical_policy_preimage,
)
from packages.backtesting.experiment_storage import (
    PUBLIC_ROLES,
    definition_domain,
    reliability_identity,
    result_identity,
)
from packages.domain.models import utc_string
from packages.research.models import content_fingerprint


class SourceIntegrityError(ValueError):
    """The requested Quant source is absent or fails its persisted bindings."""


@dataclass(frozen=True, slots=True)
class QuantEvidence:
    batch_id: str
    definition_id: str
    definition_hash: str
    manifest_hash: str
    report_id: str
    report_hash: str
    registry_hash: str
    definition: dict
    manifest: dict
    selections: tuple[dict, ...]
    results: tuple[dict, ...]
    history: dict
    current_warnings: tuple[dict, ...]
    comparison: dict


def _require(condition):
    if not condition:
        raise SourceIntegrityError("Quant evidence unavailable or inconsistent")


def _verify(value, fingerprint):
    _require(content_fingerprint(value) == fingerprint)


def _result(row, batch_id, manifest_hash, definition_hash, policy_hash):
    link = row["body"]
    role, status = row["role"], row["status"]
    candidate, phase_key = row["candidate_id"], row["phase_key"]
    phase, fold = row["phase"], row["fold_index"]
    _verify(
        result_identity(
            link, candidate_id=candidate, role=role, phase_key=phase_key, status=status
        ),
        row["fingerprint"],
    )
    result = row["result_body"]
    reliability = None
    if status == "COMPLETE":
        expected_request = dict(
            candidateId=candidate,
            role=role,
            phaseKey=phase_key,
            definitionHash=definition_hash,
            strategySelectionPolicyHash=policy_hash,
        )
        expected_manifest = dict(expected_request, batchId=batch_id, snapshotHash=manifest_hash)
        _require(
            row["run_batch_id"] == row["batch_id"]
            and row["run_status"] == "SUCCEEDED"
            and result is not None
            and row["request"] == expected_request
            and row["run_manifest"] == expected_manifest
            and all(result[key] == value for key, value in expected_manifest.items())
            and link["definitionHash"] == definition_hash
            and link["strategySelectionPolicyHash"] == policy_hash
            and link["selectionValues"] == result["selectionValues"]
            and link["dataComplete"] is result["dataComplete"] is True
            and link["requiredMetricsValid"] is result["requiredMetricsValid"]
            and link["resultHash"] == row["result_hash"]
        )
        _verify(expected_request, row["request_hash"])
        _verify(expected_manifest, row["run_manifest_hash"])
        _verify(result, row["result_hash"])

        report_id, run_id = str(row["reliability_report_id"]), str(row["run_id"])
        report, findings, references = (
            row["reliability_body"],
            row["findings"],
            row["revision_references"],
        )
        _require(
            row["reliability_batch_id"] == row["batch_id"]
            and row["reliability_phase_key"] == phase_key
            and row["reliability_run_id"] == row["run_id"]
            and report["batchId"] == batch_id
            and report["reportId"] == report_id
            and report["runId"] == run_id
            and report["candidateId"] == candidate
            and report["role"] == PUBLIC_ROLES[role]
            and report["phase"] == phase
            and report["fold"] == fold
            and report["phaseRole"] == dict(PHASE_ROLES)[phase]
            and report["strategySelectionPolicyHash"] == policy_hash
            and report["methods"]["manifestHash"] == manifest_hash
            and report["findingsCount"] == len(findings)
            and report["revisionReferenceCount"] == len(references)
        )
        _verify(
            dict(report=report, findings=findings, revisionReferences=references),
            row["reliability_fingerprint"],
        )
        _verify(reliability_identity(report, findings, references), row["reliability_hash"])
        expected_reference = dict(
            runId=run_id,
            reportId=report_id,
            reportHash=row["reliability_hash"],
            eligibility=report["overallStatus"],
            rating=report["rating"],
            evidenceStatus=report["evidenceStatus"],
        )
        _require(
            link["reliability"] == result["reliability"] == expected_reference
            and link["reliabilityStatus"] == report["evidenceStatus"]
        )
        reliability = expected_reference
    else:
        _require(row["run_id"] is None and row["reliability_report_id"] is None and result is None)
    return dict(
        batchId=batch_id,
        candidateId=candidate,
        role=PUBLIC_ROLES[role],
        phase=phase,
        researchRole=dict(PHASE_ROLES)[phase],
        fold=fold,
        phaseKey=phase_key,
        phaseHash=row["phase_hash"],
        evaluationStatus=status,
        manifestHash=manifest_hash,
        linkHash=row["fingerprint"],
        runId=str(row["run_id"]) if row["run_id"] else None,
        resultHash=row["result_hash"],
        metrics=result["metrics"] if result else None,
        selectionValues=result["selectionValues"] if result and phase == "validation" else None,
        reliability=reliability,
        reasonCodes=link.get("reasonCodes")
        or ([link.get("errorCode") or status] if not result else []),
    )


def _selection(row, results, policy):
    body, scope = row["body"], row["scope"]
    validation = sorted(
        result["linkHash"]
        for result in results
        if result["phase"] == "validation"
        and result["role"] == "SEARCH_CANDIDATE"
        and (scope == "final" or str(result["fold"]) == scope)
    )
    _require(
        body["scope"] == ("FINAL" if scope == "final" else "FOLD")
        and body["fold"] == (None if scope == "final" else int(scope))
        and body["validationInputHash"] == content_fingerprint(validation)
        and body["selectionHash"] == row["fingerprint"]
        and body["selectedCandidateId"] == row["selected_candidate_id"]
        and body["status"] == row["status"]
        and (
            row["selected_candidate_id"] is None
            or any(
                result["candidateId"] == row["selected_candidate_id"]
                and result["evaluationStatus"] == "COMPLETE"
                and result["linkHash"] in validation
                for result in results
            )
        )
    )
    payload = {
        key: value for key, value in body.items() if key not in {"selectionHash", "frozenAt"}
    }
    _verify(
        (
            "quant-selection-v1",
            policy,
            "final" if scope == "final" else f"fold:{scope}",
            validation,
            payload,
        ),
        row["fingerprint"],
    )
    return body


def _comparison_identity(comparison):
    evidence = {
        key: value
        for key, value in comparison.items()
        if key not in {"id", "batchId", "createdAt", "reportHash", "currentWarnings"}
    }
    evidence["selections"] = [
        {key: value for key, value in selection.items() if key != "frozenAt"}
        for selection in comparison["selections"]
    ]
    return evidence


def _wire_time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    _require(value.utcoffset() is not None)
    return utc_string(value.astimezone(UTC))


def _warnings(source):
    """Project current Quant warnings separately from the immutable evidence."""
    warnings = []
    if source["gateObservedAt"] is not None:
        warnings.append(
            dict(
                observedAt=_wire_time(source["gateObservedAt"]),
                code="CURRENT_RELIABILITY_BLOCK",
                relatedBatchId=None,
            )
        )
    warnings.extend(
        dict(
            observedAt=_wire_time(row["first_seen"]),
            code="LATER_REGISTERED_OVERLAP",
            relatedBatchId=row["source_id"] if row["source_kind"] == "BATCH" else None,
        )
        for row in source["laterOverlaps"]
    )
    return tuple(warnings)


class QuantReportSource:
    """Use only the report-scoped native reader on the existing agent research role."""

    def __init__(self, engine, *, idea_evidence_engine=None):
        self.engine = engine
        self.idea_evidence_engine = engine if idea_evidence_engine is None else idea_evidence_engine

    def idea_references(self, idea: dict) -> tuple[dict, ...]:
        """Resolve only exact, immutable discovery outbox references."""
        if not idea["evidence"]:
            return ()
        resolved = []
        with self.idea_evidence_engine.connect() as connection:
            for evidence in idea["evidence"]:
                kind = evidence["kind"]
                if kind not in {"MARKET_REVISION", "RESEARCH_FINDING", "QUANT_RESULT"}:
                    continue
                try:
                    source_id = UUID(evidence["evidenceId"])
                except (TypeError, ValueError):
                    continue
                reference = (
                    connection.execute(
                        text("""
                    SELECT source_id,revision,fingerprint,observed_at
                    FROM agent_research.discovery_sources
                    WHERE kind=:kind AND source_id=:source AND revision=:revision
                """),
                        dict(kind=kind, source=source_id, revision=evidence["revision"]),
                    )
                    .mappings()
                    .one_or_none()
                )
                if reference is None or _wire_time(reference["observed_at"]) != _wire_time(
                    evidence["observedAt"]
                ):
                    continue
                resolved.append(
                    dict(
                        evidenceId=str(reference["source_id"]),
                        revision=reference["revision"],
                        hash=reference["fingerprint"],
                        sourceType=kind,
                    )
                )
        return tuple(resolved)

    def load(self, batch_id: str, *, actor: str) -> QuantEvidence:
        try:
            with self.engine.connect() as connection:
                identifier = UUID(batch_id)
                source = connection.execute(
                    text("SELECT agent_research.report_quant_evidence(:batch,:actor)"),
                    {"batch": identifier, "actor": actor},
                ).scalar_one()
                _require(source is not None)
                return self._load(source, identifier)
        except (AttributeError, KeyError, TypeError, ValueError, IndexError) as exc:
            if isinstance(exc, SourceIntegrityError):
                raise
            raise SourceIntegrityError("Quant evidence unavailable or inconsistent") from None

    @staticmethod
    def _load(source, identifier):
        batch = source["batch"]
        _require(
            batch is not None
            and batch["batch_id"] == str(identifier)
            and batch["status"] == "SUCCEEDED"
            and batch["snapshot"] is not None
            and batch["report"] is not None
        )
        batch_id = str(identifier)
        definition, snapshot, report = batch["definition"], batch["snapshot"], batch["report"]
        definition_hash, manifest_hash = batch["definition_hash"], batch["manifest_hash"]
        domain = definition_domain(
            definition,
            definition_version=batch["definition_version"],
            predecessor_hash=batch["predecessor_hash"],
        )
        policy = canonical_policy_preimage(domain.strategy_selection_policy)
        _verify(canonical_definition_preimage(domain), definition_hash)
        _verify(policy, batch["policy_hash"])
        _verify(json.loads(batch["policy_canonical"]), batch["policy_hash"])
        manifest_preimage = json.loads(batch["canonical_manifest"])
        _verify(manifest_preimage, manifest_hash)
        _verify(report, batch["physical_hash"])
        manifest = snapshot["manifestReference"]
        canonical_manifest = manifest_preimage[1]
        _require(
            manifest_preimage[2] == definition_hash
            and snapshot["protocol"] == definition["protocol"]
            and snapshot["strategySelectionPolicy"] == definition["strategySelectionPolicy"]
            and manifest["simulatorVersion"] == canonical_manifest["simulator_version"]
            and manifest["metricsVersion"] == canonical_manifest["metrics_version"]
            and manifest["researchProtocolHash"] == domain.protocol.protocol_hash
            and manifest["commonWarmupBars"] == domain.warmup_count
            and manifest["snapshotReference"]
            == dict(
                snapshotVersion=canonical_manifest["snapshot_version"],
                snapshotAt=canonical_manifest["snapshot_at"],
            )
            and canonical_manifest["snapshot_version"] == batch["snapshot_version"]
            and canonical_manifest["snapshot_at"] == _wire_time(batch["snapshot_at"])
            and canonical_manifest["code_version"] == batch["code_version"] == manifest["buildSha"]
            and snapshot["definitionHash"] == report["definitionHash"] == definition_hash
            and manifest["snapshotHash"]
            == manifest["manifestHash"]
            == report["snapshotHash"]
            == manifest_hash
            and snapshot["registryHash"] == report["registryHash"] == batch["registry_hash"]
            and snapshot["strategySelectionPolicyHash"]
            == report["strategySelectionPolicyHash"]
            == manifest["strategySelectionPolicyHash"]
            == batch["policy_hash"]
        )

        phases = source["phases"]
        canonical_phases = {
            (phase["phase"], phase["fold_index"]): phase
            for phase in canonical_manifest["phase_bindings"]
        }
        _require(len(phases) == len(canonical_phases) == len(manifest["phaseBindings"]))
        projected_phases = {
            (phase["phase"], phase["fold"]): phase for phase in manifest["phaseBindings"]
        }
        for phase in phases:
            key = (phase["phase"], phase["fold_index"])
            wire, projection = canonical_phases[key], projected_phases[key]
            _verify(phase["body"], phase["fingerprint"])
            _require(
                phase["phase_key"]
                == (
                    "holdout"
                    if phase["fold_index"] is None
                    else f"{phase['fold_index']}:{phase['phase']}"
                )
                and phase["body"]["definitionHash"] == definition_hash
                and phase["body"]["strategySelectionPolicyHash"] == batch["policy_hash"]
                and _wire_time(phase["start_at"]) == wire["window"]["start"]
                and _wire_time(phase["end_at"]) == wire["window"]["end"]
                and projection["interval"] == wire["window"]
                and projection["researchRole"] == dict(PHASE_ROLES)[phase["phase"]]
                and projection["seedBarCount"] == len(wire["expected_seed_starts"])
                and projection["fittingStatus"] == "NOT_TRAINABLE"
                and phase["body"]["seedRevisionIds"] == list(map(str, phase["seed_ids"]))
            )

        links = source["links"]
        _require(
            [row["fingerprint"] for row in links] == report["resultHashes"]
            and [str(row["reliability_report_id"]) for row in links if row["reliability_report_id"]]
            == report["reliabilityReferences"]
        )
        candidates = {(candidate.candidate_id, "SEARCH") for candidate in domain.candidates}
        candidates.update(
            (candidate.candidate_id, role)
            for candidate, role in zip(
                REFERENCE_CONFIGURATIONS,
                ("FLAT_REFERENCE", "BUY_HOLD_REFERENCE"),
                strict=True,
            )
        )
        expected_links = {
            (candidate_id, role, phase["phase_key"])
            for candidate_id, role in candidates
            for phase in phases
        }
        _require(
            len(links) == len(expected_links)
            and {(row["candidate_id"], row["role"], row["phase_key"]) for row in links}
            == expected_links
        )
        for row in links:
            configuration = row["configuration"]
            candidate = CandidateConfiguration(
                configuration["strategyId"],
                tuple(sorted(configuration["parameters"].items())),
                configuration["version"],
            )
            _require(candidate.candidate_id == row["candidate_id"])
        results = tuple(
            _result(row, batch_id, manifest_hash, definition_hash, batch["policy_hash"])
            for row in links
        )
        selections = source["selections"]
        _require(
            [row["fingerprint"] for row in selections] == report["selectionHashes"]
            and {row["scope"] for row in selections}
            == {"final", *map(str, range(len(definition["protocol"]["folds"])))}
        )
        selections = tuple(_selection(row, results, policy) for row in selections)
        selections_by_fold = {selection["fold"]: selection for selection in selections}
        for row in results:
            expected = "COMPLETE"
            if row["role"] == "SEARCH_CANDIDATE" and row["phase"] in {"test", "holdout"}:
                selected = selections_by_fold[row["fold"]]["selectedCandidateId"]
                if selected is None:
                    expected = "NOT_EVALUATED_NO_SELECTION"
                elif row["candidateId"] != selected:
                    expected = "NOT_EVALUATED_BY_PROTOCOL"
            _require(row["evaluationStatus"] == expected)

        comparison = report["comparison"]
        binding = ComparisonBinding(tuple(map(tuple, snapshot["comparisonDimensions"])))
        _verify(_comparison_identity(comparison), comparison["reportHash"])
        history = comparison["researchHistory"]
        _require(
            comparison["batchId"] == batch_id
            and comparison["status"] == "COMPLETE"
            and comparison["strategySelectionPolicyHash"] == batch["policy_hash"]
            and binding.fingerprint
            == comparison["comparisonFingerprint"]
            == manifest["comparisonFingerprint"]
            and history["registryHash"] == batch["registry_hash"]
            and history["boundAt"] == _wire_time(batch["accepted_at"])
            and sorted(comparison["selections"], key=lambda item: item["selectionHash"])
            == sorted(selections, key=lambda item: item["selectionHash"])
        )
        return QuantEvidence(
            batch_id=batch_id,
            definition_id=str(batch["definition_id"]),
            definition_hash=definition_hash,
            manifest_hash=manifest_hash,
            report_id=comparison["id"],
            report_hash=comparison["reportHash"],
            registry_hash=batch["registry_hash"],
            definition=definition,
            manifest=manifest,
            selections=selections,
            results=results,
            history=history,
            current_warnings=_warnings(source),
            comparison=comparison | {"dimensions": snapshot["comparisonDimensions"]},
        )

    def compare(self, left_batch_id: str, right_batch_id: str, *, actor: str):
        sources = (self.load(left_batch_id, actor=actor), self.load(right_batch_id, actor=actor))
        return compare_batches(
            tuple(
                BatchComparisonReference(
                    UUID(source.batch_id),
                    UUID(source.report_id),
                    ComparisonBinding(tuple(map(tuple, source.comparison["dimensions"]))),
                    "COMPLETE",
                )
                for source in sources
            )
        )
