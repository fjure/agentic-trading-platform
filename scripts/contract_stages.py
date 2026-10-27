"""Ausdrücklich freigegebene Vertragsvorläufe; keine Runtime-Freigabe.

RDR-01 und RRB-01 erhalten die ausdrücklich autorisierte zweistufige Abnahme.
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


def _pending(text: str, operations: dict[str, str]) -> set[str]:
    parsed = re.findall(r"^- \[([ x])\] \d+\.\d+ ([A-Z]+-\d{2}) ", text, re.M)
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
) -> set[str]:
    text = TASKS.read_text("utf-8") if tasks_text is None else tasks_text
    retrospective = (
        RETROSPECTIVE_TASKS.read_text("utf-8")
        if retrospective_tasks_text is None else retrospective_tasks_text
    )
    return _pending(text, STAGED_OPERATIONS) | _pending(retrospective, RETROSPECTIVE_OPERATIONS)
