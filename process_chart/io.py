"""CLI entry point and report rendering (CSV + summary JSON)."""

from __future__ import annotations

import csv
import json
import os
import sys

from .analysis import LineReport, analyze_line
from .validation import ValidationError, load_input

RULE_ORDER = (
    "mean_beyond_limit",
    "range_beyond_limit",
    "same_side_8",
    "trend_up_6",
    "trend_down_6",
)


def _safe_filename(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)


def _format_bool(value: bool) -> str:
    return "true" if value else "false"


def write_line_csv(report: LineReport, output_dir: str) -> str:
    path = os.path.join(output_dir, f"line_{_safe_filename(report.line_id)}.csv")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "phase",
                "batch_id",
                "value1",
                "value2",
                "value3",
                "value4",
                "value5",
                "mean",
                "range",
                "mean_beyond_limit",
                "range_beyond_limit",
                "run_rules",
            ]
        )
        for row in report.rows:
            run_rules = ";".join(
                f"{hit.rule}:{hit.start_batch_id}:{hit.end_batch_id}"
                for hit in row.rule_hits
            )
            writer.writerow(
                [
                    row.phase,
                    row.batch_id,
                    *[repr(v) for v in row.values],
                    repr(row.mean),
                    repr(row.range_value),
                    _format_bool(row.mean_violation),
                    _format_bool(row.range_violation),
                    run_rules,
                ]
            )
    return path


def build_summary(reports: list[LineReport]) -> dict:
    totals = {rule: 0 for rule in RULE_ORDER}
    total_abnormal = 0
    lines_summary = []
    for report in reports:
        for rule in RULE_ORDER:
            totals[rule] += report.rule_hit_counts[rule]
        total_abnormal += report.abnormal_monitoring_batch_count
        abnormal_ids = sorted(
            {
                hit.end_batch_id
                for row in report.rows
                if row.phase == "monitoring"
                for hit in row.rule_hits
            }
            | {
                row.batch_id
                for row in report.rows
                if row.phase == "monitoring"
                and (row.mean_violation or row.range_violation)
            }
        )
        lines_summary.append(
            {
                "line_id": report.line_id,
                "limits": {
                    "mean_center": report.mean_center,
                    "mean_lcl": report.mean_lcl,
                    "mean_ucl": report.mean_ucl,
                    "range_center": report.range_center,
                    "range_lcl": report.range_lcl,
                    "range_ucl": report.range_ucl,
                },
                "rule_hit_counts": {
                    rule: report.rule_hit_counts[rule] for rule in RULE_ORDER
                },
                "abnormal_monitoring_batch_count": (
                    report.abnormal_monitoring_batch_count
                ),
                "abnormal_monitoring_batch_ids": abnormal_ids,
                "run_rule_hits": [
                    {
                        "rule": hit.rule,
                        "start_batch_id": hit.start_batch_id,
                        "end_batch_id": hit.end_batch_id,
                    }
                    for row in report.rows
                    if row.phase == "monitoring"
                    for hit in row.rule_hits
                ],
            }
        )
    return {
        "rule_hit_totals": totals,
        "abnormal_monitoring_batch_total": total_abnormal,
        "lines": lines_summary,
    }


def run(input_path: str, output_dir: str) -> int:
    """Validate input, analyze all lines, and write reports.

    No report files are created when validation fails.
    """
    try:
        lines = load_input(input_path)
        reports = [analyze_line(line) for line in lines]
    except (ValidationError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    os.makedirs(output_dir, exist_ok=True)
    for report in reports:
        write_line_csv(report, output_dir)
    summary_path = os.path.join(output_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(build_summary(reports), handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m process_chart INPUT.json OUTPUT_DIR", file=sys.stderr)
        return 2
    return run(args[0], args[1])
