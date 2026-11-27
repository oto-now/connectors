"""Helpers shared by the Twilio mixins: form encoding, bounds, identifiers."""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Sequence, Tuple

Form = Sequence[Tuple[str, str]]

MIN_PAGE_SIZE, MAX_PAGE_SIZE = 1, 1000
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def form(**values: Any) -> list[tuple[str, str]]:
    """Form pairs from keyword arguments: None is left out, a bool is
    `true`/`false`, a list or tuple repeats the name once per item."""
    pairs: list[tuple[str, str]] = []
    for name, value in values.items():
        if value is None:
            continue
        items: Iterable[Any] = value if isinstance(value, (list, tuple)) else (value,)
        for item in items:
            if item is None:
                continue
            if isinstance(item, bool):
                item = "true" if item else "false"
            pairs.append((name, str(item)))
    return pairs


def page_size(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not MIN_PAGE_SIZE <= int(value) <= MAX_PAGE_SIZE:
        raise ValueError(f"`page_size` must be between {MIN_PAGE_SIZE} and {MAX_PAGE_SIZE}.")
    return int(value)


def e164(value: Optional[str], name: str) -> str:
    """A phone number in E.164 (`+` then 7 to 15 digits), refused locally otherwise."""
    text = (value or "").strip()
    if not _E164.match(text):
        raise ValueError(f"`{name}` must be an E.164 phone number such as +33612345678; got {value!r}.")
    return text


def sid(value: Optional[str], prefix: str, name: str) -> str:
    """A Twilio SID (`prefix` + 32 hex), refused locally otherwise: it lands in a path."""
    text = (value or "").strip()
    if not re.match(rf"^{prefix}[0-9a-fA-F]{{32}}$", text):
        raise ValueError(f"`{name}` must be a Twilio SID starting with {prefix}; got {value!r}.")
    return text


def choice(name: str, value: Optional[str], allowed: tuple) -> Optional[str]:
    if value is None:
        return None
    if value not in allowed:
        raise ValueError(f"`{name}` invalid: {value!r}. Accepted values: "
                         + ", ".join(repr(a) for a in allowed))
    return value
