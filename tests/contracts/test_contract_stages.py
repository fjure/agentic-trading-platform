"""Die autorisierte Stufung darf vollständige Runtimeabnahme nicht umgehen."""

import re

import pytest

from scripts.contract_stages import (
    QUANT_LAB_OPERATIONS,
    QUANT_LAB_TASKS,
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


def test_quant_runtime_completion_enforces_all_additive_routes():
    text = QUANT_LAB_TASKS.read_text("utf-8")
    pending = re.sub(r"^- \[[ x]\]( \d+\.\d+ QSL-07 )", r"- [ ]\1", text, flags=re.M)
    assert set(QUANT_LAB_OPERATIONS) <= pending_contract_operations(
        quant_lab_tasks_text=pending,
    )
    complete = re.sub(r"^- \[ \]( \d+\.\d+ QSL-07 )", r"- [x]\1", pending, flags=re.M)
    assert not set(QUANT_LAB_OPERATIONS) & pending_contract_operations(
        quant_lab_tasks_text=complete,
    )


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_ambiguous_quant_stage_cannot_bypass_route_checks(mutation):
    text = QUANT_LAB_TASKS.read_text("utf-8")
    line = next(x for x in text.splitlines() if re.match(r"^- \[[ x]\] \d+\.\d+ QSL-07 ", x))
    changed = text.replace(line, "") if mutation == "missing" else text + "\n" + line
    with pytest.raises(AssertionError, match="Eindeutige Abnahmestufe fehlt: QSL-07"):
        pending_contract_operations(quant_lab_tasks_text=changed)


def test_ief_linked_stage_lifecycle_is_bounded_and_fail_closed():
    from scripts.contract_stages import IEF_IMPLEMENTED, IEF_OPERATIONS, IEF_TASKS

    text = IEF_TASKS.read_text('utf-8')
    line = next(
        line for line in text.splitlines()
        if re.match(r'^- \[[ x]\] 9\.1 \[IEF-09\]\(', line)
    )
    pending = pending_contract_operations(investment_experience_tasks_text=text)
    assert len(IEF_OPERATIONS) == 13
    assert not IEF_IMPLEMENTED & pending
    assert set(IEF_OPERATIONS) <= pending
    assert 'unknownInvestmentExecute' not in pending
    complete = text.replace(line, line.replace('- [ ]', '- [x]', 1))
    assert not set(IEF_OPERATIONS) & pending_contract_operations(
        investment_experience_tasks_text=complete
    )
    for changed in (text.replace(line, ''), text + '\n' + line):
        with pytest.raises(AssertionError, match='Eindeutige Abnahmestufe fehlt: IEF-09'):
            pending_contract_operations(investment_experience_tasks_text=changed)
