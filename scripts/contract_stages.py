"""Ausdrücklich freigegebene Vertragsvorläufe; keine Runtime-Freigabe.

Nur RDR-01 erhält die am 26.10.2026 autorisierte zweistufige Abnahme.
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


def pending_contract_operations(tasks_text: str | None = None) -> set[str]:
    text = TASKS.read_text("utf-8") if tasks_text is None else tasks_text
    parsed = re.findall(r"^- \[([ x])\] \d+\.\d+ (RDR-\d{2}) ", text, re.M)
    pending = set()
    for operation, task in STAGED_OPERATIONS.items():
        matching = [status for status, task_id in parsed if task_id == task]
        if len(matching) != 1:
            raise AssertionError(f"Eindeutige Abnahmestufe fehlt: {task}")
        if matching[0] == " ":
            pending.add(operation)
    return pending
