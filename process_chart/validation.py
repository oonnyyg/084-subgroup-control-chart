"""Input loading and strict validation for control chart data."""

from __future__ import annotations

import json
import math
from typing import Any


class ValidationError(ValueError):
    """Raised when the input document fails structural validation."""


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_batch(batch: Any, line_id: str, seen_batch_ids: set[str]) -> None:
    if not isinstance(batch, dict):
        raise ValidationError(f"line {line_id!r}: batch must be an object")
    batch_id = batch.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id:
        raise ValidationError(f"line {line_id!r}: batch_id must be a non-empty string")
    if batch_id in seen_batch_ids:
        raise ValidationError(f"line {line_id!r}: duplicate batch_id {batch_id!r}")
    seen_batch_ids.add(batch_id)

    values = batch.get("values")
    if not isinstance(values, list) or len(values) != 5:
        raise ValidationError(
            f"line {line_id!r} batch {batch_id!r}: exactly 5 values required"
        )
    for value in values:
        if not _is_finite_number(value):
            raise ValidationError(
                f"line {line_id!r} batch {batch_id!r}: values must be finite numbers"
            )


def _validate_line(line: Any, seen_line_ids: set[str]) -> None:
    if not isinstance(line, dict):
        raise ValidationError("each line must be an object")
    line_id = line.get("line_id")
    if not isinstance(line_id, str) or not line_id:
        raise ValidationError("line_id must be a non-empty string")
    if line_id in seen_line_ids:
        raise ValidationError(f"duplicate line_id {line_id!r}")
    seen_line_ids.add(line_id)

    baseline = line.get("baseline")
    if not isinstance(baseline, list) or len(baseline) < 2:
        raise ValidationError(
            f"line {line_id!r}: baseline must contain at least 2 batches"
        )

    seen_batch_ids: set[str] = set()
    for batch in baseline:
        _validate_batch(batch, line_id, seen_batch_ids)

    monitoring = line.get("monitoring")
    if monitoring is None:
        monitoring = []
        line["monitoring"] = monitoring
    if not isinstance(monitoring, list):
        raise ValidationError(f"line {line_id!r}: monitoring must be a list")
    for batch in monitoring:
        _validate_batch(batch, line_id, seen_batch_ids)


def validate_input(data: Any) -> list[dict[str, Any]]:
    """Validate and normalize the loaded document; return the line list."""
    if isinstance(data, dict) and isinstance(data.get("lines"), list):
        lines = data["lines"]
    elif isinstance(data, list):
        lines = data
    else:
        raise ValidationError("input must be a list of lines or have a 'lines' list")
    if not lines:
        raise ValidationError("at least one production line is required")

    seen_line_ids: set[str] = set()
    for line in lines:
        _validate_line(line, seen_line_ids)
    return lines


def load_input(path: str) -> list[dict[str, Any]]:
    """Load and validate a JSON file describing production lines."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return validate_input(data)
