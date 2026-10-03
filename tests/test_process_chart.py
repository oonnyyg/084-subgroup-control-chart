import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest

from process_chart.analysis import analyze_line
from process_chart.validation import ValidationError, validate_input


def line(baseline, monitoring=None, line_id="L1"):
    data = {
        "line_id": line_id,
        "baseline": [
            {"batch_id": bid, "values": values}
            for bid, values in baseline
        ],
        "monitoring": [
            {"batch_id": bid, "values": values}
            for bid, values in (monitoring or [])
        ],
    }
    return data


class LimitsAndViolationsTests(unittest.TestCase):
    def test_limits_from_baseline_only(self):
        # baseline means 9.7 and 12.1 -> center 10.9; ranges 5 and 3 -> rbar 4
        data = line(
            [("B1", [7.5, 8.5, 9.5, 10.5, 12.5]), ("B2", [10.5, 11.5, 12.5, 12.5, 13.5])],
            [("M1", [100, 100, 100, 100, 100])],
        )
        report = analyze_line(data)
        self.assertAlmostEqual(report.mean_center, 10.9)
        self.assertAlmostEqual(report.range_center, 4.0)
        self.assertAlmostEqual(report.mean_lcl, 10.9 - 0.577 * 4)
        self.assertAlmostEqual(report.mean_ucl, 10.9 + 0.577 * 4)
        self.assertEqual(report.range_lcl, 0.0)
        self.assertAlmostEqual(report.range_ucl, 2.114 * 4)
        # Extreme monitoring value must not move the estimated limits.
        self.assertAlmostEqual(report.mean_center, 10.9)
        m1 = report.rows[-1]
        self.assertTrue(m1.mean_violation)

    def test_equality_with_limit_is_not_violation(self):
        # Build baseline with zero range so every limit equals the center.
        data = line(
            [("B1", [3, 3, 3, 3, 3]), ("B2", [5, 5, 5, 5, 5])],
            [("M1", [4, 4, 4, 4, 4]), ("M2", [0, 0, 0, 0, 1])],
        )
        report = analyze_line(data)
        self.assertEqual(report.mean_ucl, report.mean_center)
        self.assertEqual(report.range_ucl, 0.0)
        m1, m2 = report.rows[-2], report.rows[-1]
        # M1 mean == center == limits and range == 0 == upper limit: in control.
        self.assertFalse(m1.mean_violation)
        self.assertFalse(m1.range_violation)
        # Zero average range is not relaxed: any nonzero range is out.
        self.assertTrue(m2.range_violation)

    def test_baseline_order_preserved(self):
        data = line([("Z", [1, 2, 3, 4, 5]), ("A", [2, 3, 4, 5, 6])])
        report = analyze_line(data)
        self.assertEqual([row.batch_id for row in report.rows], ["Z", "A"])


