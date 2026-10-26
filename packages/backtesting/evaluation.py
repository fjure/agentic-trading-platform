"""Independent chronological folds and identical-cost benchmark references."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .metrics import FoldMetrics, calculate_metrics
from .models import BacktestBlocked, ExperimentConfig, fingerprint
from .simulator import SimulationBlocked, SimulationResult, simulate


@dataclass(frozen=True)
class EvaluatedStrategy:
    simulation: SimulationResult
    metrics: FoldMetrics


@dataclass(frozen=True)
class FoldEvaluation:
    fold_index: int
    strategy: EvaluatedStrategy
    flat: EvaluatedStrategy
    buy_hold: EvaluatedStrategy


@dataclass(frozen=True)
class EvaluationResult:
    status: str
    folds: tuple[FoldEvaluation, ...]
    blocked_code: str | None = None
    blocked_reference: str | None = None

    @property
    def result_hash(self) -> str:
        return fingerprint(self)


def evaluate(
    config: ExperimentConfig,
    datasets: Iterable,
    *,
    checkpoint: Callable[[], None] = lambda: None,
    on_fold: Callable[[FoldEvaluation], None] = lambda fold: None,
) -> EvaluationResult:
    """Consume datasets lazily so later blocking preserves completed fold references."""
    complete = []
    iterator = iter(datasets)
    try:
        for index, fold in enumerate(config.folds):
            checkpoint()
            try:
                dataset = next(iterator)
            except StopIteration as exc:
                raise SimulationBlocked("BLOCKED_FOLD_DATA_MISSING", str(index)) from exc
            if dataset.fold != fold:
                raise ValueError("dataset does not match bound fold")
            values = []
            for strategy in (config.strategy_id, "flat-v1", "buy-hold-v1"):
                result = simulate(config, dataset, strategy_id=strategy, checkpoint=checkpoint)
                values.append(EvaluatedStrategy(result, calculate_metrics(result)))
            complete.append(FoldEvaluation(index, *values))
            on_fold(complete[-1])
        try:
            next(iterator)
        except StopIteration:
            pass
        else:
            raise ValueError("unexpected extra fold")
    except BacktestBlocked as exc:
        return EvaluationResult("BLOCKED", tuple(complete), exc.code, exc.reference)
    return EvaluationResult("SUCCEEDED", tuple(complete))
