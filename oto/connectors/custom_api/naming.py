"""Names of the format, drawn from names an API or a person wrote: snake_case, the class a verb implies."""

from __future__ import annotations

import re

MAX_NAME = 63


def snake_case(text: str) -> str:
    """`organizationId` → `organization_id`, `page[size]` → `page_size`, `HTTPServer` → `http_server`; `""` when nothing
    of a letter or a digit remains. A name that would start with a digit is the caller's to prefix."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text or "")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return s[:MAX_NAME].rstrip("_")


def identifier(text: str, prefix: str) -> str:
    """A name of the format (`^[a-z][a-z0-9_]*$`) from `text`: its snake_case, `prefix` before it when it is empty or
    does not start with a letter."""
    s = snake_case(text)
    if not s or not s[0].isalpha():
        s = f"{prefix}_{s}".rstrip("_")
    return s[:MAX_NAME].rstrip("_")


def class_of(method: str) -> dict:
    """What a verb implies, said as deduced: GET reads, POST, PUT and PATCH write, DELETE destroys and is confirmed. The
    confirmation names the request, its `{argument}` filled by the host."""
    method = method.upper()
    if method == "GET":
        return {"class": "read"}
    if method == "DELETE":
        return {"class": "sensitive", "effect": "destroy"}
    return {"class": "write"}


def confirmation(method: str, path: str) -> dict:
    return {"summary": f"{method.upper()} {path}: irreversible."}
