# Subgroup Control Chart

Analyze production measurement batches with X-bar / R control charts built
from an independent baseline, then flag out-of-control points and sustained
shifts in later monitoring batches. Standard library only; no plotting.

## Usage

```
python -m process_chart INPUT.json --output-dir OUT_DIR
```

Writes one `<line_id>.csv` per production line plus a `summary.json` into
`OUT_DIR`. Any invalid input exits non-zero and produces no reports.

## Input format

```json
{
  "lines": [
    {
      "line_id": "L1",
      "baseline":  [{"batch_id": "b1", "values": [10, 11, 9, 10, 10]}, ...],
      "monitoring": [{"batch_id": "m1", "values": [10, 10, 10, 10, 10]}, ...]
    }
  ]
}
```

- `line_id` is unique across the document; `batch_id` is unique within a line.
- Every batch has exactly five finite numeric values.
- Baseline needs at least two batches; `monitoring` may be empty or omitted.
- Input batch order is preserved in all outputs.

## Statistics

- Per batch: mean and range (max - min).
- Center line: average of the baseline batch means.
- Average range `R_bar`: average of all baseline batch ranges. Baseline
  outliers are never removed and monitoring data never enters the estimates.
- Mean limits: `center +/- 0.577 * R_bar` (A2 for n=5).
- Range limits: center `R_bar`, LCL `0`, UCL `2.114 * R_bar` (D4 for n=5).

## Out-of-control and run rules

- Only monitoring batches are flagged; a value must be *strictly* beyond a
  limit (equal to a limit is not a violation, and a zero `R_bar` gets no
  special relaxation).
- Run rules apply to monitoring means only:
  - `eight_same_side`: eight consecutive means strictly on the same side of
    the center line (a mean exactly on the center breaks the run).
  - `six_trend`: six consecutive means strictly increasing or strictly
    decreasing (equal adjacent values break the run).
- Every qualifying window is reported at its ending batch with start/end
  batch ids; overlapping windows are kept individually. Baseline batches
  never participate in run-rule windows.

## Outputs

- Per-line CSV: `batch_id, phase, mean, range, mean_out_of_control,
  range_out_of_control, run_rules`.
- `summary.json`: limits, per-rule hit counts, and
  `flagged_monitoring_batches` — the number of monitoring batches hitting at
  least one rule (a batch with multiple hits counts once).

## Tests

```
python -m unittest discover -s tests
```
