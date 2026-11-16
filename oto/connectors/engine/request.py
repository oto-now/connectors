"""The request of a page, composed from `call` and the arguments given, as oto-core's engine composes it.

- The path: each `{param}` replaced by its argument as text, percent-encoded whole (`encodeURIComponent`).
- The headers, names in lower case: the connector's, then the function's constants, then the arguments given; the
  authentication is set last, nothing replaces it (`sign`).
- The query, in order: the function's constants, then the arguments given in the order of `call.query` (a list as
  `connector.query_arrays` says: `repeat`, `brackets` or `comma`), then the authentication's (`api_key` in query).
- The body, JSON whatever the method: the argument named by `call.body`, whole and as given; or the body constant,
  copied, then each argument given at its path in the body table, nested or not, the parent objects created as needed.
- `call.body_list`: the body built, whole or in a table, sent as the only element of a JSON list (rule 23).
- `call.encode`: an argument serialised as JSON text before it is placed.

Only the arguments given are placed: an absent argument places nothing, a `null` is sent (as `null` in a body, as the
text `null` elsewhere). Nothing here reads a secret: the signature arrives composed (`auth.Signature`).
"""
from __future__ import annotations

import base64
import copy
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import quote

from .errors import EngineError, InvalidArguments

PARAM = re.compile(r"\{([^{}]+)\}")

#: Marks a request without a body (`None` is a JSON null, which a body may be).
NO_BODY: Any = type("NoBody", (), {"__repr__": lambda self: "NO_BODY"})()


@dataclass(frozen=True)
class Signature:
    """What authentication sets on a request: headers (names in lower case) and query pairs."""

    headers: dict = field(default_factory=dict)
    query: tuple = ()


@dataclass(frozen=True)
class Form:
    """A form-encoded body (`application/x-www-form-urlencoded`), as ordered pairs: the body of a token request."""

    pairs: tuple


@dataclass(frozen=True)
class Request:
    """A request under the base address: `path` already encoded, `query` as ordered pairs (a name may repeat),
    `headers` complete, authentication included, `body` a JSON value, a `Form`, or `NO_BODY`."""

    method: str
    path: str
    query: tuple
    headers: dict
    body: Any = NO_BODY


def basic_header(username: str, password: str) -> str:
    """The HTTP Basic header of a user name and a password, UTF-8."""
    return "Basic " + base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")


def _number(value: float) -> str:
    """A number as JavaScript writes it (`String(n)`): an integer without a decimal point, positional between 1e-6
    and 1e21, otherwise `1.5e-7`, `1e+21`."""
    if value != value or value in (float("inf"), float("-inf")):
        raise InvalidArguments("a number that is not finite cannot be sent")
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    if 1e-6 <= abs(value) < 1e21:
        return format(Decimal(repr(value)), "f")
    mantissa, exponent = repr(value).split("e")
    return f"{mantissa}e{'-' if exponent.startswith('-') else '+'}{int(exponent.lstrip('+-'))}"


def json_text(value: Any) -> str:
    """Compact JSON, non-ASCII kept (`JSON.stringify`)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def text(value: Any) -> str:
    """A value in a path, a query or a header: a text as is, a number or a boolean written, anything else as JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _number(value)
    return json_text(value)


def body_entries(table: dict, at: tuple = ()) -> list[tuple[tuple, str]]:
    """The entries of a body table, at any depth: the path of API-side keys and the argument, in order."""
    entries: list[tuple[tuple, str]] = []
    for key, value in table.items():
        if isinstance(value, str):
            entries.append(((*at, key), value))
        else:
            entries.extend(body_entries(value, (*at, key)))
    return entries


def _placed(call: dict, args: dict, argument: str) -> Any:
    """An argument as it is placed: serialised as JSON when `encode` says so."""
    if (call.get("encode") or {}).get(argument) == "json":
        return json_text(args[argument])
    return args[argument]


def _path(fn: dict, args: dict) -> str:
    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if args.get(name) is None:
            raise InvalidArguments(f"{fn['name']}: argument {name} is required.")
        return quote(text(args[name]), safe="!*'()")

    return PARAM.sub(substitute, fn["call"]["path"])


def _query(connector: dict, fn: dict, args: dict) -> list[tuple[str, str]]:
    call = fn["call"]
    pairs = [(name, text(value)) for name, value in ((call.get("constants") or {}).get("query") or {}).items()]
    arrays = connector.get("query_arrays")
    for name, argument in (call.get("query") or {}).items():
        if argument not in args:
            continue
        value = _placed(call, args, argument)
        if not isinstance(value, list):
            pairs.append((name, text(value)))
        elif arrays == "comma":
            pairs.append((name, ",".join(text(item) for item in value)))
        elif arrays == "brackets":
            pairs.extend((f"{name}[]", text(item)) for item in value)
        elif arrays == "repeat":
            pairs.extend((name, text(item)) for item in value)
        else:
            raise InvalidArguments(f"{fn['name']}: argument {argument} is a list, and {connector['name']} declares no "
                                   "query_arrays to place it in a query.")
    return pairs


def _headers(connector: dict, fn: dict, args: dict, signature: Signature) -> dict[str, str]:
    call = fn["call"]
    headers: dict[str, str] = {}
    for name, value in (connector.get("headers") or {}).items():
        headers[name.lower()] = value
    for name, value in ((call.get("constants") or {}).get("headers") or {}).items():
        headers[name.lower()] = value
    for name, argument in (call.get("headers") or {}).items():
        if argument in args:
            headers[name.lower()] = text(_placed(call, args, argument))
    headers.update(signature.headers)
    return headers


def _table_body(fn: dict, args: dict) -> dict:
    """The body constant, copied, then each argument given at its path; a parent the constant sets is an object (the
    fabrique refuses otherwise, rule 22): the contrary is a description the engine will not send."""
    call = fn["call"]
    body = copy.deepcopy((call.get("constants") or {}).get("body") or {})
    for path, argument in body_entries(call.get("body") or {}):
        if argument not in args:
            continue
        parent = body
        for key in path[:-1]:
            parent = parent.setdefault(key, {})
            if not isinstance(parent, dict):
                raise EngineError(f"{fn['name']}: body {'.'.join(path)} lies under a constant that is not an object.")
        parent[path[-1]] = _placed(call, args, argument)
    return body


def _built_body(fn: dict, args: dict) -> Any:
    call = fn["call"]
    whole = call.get("body")
    if isinstance(whole, str):
        if not isinstance(args.get(whole), dict):
            raise InvalidArguments(f"{fn['name']}: argument {whole} is required.")
        return copy.deepcopy(args[whole])
    if whole or "body" in (call.get("constants") or {}):
        return _table_body(fn, args)
    return NO_BODY


def _body(fn: dict, args: dict) -> Any:
    """The body built, in a list of one when `call.body_list` says so (the fabrique refuses it without a body, rule
    23: the contrary is a description the engine will not send)."""
    body = _built_body(fn, args)
    if fn["call"].get("body_list") is not True:
        return body
    if body is NO_BODY:
        raise EngineError(f"{fn['name']}: call.body_list without a body.")
    return [body]


def build(connector: dict, fn: dict, args: dict, signature: Optional[Signature] = None) -> Request:
    """The request of one page for the arguments given (pagination's own already removed), signed by `signature`."""
    signature = signature or Signature()
    return Request(
        method=fn["call"]["method"],
        path=_path(fn, args),
        query=tuple(_query(connector, fn, args)) + tuple(signature.query),
        headers=_headers(connector, fn, args, signature),
        body=_body(fn, args),
    )