class RunRuleTests(unittest.TestCase):
    def _hits(self, monitoring_means, center=10.0):
        baseline = [
            ("B1", [center - 1, center, center, center, center + 1]),
            ("B2", [center - 1, center, center, center, center + 1]),
        ]
        monitoring = [
            (f"M{i:02d}", [m, m, m, m, m]) for i, m in enumerate(monitoring_means)
        ]
        report = analyze_line(line(baseline, monitoring))
        return [
            (hit.rule, hit.start_batch_id, hit.end_batch_id)
            for row in report.rows
            for hit in row.rule_hits
        ]

    def test_same_side_eight_overlapping_windows(self):
        means = [11] * 9
        hits = self._hits(means)
        self.assertEqual(
            hits,
            [
                ("same_side_8", "M00", "M07"),
                ("same_side_8", "M01", "M08"),
            ],
        )

    def test_center_value_breaks_same_side(self):
        means = [11, 11, 11, 10, 11, 11, 11, 11, 11, 11, 11, 11]
        hits = self._hits(means)
        self.assertEqual(
            hits,
            [
                ("same_side_8", "M04", "M11"),
            ],
        )

    def test_six_increasing_and_overlap(self):
        means = [1, 2, 3, 4, 5, 6, 7]
        hits = self._hits(means, center=0.0)
        self.assertEqual(
            hits,
            [
                ("trend_up_6", "M00", "M05"),
                ("trend_up_6", "M01", "M06"),
            ],
        )

    def test_equal_value_breaks_trend(self):
        means = [3, 3, 4, 5, 6, 7, 8]
        hits = self._hits(means, center=0.0)
        self.assertEqual(hits, [("trend_up_6", "M01", "M06")])

    def test_six_decreasing(self):
        hits = self._hits([7, 6, 5, 4, 3, 2], center=20.0)
        self.assertEqual(hits, [("trend_down_6", "M00", "M05")])

    def test_abnormal_count_dedupes_batch(self):
        baseline = [
            ("B1", [0, 0, 0, 0, 100]),
            ("B2", [0, 0, 0, 0, 100]),
        ]
        # Nine high means with a strict increasing tail: M08 closes two windows.
        monitoring = [(f"M{i}", [21 + i, 21 + i, 21 + i, 21 + i, 21 + i])
                      for i in range(9)]
        report = analyze_line(line(baseline, monitoring))
        counts = report.rule_hit_counts
        self.assertEqual(counts["same_side_8"], 2)
        self.assertEqual(counts["trend_up_6"], 4)
        # Windows end on M07..M10 for same-side/trends; M08 closes both.
        self.assertEqual(report.abnormal_monitoring_batch_count, 4)


class ValidationTests(unittest.TestCase):
    def test_rejects_bad_inputs(self):
        # Single-batch baseline.
        single = {
            "line_id": "L",
            "baseline": [{"batch_id": "B1", "values": [1, 2, 3, 4, 5]}],
            "monitoring": [],
        }
        with self.assertRaises(ValidationError):
            validate_input([single])
        with self.assertRaises(ValidationError):
            validate_input("nope")
        with self.assertRaises(ValidationError):
            validate_input([
                {
                    "line_id": "L",
                    "baseline": [
                        {"batch_id": "B1", "values": [1, 2, 3, 4, 5]},
                        {"batch_id": "B2", "values": [1, 2, 3, 4, float("nan")]},
                    ],
                }
            ])
        with self.assertRaises(ValidationError):
            validate_input([
                {
                    "line_id": "L",
                    "baseline": [
                        {"batch_id": "B", "values": [1, 2, 3, 4, 5]},
                        {"batch_id": "B", "values": [1, 2, 3, 4, 5]},
                    ],
                }
            ])


class CliTests(unittest.TestCase):
    def test_end_to_end_reports(self):
        data = {
            "lines": [
                line(
                    [("B1", [9, 10, 10, 10, 11]), ("B2", [9, 10, 10, 10, 11])],
                    [("M1", [20, 20, 20, 20, 20])],
                    line_id="L-1",
                )
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "input.json")
            out_dir = os.path.join(tmp, "reports")
            with open(input_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            result = subprocess.run(
                [sys.executable, "-m", "process_chart", input_path, out_dir],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            csv_path = os.path.join(out_dir, "line_L-1.csv")
            with open(csv_path, encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["phase"], "baseline")
            self.assertEqual(rows[2]["mean_beyond_limit"], "true")
            with open(os.path.join(out_dir, "summary.json"), encoding="utf-8") as h:
                summary = json.load(h)
            self.assertEqual(summary["rule_hit_totals"]["mean_beyond_limit"], 1)
            self.assertEqual(summary["abnormal_monitoring_batch_total"], 1)

    def test_invalid_input_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "bad.json")
            out_dir = os.path.join(tmp, "reports")
            with open(input_path, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            result = subprocess.run(
                [sys.executable, "-m", "process_chart", input_path, out_dir],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(os.path.exists(out_dir))


if __name__ == "__main__":
    unittest.main()
