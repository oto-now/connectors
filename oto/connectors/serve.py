"""Serving a described function, in pure functions that a host calls around its client.

They read a description as `oto.connectors.descriptions.load` returns it, with the semantics of the format
(docs/conception/signal-de-refus.md, docs/conception/sortie.md, docs/conception/cout-et-solde.md) and of the oto 2 host
package (`errorTable`, `shaped`, `itemsOf`, `pageCount`, `nextCursor`, `probeAccount`):

- `refusal`: the named refusal an answer of the vendor selects, from the function's refusals under a status, then the
  connector's error table ;
- `shaped`, `items_of`, `result`: what the caller receives of the answers, `output.strip` then `output.projection`, on
  the answer or on each element of `output.items` ;
- `page_count`, `next_cursor`, `call_arguments`: the pages a call reads and the arguments of each ;
- `probe_health`: whether the answer of the probe says a healthy account.

Nothing is called here, no I/O: the host calls its client, these functions read what it sends and receives.
"""
from __future__ import annotations

from typing import Any, Optional

from .cost import _finite

#: The arguments that pagination adds to the served input (`all_pages`, `max_pages`): they never reach the client.
PAGINATION_ARGUMENTS = ("all_pages", "max_pages")


class ServeError(ValueError):
    """An answer the description says cannot be: a list declared by `output.items` that is not a list."""


class _Absent:
    def __repr__(self) -> str:
        return "ABSENT"


#: The value at a path that leads nowhere — distinct from `None`, a JSON null that is there.
ABSENT: Any = _Absent()


def function(description: dict, name: str) -> dict:
    """The function `name` of the description; `KeyError` naming it when the description has none."""
    for candidate in description["functions"]:
        if candidate["name"] == name:
            return candidate
    raise KeyError(f"{description['connector']['name']}: no function {name!r}")


def value_at(value: Any, path: str) -> Any:
    """The value at a dotted path, a list index as digits; `ABSENT` when the path leads nowhere."""
    current = value
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return ABSENT
    return current


def non_empty(value: Any) -> bool:
    """Neither absent, nor null, nor an empty text, list or object (`expect` and the probe)."""
    if value is ABSENT or value is None or value == "":
        return False
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


# --- the refusal ---------------------------------------------------------------------------------------------------

def _holds(condition: dict, body: Any) -> bool:
    """A condition on the body: `equals`, same type and exact value (`190` is not `"190"`, `True` is not `1`);
    `contains`, a text that contains one of the texts, both lower-cased (`str.lower`, as `toLowerCase`). A body that is
    not JSON (raw text) has nothing at a path."""
    value = value_at(body, condition["path"]) if isinstance(body, (dict, list)) else ABSENT
    if "equals" in condition:
        return any(type(value) is type(expected) and value == expected for expected in condition["equals"])
    return isinstance(value, str) and any(text.lower() in value.lower() for text in condition["contains"])


def _statuses(entry: dict) -> list[int]:
    status = entry["status"]
    return status if isinstance(status, list) else [status]


def refusal(description: dict, fn: dict, status: int, body: Any) -> Optional[dict]:
    """The named refusal that an answer of `status` with `body` (parsed JSON, or raw text) selects: the function's
    refusals whose `when` is a status first, then the connector's table; the first entry whose status covers the answer
    and whose condition on the body holds, when it has one. `{code, message, retryable, marks_account}` (`retryable`
    false and `marks_account` `None` when the entry does not say), or `None` when no entry applies."""
    own = [{**r, "status": r["when"]} for r in fn.get("refusals", []) if isinstance(r["when"], int)]
    for entry in own + description["connector"]["errors"]:
        if status in _statuses(entry) and ("body" not in entry or _holds(entry["body"], body)):
            return {"code": entry["code"], "message": entry["message"], "retryable": entry.get("retryable", False),
                    "marks_account": entry.get("marks_account")}
    return None


# --- the output ----------------------------------------------------------------------------------------------------

def _without(value: Any, keys: list[str]) -> Any:
    """`value` without the key at the end of `keys`, copied where it changes, never modified in place: a list is
    traversed, the rest of the path holding for each element; an absent, null or scalar value has nothing to remove; a
    key that is there goes whatever its value. A segment is never an index."""
    if isinstance(value, list):
        return [_without(element, keys) for element in value]
    if not isinstance(value, dict) or keys[0] not in value:
        return value
    if len(keys) == 1:
        return {key: inner for key, inner in value.items() if key != keys[0]}
    return {**value, keys[0]: _without(value[keys[0]], keys[1:])}


