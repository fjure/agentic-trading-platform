"""Build and persist a report for an already approved Quant batch."""

from collections.abc import Callable
from itertools import zip_longest

from packages.research.models import content_fingerprint

from .claims import PHASES, InterpretationDenied, interpret, metric_claims
from .store import ReportStoreError


class ReportDenied(ValueError):
    pass


def _phase_report(rows, final, fold_selections, manifest_hash, native_phase):
    def relevant(row):
        if row["role"] != "SEARCH_CANDIDATE":
            return True
        selected = final if row["fold"] is None else fold_selections.get(row["fold"])
        return selected is not None and row["candidateId"] == selected["selectedCandidateId"]

    rows = [row for row in rows if row["phase"] == native_phase and relevant(row)]
    rows.sort(key=lambda row: (
        -1 if row["fold"] is None else row["fold"], row["role"] != "SEARCH_CANDIDATE",
        row["candidateId"],
    ))
    groups = [metric_claims(row, manifest_hash) for row in rows]
    # Keep the primary measures across folds and subjects before optional details.
    all_claims = [claim for group in zip_longest(*groups) for claim in group if claim is not None]
    limitations = []
    if len(all_claims) > 64:
        limitations.append("Additional metrics are available in the original Quant results.")
    claims = tuple(all_claims[:64])
    if claims:
        status = "AVAILABLE"
        if not any(claim.reference["subject"] == "CANDIDATE" for claim in claims):
            limitations.append("Only benchmark values are available for this phase.")
        if any(
            row["role"] == "SEARCH_CANDIDATE" and row["metrics"] is not None
            and row["metrics"].get("sharpe", {}).get("status") != "VALID"
            for row in rows
        ):
            limitations.append("Sharpe ratio is unavailable for at least one candidate evaluation.")
    elif native_phase == "holdout" and final and final["status"] == "NO_ELIGIBLE_CANDIDATE":
        status = "NOT_SELECTED"
    else:
        status = "NOT_EVALUABLE" if rows else "UNAVAILABLE"
    codes = sorted({code for row in rows for code in row["reasonCodes"]})
    limitations.extend(codes[:16 - len(limitations)])
    return {"status": status, "metrics": [claim.document() for claim in claims],
            "limitations": limitations}, claims


def _display_text(idea: dict, definition: dict, permits: Callable[[list[str]], bool]) -> tuple:
    hypothesis = (idea.get("hypothesis") or {}).get("statement") or "Hypothesis unavailable."
    assumptions = [entry["statement"] for entry in idea["assumptions"]]
    title, question = idea["title"], idea["description"]
    if permits([title, question, hypothesis, definition["hypothesis"], *assumptions]) is not True:
        raise ReportDenied("REPORT_CONTEXT_DENIED")
    return title, question, hypothesis, assumptions


