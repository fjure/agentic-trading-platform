"""Ausdrücklich freigegebene Vertragsvorläufe; keine Runtime-Freigabe.

Der Abschluss des jeweiligen Runtimepakets erzwingt dessen exakte Routen.
"""

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
    "probeAgentProvider", "saveAgentProviderCredential", "rotateAgentProviderCredential",
    "activateAgentProviderCredential", "revokeAgentProviderCredential", "listAgentQuantApprovals",
    "getAgentQuantApproval", "decideAgentQuantApproval", "listAgentResearchReports",
    "getAgentResearchReport",
), "APF-08")


def _pending(text: str, operations: dict[str, str]) -> set[str]:
    parsed = re.findall(r"^- \[([ x])\] \d+\.\d+ \[?([A-Z]+-\d{2}) ", text, re.M)
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
    )
