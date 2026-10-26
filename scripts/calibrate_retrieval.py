#!/usr/bin/env python3
"""Development-only abstention curves; ranking scores are never probabilities."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins"))


def calibrate(run, *, max_fpr=.05, minimum_coverage=.9):
    from kg.research.runner import validate_run
    for name, value in (("max_fpr", max_fpr), ("minimum_coverage", minimum_coverage)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not 0 <= value <= 1 or not math.isfinite(value)):
            raise ValueError(f"{name} must be a finite number in [0, 1]")
    try:
        validate_run(run)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid research run structure") from exc
    config = run["manifest"].get("config")
    if not isinstance(config, dict) or config.get("split") != "dev":
        raise ValueError("Thresholds must be calibrated on development data, never blind results")
    rows = [row for row in run["rows"] if row["track"] == "facts"
            and row.get("task_type") not in {"aggregate", "multihop", "path", "count"}]
    positives = [row for row in rows if row["metrics"].get("answerable_coverage") is not None]
    negatives = [row for row in rows if row["metrics"].get("noanswer_false_positive") is not None]
    if not positives or not negatives:
        raise ValueError("Calibration requires labeled answerable and genuine no-answer questions")
    for row in rows:
        scores = row.get("scores")
        if (not isinstance(scores, list) or len(scores) != len(row.get("ranked", []))
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or abs(value) > sys.float_info.max or not math.isfinite(value) for value in scores)
                or any(a < b for a, b in zip(scores, scores[1:]))):
            raise ValueError("Calibration requires finite, descending scores aligned with ranked results")
    scores = [float(row["scores"][0]) for row in rows if row["scores"]]
    if not scores:
        raise ValueError("Calibration requires finite retrieval scores")
    reject_all = math.nextafter(max(scores), math.inf)
    if not math.isfinite(reject_all):
        raise ValueError("Retrieval scores leave no finite reject-all threshold")
    # min(scores) already accepts every nonempty result. Avoid a redundant
    # -infinity endpoint when the minimum is the most negative finite float.
    thresholds = sorted({*scores, reject_all})
    curve = []
    for threshold in thresholds:
        def accepted(row):
            return bool(row["scores"] and row["scores"][0] >= threshold)
        fpr = sum(accepted(row) for row in negatives) / len(negatives)
        coverage = sum(accepted(row) and row["metrics"].get("sufficiency@5") == 1
                       for row in positives) / len(positives)
        curve.append({"threshold": threshold, "noanswer_fpr": fpr,
                      "sufficient_coverage": coverage,
                      "acceptance_rate": sum(accepted(row) for row in rows) / len(rows)})
    feasible = [point for point in curve if point["noanswer_fpr"] <= max_fpr]
    selected = max(feasible, key=lambda point: (point["sufficient_coverage"], -point["noanswer_fpr"], point["threshold"]))
    return {"format": "kg-retrieval-calibration-v1", "run_id": run["manifest"]["run_id"],
            "model_fingerprint": run["manifest"]["config"]["model_fingerprint"],
            "tool_code_sha256": run["manifest"]["tool_code_sha256"],
            "dataset_sha256": run["manifest"]["dataset_sha256"],
            "mode": run["manifest"]["config"]["mode"],
            "score_kind": "cosine" if run["manifest"]["config"]["mode"] == "vector" else "retrieval_ranking_score",
            "split": "dev", "positive_questions": len(positives), "noanswer_questions": len(negatives),
            "max_fpr": max_fpr, "minimum_coverage": minimum_coverage,
            "selected": selected, "curve": curve,
            "feasible": selected["sufficient_coverage"] >= minimum_coverage,
            "promotion": "blocked",
            "interpretation": "Exploratory calibration on labeled dev only, with no held-out guarantee. Scores are not probabilities. An infeasible curve means a single score threshold cannot meet both gates; do not activate it as a production fix."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    from kg.research.common import read_json, write_json
    try:
        report = calibrate(read_json(args.run))
        write_json(args.output, report, replace=False)
        print(json.dumps({key: report[key] for key in ("feasible", "positive_questions", "noanswer_questions", "selected", "promotion")}))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
