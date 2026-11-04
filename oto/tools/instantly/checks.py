"""Local refusals of the Instantly client, raised BEFORE any call (`ValueError`, naming the
argument), and the two removals every answer goes through.

Each check mirrors the input schema of its function in `connectors/instantly/connector.yaml`:
what the description refuses, the client refuses too, so that a call that reaches Instantly
from either side is the same call.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PATH_EMAIL = re.compile(r"^[^/\s]+@[^/\s]+$")
_EMAIL = re.compile(r"^\S+@\S+$")
_HTTPS_URL = re.compile(r"^https://[^\s/]+(/\S*)?$")

MIN_LIMIT, MAX_LIMIT = 1, 100
MAX_LEADS_PER_ADD = 1000


def uuid(value: Any, name: str) -> str:
    """An Instantly id (a UUID), as every path and filter takes it."""
    if not isinstance(value, str) or not _UUID.match(value):
        raise ValueError(f"`{name}` must be an Instantly id (a UUID); got {value!r}.")
    return value


def optional_uuid(value: Any, name: str) -> Optional[str]:
    return None if value is None else uuid(value, name)


def uuids(values: Any, name: str) -> Optional[list]:
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"`{name}` must be a non-empty list of Instantly ids.")
    return [uuid(v, name) for v in values]


def job_id(value: Any) -> str:
    if not isinstance(value, str) or not _JOB_ID.match(value):
        raise ValueError(f"`job_id` must be a background job id; got {value!r}.")
    return value


def path_email(value: Any, name: str) -> str:
    """An email as one path segment, percent-encoded (`@` kept)."""
    if not isinstance(value, str) or not _PATH_EMAIL.match(value):
        raise ValueError(f"`{name}` must be an email address; got {value!r}.")
    return quote(value, safe="@")


def email(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _EMAIL.match(value):
        raise ValueError(f"`{name}` must be an email address; got {value!r}.")
    return value


def emails(values: Any, name: str) -> Optional[list]:
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"`{name}` must be a non-empty list of emails.")
    return [email(v, name) for v in values]


def limit(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_LIMIT <= value <= MAX_LIMIT:
        raise ValueError(f"`limit` must be an integer from {MIN_LIMIT} to {MAX_LIMIT}; got {value!r}.")
    return value


def text(value: Any, name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"`{name}` must be a non-empty string; got {value!r}.")
    return value


def required_text(value: Any, name: str) -> str:
    if value is None:
        raise ValueError(f"`{name}` is required.")
    return text(value, name)


def choice(value: Any, name: str, allowed: Iterable) -> Any:
    if value is None:
        return None
    allowed = tuple(allowed)
    if value not in allowed or isinstance(value, bool):
        raise ValueError(f"`{name}` invalid: {value!r}. Accepted values: "
                         + ", ".join(repr(a) for a in allowed))
    return value


def one_of(name_a: str, a: Any, name_b: str, b: Any) -> None:
    """Exactly one of two arguments."""
    if (a is None) == (b is None):
        raise ValueError(f"Give exactly one of `{name_a}` and `{name_b}`.")


def not_both(name_a: str, a: Any, name_b: str, b: Any) -> None:
    if a is not None and b is not None:
        raise ValueError(f"Give `{name_a}` or `{name_b}`, not both.")


def https_url(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _HTTPS_URL.match(value):
        raise ValueError(f"`{name}` must be an https:// address; got {value!r}.")
    return value


def present(values: Dict[str, Any]) -> Dict[str, Any]:
    """The given arguments only: `None` means absent and is never sent."""
    return {k: v for k, v in values.items() if v is not None}


def query(values: Dict[str, Any]) -> Dict[str, Any]:
    """Query parameters: absent ones dropped, booleans written `true` / `false` (as the
    description sends them; `requests` would write `True`). A list repeats its key."""
    return {k: ("true" if v else "false") if isinstance(v, bool) else v
            for k, v in present(values).items()}


# --- removals ---------------------------------------------------------------------------------

def _each(answer: Any):
    """The records of an answer: each element of `items` in a list answer, itself otherwise."""
    if isinstance(answer, dict) and isinstance(answer.get("items"), list):
        return [r for r in answer["items"] if isinstance(r, dict)]
    return [answer] if isinstance(answer, dict) else []


def without_webhook_headers(answer: Any) -> Any:
    """A webhook answer without `headers`: they are delivered to its destination and may
    carry its secret (an `Authorization`). Changes the answer in place and returns it."""
    for record in _each(answer):
        record.pop("headers", None)
    return answer


def without_attachment_urls(answer: Any) -> Any:
    """An email answer without its attachments' download addresses
    (`attachment_json.files[].url`, a CDN address that opens without a login); the name,
    size and type stay. Changes the answer in place and returns it."""
    for record in _each(answer):
        attachments = record.get("attachment_json")
        files = attachments.get("files") if isinstance(attachments, dict) else None
        for f in files if isinstance(files, list) else ():
            if isinstance(f, dict):
                f.pop("url", None)
    return answer
