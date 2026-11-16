"""The token of an exchange (`oauth2_client_credentials`, `oauth2_jwt_bearer`): kept in the memory of the process, and
read from the token endpoint.

The cache is indexed by a fingerprint (a SHA-256 of the connector, the credential, the token address and the scope),
never by a secret in clear. A token serves until a minute before its expiry (`expires_in`, else the description's
`expires_in_default`, else no expiry); a 401 of the vendor asks a fresh one, once (`transport.respond`). A host with
several processes exchanges once per process: oto-core keeps its token in the account's row, ciphered.

The token endpoint is reached by the engine's transport (no redirect followed, its delay, a 429 replayed). Outside
2xx, only the OAuth error code of its body is read (`error`, RFC 6749 § 5.2), never the rest.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import requests

from . import transport
from .errors import TokenRefused, UnreadableAnswer
from .request import Request

#: A kept token is renewed when it expires in less than this.
EXPIRY_MARGIN_S = 60
#: An OAuth error code has only these characters; anything else is not read.
OAUTH_ERROR = re.compile(r"^[\x20\x21\x23-\x5B\x5D-\x7E]{1,64}$")

# Read at the call, never bound at import: a test replaces it.
_now: Callable[[], float] = time.time


def now() -> float:
    """The wall clock, in seconds since the epoch."""
    return _now()


@dataclass(frozen=True)
class Token:
    value: str
    expires_at: Optional[float]

    def usable(self) -> bool:
        return self.expires_at is None or self.expires_at - EXPIRY_MARGIN_S > now()


class TokenCache:
    """The tokens of the process, by fingerprint."""

    def __init__(self) -> None:
        self._tokens: dict[str, Token] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Token]:
        """The kept token of `key`, if it is still usable."""
        with self._lock:
            token = self._tokens.get(key)
        return token if token is not None and token.usable() else None

    def put(self, key: str, token: Token) -> None:
        with self._lock:
            self._tokens[key] = token

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._tokens)


TOKENS = TokenCache()


def cache_key(fingerprint: str, token_url: str, scope: str) -> str:
    """The key of a token: the credential's fingerprint, where it is exchanged and for what scope, hashed again."""
    return hashlib.sha256("\n".join((fingerprint, token_url, scope)).encode("utf-8")).hexdigest()


def expiry(answer: dict, default: Optional[int], at: float) -> Optional[float]:
    """When a token expires: `expires_in` of the answer (seconds, a number or a numeric text), else `default`, else
    never."""
    given = answer.get("expires_in")
    if isinstance(given, str):
        try:
            given = float(given)
        except ValueError:
            given = None
    if isinstance(given, bool) or not isinstance(given, (int, float)) or not given > 0 or given != given:
        given = default
    return None if given is None else at + given


def oauth_error(body: Any) -> Optional[str]:
    """The OAuth error code of a refusal of the token endpoint, `None` when unreadable."""
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) and OAUTH_ERROR.match(code) else None


def exchange(session: requests.Session, target: transport.Target, request: Request, *,
             refused: Callable[[int, Optional[str]], TokenRefused], expires_in_default: Optional[int] = None) -> Token:
    """The token the endpoint answers to `request`. Outside 2xx: `refused(status, error)`, the OAuth error code read
    from its body (a redirect included, never followed); a success without a non-empty `access_token`:
    `UnreadableAnswer`."""
    at = now()
    response = transport.respond(session, target, request)
    if not 200 <= response.status_code < 300:
        raise refused(response.status_code, oauth_error(transport.read_error_body(target, response)))
    answer = transport.read_json(target, response)
    token = answer.get("access_token") if isinstance(answer, dict) else None
    if not isinstance(token, str) or not token:
        raise UnreadableAnswer(f"{target.label} sent an unreadable token answer. Retry later.")
    return Token(value=token, expires_at=expiry(answer, expires_in_default, at))
