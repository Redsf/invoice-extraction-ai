"""Deterministic scoring for invoice extraction.

Extraction is a structured task with a known ground truth, so there is no LLM
judge here — every metric is computed directly from the extracted object. That
keeps eval runs free, instant, and perfectly repeatable.

Four axes:
- accuracy      — extracted value matches the expected value
- hallucination — a field was populated when the invoice does not contain it
- omission      — a field was left empty when the invoice does contain it
- arithmetic    — line items sum to the stated subtotal/total (self-consistency,
                  checkable without ground truth, and the check that actually
                  catches a bad parse in production)
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

MONEY_FIELDS = frozenset({"subtotal", "total_price", "vat_amount"})
MONEY_PATTERN = re.compile(r"-?\d+(?:[.,]\d+)?")
# Invoice numbers and PO numbers vary in punctuation between vendors; compare
# on alphanumerics only so "INV-2024-001" and "INV 2024 001" are one value.
ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")

ARITHMETIC_TOLERANCE = Decimal("0.02")


def normalize_money(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    match = MONEY_PATTERN.search(str(value).replace(",", "."))
    if not match:
        return None
    try:
        return Decimal(match.group()).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return ALNUM_PATTERN.sub("", str(value).strip().lower())


def values_match(field: str, expected: object, actual: object) -> bool:
    if field in MONEY_FIELDS:
        return normalize_money(expected) == normalize_money(actual)
    return normalize_text(expected) == normalize_text(actual)


def is_empty(value: object) -> bool:
    return value is None or (isinstance(value, (str, list, dict)) and len(value) == 0)


def check_arithmetic(extracted: dict) -> bool | None:
    """Do the line items add up to the stated total? None if not checkable."""
    items = extracted.get("line_items")
    total = normalize_money(extracted.get("total_price"))
    if not isinstance(items, list) or not items or total is None:
        return None

    line_sum = Decimal("0")
    for item in items:
        if not isinstance(item, dict):
            return None
        amount = normalize_money(item.get("amount") or item.get("total"))
        if amount is None:
            unit = normalize_money(item.get("unit_price"))
            qty = normalize_money(item.get("quantity"))
            if unit is None or qty is None:
                return None
            amount = (unit * qty).quantize(Decimal("0.01"))
        line_sum += amount

    vat = normalize_money(extracted.get("vat_amount")) or Decimal("0")
    return abs((line_sum + vat) - total) <= ARITHMETIC_TOLERANCE


def score_case(case: dict, extracted: dict) -> dict:
    """Score one extraction against its expected fields."""
    expected = case.get("expected_fields", {})
    fields: dict[str, dict] = {}

    for field, expected_value in expected.items():
        actual_value = extracted.get(field)
        expected_empty = is_empty(expected_value)
        actual_empty = is_empty(actual_value)

        fields[field] = {
            "expected": expected_value,
            "actual": actual_value,
            "correct": (
                True
                if expected_empty and actual_empty
                else False
                if expected_empty or actual_empty
                else values_match(field, expected_value, actual_value)
            ),
            "hallucinated": expected_empty and not actual_empty,
            "omitted": not expected_empty and actual_empty,
        }

    expected_items = case.get("expected_line_item_count")
    actual_items = extracted.get("line_items")
    line_item_count_correct = (
        None
        if expected_items is None
        else isinstance(actual_items, list) and len(actual_items) == expected_items
    )

    scored = list(fields.values())
    return {
        "fields": fields,
        "field_accuracy": (
            sum(1 for f in scored if f["correct"]) / len(scored) if scored else None
        ),
        "hallucinated_fields": [k for k, f in fields.items() if f["hallucinated"]],
        "omitted_fields": [k for k, f in fields.items() if f["omitted"]],
        "line_item_count_correct": line_item_count_correct,
        "arithmetic_consistent": check_arithmetic(extracted),
    }
