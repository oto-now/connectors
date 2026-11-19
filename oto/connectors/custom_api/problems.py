"""A problem of a Custom API definition: its code, where it lies, what it says.

Every judge of the package (`profile.validate`, `oto1.from_oto1`, `openapi.from_openapi`) names each problem it finds,
all of them, never the first one only; a definition with a problem is never saved, not even in part.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: The codes of the profile (`profile.validate`) and of the takeover of oto 1's definitions (`oto1.from_oto1`).
KEY_NOT_ALLOWED = "custom_api_key_not_allowed"
AUTH_NOT_SUPPORTED = "custom_api_auth_not_supported"
BASE_URL_NOT_HTTPS = "custom_api_base_url_not_https"
BASE_URL_INTERNAL = "custom_api_base_url_internal"
NAME_RESERVED = "custom_api_name_reserved"
HEADER_AUTHENTICATES = "custom_api_header_authenticates"
TOO_LARGE = "custom_api_too_large"
EXAMPLE_MISSING = "custom_api_example_missing"
EXAMPLE_INVALID = "custom_api_example_invalid"
SCHEMA_INVALID = "custom_api_schema_invalid"
#: A rule of the format the schema cannot say (`oto.connectors.rules`); `Problem.rule` names it when it has a code.
RULE_BROKEN = "custom_api_rule_broken"
#: oto 1 only: two arguments of an action give the same snake_case name.
ARGUMENT_NAME_COLLISION = "custom_api_argument_name_collision"
#: oto 1 only: a variable that would fill a path argument with a value that is not one path segment.
VARIABLE_INVALID = "custom_api_variable_invalid"

CODES = (
    KEY_NOT_ALLOWED,
    AUTH_NOT_SUPPORTED,
    BASE_URL_NOT_HTTPS,
    BASE_URL_INTERNAL,
    NAME_RESERVED,
    HEADER_AUTHENTICATES,
    TOO_LARGE,
    EXAMPLE_MISSING,
    EXAMPLE_INVALID,
    SCHEMA_INVALID,
    RULE_BROKEN,
    ARGUMENT_NAME_COLLISION,
    VARIABLE_INVALID,
)


@dataclass(frozen=True)
class Problem:
    """`path`: dotted, a list index as a number (`functions.0.examples`), empty for the whole document. `rule`: the code
    of the broken rule of the format, for `custom_api_rule_broken` (`body_key_twice`), when that rule has one."""

    code: str
    path: str
    message: str
    rule: Optional[str] = None

    def as_dict(self) -> dict:
        out = {"code": self.code, "path": self.path, "message": self.message}
        if self.rule is not None:
            out["rule"] = self.rule
        return out


def dotted(parts) -> str:
    """A path as `Problem.path` says it, from its keys and indexes."""
    return ".".join(str(part) for part in parts)


def unique(problems: list[Problem]) -> list[Problem]:
    """The problems in their order, each `(code, path, message)` once."""
    seen: set[tuple[str, str, str]] = set()
    out = []
    for problem in problems:
        key = (problem.code, problem.path, problem.message)
        if key not in seen:
            seen.add(key)
            out.append(problem)
    return out
