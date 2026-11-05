"""Microsoft Entra auth — signing in a PERSON (OAuth 2.0, authorization code),
single source for ALL of the lib's Microsoft clients.

The application is the one of the publisher that consumes the lib, registered once as
"multi-tenant" in its own Entra directory: `client_id` + `client_secret`,
passed as arguments. Each person authorizes it from THEIR OWN Microsoft 365 account;
the token returned acts with THEIR rights (DELEGATED permissions), no more, no less.
The permissions asked for are passed explicitly, by surface (`scopes`).

Four operations, one per authorization-server endpoint:
- `authorize_url` — the URL to send the browser to;
- `exchange_code` — the returned code in exchange for a `Grant`;
- `refresh` — a `refresh_token` in exchange for a fresh `Grant`. ⚠️ Entra ROTATES the
  refresh token: the one returned replaces the old one, the caller must store it;
- `admin_consent_url` — the URL a tenant administrator opens to consent, for the
  whole organization, to the scopes a person cannot consent to alone.

`tenant` picks the directory that signs the person in:
- `organizations` (default): any work or school account, in its home tenant;
- a tenant id (GUID) or a verified domain (`contoso.onmicrosoft.com`,
  `contoso.com`): that directory only. This is how a GUEST of the tenant (B2B,
  including a personal Microsoft account invited there) signs in to it —
  `organizations` would send them to their own home directory, or refuse them.
`common` and `consumers` are refused: they admit personal accounts in their own
directory, which has no SharePoint.

Same guards as the lib's other auth modules: the secret goes in **`data=`**, never in
`params=`, and no `raise_for_status()` (its message embeds the URL).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import requests

from ..common.credentials import require
from ..http.client import refuse_redirect
from . import scopes as _scopes

_HTTP_TIMEOUT = (10, 30)  # (connect, read)
_LOGIN = "https://login.microsoftonline.com"
DEFAULT_TENANT = "organizations"
# A tenant id (GUID), a verified domain, or `organizations`: nothing that could
# leave the path segment.
_TENANT_FORM = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_REFUSED_TENANTS = ("common", "consumers")

# AADSTS codes meaning "the authorization is dead, reconnect".
_GRANT_MORT = ("invalid_grant", "interaction_required")


class MicrosoftAuthError(ValueError):
    """Refusal by the Entra authorization server. Carries a `status_code` (the
    `UpstreamHTTPError` contract). `code` = the AADSTS code when readable
    (e.g. `AADSTS7000215`, invalid application secret), otherwise `None`."""

    def __init__(self, message: str, *, status_code: int = 401, code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class MicrosoftGrantExpired(MicrosoftAuthError):
    """The person's authorization is no longer valid (revoked, expired, password
    changed, MFA required again…): they must reconnect. The application's
    configuration is not at fault."""


@dataclass(frozen=True)
class Grant:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str


def _aadsts(description: str) -> Optional[str]:
    """`AADSTS7000215` from an Entra `error_description`, or `None`."""
    head = (description or "").split(":", 1)[0].strip()
    return head if head.startswith("AADSTS") else None


def _tenant(tenant: str) -> str:
    value = (tenant or "").strip()
    if value.lower() in _REFUSED_TENANTS:
        raise ValueError(
            f"tenant {value!r} is not accepted: SharePoint, Outlook and Teams for "
            "business need an organization's directory — pass 'organizations', a "
            "tenant id or a verified domain")
    if not _TENANT_FORM.match(value):
        raise ValueError(
            f"tenant {tenant!r} is not a tenant id, a domain or 'organizations'")
    return value


def _authority(tenant: str) -> str:
    """`https://login.microsoftonline.com/{tenant}/oauth2/v2.0`."""
    return f"{_LOGIN}/{_tenant(tenant)}/oauth2/v2.0"


def _scope_param(scopes: tuple[str, ...]) -> str:
    if not scopes:
        raise ValueError("scopes is empty: name the permissions to ask for")
    return " ".join(scopes)


def authorize_url(client_id: str, redirect_uri: str, state: str, *,
                  scopes: tuple[str, ...], login_hint: Optional[str] = None,
                  tenant: str = DEFAULT_TENANT) -> str:
    """The URL of the Microsoft sign-in dialog. `prompt=select_account`: a
    person with several accounts picks the right one instead of being signed
    in automatically with the one from their browser.

    `scopes`: the union of the surfaces wanted (see `scopes`), `offline_access`
    included to obtain a refresh token."""
    params = {
        "client_id": require(client_id, "MICROSOFT_CLIENT_ID"),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": _scope_param(scopes),
        "state": state,
        "prompt": "select_account",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{_authority(tenant)}/authorize?{urlencode(params)}"


def admin_consent_url(client_id: str, redirect_uri: str, state: str, *,
                      scopes: tuple[str, ...], tenant: str = DEFAULT_TENANT) -> str:
    """The URL a tenant administrator opens to grant `scopes` to the application
    for every person of their organization (e.g. `scopes.TEAMS_ADMIN`).

    Only permissions are sent, as full URIs (a short name is a Graph one; a scope of
    another resource, Power BI, keeps its own URI): the OpenID Connect scopes
    (`offline_access`, `openid`, `profile`, `email`) are not permissions an
    administrator grants, and are dropped. With `tenant="organizations"` the
    administrator signs in and their own tenant is the one consented.

    Microsoft redirects back to `redirect_uri` with, on success,
    `admin_consent=True&tenant={tenant id}&scope={granted scopes}&state=…`, or, on
    refusal or failure, `error=…&error_description=…&state=…`. Never a `code`:
    nothing is exchanged afterwards, each person still signs in with
    `authorize_url` to obtain their own token.
    """
    graph = tuple(s if "://" in s else _scopes.GRAPH + s
                  for s in scopes if s.lower() not in _scopes.OIDC)
    params = {
        "client_id": require(client_id, "MICROSOFT_CLIENT_ID"),
        "scope": _scope_param(graph),
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{_LOGIN}/{_tenant(tenant)}/v2.0/adminconsent?{urlencode(params)}"


def _token(data: dict, tenant: str) -> Grant:
    # Never follow a redirect: a 307/308 would replay the body — client secret, code or
    # refresh token — to the host named by `Location`.
    resp = requests.post(f"{_authority(tenant)}/token", data=data, timeout=_HTTP_TIMEOUT,
                         allow_redirects=False)
    refuse_redirect(resp)
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if resp.status_code >= 400 or "access_token" not in payload:
        description = str(payload.get("error_description") or "")
        # First line only: the rest carries a Trace ID and a timestamp.
        first_line = description.splitlines()[0] if description else ""
        error = str(payload.get("error") or "")
        detail = first_line or error or "response without a token"
        status = resp.status_code if resp.status_code >= 400 else 401
        cls = MicrosoftGrantExpired if error in _GRANT_MORT else MicrosoftAuthError
        raise cls(f"Entra refused the token request (HTTP {resp.status_code}): {detail}",
                  status_code=status, code=_aadsts(description))
    return Grant(access_token=payload["access_token"],
                 refresh_token=payload.get("refresh_token") or data.get("refresh_token") or "",
                 expires_in=int(payload.get("expires_in", 3600)),
                 scope=str(payload.get("scope") or ""))


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str, *,
                  scopes: tuple[str, ...], tenant: str = DEFAULT_TENANT) -> Grant:
    """The code brought back by the browser in exchange for a `Grant` (with refresh
    token). `scopes` and `tenant` are the ones of the `authorize_url` call."""
    return _token({  # ⚠️ `data=`, JAMAIS `params=`
        "grant_type": "authorization_code",
        "client_id": require(client_id, "MICROSOFT_CLIENT_ID"),
        "client_secret": require(client_secret, "MICROSOFT_CLIENT_SECRET"),
        "code": require(code, "code"),
        "redirect_uri": redirect_uri,
        "scope": _scope_param(scopes),
    }, tenant)


def refresh(client_id: str, client_secret: str, refresh_token: str, *,
            scopes: tuple[str, ...] = _scopes.REFRESH,
            tenant: str = DEFAULT_TENANT) -> Grant:
    """A fresh `Grant`. ⚠️ Its `refresh_token` replaces the one passed in: store it.

    `scopes` defaults to `scopes.REFRESH`: everything already consented, without
    naming a scope that was not (AADSTS65001). `tenant`: a refresh token obtained
    on a given tenant is renewed on THAT tenant — the caller keeps it with the
    token."""
    return _token({  # ⚠️ `data=`, JAMAIS `params=`
        "grant_type": "refresh_token",
        "client_id": require(client_id, "MICROSOFT_CLIENT_ID"),
        "client_secret": require(client_secret, "MICROSOFT_CLIENT_SECRET"),
        "refresh_token": require(refresh_token, "MICROSOFT_REFRESH_TOKEN"),
        "scope": _scope_param(scopes),
    }, tenant)
