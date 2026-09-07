"""Validate and export the small JSONL format used by frozen experiments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.orchestration.experiments import (
    DatasetLabelView,
    export_frozen_dataset,
    load_frozen_dataset,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage LeagueNews frozen datasets")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)

    export = subparsers.add_parser("export")
    export.add_argument("--manifest", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--overwrite", action="store_true")

    review = subparsers.add_parser("review")
    review.add_argument("--manifest", type=Path, required=True)
    review.add_argument("--case-id", required=True)
    review.add_argument("--values-json", required=True)
    review.add_argument(
        "--source",
        choices=("human_confirmed", "model_prefill", "synthetic_fixture", "unlabeled"),
        default="human_confirmed",
    )
    review.add_argument("--evidence-basis", action="append", default=[])
    review.add_argument("--ambiguity", action="append", default=[])
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()
    dataset = load_frozen_dataset(args.manifest)
    if args.command == "validate":
        print(
            json.dumps(
                {
                    "status": "valid",
                    "name": dataset.name,
                    "version": dataset.version,
                    "target": dataset.target,
                    "shape": dataset.shape,
                    "cases": len(dataset.cases),
                    "fingerprint": dataset.fingerprint,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "review":
        try:
            values = json.loads(args.values_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--values-json must be valid JSON: {exc}") from exc
        if not isinstance(values, dict):
            raise SystemExit("--values-json must contain an object")
        if args.source == "unlabeled":
            values = {}
        updated = []
        for case in dataset.cases:
            if case.case_id != args.case_id:
                updated.append(case)
                continue
            updated.append(
                case.model_copy(
                    update={
                        "labels": DatasetLabelView(
                            values=values,
                            source=args.source,
                            schema_version=dataset.label_schema_version,
                            evidence_basis=args.evidence_basis,
                            ambiguity=args.ambiguity,
                        ),
                    }
                )
            )
        if len(updated) == len(dataset.cases) and not any(
            case.case_id == args.case_id for case in dataset.cases
        ):
            raise SystemExit(f"unknown case id: {args.case_id}")
        dataset = dataset.model_copy(update={"cases": updated})
        manifest, cases = export_frozen_dataset(dataset, args.output, overwrite=args.overwrite)
        print(json.dumps({"manifest": str(manifest), "cases": str(cases)}, ensure_ascii=False))
        return
    manifest, cases = export_frozen_dataset(dataset, args.output, overwrite=args.overwrite)
    print(json.dumps({"manifest": str(manifest), "cases": str(cases)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
