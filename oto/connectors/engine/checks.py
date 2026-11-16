"""The checks a function declares: of its arguments before anything is sent (`checks`), of each answer (`expect`).

- `equal_sums`: two fields summed over the elements of a list argument, exactly (decimal texts or numbers; absent counts
  as zero; a value that cannot be read never balances).
- `non_empty`: the value at a path of the answer is neither absent, nor null, nor an empty text, list or object.

The first check that fails raises the function's refusal it names (`Refused`, without a status).
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Optional

from .. import serve
from .errors import EngineError, Refused, Unsupported

DECIMAL = re.compile(r"^-?\d+(?:\.\d+)?$")


def _refusal(fn: dict, code: str, suffix: str = "") -> Refused:
    declared = next((refusal for refusal in fn.get("refusals", []) if refusal["code"] == code), None)
    if declared is None:
        raise EngineError(f"{fn['name']}: a check names the refusal {code}, which the function does not declare.")
    return Refused(code, f"{declared['message']}{suffix}", retryable=False)


def _decimal(value: Any) -> Optional[Decimal]:
    """An exact decimal amount: `"700.10"`; absent or null: zero; unreadable (a boolean, a text that is not a plain
    decimal): `None`."""
    if value is serve.ABSENT or value is None:
        return Decimal(0)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = repr(value) if isinstance(value, float) else str(value)
    if not isinstance(value, str) or not DECIMAL.match(value.strip()):
        return None
    return Decimal(value.strip())


def _sum(items: list, field: str) -> Optional[Decimal]:
    values = [_decimal(item.get(field, serve.ABSENT) if isinstance(item, dict) else serve.ABSENT) for item in items]
    if any(value is None for value in values):
        return None
    return sum(values, Decimal(0))


def run_checks(fn: dict, args: dict) -> None:
    """The checks of the arguments, before anything is sent."""
    for check in fn.get("checks", []):
        if check["kind"] != "equal_sums":
            raise Unsupported(f"{fn['name']}: a check of kind {check['kind']} is not run by the Python engine.")
        items = serve.value_at(args, check["items"])
        items = items if isinstance(items, list) else []
        left, right = (_sum(items, field) for field in check["fields"])
        if left is None or right is None or left != right:
            raise _refusal(fn, check["refusal"], " Nothing was sent.")


def run_expectations(fn: dict, answer: Any) -> None:
    """The checks of an answer: the first path that is empty raises its refusal instead of the answer."""
    for expectation in fn.get("expect", []):
        if expectation["kind"] != "non_empty":
            raise Unsupported(f"{fn['name']}: an expectation of kind {expectation['kind']} is not run by the Python engine.")
        if not serve.non_empty(serve.value_at(answer, expectation["path"])):
            raise _refusal(fn, expectation["refusal"])
