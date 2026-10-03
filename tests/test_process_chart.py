import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from process_chart.core import InputError, analyze, load_input, write_reports


def make_batch(batch_id, values):
    return {"batch_id": batch_id, "values": values}


def make_line(monitoring_values):
    baseline = [make_batch("b1", [10, 10, 10, 10, 10]),
                make_batch("b2", [12, 12, 12, 12, 12])]
    monitoring = [make_batch(f"m{i}", [v] * 5) for i, v in enumerate(monitoring_values)]
    return {"line_id": "L1", "baseline": baseline, "monitoring": monitoring}


class ValidationTests(unittest.TestCase):
    def _load(self, doc):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "in.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            return load_input(path)

    def test_valid_document(self):
        lines = self._load({"lines": [make_line([])]})
        self.assertEqual(lines[0]["line_id"], "L1")
        self.assertEqual(lines[0]["monitoring"], [])

    def test_duplicate_line_id_rejected(self):
        doc = {"lines": [make_line([]), make_line([])]}
        with self.assertRaises(InputError):
            self._load(doc)

    def test_duplicate_batch_id_rejected(self):
        line = make_line([])
        line["monitoring"] = [make_batch("b1", [1, 2, 3, 4, 5])]
        with self.assertRaises(InputError):
            self._load({"lines": [line]})

    def test_wrong_subgroup_size_rejected(self):
        line = make_line([])
        line["baseline"][0] = make_batch("b1", [1, 2, 3, 4])
        with self.assertRaises(InputError):
            self._load({"lines": [line]})

    def test_non_finite_value_rejected(self):
        line = make_line([])
        line["baseline"][0] = make_batch("b1", [1, 2, 3, 4, float("nan")])
        with self.assertRaises(InputError):
            self._load({"lines": [line]})

    def test_single_baseline_batch_rejected(self):
        line = make_line([])
        line["baseline"] = line["baseline"][:1]
        with self.assertRaises(InputError):
            self._load({"lines": [line]})

    def test_invalid_json_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "in.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(InputError):
                load_input(path)


class LimitTests(unittest.TestCase):
    def test_limits_from_baseline_only(self):
        # baseline means 10 and 12 -> center 11, r_bar 0 -> limits collapse to center
        result = analyze([make_line([11.0, 11.5])])[0]
        self.assertEqual(result["limits"]["mean_center"], 11.0)
        self.assertEqual(result["limits"]["mean_ucl"], 11.0)
        self.assertEqual(result["limits"]["range_ucl"], 0.0)
        # equal to the limit is not out of control
        self.assertFalse(result["monitoring"][0]["mean_out_of_control"])
        # strictly beyond is out of control
        self.assertTrue(result["monitoring"][1]["mean_out_of_control"])

    def test_limit_constants(self):
        baseline = [make_batch("b1", [0, 2, 1, 1, 1]), make_batch("b2", [0, 2, 1, 1, 1])]
        line = {"line_id": "L", "baseline": baseline, "monitoring": []}
        limits = analyze([line])[0]["limits"]
        self.assertAlmostEqual(limits["mean_center"], 1.0)
        self.assertAlmostEqual(limits["range_center"], 2.0)
        self.assertAlmostEqual(limits["mean_ucl"], 1.0 + 0.577 * 2.0)
        self.assertAlmostEqual(limits["mean_lcl"], 1.0 - 0.577 * 2.0)
        self.assertAlmostEqual(limits["range_ucl"], 2.114 * 2.0)
        self.assertEqual(limits["range_lcl"], 0.0)

    def test_range_out_of_control_strict(self):
        baseline = [make_batch("b1", [0, 1, 0, 1, 0.5]), make_batch("b2", [0, 1, 0, 1, 0.5])]
        # r_bar = 1 -> range UCL = 2.114
        mon = [make_batch("m1", [0, 2.114, 1, 1, 1]), make_batch("m2", [0, 2.2, 1, 1, 1])]
        result = analyze([{"line_id": "L", "baseline": baseline, "monitoring": mon}])[0]
        self.assertFalse(result["monitoring"][0]["range_out_of_control"])
        self.assertTrue(result["monitoring"][1]["range_out_of_control"])

    def test_baseline_never_flagged_and_excluded_from_estimates(self):
        baseline = [make_batch("b1", [0, 0, 0, 0, 0]), make_batch("b2", [100, 100, 100, 100, 100])]
        result = analyze([{"line_id": "L", "baseline": baseline, "monitoring": []}])[0]
        self.assertEqual(result["limits"]["mean_center"], 50.0)
        for row in result["baseline"]:
            self.assertFalse(row["mean_out_of_control"])
            self.assertFalse(row["range_out_of_control"])


