"""Core logic: input validation, statistics, run rules, report writing."""

import csv
import json
import math
from pathlib import Path

A2 = 0.577
D4 = 2.114
SUBGROUP_SIZE = 5
MIN_BASELINE_BATCHES = 2
RUN_SIDE_LENGTH = 8
RUN_TREND_LENGTH = 6


class InputError(ValueError):
    """Raised when the input document is invalid."""


def load_input(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read input JSON: {exc}") from exc
    return _validate(data)


def _is_finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_batch(raw, seen_ids, context):
    if not isinstance(raw, dict):
        raise InputError(f"{context}: batch must be an object")
    batch_id = raw.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id:
        raise InputError(f"{context}: batch_id must be a non-empty string")
    if batch_id in seen_ids:
        raise InputError(f"{context}: duplicate batch_id {batch_id!r}")
    seen_ids.add(batch_id)
    values = raw.get("values")
    if not isinstance(values, list) or len(values) != SUBGROUP_SIZE:
        raise InputError(f"{context} batch {batch_id!r}: values must have exactly {SUBGROUP_SIZE} entries")
    if not all(_is_finite_number(v) for v in values):
        raise InputError(f"{context} batch {batch_id!r}: values must be finite numbers")
    return {"batch_id": batch_id, "values": [float(v) for v in values]}


def _validate(data):
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        raise InputError("top level must be an object with a 'lines' array")
    lines = []
    seen_lines = set()
    for index, raw_line in enumerate(data["lines"]):
        context = f"line {index}"
        if not isinstance(raw_line, dict):
            raise InputError(f"{context}: line must be an object")
        line_id = raw_line.get("line_id")
        if not isinstance(line_id, str) or not line_id:
            raise InputError(f"{context}: line_id must be a non-empty string")
        if line_id in seen_lines:
            raise InputError(f"{context}: duplicate line_id {line_id!r}")
        seen_lines.add(line_id)
        baseline_raw = raw_line.get("baseline")
        monitoring_raw = raw_line.get("monitoring", [])
        if not isinstance(baseline_raw, list) or len(baseline_raw) < MIN_BASELINE_BATCHES:
            raise InputError(f"line {line_id!r}: baseline needs at least {MIN_BASELINE_BATCHES} batches")
        if not isinstance(monitoring_raw, list):
            raise InputError(f"line {line_id!r}: monitoring must be an array")
        seen_ids = set()
        baseline = [_validate_batch(b, seen_ids, f"line {line_id!r} baseline") for b in baseline_raw]
        monitoring = [_validate_batch(b, seen_ids, f"line {line_id!r} monitoring") for b in monitoring_raw]
        lines.append({"line_id": line_id, "baseline": baseline, "monitoring": monitoring})
    return lines


def _batch_stats(batch):
    values = batch["values"]
    mean = sum(values) / len(values)
    spread = max(values) - min(values)
    return mean, spread


def _control_limits(baseline):
    means = []
    spreads = []
    for batch in baseline:
        mean, spread = _batch_stats(batch)
        means.append(mean)
        spreads.append(spread)
    center = sum(means) / len(means)
    r_bar = sum(spreads) / len(spreads)
    return {
        "mean_center": center,
        "mean_ucl": center + A2 * r_bar,
        "mean_lcl": center - A2 * r_bar,
        "range_center": r_bar,
        "range_ucl": D4 * r_bar,
        "range_lcl": 0.0,
    }


def _run_rule_events(monitoring, means, center):
    events = []
    n = len(monitoring)
    for end in range(n):
        if end + 1 >= RUN_SIDE_LENGTH:
            window = means[end + 1 - RUN_SIDE_LENGTH:end + 1]
            if all(m > center for m in window):
                side = "above"
            elif all(m < center for m in window):
                side = "below"
            else:
                side = None
            if side:
                events.append({
                    "rule": "eight_same_side",
                    "side": side,
                    "start_batch_id": monitoring[end + 1 - RUN_SIDE_LENGTH]["batch_id"],
                    "end_batch_id": monitoring[end]["batch_id"],
                })
        if end + 1 >= RUN_TREND_LENGTH:
            window = means[end + 1 - RUN_TREND_LENGTH:end + 1]
            if all(window[i] < window[i + 1] for i in range(len(window) - 1)):
                direction = "increasing"
            elif all(window[i] > window[i + 1] for i in range(len(window) - 1)):
                direction = "decreasing"
            else:
                direction = None
            if direction:
                events.append({
                    "rule": "six_trend",
                    "direction": direction,
                    "start_batch_id": monitoring[end + 1 - RUN_TREND_LENGTH]["batch_id"],
                    "end_batch_id": monitoring[end]["batch_id"],
                })
    return events


def analyze(lines):
    """Analyze validated lines, returning per-line results."""
    results = []
    for line in lines:
        limits = _control_limits(line["baseline"])
        center = limits["mean_center"]
        baseline_rows = []
        for batch in line["baseline"]:
            mean, spread = _batch_stats(batch)
            baseline_rows.append({
                "batch_id": batch["batch_id"],
                "phase": "baseline",
                "mean": mean,
                "range": spread,
                "mean_out_of_control": False,
                "range_out_of_control": False,
                "run_rules": [],
            })
        monitoring_rows = []
        means = []
        for batch in line["monitoring"]:
            mean, spread = _batch_stats(batch)
            means.append(mean)
            monitoring_rows.append({
                "batch_id": batch["batch_id"],
                "phase": "monitoring",
                "mean": mean,
                "range": spread,
                "mean_out_of_control": mean > limits["mean_ucl"] or mean < limits["mean_lcl"],
                "range_out_of_control": spread > limits["range_ucl"] or spread < limits["range_lcl"],
                "run_rules": [],
            })
        events = _run_rule_events(line["monitoring"], means, center)
        for event in events:
            for row in monitoring_rows:
                if row["batch_id"] == event["end_batch_id"]:
                    row["run_rules"].append(event)
                    break
        rule_counts = {"mean_out_of_control": 0, "range_out_of_control": 0,
                       "eight_same_side": 0, "six_trend": 0}
        flagged = 0
        for row in monitoring_rows:
            hit = False
            if row["mean_out_of_control"]:
                rule_counts["mean_out_of_control"] += 1
                hit = True
            if row["range_out_of_control"]:
                rule_counts["range_out_of_control"] += 1
                hit = True
            for event in row["run_rules"]:
                rule_counts[event["rule"]] += 1
                hit = True
            if hit:
                flagged += 1
        results.append({
            "line_id": line["line_id"],
            "limits": limits,
            "baseline": baseline_rows,
            "monitoring": monitoring_rows,
            "summary": {
                "baseline_batches": len(baseline_rows),
                "monitoring_batches": len(monitoring_rows),
                "rule_counts": rule_counts,
                "flagged_monitoring_batches": flagged,
            },
        })
    return results


def _format_number(value):
    return repr(float(value))


def write_reports(results, output_dir):
    """Write one CSV per line plus a summary JSON into output_dir."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary_lines = []
    for result in results:
        csv_path = out / f"{result['line_id']}.csv"
        with open(csv_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["batch_id", "phase", "mean", "range",
                             "mean_out_of_control", "range_out_of_control", "run_rules"])
            for row in result["baseline"] + result["monitoring"]:
                rules = ";".join(
                    f"{e['rule']}:{e['start_batch_id']}->{e['end_batch_id']}"
                    for e in row["run_rules"]
                )
                writer.writerow([
                    row["batch_id"], row["phase"],
                    _format_number(row["mean"]), _format_number(row["range"]),
                    str(row["mean_out_of_control"]).lower(),
                    str(row["range_out_of_control"]).lower(),
                    rules,
                ])
        summary_lines.append({
            "line_id": result["line_id"],
            "csv_file": csv_path.name,
            "limits": result["limits"],
            "summary": result["summary"],
            "run_rule_events": [
                event for row in result["monitoring"] for event in row["run_rules"]
            ],
        })
    summary_path = out / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump({"lines": summary_lines}, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return summary_path
