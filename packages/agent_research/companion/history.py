"""Combine actor-scoped idea repetition with the existing Quant exposure history."""

from dataclasses import dataclass

from sqlalchemy.engine import Connection

from packages.backtesting.quant_lab_history import project_history

from .store import CompanionStore


@dataclass(frozen=True, slots=True)
class CompanionHistory:
    exact_repetitions: tuple[tuple[str, int], ...]
    related_idea_ids: tuple[str, ...]
    exposure: dict | None


def companion_history(
    store: CompanionStore, actor: str, idea_id: str, version: int, *,
    quant_connection: Connection | None = None,
    quant_definition: dict | None = None,
    admission_receipt: dict | None = None,
) -> CompanionHistory:
    idea = store.read_idea(actor, idea_id, version)
    exact = store.exact_repetitions(actor, idea_id, version)
    exposure = None
    if quant_connection is not None and quant_definition is not None:
        binding = {} if admission_receipt is None else {
            name: admission_receipt[name] for name in ("mark", "registry_hash", "bound_at")
        }
        exposure = project_history(quant_connection, quant_definition, **binding)
    return CompanionHistory(exact, tuple(idea["relatedIdeaIds"]), exposure)