def shaped(record: Any, output: Optional[dict]) -> Any:
    """A record shaped by `output` (an element of `items`, or the whole answer): each path of `strip` removed, then only
    the first-level keys of `projection` kept. The record received is not modified."""
    output = output or {}
    for path in output.get("strip", []):
        record = _without(record, path.split("."))
    projection = output.get("projection")
    if projection is None or not isinstance(record, dict):
        return record
    return {key: inner for key, inner in record.items() if key in projection}


def items_of(fn: dict, answers: list) -> list:
    """The shaped elements of the list at `output.items` of each answer, in order; `ServeError` for an answer without
    its list (an unexpected answer, never read as empty)."""
    output = fn["output"]
    path = output["items"]
    items: list = []
    for answer in answers:
        found = value_at(answer, path)
        if not isinstance(found, list):
            raise ServeError(f"{fn['name']}: answer without its list {path}")
        items += [shaped(element, output) for element in found]
    return items


def result(fn: dict, answers: list, next_cursor: Any = None) -> dict:
    """What the caller receives of a call's answers (one per page read): with `output.items`, `{items, next_cursor,
    pages}`; otherwise the first answer shaped, a JSON object as is, `{}` for no content, `{"value": …}` for any other
    value."""
    if not answers:
        raise ServeError(f"{fn['name']}: no answer to read")
    output = fn.get("output")
    if output is not None and "items" in output:
        return {"items": items_of(fn, answers), "next_cursor": next_cursor, "pages": len(answers)}
    value = shaped(answers[0], output)
    if value is None:
        return {}
    return value if isinstance(value, dict) else {"value": value}


# --- pagination ----------------------------------------------------------------------------------------------------

def page_count(fn: dict, args: dict) -> int:
    """The pages a call reads at most: one; with `all_pages`, `max_pages` of the call bounded by the function's
    `max_pages` (that one when the call gives none)."""
    pagination = fn.get("pagination")
    if pagination is None or args.get("all_pages") is not True:
        return 1
    limit = pagination["max_pages"]
    wanted = args.get("max_pages", limit)
    if isinstance(wanted, bool) or not isinstance(wanted, int):
        wanted = limit
    return max(1, min(wanted, limit))


def next_cursor(fn: dict, answer: Any) -> Any:
    """The cursor of the next page, `None` at the last one (`more` false, or `next` absent, null or empty). A number is
    kept a number (the host package writes it as text in a query, where both read the same)."""
    pagination = fn.get("pagination")
    if pagination is None:
        return None
    if "more" in pagination and value_at(answer, pagination["more"]) is False:
        return None
    following = value_at(answer, pagination["next"])
    if isinstance(following, bool):
        return None
    if isinstance(following, int):
        return following
    return following if isinstance(following, str) and following != "" else None


def call_arguments(fn: dict, args: dict, cursor: Any = None) -> dict:
    """The arguments the client receives: the call's, without those that pagination adds (`all_pages`, `max_pages`),
    and, for a following page, the cursor in the function's `request_param`."""
    sent = {key: value for key, value in args.items() if key not in PAGINATION_ARGUMENTS}
    if cursor is not None:
        pagination = fn.get("pagination")
        if pagination is None:
            raise ServeError(f"{fn['name']}: a cursor for a function that does not paginate")
        sent[pagination["request_param"]] = cursor
    return sent


# --- the probe -----------------------------------------------------------------------------------------------------

def probe_health(description: dict, answer: Any) -> dict:
    """Whether the answer of the probe function says a healthy account: each path of `non_empty` non-empty, and the
    balance (`balance`), when declared, a finite JSON number at or above its threshold. `{healthy: True}` or
    `{healthy: False, reason}`, with `balance: {value, unit, min}` (`value` `None` when absent or not a number) when the
    connector declares one. A balance that cannot be read is never healthy."""
    connector = description["connector"]
    probe = connector["probe"]
    label = connector["label"]
    reason = None
    missing = next((path for path in probe.get("non_empty", []) if not non_empty(value_at(answer, path))), None)
    if missing is not None:
        reason = f"{label} answered without {missing}."
    balance = probe.get("balance")
    if balance is None:
        return {"healthy": True} if reason is None else {"healthy": False, "reason": reason}
    found = value_at(answer, balance["path"])
    value = found if _finite(found) else None
    read = {"value": value, "unit": balance["unit"], "min": balance["min"]}
    if reason is None:
        if value is None:
            reason = (f"{label} answered without its balance {balance['path']}." if found is ABSENT
                      else f"{label} answered a balance {balance['path']} that is not a number.")
        elif value < balance["min"]:
            reason = f"{label} balance is {value} {balance['unit']}, under {balance['min']} {balance['unit']}."
    health: dict = {"healthy": True} if reason is None else {"healthy": False, "reason": reason}
    health["balance"] = read
    return health
