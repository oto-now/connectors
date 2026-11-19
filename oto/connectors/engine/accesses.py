"""The access of a call, for a connector that opens by several (docs/conception/compte.md, « Plusieurs accès »).

A connector declares either one access, its `auth`, `credential` and `modes`, or several, `accesses`: each a `name`, a
`label` and its own `auth`, `credential` and `modes`, the functions, the address and the error table shared. An account
opens by one access, the one the host recorded with it. `view` gives the description as that access sees it: `accesses`
gone, its `auth`, `credential` and `modes` in their place, so that everything after reads one access, as for a
connector that declares a single one. The fabrique checks each access by the same view (`merged`).
"""
from __future__ import annotations

from typing import Optional

from .errors import EngineError

#: The keys of an access that take their place in the connector.
ACCESS_KEYS = ("auth", "credential", "modes")


class UnknownAccess(EngineError, ValueError):
    """The host named no access for a connector that declares several, one it does not declare, or one for a connector
    that declares a single access: nothing was sent."""


def merged(connector: dict, access: dict) -> dict:
    """The connector as one of its accesses sees it: without `accesses`, the access's `auth`, `credential` and `modes`
    in their place."""
    view = {key: value for key, value in connector.items() if key != "accesses"}
    view.update({key: access[key] for key in ACCESS_KEYS if key in access})
    return view


def view(description: dict, access: Optional[str]) -> dict:
    """The description as the account's access sees it; the description itself for a connector with a single access,
    which takes no `access`. `UnknownAccess` when the access is missing, undeclared, or given for a single access."""
    connector = description["connector"]
    declared = connector.get("accesses")
    if declared is None:
        if access is not None:
            raise UnknownAccess(f"{connector['name']}: access {access!r} is not declared: the connector opens by a "
                                "single access.")
        return description
    names = [entry["name"] for entry in declared]
    if access is None:
        raise UnknownAccess(f"{connector['name']}: the connector opens by several accesses ({', '.join(names)}): name "
                            "the one of the account.")
    chosen = next((entry for entry in declared if entry["name"] == access), None)
    if chosen is None:
        raise UnknownAccess(f"{connector['name']}: access {access!r} is not declared (it declares {', '.join(names)}).")
    return {**description, "connector": merged(connector, chosen)}