class ResearchReports:
    def __init__(self, source, store, *, permits: Callable[[list[str]], bool]):
        self.source, self.store, self.permits = source, store, permits

    def compare(self, left_batch_id: str, right_batch_id: str, *, actor: str) -> dict:
        for batch_id in (left_batch_id, right_batch_id):
            if self.store.authorize(batch_id, actor=actor) is None:
                raise ReportDenied("REPORT_SCOPE_DENIED")
        return self.source.compare(left_batch_id, right_batch_id, actor=actor)

    def current_quant_warnings(self, batch_id: str, *, actor: str) -> tuple[dict, ...]:
        if self.store.authorize(batch_id, actor=actor) is None:
            raise ReportDenied("REPORT_SCOPE_DENIED")
        return self.source.load(batch_id, actor=actor).current_warnings

    def publish(
        self, batch_id: str, *, actor: str, proposal: bytes | None = None, claim=None,
    ) -> dict:
        authority = self.store.authorize(batch_id, actor=actor)
        if authority is None:
            raise ReportDenied("REPORT_SCOPE_DENIED")
        idea, binding = authority["idea"], authority["binding"]
        report_id = content_fingerprint((
            "quant-research-report-v1", authority["approvalId"], batch_id,
        ))
        existing = self.store.get(report_id, actor=actor, version=1)
        if existing is not None:
            return existing
        evidence = self.source.load(batch_id, actor=actor)
        if (evidence.batch_id, evidence.definition_id, evidence.definition_hash,
            evidence.manifest_hash) != (
            batch_id, binding["experimentId"], binding["definitionHash"], authority["manifestHash"],
        ):
            raise ReportDenied("REPORT_RESULT_BINDING_MISMATCH")
        title, question, hypothesis, assumptions = _display_text(
            idea, evidence.definition, self.permits,
        )
        final = next((row for row in evidence.selections if row["scope"] == "FINAL"), None)
        fold_selections = {
            row["fold"]: row for row in evidence.selections if row["scope"] == "FOLD"
        }
        phases, claims, unknowns = {}, [], []
        for native_phase, (field, _) in PHASES.items():
            phase, bound = _phase_report(
                evidence.results, final, fold_selections, evidence.manifest_hash, native_phase,
            )
            phases[field] = phase
            claims.extend(bound)
            if phase["status"] != "AVAILABLE":
                reason = {"NOT_SELECTED": "NOT_SELECTED", "NOT_EVALUABLE": "INSUFFICIENT_DATA",
                          "UNAVAILABLE": "UNAVAILABLE"}[phase["status"]]
                unknowns.append({"field": field, "reason": reason})

        if final and final["status"] == "NO_ELIGIBLE_CANDIDATE":
            unknowns.append({"field": "holdout.candidate", "reason": "NOT_SELECTED"})

        interpretations, rejection = [], None
        if proposal is not None:
            try:
                interpretations = interpret(proposal, tuple(claims))
            except InterpretationDenied as error:
                rejection = str(error)
                unknowns.append({"field": "interpretation", "reason": "INTERPRETATION_REJECTED"})

        native_ref = {"evidenceId": evidence.report_id, "revision": 1, "hash": evidence.report_hash,
                      "sourceType": "QUANT_RESULT"}
        references = [native_ref, *self.source.idea_references(idea)]
        if len(references) - 1 < len(idea["evidence"]):
            unknowns.append({"field": "ideaEvidence", "reason": "UNAVAILABLE"})
        selected = final is not None and final["status"] == "SELECTED"
        if selected:
            statement = "The candidate was selected by the persisted Validation decision."
        elif final and final["status"] == "NO_ELIGIBLE_CANDIDATE":
            statement = "The persisted Validation decision found no eligible final candidate."
        else:
            statement = "A final candidate selection is unavailable."
        data_basis = "The Quant batch and manifest bind the source snapshot and phase windows."
        limitations = ["Validation is used for selection. OOS and Holdout are evaluation phases.",
                       "Historical results do not establish future performance.",
                       "External prior knowledge is unknown."]
        if evidence.definition["researchMode"] == "RETROSPECTIVE_EXPLORATORY":
            limitations.append("Retrospective data may reflect ex-post revisions and survivorship.")
        interpretation_status = "VALIDATED" if interpretations else "FACTS_ONLY"
        document = {
            "summary": {
                "reportId": report_id,
                "reportVersion": 1, "reportHash": "0" * 64, "createdAt": authority["createdAt"],
                "ideaId": idea["id"], "ideaVersion": idea["version"], "batchId": batch_id,
                "title": title, "interpretationStatus": interpretation_status,
            },
            "hypothesisVersion": binding["hypothesisVersion"], "researchQuestion": question,
            "hypothesis": hypothesis, "assumptions": assumptions, "dataBasis": data_basis,
            "evidence": references, "experimentId": evidence.definition_id,
            "definitionVersion": binding["definitionVersion"],
            "definitionHash": evidence.definition_hash,
            "manifest": evidence.manifest, "definition": evidence.definition, "selection": final,
            "reliability": self._reliability(evidence.results, final),
            "researchHistory": evidence.history,
            "phases": phases, "facts": [{"claimId": "selection", "statement": statement,
                                          "evidence": [native_ref]}],
            "interpretations": interpretations, "unknowns": unknowns, "limitations": limitations,
            "conclusion": "The findings apply to the historical data and bound evaluation windows.",
            "furtherResearch": ["Evaluate an independent window after operator approval."],
            "currentRightsWarnings": [],
        }
        try:
            fence = {} if claim is None else {"claim": claim}
            return self.store.publish(document, actor=actor, rejection_code=rejection, **fence)
        except ReportStoreError as error:
            if error.code != "REPORT_VERSION_CONFLICT":
                raise
            winner = self.store.get(report_id, actor=actor, version=1)
            if winner is None:
                raise
            return winner

    @staticmethod
    def _reliability(results, final):
        if final is None or final["status"] != "SELECTED":
            return None
        for row in results:
            if (row["candidateId"] == final["selectedCandidateId"] and row["phase"] == "holdout"
                    and row["evaluationStatus"] == "COMPLETE"):
                return row["reliability"]
        return None
