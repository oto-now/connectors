"""The exchange `oauth2_client_credentials`: the client id and secret of the account for an access token.

One `POST` to `token_url` (or the address `token_urls` gives for a `choice` setting; a template on the settings, guarded
when a `url` or `text` setting enters it), `grant_type=client_credentials`, then `scope` when declared; the client id and
secret in the body (`client_auth: body`) or in HTTP Basic (`basic`); the body as a form (`token_request: form`) or JSON
(`json`). A refusal names neither field's value.
"""
from __future__ import annotations

from typing import Mapping, Optional

import requests

from . import tokens, transport
from .address import resolve
from .errors import TokenRefused
from .request import Form, Request, basic_header


def token_address(connector: dict, settings: Mapping[str, str]):
    """The token address of the exchange, resolved on the settings of the account."""
    auth = connector["auth"]
    return resolve(auth["token_urls"] if "token_urls" in auth else auth["token_url"], settings, connector)


def _refused(label: str):
    def refused(status: int, error: Optional[str]) -> TokenRefused:
        if status in (400, 401, 403):
            message = f"{label} refused the client credentials of this account: check its client id and secret."
        else:
            message = f"{label} answered the token request with an unexpected status ({status}). Retry later."
        return TokenRefused(message, status_code=status, error=error)
    return refused


def client_credentials_token(connector: dict, client_id: str, client_secret: str, settings: Mapping[str, str], *,
                             session: requests.Session, timeout_s: float, pace_key: str) -> tokens.Token:
    """A fresh token for the client id and secret."""
    auth = connector["auth"]
    address = token_address(connector, settings)
    pairs = [("grant_type", "client_credentials")]
    headers: dict[str, str] = {}
    if auth["client_auth"] == "basic":
        headers["authorization"] = basic_header(client_id, client_secret)
    else:
        pairs += [("client_id", client_id), ("client_secret", client_secret)]
    if auth.get("scope"):
        pairs.append(("scope", auth["scope"]))
    body = Form(tuple(pairs)) if auth["token_request"] == "form" else dict(pairs)
    target = transport.Target(label=connector["label"], base_url=address.url, timeout_s=timeout_s,
                              pace_key=f"token:{pace_key}", guarded=address.guarded)
    request = Request(method="POST", path="", query=(), headers=headers, body=body)
    return tokens.exchange(session, target, request, refused=_refused(connector["label"]),
                           expires_in_default=auth.get("expires_in_default"))
