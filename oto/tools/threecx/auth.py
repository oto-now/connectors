"""3CX access token — two grants, one Bearer.

- **API client** (`client_id`/`client_secret`, created in the admin console,
  Integrations > API): `POST {base}/connect/token`, OAuth2
  `grant_type=client_credentials`. 3CX reserves API clients to some licences.
- **User account** (`username`/`password`): `POST
  {base}/webclient/api/Login/GetAccessToken`, the login of 3CX's own web client.
  The token carries that user's rights; an account with two-factor
  authentication enabled is refused.

- Secrets travel in the request body (`data=`/`json=`), never in the query
  string, and no `raise_for_status()` is called here: an exception message must
  never carry a credential.
- The cache is process-wide, keyed by a hash of the credential: a server builds
  one client per call, so a cache held by the instance would never be reused.
"""
from __future__ import annotations

import hashlib
import time
from typing import Optional

import requests

from ..common import UpstreamHTTPError
from ..common.url_guard import assert_public_https_url, refuse_redirect

_HTTP_TIMEOUT = (10, 30)

# {cred_key: (access_token, expires_at_epoch)}
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}

# OAuth2 error codes that mean "this credential does not authenticate".
_REFUS = {"invalid_grant", "invalid_client", "unauthorized_client", "invalid_scope"}


class ThreeCXAuthError(UpstreamHTTPError):
    """The PBX refused the credential.

    Always `status_code == 401` whatever the endpoint answered, so that a
    consumer routes it as a credential refusal, not as bad input.
    """

    def __init__(self, error: str):
        super().__init__(401, {"error": error}, service="3cx")


def cred_key(base_url: str, identity: str, secret: str) -> str:
    """Opaque, stable id of a credential — never a secret in clear."""
    return hashlib.sha256(f"{base_url}|{identity}|{secret}".encode()).hexdigest()


def _json(resp) -> Optional[dict]:
    try:
        payload = resp.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _client_token(base_url: str, client_id: str, client_secret: str,
                  http=requests) -> tuple[str, int]:
    assert_public_https_url(base_url)
    resp = http.post(
        f"{base_url}/connect/token",
        data={  # body, never params= — see module docstring
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=_HTTP_TIMEOUT,
        allow_redirects=False,
    )
    refuse_redirect(resp, service="3cx")
    payload = _json(resp)
    error = payload.get("error") if payload else None
    if resp.status_code >= 400:
        if resp.status_code == 401 or error in _REFUS:
            raise ThreeCXAuthError(error or "unauthorized")
        # Only the OAuth error code is kept: the raw body is never echoed.
        raise UpstreamHTTPError(resp.status_code, {"error": error or "token endpoint error"},
                                service="3cx")
    if not payload or not payload.get("access_token"):
        raise UpstreamHTTPError(502, {"error": "no access_token in token response"},
                                service="3cx")
    return payload["access_token"], int(payload.get("expires_in") or 3600)


def _user_token(base_url: str, username: str, password: str,
                http=requests) -> tuple[str, int]:
    assert_public_https_url(base_url)
    resp = http.post(
        f"{base_url}/webclient/api/Login/GetAccessToken",
        json={"Username": username, "Password": password, "SecurityCode": ""},
        timeout=_HTTP_TIMEOUT,
        allow_redirects=False,
    )
    refuse_redirect(resp, service="3cx")
    payload = _json(resp)
    if resp.status_code >= 400 and resp.status_code not in (401, 403):
        raise UpstreamHTTPError(resp.status_code, {"error": "login endpoint error"},
                                service="3cx")
    status = payload.get("Status") if payload else None
    if status != "AuthSuccess":
        if payload and payload.get("TwoFactorAuth"):
            raise ThreeCXAuthError("two-factor authentication is enabled on this account")
        raise ThreeCXAuthError(status or "unauthorized")
    token = payload.get("Token") or {}
    if not token.get("access_token"):
        raise UpstreamHTTPError(502, {"error": "no access_token in login response"},
                                service="3cx")
    return token["access_token"], int(token.get("expires_in") or 3600)


def get_access_token(base_url: str, *, key: str, client_id: Optional[str] = None,
                     client_secret: Optional[str] = None, username: Optional[str] = None,
                     password: Optional[str] = None,
                     session: Optional[requests.Session] = None) -> str:
    """A valid access token for this credential, requested only when needed.

    `session`: the `requests.Session` the token request goes out through, when the
    consumer supplies one. Absent, module-level `requests`, as before."""
    http = session if session is not None else requests
    cached = _TOKEN_CACHE.get(key)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    if client_id:
        token, ttl = _client_token(base_url, client_id, client_secret or "", http)
    else:
        token, ttl = _user_token(base_url, username or "", password or "", http)
    _TOKEN_CACHE[key] = (token, time.time() + ttl)
    return token


def invalidate(key: str) -> None:
    """Forget the cached token of this credential (called on an upstream 401)."""
    _TOKEN_CACHE.pop(key, None)
