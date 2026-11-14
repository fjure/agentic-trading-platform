"""Score versioned synthetic evaluation evidence without contacting a provider."""

import argparse
import json
from pathlib import Path

from . import scorer
from .catalog import FIXTURES, _read_json, load_catalog
from .harness import file_sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "holdout"), default="development")
    parser.add_argument("--holdout", type=Path)
    parser.add_argument("--budget-aborted", action="store_true")
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()
    if (args.split == "holdout") != (args.holdout is not None):
        parser.error("select holdout and supply its private path together")
    manifest = FIXTURES / "split-manifest.v1.json"
    development, holdout = load_catalog(manifest, FIXTURES / "development.v1.json", args.holdout)
    cases = development if args.split == "development" else holdout
    response = _read_json(args.responses)
    if type(response) is not dict or not response.keys() <= {case["caseId"] for case in cases}:
        parser.error("responses must be keyed by the selected case IDs")
    score = scorer.evaluate(cases, response, mode="offline", budget_aborted=args.budget_aborted)
    document = {
        "schemaVersion": 1,
        "split": args.split,
        "manifestSha256": file_sha(manifest),
        "scorerSha256": file_sha(Path(scorer.__file__)),
        "responsesSha256": file_sha(args.responses),
        "score": score,
    }
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(document, destination, ensure_ascii=False, allow_nan=False, indent=2)
        destination.write("\n")
    return int(args.require_pass and not score["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
