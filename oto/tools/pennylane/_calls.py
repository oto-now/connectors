"""What the mixins of the Pennylane client share to say a call: the query of a
list, and the body of a write.

A list method of a mixin takes the arguments of its described function
(`filter` as a list of clauses, `sort`, `limit`, `cursor`) plus `max_pages`,
and reads every page from `cursor` on, as `fetch_all_pages` does. A write sends
only the arguments given: `None` means "not given", never JSON null; the few
fields where null means something (detach a transaction, clear a supplier
setting) take `UNSET` as their default instead, and `None` is then sent.
"""
from __future__ import annotations

import json
from typing import Optional


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


#: Default of an argument where `None` is a value to send (JSON null).
UNSET = _Unset()


def query(*, filter: Optional[list] = None, sort: Optional[str] = None,
          cursor: Optional[str] = None, **extra) -> dict:
    """The query of a list: `filter` serialised as the JSON array Pennylane
    takes, the other arguments as given; absent ones are not sent."""
    params = {key: value for key, value in extra.items() if value is not None}
    if filter is not None:
        if not isinstance(filter, list) or not filter:
            raise ValueError("filter: expected a non-empty list of {field, operator, value}")
        params["filter"] = json.dumps(filter)
    if sort is not None:
        params["sort"] = sort
    if cursor is not None:
        params["cursor"] = cursor
    return params


def body(**fields) -> dict:
    """The body of a write: the fields given. `None` is dropped; a value that
    is not `UNSET` (including `None` passed for a nullable field through
    `nullable`) is sent."""
    return {key: value for key, value in fields.items()
            if value is not None and value is not UNSET}


def nullable(**fields) -> dict:
    """The fields of a write where JSON null is a value: every field that is
    not `UNSET` is sent, `None` as null."""
    return {key: value for key, value in fields.items() if value is not UNSET}


def pages(client, endpoint: str, *, limit: Optional[int] = None,
          max_pages: Optional[int] = None, **arguments) -> list:
    """Every page of a list endpoint, from `cursor` when given, `max_pages` at
    most; `limit` is the page size (100 at most, Pennylane's bound)."""
    return client.fetch_all_pages(endpoint, query(**arguments), max_pages=max_pages,
                                  per_page=limit or 100)
