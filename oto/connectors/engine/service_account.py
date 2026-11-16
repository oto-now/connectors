"""The service account (`oauth2_jwt_bearer`, RFC 7523): the one secret field of the credential holds the JSON key file;
the engine reads it, signs an RS256 assertion and exchanges it at the description's fixed `token_url` for an access
token.

The key is read before anything is sent, and refused in this order (`Refused`, without a status, named like the common
cases `connectors/_cases/jwt_bearer.yaml`): `service_account_key_not_json` (not a JSON object),
`service_account_key_incomplete` (`client_email` or `private_key` absent or empty),
`service_account_key_foreign_token_uri` (a `token_uri` that is not `token_url`: a typed text never chooses where a
signed assertion goes), `service_account_key_unreadable` (not an RSA private key in a PKCS#8 PEM).

The assertion: header `{alg, typ, kid?}` and claims `{iss, scope, aud, iat, exp}`, `scope` the scopes of the account's
access level joined by spaces (`levels`; `auth.scopes` without levels), compact JSON in this order, UTF-8,
base64url without padding, then the RSASSA-PKCS1-v1_5 SHA-256 signature of `header.claims` (`google.auth.crypt`, the
`google` extra). It goes in the form body of one `POST`, never in a query. `invalid_grant` is the key revoked or the
account disabled (`TokenRefused`, `marks_account: credential_rejected`); any other refusal, a redirect included, marks
nothing. Neither the key nor the assertion appears in a message.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Optional

import requests

from . import tokens, transport
from .errors import EngineError, Refused, TokenRefused
from .request import Form, Request

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"
#: The lifetime of an assertion, in seconds (Google's maximum).
ASSERTION_LIFETIME_S = 3600
# The PEM label of a PKCS#8 key, never written whole: secret guards of public repositories refuse a private key header.
PEM_HEAD = "-----BEGIN " + "PRIVATE KEY" + "-----\n"


def _key_refused(label: str, code: str, why: str) -> Refused:
    return Refused(code, f"The {label} service account key of this account {why}: refused. Ask whoever manages the "
                         "account to paste its JSON key file again.")


def _rsa_signer():
    try:
        from google.auth import crypt
    except ImportError as missing:
        raise EngineError("auth oauth2_jwt_bearer needs the google extra of the library (oto-core[google]).") from missing
    return crypt.RSASigner


def read_key(auth: dict, text: Any, label: str) -> tuple[dict, Any]:
    """The key file read, and its signer; `Refused` in the order of the common cases."""
    try:
        key = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        key = None
    if not isinstance(key, dict):
        raise _key_refused(label, "service_account_key_not_json", "is not a JSON object")
    if any(not isinstance(key.get(field), str) or not key[field] for field in ("client_email", "private_key")):
        raise _key_refused(label, "service_account_key_incomplete", "lacks client_email or private_key")
    if "token_uri" in key and key["token_uri"] != auth["token_url"]:
        raise _key_refused(label, "service_account_key_foreign_token_uri", "names another token address (token_uri)")
    unreadable = _key_refused(label, "service_account_key_unreadable",
                              "has a private_key that is not an RSA key in PKCS#8 PEM")
    if not key["private_key"].startswith(PEM_HEAD):
        raise unreadable
    key_id = key.get("private_key_id") if isinstance(key.get("private_key_id"), str) and key.get("private_key_id") else None
    try:
        signer = _rsa_signer().from_string(key["private_key"], key_id)
        # A key of another type may load: signing is what proves it RSA (before anything is sent).
        signer.sign(b"probe")
    except (ValueError, TypeError, AttributeError):
        raise unreadable from None
    return key, signer


def _segment(value: dict) -> str:
    return _b64url(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def assertion(auth: dict, key: dict, signer: Any, at: int, scopes: list[str]) -> str:
    """The signed assertion, `at` in seconds since the epoch, claiming `scopes` (those of the account's access level,
    `levels.scopes`)."""
    header = {"alg": "RS256", "typ": "JWT"}
    key_id: Optional[str] = key.get("private_key_id") or None
    if key_id:
        header["kid"] = key_id
    claims = {"iss": key["client_email"], "scope": " ".join(scopes), "aud": auth["token_url"], "iat": at,
              "exp": at + ASSERTION_LIFETIME_S}
    signing_input = f"{_segment(header)}.{_segment(claims)}"
    return f"{signing_input}.{_b64url(signer.sign(signing_input.encode('ascii')))}"


def _refused(label: str):
    def refused(status: int, error: Optional[str]) -> TokenRefused:
        if error == "invalid_grant":
            return TokenRefused(f"{label} refused the service account key of this account (invalid_grant): the key was "
                                "revoked or deleted, or the service account disabled.", status_code=status, error=error,
                                marks_account="credential_rejected")
        said = "" if error is None else f", {error}"
        return TokenRefused(f"{label} refused the token request of this service account (status {status}{said}). Check "
                            "the rights granted to it, or retry later.", status_code=status, error=error)
    return refused


def service_account_token(connector: dict, key_text: str, *, scopes: list[str], session: requests.Session,
                          timeout_s: float, pace_key: str) -> tokens.Token:
    """A fresh token for the key: read, signed for `scopes`, exchanged."""
    auth = connector["auth"]
    label = connector["label"]
    if "{" in auth["token_url"] or not auth["token_url"].startswith("https://"):
        raise EngineError(f"{connector['name']}: auth oauth2_jwt_bearer needs a fixed https token_url.")
    key, signer = read_key(auth, key_text, label)
    jwt = assertion(auth, key, signer, int(tokens.now()), scopes)
    target = transport.Target(label=label, base_url=auth["token_url"], timeout_s=timeout_s, pace_key=f"token:{pace_key}")
    request = Request(method="POST", path="", query=(), headers={},
                      body=Form((("grant_type", GRANT_TYPE), ("assertion", jwt))))
    return tokens.exchange(session, target, request, refused=_refused(label))
