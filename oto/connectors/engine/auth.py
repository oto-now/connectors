"""The authentication of a call, set on each request from the credential the caller passes.

- `none`: nothing is set.
- `api_key`: in a header (`name`, its `prefix` before the key) or in the query (`name`); a key in the query is masked in
  every message, as any secret of the call (`secrets`).
- `bearer`: `Authorization: Bearer <token>`.
- `basic`: `Authorization: Basic base64(username:password)`, UTF-8; without `username`, an empty user name.
- `oauth2_client_credentials` and `oauth2_jwt_bearer`: `Authorization: Bearer <token>`, the token exchanged
  (`client_credentials`, `service_account`) and kept in the memory of the process (`tokens`), by scope too: a service
  account signs for the scopes of the account's access level (`levels`); a 401 of the vendor is replayed once on a
  fresh token (`renews`).

The credential is a mapping of the connector's credential fields to their values (the fields `holder: host` included,
which the host merges in): the engine reads no secret anywhere else. Each field the authentication reads is required,
`MissingCredential` naming it.
"""
from __future__ import annotations

import hashlib
import json
from typing import Mapping, Optional

import requests

from oto.tools.common.credentials import require

from . import levels, tokens
from .client_credentials import client_credentials_token, token_address
from .errors import Unsupported
from .request import Signature, basic_header
from .service_account import service_account_token


def fingerprint(connector: dict, credential: Mapping[str, str]) -> str:
    """A stable fingerprint of a credential of a connector, the key of what the process keeps for it (a token, its
    pace): a SHA-256, never the secret itself."""
    material = json.dumps([connector["name"], sorted(credential.items())], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class Signer:
    """The authentication of one call: `sign` gives the signature of a request; `renews`, whether a 401 is replayed
    once on a fresh signature; `secrets`, the values masked in any message."""

    renews = False

    def __init__(self, connector: dict, credential: Mapping[str, str]):
        self.connector = connector
        self.auth = connector["auth"]
        self.credential = dict(credential)

    def field(self, name: str) -> str:
        """The value of a credential field the authentication reads; `MissingCredential` when absent or empty."""
        return require(self.credential.get(name), name)

    @property
    def secrets(self) -> list[str]:
        """The values of the secret fields of the credential, longest first."""
        secret = {field["name"] for field in self.connector.get("credential", []) if field.get("secret")}
        values = {value for name, value in self.credential.items() if name in secret and isinstance(value, str) and value}
        return sorted(values, key=len, reverse=True)

    def sign(self, fresh: bool = False) -> Signature:  # noqa: ARG002 — `fresh` matters to an exchange only
        auth = self.auth
        kind = auth["kind"]
        if kind == "none":
            return Signature()
        if kind == "bearer":
            return Signature(headers={"authorization": f"Bearer {self.field(auth['token'])}"})
        if kind == "basic":
            username = self.field(auth["username"]) if "username" in auth else ""
            return Signature(headers={"authorization": basic_header(username, self.field(auth["password"]))})
        if kind == "api_key":
            key = self.field(auth["key"])
            if auth["in"] == "query":
                return Signature(query=((auth["name"], key),))
            return Signature(headers={auth["name"].lower(): auth.get("prefix", "") + key})
        raise Unsupported(f"{self.connector['name']}: auth {kind} is not run by the Python engine.")


class ExchangeSigner(Signer):
    """The authentication of an exchange: a Bearer token, kept by fingerprint of the credential, renewed a minute before
    its expiry, or fresh after a 401 (`sign(fresh=True)`)."""

    renews = True

    def __init__(self, connector: dict, credential: Mapping[str, str], *, settings: Mapping[str, str],
                 session: requests.Session, timeout_s: float, level: Optional[str]):
        super().__init__(connector, credential)
        self.settings = settings
        self.level = level
        self.session = session
        self.timeout_s = timeout_s
        self.print = fingerprint(connector, self.credential)

    def _key(self) -> str:
        auth = self.auth
        if auth["kind"] == "oauth2_jwt_bearer":
            return tokens.cache_key(self.print, auth["token_url"], " ".join(levels.scopes(self.connector, self.level)))
        return tokens.cache_key(self.print, token_address(self.connector, self.settings).url, auth.get("scope", ""))

    def _exchange(self) -> tokens.Token:
        auth = self.auth
        common = {"session": self.session, "timeout_s": self.timeout_s, "pace_key": self.print}
        if auth["kind"] == "oauth2_jwt_bearer":
            return service_account_token(self.connector, self.field(auth["key"]),
                                         scopes=levels.scopes(self.connector, self.level), **common)
        return client_credentials_token(self.connector, self.field(auth["client_id"]), self.field(auth["client_secret"]),
                                        self.settings, **common)

    def sign(self, fresh: bool = False) -> Signature:
        key = self._key()
        kept: Optional[tokens.Token] = None if fresh else tokens.TOKENS.get(key)
        if kept is None:
            kept = self._exchange()
            tokens.TOKENS.put(key, kept)
        return Signature(headers={"authorization": f"Bearer {kept.value}"})


EXCHANGES = ("oauth2_client_credentials", "oauth2_jwt_bearer")


def signer(connector: dict, credential: Mapping[str, str], *, settings: Mapping[str, str],
           session: requests.Session, timeout_s: float, level: Optional[str] = None) -> Signer:
    """The signer of the connector's authentication, for this credential; an exchange reaches its token endpoint by
    `session`, under the connector's delay `timeout_s`; a service account claims the scopes of `level`
    (`levels.resolve`)."""
    if connector["auth"]["kind"] in EXCHANGES:
        return ExchangeSigner(connector, credential, settings=settings, session=session, timeout_s=timeout_s,
                              level=level)
    return Signer(connector, credential)
