"""Die autorisierte Stufung darf vollständige Runtimeabnahme nicht umgehen."""

import re

import pytest

from scripts.contract_stages import (
    RETROSPECTIVE_OPERATIONS,
    RETROSPECTIVE_TASKS,
    STAGED_OPERATIONS,
    TASKS,
    pending_contract_operations,
)


def pending_fixture():
    return re.sub(
        r"^- \[[ x]\]( \d+\.\d+ RDR-0[56] )", r"- [ ]\1",
        TASKS.read_text("utf-8"), flags=re.M,
    )


@pytest.mark.parametrize("task", ["RDR-05", "RDR-06"])
def test_completing_runtime_package_requires_every_one_of_its_routes(task):
    text = pending_fixture()
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("- [ ]")
                 and f" {task} " in line)
    lines[index] = lines[index].replace("- [ ]", "- [x]", 1)
    pending = pending_contract_operations("\n".join(lines))
    for operation, owner in STAGED_OPERATIONS.items():
        assert (operation in pending) == (owner != task)


@pytest.mark.parametrize("task", ["RDR-05", "RDR-06"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_missing_or_ambiguous_stage_blocks_contract_acceptance(task, mutation):
    text = pending_fixture()
    lines = text.splitlines()
    line = next(line for line in lines if line.startswith("- [ ]") and f" {task} " in line)
    changed = text.replace(line, "") if mutation == "missing" else text + "\n" + line
    with pytest.raises(AssertionError, match="Eindeutige Abnahmestufe fehlt"):
        pending_contract_operations(changed)


def test_retrospective_runtime_completion_enforces_all_three_real_routes():
    text = RETROSPECTIVE_TASKS.read_text("utf-8")
    pending = re.sub(r"^- \[[ x]\]( \d+\.\d+ RRB-06 )", r"- [ ]\1", text, flags=re.M)
    assert set(RETROSPECTIVE_OPERATIONS) <= pending_contract_operations(
        retrospective_tasks_text=pending,
    )
    complete = re.sub(r"^- \[ \]( \d+\.\d+ RRB-06 )", r"- [x]\1", pending, flags=re.M)
    assert not set(RETROSPECTIVE_OPERATIONS) & pending_contract_operations(
        retrospective_tasks_text=complete,
    )


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_ambiguous_retrospective_stage_cannot_bypass_route_checks(mutation):
    text = RETROSPECTIVE_TASKS.read_text("utf-8")
    line = next(x for x in text.splitlines() if re.match(r"^- \[[ x]\] \d+\.\d+ RRB-06 ", x))
    changed = text.replace(line, "") if mutation == "missing" else text + "\n" + line
    with pytest.raises(AssertionError, match="Eindeutige Abnahmestufe fehlt: RRB-06"):
        pending_contract_operations(retrospective_tasks_text=changed)
