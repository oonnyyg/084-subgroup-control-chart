"""Mean/range statistics, control limits and run-rule detection."""

from __future__ import annotations

from dataclasses import dataclass, field

# Constants for subgroups of size n = 5.
A2 = 0.577
D3 = 0.0
D4 = 2.114

SAME_SIDE_LENGTH = 8
TREND_LENGTH = 6


@dataclass
class RuleHit:
    """A run rule recorded on the batch that closes its window."""

    rule: str
    start_batch_id: str
    end_batch_id: str


@dataclass
class BatchRow:
    phase: str
    batch_id: str
    values: list[float]
    mean: float
    range_value: float
    mean_violation: bool
    range_violation: bool
    rule_hits: list[RuleHit] = field(default_factory=list)


@dataclass
class LineReport:
    line_id: str
    mean_center: float
    mean_lcl: float
    mean_ucl: float
    range_center: float
    range_lcl: float
    range_ucl: float
    rows: list[BatchRow]
    rule_hit_counts: dict[str, int]
    abnormal_monitoring_batch_count: int


def batch_mean(values: list[float]) -> float:
    return sum(values) / len(values)


def batch_range(values: list[float]) -> float:
    return max(values) - min(values)


def detect_run_rules(rows: list[BatchRow], center: float) -> list[RuleHit]:
    """Find same-side (8) and monotonic trend (6) windows in monitoring means.

    Only monitoring rows participate, input order is preserved, and every
    overlapping window is recorded on its closing batch.
    """
    hits: list[RuleHit] = []
    count = len(rows)

    for end in range(count):
        start = end - SAME_SIDE_LENGTH + 1
        if start >= 0:
            window = rows[start : end + 1]
            signs = {(row.mean > center) - (row.mean < center) for row in window}
            if signs == {1} or signs == {-1}:
                hits.append(
                    RuleHit("same_side_8", window[0].batch_id, window[-1].batch_id)
                )

        trend_start = end - TREND_LENGTH + 1
        if trend_start >= 0:
            window = rows[trend_start : end + 1]
            increasing = all(
                window[i].mean < window[i + 1].mean
                for i in range(len(window) - 1)
            )
            decreasing = all(
                window[i].mean > window[i + 1].mean
                for i in range(len(window) - 1)
            )
            if increasing:
                hits.append(
                    RuleHit("trend_up_6", window[0].batch_id, window[-1].batch_id)
                )
            elif decreasing:
                hits.append(
                    RuleHit("trend_down_6", window[0].batch_id, window[-1].batch_id)
                )
    return hits


def analyze_line(line: dict) -> LineReport:
    """Compute limits from baseline only and evaluate every batch."""
    baseline_stats = [
        (batch["batch_id"], batch_mean(batch["values"]), batch_range(batch["values"]))
        for batch in line["baseline"]
    ]
    mean_center = sum(mean for _, mean, _ in baseline_stats) / len(baseline_stats)
    range_center = sum(rng for _, _, rng in baseline_stats) / len(baseline_stats)

    mean_lcl = mean_center - A2 * range_center
    mean_ucl = mean_center + A2 * range_center
    range_lcl = D3 * range_center
    range_ucl = D4 * range_center

    rows: list[BatchRow] = []
    for phase, batches in (
        ("baseline", line["baseline"]),
        ("monitoring", line["monitoring"]),
    ):
        for batch in batches:
            values = [float(v) for v in batch["values"]]
            mean = batch_mean(values)
            rng = batch_range(values)
            rows.append(
                BatchRow(
                    phase=phase,
                    batch_id=batch["batch_id"],
                    values=values,
                    mean=mean,
                    range_value=rng,
                    # Strict crossing only: equality with a limit is in control.
                    mean_violation=mean < mean_lcl or mean > mean_ucl,
                    range_violation=rng < range_lcl or rng > range_ucl,
                )
            )

    monitoring_rows = [row for row in rows if row.phase == "monitoring"]
    rule_hits = detect_run_rules(monitoring_rows, mean_center)
    hits_by_end: dict[str, list[RuleHit]] = {}
    for hit in rule_hits:
        hits_by_end.setdefault(hit.end_batch_id, []).append(hit)
    for row in monitoring_rows:
        row.rule_hits = hits_by_end.get(row.batch_id, [])

    rule_hit_counts = {
        "mean_beyond_limit": 0,
        "range_beyond_limit": 0,
        "same_side_8": 0,
        "trend_up_6": 0,
        "trend_down_6": 0,
    }
    abnormal_ids: set[str] = set()
    for row in monitoring_rows:
        if row.mean_violation:
            rule_hit_counts["mean_beyond_limit"] += 1
            abnormal_ids.add(row.batch_id)
        if row.range_violation:
            rule_hit_counts["range_beyond_limit"] += 1
            abnormal_ids.add(row.batch_id)
        for hit in row.rule_hits:
            rule_hit_counts[hit.rule] += 1
            abnormal_ids.add(hit.end_batch_id)

    return LineReport(
        line_id=line["line_id"],
        mean_center=mean_center,
        mean_lcl=mean_lcl,
        mean_ucl=mean_ucl,
        range_center=range_center,
        range_lcl=range_lcl,
        range_ucl=range_ucl,
        rows=rows,
        rule_hit_counts=rule_hit_counts,
        abnormal_monitoring_batch_count=len(abnormal_ids),
    )
