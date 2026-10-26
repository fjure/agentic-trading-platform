"""Die autorisierte Stufung darf vollständige Runtimeabnahme nicht umgehen."""

import re

import pytest

from scripts.contract_stages import STAGED_OPERATIONS, TASKS, pending_contract_operations


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
