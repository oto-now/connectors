"""Twilio webhook signature (`X-Twilio-Signature`).

Twilio signs every webhook it sends with HMAC-SHA1, keyed by the auth token of
the account that owns the resource, over the full URL it called followed by
each POST parameter, sorted by name, as `name` + `value` with no separator.
The digest is base64-encoded. A parameter sent several times contributes each
of its values, in the order received, under its name.

Reference: https://www.twilio.com/docs/usage/webhooks/webhooks-security
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Iterable, Mapping, Sequence, Tuple, Union

Params = Union[Mapping[str, Union[str, Sequence[str]]], Iterable[Tuple[str, str]]]


def _pairs(params: Params) -> list[tuple[str, str]]:
    if isinstance(params, Mapping):
        out: list[tuple[str, str]] = []
        for key, value in params.items():
            if isinstance(value, (list, tuple)):
                out.extend((key, str(v)) for v in value)
            else:
                out.append((key, "" if value is None else str(value)))
        return out
    return [(str(k), "" if v is None else str(v)) for k, v in params]


def expected_signature(auth_token: str, url: str, params: Params = ()) -> str:
    """The signature Twilio would send for this URL and these POST parameters.

    `url` is the exact URL Twilio called, scheme, host, path and query included.
    For a GET webhook, pass no parameters: the query is already in the URL.
    """
    if not auth_token:
        raise ValueError("auth_token is required to compute a Twilio signature.")
    pairs = sorted(_pairs(params), key=lambda kv: kv[0])
    payload = url + "".join(k + v for k, v in pairs)
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def valid_signature(auth_token: str, url: str, params: Params, signature: str) -> bool:
    """True when `signature` (the `X-Twilio-Signature` header) matches.

    Constant-time comparison. A missing header or token is never valid.
    """
    if not signature or not auth_token:
        return False
    return hmac.compare_digest(expected_signature(auth_token, url, params), signature.strip())
