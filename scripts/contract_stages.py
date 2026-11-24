"""Ausdrücklich freigegebene Vertragsvorläufe; keine Runtime-Freigabe.

Der Abschluss des jeweiligen Runtimepakets erzwingt dessen exakte Routen.
"""

import json
import re
from pathlib import Path

STAGED_OPERATIONS = {
    "createReadinessAssessment": "RDR-05",
    "getReadinessAssessment": "RDR-05",
    "listReadinessFindings": "RDR-05",
    "listCollectionPlans": "RDR-06",
    "updateCollectionPlan": "RDR-06",
}
TASKS = (
    Path(__file__).resolve().parents[1]
    / "openspec/changes/research-data-readiness/tasks.md"
)
RETROSPECTIVE_TASKS = (
    Path(__file__).resolve().parents[1]
    / "openspec/changes/retrospective-research-backtesting/tasks.md"
)
RETROSPECTIVE_OPERATIONS = {
    "createBacktestAssessment": "RRB-06",
    "getBacktestAssessment": "RRB-06",
    "getBacktestReliabilityReport": "RRB-06",
}
QUANT_LAB_TASKS = (
    Path(__file__).resolve().parents[1]
    / "openspec/changes/quant-strategy-lab-v1/tasks.md"
)
QUANT_LAB_OPERATIONS = dict.fromkeys((
    "getQuantLabStrategies", "createQuantLabExperiment", "listQuantLabExperiments",
    "getQuantLabExperiment", "validateQuantLabExperiment", "startQuantLabExperimentBatch",
    "listQuantLabExperimentBatches", "getQuantLabExperimentBatch", "listQuantLabCandidates",
    "listQuantLabResults", "getQuantLabComparison", "getQuantLabEquity",
    "getQuantLabDrawdown", "compareQuantLabExperiments", "getQuantLabReliabilityReport",
), "QSL-07")

AGENT_RESEARCH_TASKS = (
    Path(__file__).resolve().parents[1]
    / "openspec/changes/agent-platform-foundation/tasks.md"
)
AGENT_RESEARCH_OPERATIONS = dict.fromkeys((
    "createAgentProcessingPreview", "getAgentProcessingStatus", "decideAgentProcessing",
    "listAgentIdeas", "createAgentIdea", "getAgentIdea", "reviseAgentIdea",
    "createAgentDialogue", "getAgentDialogue", "createAgentDialogueTurn", "decideAgentHypothesis",
    "getAgentRegistry", "listResearchAgentRuns", "getAgentRun", "listAgentRunEvents",
    "cancelAgentRun",
    "getAgentSchedule", "updateAgentSchedule", "commandAgentSchedule", "listAgentProviders",
    "prepareAgentProviderSmoke", "probeAgentProvider", "saveAgentProviderCredential",
    "rotateAgentProviderCredential",
    "activateAgentProviderCredential", "revokeAgentProviderCredential", "listAgentQuantApprovals",
    "getAgentQuantApproval", "decideAgentQuantApproval", "listAgentResearchReports",
    "getAgentResearchReport",
), "APF-08")


# IEF-01 deliberately stages additive contracts; IEF-09 must prove full runtime parity.
# This explicit bounded operation matrix must never exempt unrelated operations.
IEF_TASKS = (
    Path(__file__).resolve().parents[1]
    / "openspec/changes/investment-experience-foundation/tasks.md"
)
IEF_OPERATION_MATRIX = json.loads(
    (Path(__file__).resolve().parents[1] / "contracts/ief-operations.json").read_text("utf-8")
)
IEF_IMPLEMENTED = frozenset({
    "getInvestmentPreferences", "updateInvestmentPreferences", "getInvestmentPortfolio",
})
IEF_OPERATIONS = dict.fromkeys(set(IEF_OPERATION_MATRIX) - IEF_IMPLEMENTED, "IEF-09")
IEF_PAPER_SCHEMAS = frozenset({
    "PaperPreviewProvenanceV1", "PaperLimitPreviewRequestV1", "PaperRiskPrecheckV1",
    "PaperLimitPreviewV1", "PaperGuidedLimitOrderRequestV1", "PaperLimitPreviewTermsV1",
})


def _pending(text: str, operations: dict[str, str]) -> set[str]:
    parsed = re.findall(r"^- \[([ x])\] \d+\.\d+ \[?([A-Z]+-\d{2})(?:\]\([^)]+\)|\])? ", text, re.M)
    pending = set()
    for operation, task in operations.items():
        matching = [status for status, task_id in parsed if task_id == task]
        if len(matching) != 1:
            raise AssertionError(f"Eindeutige Abnahmestufe fehlt: {task}")
        if matching[0] == " ":
            pending.add(operation)
    return pending


def pending_contract_operations(
    tasks_text: str | None = None, retrospective_tasks_text: str | None = None,
    quant_lab_tasks_text: str | None = None, agent_research_tasks_text: str | None = None,
    investment_experience_tasks_text: str | None = None,
) -> set[str]:
    text = TASKS.read_text("utf-8") if tasks_text is None else tasks_text
    retrospective = (
        RETROSPECTIVE_TASKS.read_text("utf-8")
        if retrospective_tasks_text is None else retrospective_tasks_text
    )
    quant_lab = (
        QUANT_LAB_TASKS.read_text("utf-8")
        if quant_lab_tasks_text is None else quant_lab_tasks_text
    )
    agent_research = (
        AGENT_RESEARCH_TASKS.read_text("utf-8")
        if agent_research_tasks_text is None else agent_research_tasks_text
    )
    return (
        _pending(text, STAGED_OPERATIONS)
        | _pending(retrospective, RETROSPECTIVE_OPERATIONS)
        | _pending(quant_lab, QUANT_LAB_OPERATIONS)
        | _pending(agent_research, AGENT_RESEARCH_OPERATIONS)
        | _pending(
            IEF_TASKS.read_text("utf-8")
            if investment_experience_tasks_text is None
            else investment_experience_tasks_text,
            IEF_OPERATIONS,
        )
    )
