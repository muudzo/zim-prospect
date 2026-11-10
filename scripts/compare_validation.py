"""Compare two validation.json files: `python scripts/compare_validation.py BASELINE NEW [--tol 0.02]`.

Prints the AUC change per commodity and exits 1 if any commodity drops by more than the tolerance.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_TOLERANCE = 0.02


def best_auc(entry: dict) -> float | None:
    """The score the README reports: spatial-CV ensemble if present, else fuzzy."""
    best = entry.get("ensemble_cv") or entry.get("fuzzy")
    return None if not best else float(best["auc"])


def compare(baseline: dict, new: dict, tolerance: float) -> tuple[list[str], bool]:
    lines, ok = [f"{'commodity':<14}{'baseline':>9}{'new':>8}{'change':>9}"], True
    for key in sorted(set(baseline) | set(new)):
        old_auc = best_auc(baseline.get(key, {}))
        new_auc = best_auc(new.get(key, {}))
        if old_auc is None or new_auc is None:
            lines.append(f"{key:<14}{'-' if old_auc is None else old_auc:>9}{'-' if new_auc is None else new_auc:>8}")
            continue
        delta = new_auc - old_auc
        flag = "  <-- worse than tolerance" if delta < -tolerance else ""
        ok = ok and delta >= -tolerance
        lines.append(f"{key:<14}{old_auc:>9.3f}{new_auc:>8.3f}{delta:>+9.3f}{flag}")
    return lines, ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--tol", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args(argv)
    lines, ok = compare(json.loads(args.baseline.read_text()), json.loads(args.new.read_text()), args.tol)
    sys.stdout.write("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
