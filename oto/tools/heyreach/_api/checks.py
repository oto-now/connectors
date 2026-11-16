"""Local checks of the HeyReach client: what the `heyreach` description's schemas refuse,
refused here too before any call (a `ValueError` naming the argument)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

MAX_PAGE = 100
MAX_LIST_LEADS_PAGE = 1000
MAX_LEADS_PER_CALL = 100

CAMPAIGN_STATUSES = ("DRAFT", "IN_PROGRESS", "PAUSED", "FINISHED", "CANCELED", "FAILED",
                     "STARTING", "SCHEDULED")
TIME_FILTERS = ("CreationTime", "Everywhere", "LastActionTakenTime", "FailedTime",
                "LastActionTakenOrFailedTime")
LIST_TYPES = ("USER_LIST", "COMPANY_LIST")
EVENT_TYPES = ("CONNECTION_REQUEST_SENT", "CONNECTION_REQUEST_ACCEPTED", "MESSAGE_SENT",
               "MESSAGE_REPLY_RECEIVED", "INMAIL_SENT", "INMAIL_REPLY_RECEIVED",
               "EVERY_MESSAGE_REPLY_RECEIVED", "FOLLOW_SENT", "LIKED_POST", "VIEWED_PROFILE",
               "CAMPAIGN_COMPLETED", "LEAD_TAG_UPDATED")

#: The keys of a lead, as HeyReach names them (`AddLeadsToCampaignV2`, `AddLeadsToListV2`).
LEAD_KEYS = frozenset({"profileUrl", "firstName", "lastName", "location", "summary",
                       "companyName", "position", "about", "emailAddress", "customUserFields"})
_FIELD_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_HTTPS = re.compile(r"^https://\S+$")


def ident(value: Any, name: str, *, required: bool = True) -> Optional[int]:
    """A HeyReach id: an integer >= 1."""
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"`{name}` must be an integer id >= 1; got {value!r}.")
    return value


def idents(values: Any, name: str, *, allow_empty: bool = False) -> Optional[List[int]]:
    """A list of ids, or None. Empty only where the reference reads it as « all »."""
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or (not values and not allow_empty):
        raise ValueError(f"`{name}` must be a non-empty list of ids; got {values!r}.")
    return [ident(v, name) for v in values]


def text(value: Any, name: str, *, required: bool = False) -> Optional[str]:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"`{name}` must be a non-empty string; got {value!r}.")
    return value


def texts(values: Any, name: str) -> Optional[List[str]]:
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"`{name}` must be a non-empty list of strings; got {values!r}.")
    return [text(v, name, required=True) for v in values]


def flag(value: Any, name: str, *, required: bool = False) -> Optional[bool]:
    if value is None and not required:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"`{name}` must be true or false; got {value!r}.")
    return value


def limit(value: Any, *, maximum: int = MAX_PAGE, required: bool = True) -> Optional[int]:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"`limit` must be between 1 and {maximum}; got {value!r}.")
    return value


def offset(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"`offset` must be an integer >= 0; got {value!r}.")
    return value


def choice(value: Any, name: str, allowed: Iterable[str], *, required: bool = False) -> Optional[str]:
    if value is None and not required:
        return None
    if value not in allowed:
        raise ValueError(f"`{name}` invalid: {value!r}. Accepted values: " + ", ".join(allowed))
    return value


def choices(values: Any, name: str, allowed: Iterable[str]) -> Optional[List[str]]:
    if values is None:
        return None
    allowed = tuple(allowed)
    if not isinstance(values, (list, tuple)) or not values or len(set(values)) != len(values):
        raise ValueError(f"`{name}` must be a non-empty list without duplicates; got {values!r}.")
    return [choice(v, name, allowed, required=True) for v in values]


def date_time(value: Any, name: str, *, required: bool = False) -> Optional[str]:
    """An ISO 8601 date-time WITH its offset, sent as given."""
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"`{name}` must be an ISO 8601 date-time; got {value!r}.")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw[-1:] in ("Z", "z") else raw)
    except ValueError:
        raise ValueError(f"`{name}` unreadable: {value!r}. Expected an ISO 8601 date-time with "
                         "its offset (2026-11-02T00:00:00Z).") from None
    if parsed.tzinfo is None:
        raise ValueError(f"`{name}`: {value!r} has no offset. Add `Z` (UTC) or an offset such "
                         "as `+01:00`.")
    return value


def one_lead_key(**given: Any) -> None:
    """At least one way to name the lead."""
    if all(v is None for v in given.values()):
        raise ValueError("Name the lead: " + " or ".join(f"`{k}`" for k in given) + ".")


def not_both(name_a: str, a: Any, name_b: str, b: Any) -> None:
    if a is not None and b is not None:
        raise ValueError(f"`{name_a}` and `{name_b}` cannot be given together: pick one.")


def https(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _HTTPS.match(value):
        raise ValueError(f"`{name}` must be an https:// address; got {value!r}.")
    return value


def lead(value: Any, where: str) -> Dict[str, Any]:
    """One lead with HeyReach's own keys, sent as given: its profileUrl required, custom
    field names letters, digits and `_`."""
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object; got {value!r}.")
    stray = sorted(set(value) - LEAD_KEYS)
    if stray:
        raise ValueError(f"{where} does not take {', '.join(stray)} "
                         f"(takes {', '.join(sorted(LEAD_KEYS))}).")
    text(value.get("profileUrl"), f"{where}.profileUrl", required=True)
    for key in LEAD_KEYS - {"profileUrl", "customUserFields"}:
        if key in value and not isinstance(value[key], str):
            raise ValueError(f"{where}.{key} must be a string; got {value[key]!r}.")
    fields = value.get("customUserFields")
    if fields is not None:
        if not isinstance(fields, list):
            raise ValueError(f"{where}.customUserFields must be a list of {{name, value}}.")
        for field in fields:
            if (not isinstance(field, dict) or set(field) != {"name", "value"}
                    or not isinstance(field["value"], str)
                    or not isinstance(field["name"], str) or not _FIELD_NAME.match(field["name"])):
                raise ValueError(f"{where}.customUserFields: each is {{name, value}}, the name made "
                                 f"of letters, digits and `_`, the value a string; got {field!r}.")
    return value


def leads(values: Any, name: str) -> List[Dict[str, Any]]:
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_LEADS_PER_CALL:
        raise ValueError(f"`{name}` must be a list of 1 to {MAX_LEADS_PER_CALL} leads.")
    return [lead(v, f"`{name}`[{i}]") for i, v in enumerate(values)]


def account_lead_pairs(values: Any) -> List[Dict[str, Any]]:
    """Each {lead, linkedInAccountId?}, sent as given."""
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_LEADS_PER_CALL:
        raise ValueError(f"`account_lead_pairs` must be a list of 1 to {MAX_LEADS_PER_CALL} "
                         "{lead, linkedInAccountId?}.")
    for i, pair in enumerate(values):
        where = f"`account_lead_pairs`[{i}]"
        if not isinstance(pair, dict) or "lead" not in pair or set(pair) - {"lead", "linkedInAccountId"}:
            raise ValueError(f"{where} must be {{lead, linkedInAccountId?}}; got {pair!r}.")
        if "linkedInAccountId" in pair:
            ident(pair["linkedInAccountId"], f"{where}.linkedInAccountId")
        lead(pair["lead"], f"{where}.lead")
    return values


def compact(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """A body or a query without its absent values; a nested table absent when empty."""
    out = {}
    for key, value in mapping.items():
        if isinstance(value, dict):
            value = compact(value) or None
        if value is not None:
            out[key] = value
    return out
