"""Forward references are valid; missing prerequisites and cycles never sync."""

import re
from pathlib import Path

import pytest

from scripts.issue_graph import validated_task_order


def test_forward_prerequisite_is_checked_topologically_not_by_heading_order():
    graph = {"QSL-01": [], "QSL-02": ["QSL-01"], "QSL-03": ["QSL-04"],
             "QSL-04": ["QSL-02"]}
    assert validated_task_order(graph) == ("QSL-01", "QSL-02", "QSL-04", "QSL-03")


@pytest.mark.parametrize("graph,reason", [
    ({"A": ["B"], "B": ["A"]}, "cycle"),
    ({"A": ["A"]}, "cycle"),
    ({"A": ["missing"]}, "Unknown"),
    ({"A": ["B", "B"], "B": []}, "Duplicate"),
    ({"A": "B", "B": []}, "sequences"),
    ({"A": [None]}, "sequences"),
])
def test_invalid_graph_blocks_before_issue_mutations(graph, reason):
    with pytest.raises(ValueError, match=reason):
        validated_task_order(graph)


def test_current_quant_graph_has_nine_tasks_and_pins_execution_before_sql_registry():
    root = Path(__file__).resolve().parents[2]
    text = (root / "openspec/changes/quant-strategy-lab-v1/tasks.md").read_text("utf-8")
    graph = {
        task: [] if deps == "none" else deps.split(", ")
        for task, deps in re.findall(
            r"^- \[[ x]\] \d+\.\d+ (QSL-\d{2}) .*?Depends on: (.*?)\. Paths:", text, re.M,
        )
    }
    assert len(graph) == 9
    assert sum(map(len, graph.values())) == 19
    assert graph["QSL-03"] == ["QSL-01", "QSL-04"]
    order = validated_task_order(graph)
    for task, deps in graph.items():
        assert all(order.index(dep) < order.index(task) for dep in deps)
