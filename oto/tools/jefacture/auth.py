"""jefacture.com auth — OAuth 2.0 of the Banqup platform, region France.

jefacture.com runs on the Banqup platform, French region: one authorization
server, `https://api.btx.fr.banqup.com/oauth2`. The application is the one of
the publisher that consumes the lib, created on the Banqup developer portal:
its API key is the `client_id`, with its `client_secret`, both passed as
arguments.

Two ways to obtain an access token:

- **authorization code** (`authorize_url`, `exchange_code`, `refresh`) — a
  person signs in to jefacture.com and consents, once, to the application
  reading the spaces they reach; the token acts with their rights. The
  redirect URI must have been activated beforehand for the application
  (`POST /oauth2/activate_redirect`, outside this module). Only `openid` is
  asked for: what the token reads is decided by the API products enabled on
  the application and by the rights of the person, not by a scope.
  ⚠️ `refresh` returns a NEW refresh token, which replaces the old one: the
  caller must store it.
- **client credentials** (`client_credentials_token`) — the application acts
  on its own, on the spaces whose administrator has added its App ID under
  « Applications externes ». No refresh token: ask again when it expires.

Same guards as the lib's other auth modules: secrets go in **`data=`**, never
in `params=`, no redirect is followed, and no `raise_for_status()` (its
message embeds the URL). ⚠️ Every request carries `USER_AGENT`: the platform's
firewall answers `/oauth2/token` with an HTML 403 when the User-Agent is the
default one of `requests` (measured: same body, explicit UA → OAuth answer).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import requests

from ..common.credentials import require
from ..http.client import refuse_redirect

_HTTP_TIMEOUT = (10, 30)  # (connect, read)
OAUTH_BASE = "https://api.btx.fr.banqup.com/oauth2"
AUTHORIZE_URL = f"{OAUTH_BASE}/auth"
TOKEN_URL = f"{OAUTH_BASE}/token"
REFRESH_URL = f"{OAUTH_BASE}/refresh"
CLIENT_CREDENTIALS_URL = f"{OAUTH_BASE}/client_credential/gettoken"
SCOPES = ("openid",)
# Explicit User-Agent: `python-requests/*` is refused (HTML 403) on the token endpoint.
USER_AGENT = "oto-jefacture/1 (+https://oto.cx)"
# Lifetime announced when a token response gives none.
DEFAULT_EXPIRES_IN = 3600

# OAuth errors meaning "the authorization is dead, sign in again".
_GRANT_MORT = ("invalid_grant",)


class JefactureAuthError(ValueError):
    """Refusal by the authorization server. Carries a `status_code` (the
    `UpstreamHTTPError` contract) and the OAuth `code` when readable."""

    def __init__(self, message: str, *, status_code: int = 401, code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class JefactureGrantExpired(JefactureAuthError):
    """The person's authorization is no longer valid (revoked, expired): they
    must sign in again. The application's configuration is not at fault."""


@dataclass(frozen=True)
class Grant:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """The URL of the jefacture.com sign-in page (a QR code scanned with the
    mobile app, then the consent on first sign-in)."""
    params = {
        "client_id": require(client_id, "JEFACTURE_CLIENT_ID"),
        "response_type": "code",
        "redirect_uri": require(redirect_uri, "redirect_uri"),
        "scope": " ".join(SCOPES),
        "state": require(state, "state"),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _token(url: str, data: dict, *, keep_refresh: str = "") -> Grant:
    # Never follow a redirect: a 307/308 would replay the body — client secret,
    # code or refresh token — to the host named by `Location`.
    resp = requests.post(url, data=data, headers={"User-Agent": USER_AGENT},
                         timeout=_HTTP_TIMEOUT, allow_redirects=False)
    refuse_redirect(resp)
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if resp.status_code >= 400 or "access_token" not in payload:
        error = str(payload.get("error") or "")
        description = str(payload.get("error_description") or payload.get("detail")
                          or payload.get("title") or "")
        detail = description.splitlines()[0] if description else (error or "response without a token")
        status = resp.status_code if resp.status_code >= 400 else 401
        cls = JefactureGrantExpired if error in _GRANT_MORT else JefactureAuthError
        raise cls(f"jefacture.com refused the token request (HTTP {resp.status_code}): {detail}",
                  status_code=status, code=error or None)
    return Grant(access_token=payload["access_token"],
                 refresh_token=payload.get("refresh_token") or keep_refresh,
                 expires_in=int(payload.get("expires_in") or DEFAULT_EXPIRES_IN),
                 scope=str(payload.get("scope") or ""))


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> Grant:
    """The code brought back by the browser in exchange for a `Grant` (with
    refresh token). `redirect_uri` is the one of the `authorize_url` call."""
    return _token(TOKEN_URL, {  # ⚠️ `data=`, JAMAIS `params=`
        "grant_type": "authorization_code",
        "client_id": require(client_id, "JEFACTURE_CLIENT_ID"),
        "client_secret": require(client_secret, "JEFACTURE_CLIENT_SECRET"),
        "code": require(code, "code"),
        "redirect_uri": require(redirect_uri, "redirect_uri"),
    })


def refresh(client_id: str, client_secret: str, refresh_token: str) -> Grant:
    """A fresh `Grant`, from the platform's refresh endpoint. ⚠️ Its
    `refresh_token` replaces the one passed in: store it."""
    token = require(refresh_token, "JEFACTURE_REFRESH_TOKEN")
    return _token(REFRESH_URL, {  # ⚠️ `data=`, JAMAIS `params=`
        "grant_type": "refresh_token",
        "client_id": require(client_id, "JEFACTURE_CLIENT_ID"),
        "client_secret": require(client_secret, "JEFACTURE_CLIENT_SECRET"),
        "refresh_token": token,
    }, keep_refresh=token)


def client_credentials_token(client_id: str, client_secret: str) -> Grant:
    """An access token of the application itself, for the spaces that added its
    App ID. Its `refresh_token` is empty: ask again when it expires."""
    return _token(CLIENT_CREDENTIALS_URL, {  # ⚠️ `data=`, JAMAIS `params=`
        "grant_type": "client_credentials",
        "client_id": require(client_id, "JEFACTURE_CLIENT_ID"),
        "client_secret": require(client_secret, "JEFACTURE_CLIENT_SECRET"),
    })
