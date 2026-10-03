"""Command line entry: python -m process_chart INPUT_JSON --output-dir DIR."""

import argparse
import sys

from .core import InputError, analyze, load_input, write_reports


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="process_chart",
        description="X-bar/R control chart analysis with run rules (JSON in, CSV/JSON out).",
    )
    parser.add_argument("input", help="path to the input JSON document")
    parser.add_argument("--output-dir", required=True, help="directory for CSV and summary JSON output")
    args = parser.parse_args(argv)
    try:
        lines = load_input(args.input)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    results = analyze(lines)
    summary_path = write_reports(results, args.output_dir)
    print(f"wrote reports to {summary_path.parent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
