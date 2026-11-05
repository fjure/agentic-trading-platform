"""Validate OpenSpec prerequisites independently of the order of task headings."""

from collections.abc import Mapping, Sequence
from graphlib import CycleError, TopologicalSorter


def validated_task_order(dependencies: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    nodes = set(dependencies)
    if any(type(node) is not str for node in nodes):
        raise ValueError("Task IDs must be strings")
    graph = {}
    for node in sorted(nodes):
        refs = dependencies[node]
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)) or any(
            type(ref) is not str for ref in refs
        ):
            raise ValueError("Prerequisites must be explicit task-ID sequences")
        if len(set(refs)) != len(refs):
            raise ValueError("Duplicate prerequisite")
        if set(refs) - nodes:
            raise ValueError("Unknown prerequisite")
        graph[node] = tuple(sorted(refs))
    try:
        return tuple(TopologicalSorter(graph).static_order())
    except CycleError:
        raise ValueError("OpenSpec task dependencies contain a cycle") from None
