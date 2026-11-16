"""The reading of the vendor's answer to a request of a described function.

Outside 2xx: the refusal the description names (`serve.refusal`: the function's refusals under a status, then the
connector's table; the first entry whose status covers the answer and whose body condition holds), raised as
`Refused`; a status it names nothing for raises `UpstreamHTTPError`, the library's error of a vendor's refusal, its body
masked of every secret of the call and its address reduced to scheme, host and path. Under 2xx: the JSON, unless an
entry with a body condition covers the status and holds (a refusal under a 200).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import requests

from oto.tools.common.errors import UpstreamHTTPError, retry_after_seconds

from .. import serve
from . import transport
from .errors import Refused
from .request import Request

MASK = "[secret]"


def masked(value: Any, secrets: list[str]) -> Any:
    """`value` with every secret replaced by `[secret]`, in every text it holds, keys included."""
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, MASK)
        return value
    if isinstance(value, list):
        return [masked(item, secrets) for item in value]
    if isinstance(value, dict):
        return {masked(key, secrets): masked(item, secrets) for key, item in value.items()}
    return value


def refused(description: dict, fn: dict, status: int, body: Any) -> Optional[Refused]:
    """The named refusal the answer selects, `None` when no entry applies."""
    entry = serve.refusal(description, fn, status, body)
    if entry is None:
        return None
    return Refused(entry["code"], entry["message"], status_code=status, retryable=entry["retryable"],
                   marks_account=entry["marks_account"])


def read(session: requests.Session, target: transport.Target, request: Request, *, description: dict, fn: dict,
         secrets: list[str], resign: Optional[Callable[[], Request]] = None) -> Any:
    """The JSON the vendor answers to the request, or the refusal its answer selects."""
    response = transport.respond(session, target, request, resign)
    status = response.status_code
    if not 200 <= status < 300:
        body = transport.read_error_body(target, response)
        refusal = refused(description, fn, status, body)
        if refusal is not None:
            raise refusal
        raise UpstreamHTTPError(status, masked(body, secrets), service=description["connector"]["name"],
                                method=request.method, url=transport.url_of(target, request),
                                retry_after=retry_after_seconds(response) if status == 429 else None)
    value = transport.read_json(target, response)
    refusal = refused(description, fn, status, value)
    if refusal is not None:
        raise refusal
    return value
