"""The access level of a call (docs/conception/niveaux-d-acces.md), for a connector the engine signs.

A connector without `auth.access_levels` has one level, `full`: no level, or `full`, is accepted; any other raises.
With levels, the level is the one the host passes (its organisation's or team's choice), else `auth.default_level`,
else `full`; a level the description does not declare raises `UnknownAccessLevel`, never falls back to another.

A function is served at a level when each of its `scopes` is a scope of the level or implied by one (`scope_implies`);
otherwise it is refused before anything is sent, `Refused("access_level_denied")`, naming the level and the function.
For a service account (`oauth2_jwt_bearer`), the scopes of the level are the scope claim of the assertion: no consent,
so changing the level only changes what the next assertion asks for.
"""
from __future__ import annotations

from typing import Optional

from .errors import EngineError, Refused

FULL = "full"


class UnknownAccessLevel(EngineError, ValueError):
    """The host passed a level the description does not declare: nothing was sent."""


def resolve(connector: dict, access_level: Optional[str]) -> Optional[str]:
    """The level of the call, `None` for a connector without levels; `UnknownAccessLevel` for an undeclared one."""
    auth = connector["auth"]
    levels = auth.get("access_levels")
    if levels is None:
        if access_level not in (None, FULL):
            raise UnknownAccessLevel(f"{connector['name']}: access level {access_level!r} is not declared: the connector "
                                     "has no access levels.")
        return None
    level = access_level if access_level is not None else auth.get("default_level", FULL)
    if level not in levels:
        raise UnknownAccessLevel(f"{connector['name']}: access level {level!r} is not declared "
                                 f"(it declares {', '.join(sorted(levels))}).")
    return level


def scopes(connector: dict, level: Optional[str]) -> list[str]:
    """The scopes the level asks for; without levels, `auth.scopes`."""
    auth = connector["auth"]
    return list(auth["scopes"]) if level is None else list(auth["access_levels"][level]["scopes"])


def covered(connector: dict, level: str) -> set[str]:
    """The scopes of the level and what they imply (`scope_implies`, closed: one step says it all)."""
    implies = connector["auth"].get("scope_implies", {})
    own = scopes(connector, level)
    return set(own).union(*(implies.get(scope, []) for scope in own))


def refuse_unserved(connector: dict, fn: dict, level: Optional[str]) -> None:
    """`Refused("access_level_denied")` when the function is not served at the level; `full` filters nothing."""
    if level is None or level == FULL:
        return
    if not set(fn["scopes"]) <= covered(connector, level):
        raise Refused("access_level_denied", f"{connector['label']}: {fn['name']} is not allowed at the access level "
                                             f"{level!r} of this account; it needs a level that grants "
                                             f"{', '.join(fn['scopes'])}.")
