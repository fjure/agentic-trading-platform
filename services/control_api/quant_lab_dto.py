"""Executable wire schemas and operation registry for the Quant Lab transport."""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from services.control_api.retrospective_dto import FORMATS

BASE = "/api/v1/research/"
SCOPE_HEADER = "x-atl-quant-session-scope"
HEX = re.compile(r"[0-9a-f]{64}\Z")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
TRACE = re.compile(r"[0-9a-f]{32}\Z")
PHASES = {"training", "validation", "test", "holdout"}
ROLES = {"SEARCH_CANDIDATE", "REFERENCE_FLAT", "REFERENCE_BUY_HOLD"}
PAGED = ("cursor", "limit")
PHASE_FILTER = (*PAGED, "phase", "fold")
CURVE = (*PHASE_FILTER, "role")


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    service: str
    response: str
    query: tuple[str, ...] = ()
    request: str | None = None
    status: int = 200


OPERATIONS = {
    "getQuantLabStrategies": Operation("GET", "strategies", "get_strategies", "StrategyCatalog"),
    "createQuantLabExperiment": Operation(
        "POST",
        "experiments",
        "create_experiment",
        "ExperimentDefinition",
        request="ExperimentCreateRequest",
        status=201,
    ),
    "listQuantLabExperiments": Operation(
        "GET",
        "experiments",
        "list_experiments",
        "ExperimentPage",
        PAGED,
    ),
    "getQuantLabExperiment": Operation(
        "GET",
        "experiments/{id}",
        "get_experiment",
        "ExperimentDefinition",
        (*PAGED, "validationId"),
    ),
    "validateQuantLabExperiment": Operation(
        "POST",
        "experiments/{id}/validations",
        "validate_experiment",
        "Validation",
        request="ValidationRequest",
        status=201,
    ),
    "startQuantLabExperimentBatch": Operation(
        "POST",
        "experiments/{id}/batches",
        "start_experiment_batch",
        "Batch",
        request="BatchStartRequest",
        status=202,
    ),
    "listQuantLabExperimentBatches": Operation(
        "GET",
        "experiments/{id}/batches",
        "list_experiment_batches",
        "BatchPage",
        PAGED,
    ),
    "getQuantLabExperimentBatch": Operation(
        "GET",
        "experiment-batches/{id}",
        "get_experiment_batch",
        "Batch",
        PAGED,
    ),
    "listQuantLabCandidates": Operation(
        "GET",
        "experiment-batches/{id}/candidates",
        "list_candidates",
        "CandidatePage",
        PAGED,
    ),
    "listQuantLabResults": Operation(
        "GET",
        "experiment-batches/{id}/results",
        "list_results",
        "ResultPage",
        (*PHASE_FILTER, "candidateId"),
    ),
    "getQuantLabComparison": Operation(
        "GET",
        "experiment-batches/{id}/comparison",
        "get_comparison",
        "ComparisonReport",
        PAGED,
    ),
    "getQuantLabEquity": Operation(
        "GET",
        "experiment-batches/{id}/results/{candidateId}/equity",
        "get_equity",
        "EquityPage",
        CURVE,
    ),
    "getQuantLabDrawdown": Operation(
        "GET",
        "experiment-batches/{id}/results/{candidateId}/drawdown",
        "get_drawdown",
        "DrawdownPage",
        CURVE,
    ),
    "compareQuantLabExperiments": Operation(
        "GET",
        "experiment-comparisons",
        "compare_experiments",
        "ExperimentComparison",
        ("batchId",),
    ),
    "getQuantLabReliabilityReport": Operation(
        "GET",
        "experiment-batches/{id}/reliability-reports/{reportId}",
        "get_reliability_report",
        "ReliabilityReportPage",
        (*PAGED, "section"),
    ),
}


@lru_cache(maxsize=1)
def _schema():
    path = Path(__file__).resolve().parents[2] / "contracts/schemas/v1/quant-lab.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache
def validator(component):
    return Draft202012Validator(
        {**_schema(), "$ref": "#/$defs/QuantLab" + component}, format_checker=FORMATS
    )