class RunRuleTests(unittest.TestCase):
    def _analyze(self, monitoring_values):
        return analyze([make_line(monitoring_values)])[0]

    def test_eight_same_side_above(self):
        result = self._analyze([12.0] * 8)
        events = result["monitoring"][-1]["run_rules"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rule"], "eight_same_side")
        self.assertEqual(events[0]["side"], "above")
        self.assertEqual(events[0]["start_batch_id"], "m0")
        self.assertEqual(events[0]["end_batch_id"], "m7")

    def test_overlapping_windows_kept(self):
        result = self._analyze([12.0] * 9)
        events = [e for row in result["monitoring"] for e in row["run_rules"]]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["start_batch_id"], "m1")

    def test_center_value_breaks_same_side(self):
        result = self._analyze([12.0] * 7 + [11.0] + [12.0] * 7)
        events = [e for row in result["monitoring"] for e in row["run_rules"]]
        self.assertEqual(events, [])

    def test_seven_same_side_not_enough(self):
        result = self._analyze([12.0] * 7)
        events = [e for row in result["monitoring"] for e in row["run_rules"]]
        self.assertEqual(events, [])

    def test_six_increasing(self):
        result = self._analyze([5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        events = result["monitoring"][-1]["run_rules"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rule"], "six_trend")
        self.assertEqual(events[0]["direction"], "increasing")

    def test_six_decreasing(self):
        result = self._analyze([10.0, 9.0, 8.0, 7.0, 6.0, 5.0])
        events = result["monitoring"][-1]["run_rules"]
        self.assertEqual(events[0]["direction"], "decreasing")

    def test_equal_values_break_trend(self):
        result = self._analyze([5.0, 6.0, 7.0, 7.0, 8.0, 9.0])
        events = [e for row in result["monitoring"] for e in row["run_rules"]]
        self.assertEqual(events, [])

    def test_summary_counts_flagged_batches_once(self):
        # batch beyond limits also contributes to run rules; count batch once
        result = self._analyze([12.0] * 8)
        summary = result["summary"]
        self.assertEqual(summary["rule_counts"]["eight_same_side"], 1)
        self.assertEqual(summary["rule_counts"]["mean_out_of_control"], 8)
        self.assertEqual(summary["flagged_monitoring_batches"], 8)


class CliTests(unittest.TestCase):
    def _run(self, doc):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        in_path = root / "in.json"
        in_path.write_text(json.dumps(doc), encoding="utf-8")
        out_dir = root / "out"
        proc = subprocess.run(
            [sys.executable, "-m", "process_chart", str(in_path), "--output-dir", str(out_dir)],
            capture_output=True, text=True,
        )
        return proc, out_dir

    def test_cli_writes_reports(self):
        proc, out_dir = self._run({"lines": [make_line([11.0])]})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((out_dir / "L1.csv").exists())
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["lines"][0]["line_id"], "L1")

    def test_cli_invalid_input_no_reports(self):
        proc, out_dir = self._run({"lines": []})
        # empty lines list is structurally valid; use an actually invalid doc
        proc2, out_dir2 = self._run({"lines": [{"line_id": "X", "baseline": []}]})
        self.assertNotEqual(proc2.returncode, 0)
        self.assertFalse((out_dir2 / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
