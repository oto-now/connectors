"""Client of the generic `http` connector — multi-method, multi-auth HTTP call.

A simple "HTTP node" (like n8n's HTTP Request node / a Zapier action):
injects the configured auth mode (bearer / key in header or query / basic /
oauth2 client-credentials, a login session, a visitor session) and forwards the
requested method (GET by default; POST / PUT / PATCH / DELETE with a JSON body, or a
raw body of a declared content type) to the target API.

SSRF protection does NOT live here: as in market products (Zapier,
Make, n8n, GPT Actions…), outbound traffic initiated by a tenant is filtered at
the platform's **network/egress** level, not by per-connector code.

Pure (`requests` only) — credential resolution and translation into MCP errors
live in the `oto_mcp/tools/http.py` adapter.
"""
from __future__ import annotations

import threading
import time
from abc import ABC
from collections.abc import Callable
from urllib.parse import urlsplit

import requests
from requests.auth import HTTPBasicAuth

from .session import SessionConfig, SessionLogin, VisitorSession

AUTH_MODES = ("bearer", "header", "query", "basic", "oauth2", "session", "visitor", "none")

# The modes whose agent only reads: the session mode logs in with a person's password,
# so its only POST is the login, never the agent's.
READ_ONLY_MODES = ("session",)
READ_METHODS = ("GET", "HEAD")


# --- auth modes ----------------------------------------------------------------

class UpstreamAuth(ABC):
    """Interface for injecting auth into outbound requests."""

    def configure(self, session: requests.Session) -> None:  # noqa: B027
        """Apply the auth to a fresh session. No-op by default."""

    def query_params(self) -> dict:
        return {}

    def refresh(self, session: requests.Session) -> None:  # noqa: B027
        """Re-authenticate after a 401. No-op by default (static credential)."""

    def needs_login(self, r: requests.Response) -> bool:
        """Whether this answer asks for a re-authentication and one replay: a 401."""
        return r.status_code == 401

    def check_replay(self, r: requests.Response) -> None:  # noqa: B027
        """Look at the replay's answer. No-op by default: its 401 raises as an HTTPError."""

    def after_response(self, session: requests.Session) -> None:  # noqa: B027
        """After each answer of the API. No-op by default."""


class NoAuth(UpstreamAuth):
    """No authentication (public API)."""


class StaticBearer(UpstreamAuth):
    def __init__(self, token: str):
        if not token:
            raise ValueError("StaticBearer: empty token")
        self._token = token

    def configure(self, session: requests.Session) -> None:
        session.headers["Authorization"] = f"Bearer {self._token}"


class ApiKeyHeader(UpstreamAuth):
    def __init__(self, name: str, value: str):
        if not name or not value:
            raise ValueError("ApiKeyHeader: name/value required")
        self._name, self._value = name, value

    def configure(self, session: requests.Session) -> None:
        session.headers[self._name] = self._value


class ApiKeyQuery(UpstreamAuth):
    def __init__(self, param: str, value: str):
        if not param or not value:
            raise ValueError("ApiKeyQuery: param/value required")
        self._param, self._value = param, value

    def query_params(self) -> dict:
        return {self._param: self._value}


class BasicAuth(UpstreamAuth):
    def __init__(self, username: str, password: str):
        if not username:
            raise ValueError("BasicAuth: username required")
        self._auth = HTTPBasicAuth(username, password)

    def configure(self, session: requests.Session) -> None:
        session.auth = self._auth


class OAuth2ClientCredentials(UpstreamAuth):
    """OAuth2 client-credentials: fetch an access_token + TTL cache."""

    def __init__(self, token_url: str, client_id: str, client_secret: str,
                 scope: str | None = None, timeout: int = 30, leeway: int = 30):
        if not token_url or not client_id or not client_secret:
            raise ValueError("OAuth2ClientCredentials: token_url/client_id/client_secret required")
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._timeout = timeout
        self._leeway = leeway
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expiry = 0.0

    def _fetch(self) -> str:
        data = {"grant_type": "client_credentials",
                "client_id": self._client_id, "client_secret": self._client_secret}
        if self._scope:
            data["scope"] = self._scope
        r = requests.post(self._token_url, data=data, timeout=self._timeout)
        r.raise_for_status()
        payload = r.json()
        token = payload.get("access_token")
        if not token:
            raise ValueError("OAuth2ClientCredentials: response without access_token")
        self._token = token
        self._expiry = time.monotonic() + int(payload.get("expires_in", 3600)) - self._leeway
        return token

    def _current(self) -> str:
        with self._lock:
            if self._token is None or time.monotonic() >= self._expiry:
                return self._fetch()
            return self._token

    def configure(self, session: requests.Session) -> None:
        session.headers["Authorization"] = f"Bearer {self._current()}"

    def refresh(self, session: requests.Session) -> None:
        with self._lock:
            self._fetch()
        session.headers["Authorization"] = f"Bearer {self._token}"


def build_auth(mode: str, fields: dict, *, base_url: str | None = None,
               guard: Callable[[str], None] | None = None, jar: str | None = None,
               on_jar: Callable[[str], None] | None = None,
               timeout: int = 45) -> UpstreamAuth:
    """Build the `UpstreamAuth` for an `auth_mode` + the credential fields.

    The session mode also takes the base URL, the host's egress `guard` (called on
    each hop of the login, required), the stored cookie `jar` and the `on_jar`
    callback that receives it to store (see `session.py`). The visitor mode takes the
    same, without login nor password: only its `session` field.

    Raises `ValueError` (actionable message) if the mode is unknown or a
    required field is missing."""
    mode = (mode or "").strip().lower()

    def val(key: str) -> str:
        return (fields.get(key) or "").strip()

    def need(*keys: str) -> None:
        missing = [k for k in keys if not val(k)]
        if missing:
            raise ValueError(f"auth_mode={mode!r} requires: {', '.join(missing)}")

    if mode == "bearer":
        need("token"); return StaticBearer(val("token"))
    if mode == "header":
        need("header_name", "token"); return ApiKeyHeader(val("header_name"), val("token"))
    if mode == "query":
        need("query_param", "token"); return ApiKeyQuery(val("query_param"), val("token"))
    if mode == "basic":
        need("username", "password"); return BasicAuth(val("username"), fields.get("password") or "")
    if mode == "oauth2":
        need("token_url", "client_id", "client_secret")
        return OAuth2ClientCredentials(val("token_url"), val("client_id"),
                                       val("client_secret"), scope=val("scope") or None)
    if mode == "session":
        need("username", "password", "session")
        if not base_url:
            raise ValueError("auth_mode='session' requires the base URL")
        return SessionLogin(base_url, val("username"), fields.get("password") or "",
                            SessionConfig(fields.get("session")), guard=guard, jar=jar,
                            on_jar=on_jar, timeout=timeout)
    if mode == "visitor":
        need("session")
        if not base_url:
            raise ValueError("auth_mode='visitor' requires the base URL")
        return VisitorSession(base_url, SessionConfig(fields.get("session"), visitor=True),
                              guard=guard, jar=jar, on_jar=on_jar, timeout=timeout)
    if mode == "none":
        return NoAuth()
    raise ValueError(f"unknown auth_mode: {mode!r} (expected: {'|'.join(AUTH_MODES)})")


# --- forward -------------------------------------------------------------------

METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")


class RedirectRefused(ValueError):
    """The target API answered a 3xx. Never followed: the auth this client injects
    (custom header, query parameter) would leave with it — `requests` only strips
    `Authorization` on a cross-host redirect — and a 3xx is the classic way around
    an egress guard. The caller who wants the target calls its path explicitly."""

    def __init__(self, status: int, location: str):
        self.status = status
        # Only scheme, host, port and path: a 3xx often keeps the original query, and
        # the auth this client injects in it (`?api_key=`), or a userinfo, would
        # otherwise reach the caller's message and every log that prints it.
        self.location = safe_location(location)
        super().__init__(f"redirect refused ({status} to {self.location or '?'})")


def safe_location(location: str) -> str:
    """`Location` reduced to scheme://host[:port]/path — no query, fragment or
    userinfo. A relative target keeps its path alone."""
    from urllib.parse import urlsplit, urlunsplit
    p = urlsplit(location or "")
    host = p.hostname or ""
    if host and p.port:
        host = f"{host}:{p.port}"
    return urlunsplit((p.scheme, host, p.path, "", ""))


def refuse_redirect(r: requests.Response) -> None:
    """Raise `RedirectRefused` on a 3xx. Shared by every client that injects its auth
    outside `Authorization` (custom header, query parameter): `requests` only strips
    `Authorization` on a cross-host redirect, so each of them calls with
    `allow_redirects=False` and passes the response here BEFORE any retry."""
    if 300 <= r.status_code < 400:
        raise RedirectRefused(r.status_code, r.headers.get("Location") or "")


class HttpConnectorClient:
    """HTTP node: (base_url, auth_mode, fields) → `.request(method, path, …)`.

    Injects the auth, forwards the method (GET/HEAD/POST/PUT/PATCH/DELETE), single retry
    after re-auth on 401. A 3xx is never followed (`RedirectRefused`). `.get()`/`.post()` = shortcuts. Raises `ValueError` on
    invalid config (non-http(s) scheme, invalid mode/field/method).

    The session mode (`auth_mode='session'`) reads only: GET and HEAD. A 401 or a
    redirect to the login page triggers one full login and one replay; refused again,
    `SessionRejected`. `guard`, `jar` and `on_jar` serve that mode only.

    Like n8n/Make's HTTP node: write methods are carried through;
    responsibility for what they do lies with the target API (and, for a
    downstream bridge, with ITS own allowlist)."""

    def __init__(self, base_url: str, auth_mode: str, fields: dict, *, timeout: int = 45,
                 guard: Callable[[str], None] | None = None, jar: str | None = None,
                 on_jar: Callable[[str], None] | None = None):
        base_url = (base_url or "").strip().rstrip("/")
        if urlsplit(base_url).scheme not in ("http", "https"):
            raise ValueError("base_url must be http(s)")
        self._base_url = base_url
        self._read_only = (auth_mode or "").strip().lower() in READ_ONLY_MODES
        self._auth = build_auth(auth_mode, fields, base_url=base_url, guard=guard,
                                jar=jar, on_jar=on_jar, timeout=timeout)
        self._timeout = timeout
        self._session: requests.Session | None = None

    def _ready(self) -> requests.Session:
        if self._session is None:
            s = requests.Session()
            self._auth.configure(s)
            self._session = s
        return self._session

    def _reauthed(self, s: requests.Session, send: Callable[[], requests.Response]) -> requests.Response:
        """Send; on an answer that asks for it, re-authenticate once and replay once."""
        r = send()
        if self._auth.needs_login(r):
            r.close()
            self._auth.refresh(s)
            r = send()
            self._auth.check_replay(r)
        self._auth.after_response(s)
        return r

    def _exchange(self, method: str, path: str, *, params: dict | None,
                  json: dict | list | None, data: str | bytes | None,
                  content_type: str | None, stream: bool) -> requests.Response:
        method = (method or "").strip().upper()
        if method not in METHODS:
            raise ValueError(f"invalid method: {method!r} (expected: {'|'.join(METHODS)})")
        if self._read_only and method not in READ_METHODS:
            raise ValueError(f"the session mode only reads: {'|'.join(READ_METHODS)}, not {method}")
        if not path.startswith("/"):
            raise ValueError("path must start with / (relative to base_url)")
        if data is not None and json is not None:
            raise ValueError("a body is either json or data, not both")
        if (data is None) != (content_type is None):
            raise ValueError("data and content_type go together: a raw body declares its type")
        if isinstance(data, str):
            data = data.encode("utf-8")
        headers = {"Content-Type": content_type} if content_type else None
        s = self._ready()
        url = self._base_url + path

        def _send():
            merged = {**(params or {}), **self._auth.query_params()}
            return s.request(method, url, params=merged or None, json=json, data=data,
                             headers=headers, timeout=self._timeout, allow_redirects=False,
                             stream=stream)

        return self._reauthed(s, _send)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | list | None = None,
        data: str | bytes | None = None,
        content_type: str | None = None,
    ) -> dict:
        """Call `path` and return its JSON answer. The body is `json`, or a raw `data`
        (text sent as UTF-8, or bytes) with its `content_type` — both or neither."""
        r = self._exchange(method, path, params=params, json=json, data=data,
                           content_type=content_type, stream=False)
        refuse_redirect(r)
        r.raise_for_status()
        return r.json() if r.content else {}

    def request_raw(
        self,
        method: str,
        path: str,
        *,
        max_bytes: int,
        params: dict | None = None,
        json: dict | list | None = None,
        data: str | bytes | None = None,
        content_type: str | None = None,
    ) -> dict:
        """Call `path` and return its answer as is: `{status, content_type, text}`, the
        body decoded from its declared charset (UTF-8 when none is declared), never
        parsed. Read as a stream and bounded by `max_bytes`, like `get_raw`. Same body
        rules as `request`, same auth, same refused redirect; an upstream 4xx/5xx
        raises `requests.HTTPError`."""
        r = self._exchange(method, path, params=params, json=json, data=data,
                           content_type=content_type, stream=True)
        try:
            refuse_redirect(r)
            r.raise_for_status()
            body = _bounded(r, max_bytes)
            declared = r.headers.get("Content-Type") or ""
            return {"status": r.status_code, "content_type": declared,
                    "text": body.decode(_charset(declared), errors="replace")}
        finally:
            r.close()

    def get(self, path: str, params: dict | None = None) -> dict:
        return self.request("GET", path, params=params)

    def get_raw(self, path: str, params: dict | None = None, *,
                max_bytes: int) -> tuple[bytes, str]:
        """GET `path` and return the body's BYTES and its content type, read as a
        stream and bounded by `max_bytes` — for a body that is a file (a CSV, a
        PDF), not JSON. Same auth and single re-auth on 401 as `request`.

        A redirect is refused, not followed: the auth this client injects would
        leave with it, and a 3xx is the classic way around an egress guard. A body
        above `max_bytes` raises before being read whole. An upstream 4xx/5xx raises
        `requests.HTTPError`, like `request`."""
        if not path.startswith("/"):
            raise ValueError("path must start with / (relative to base_url)")
        s = self._ready()
        url = self._base_url + path

        def _send():
            merged = {**(params or {}), **self._auth.query_params()}
            return s.get(url, params=merged or None, timeout=self._timeout,
                         stream=True, allow_redirects=False)

        r = self._reauthed(s, _send)
        try:
            refuse_redirect(r)
            r.raise_for_status()
            return _bounded(r, max_bytes), r.headers.get("Content-Type") or ""
        finally:
            r.close()

    def post(self, path: str, json: dict | list | None = None,
             params: dict | None = None) -> dict:
        return self.request("POST", path, params=params, json=json)


def _bounded(r: requests.Response, max_bytes: int) -> bytes:
    """The body of a streamed answer, refused above `max_bytes` before being read whole."""
    declared = r.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ValueError(f"response of {declared} bytes > limit {max_bytes}")
    data = bytearray()
    for chunk in r.iter_content(64 * 1024):
        data += chunk
        if len(data) > max_bytes:
            raise ValueError(f"response > limit {max_bytes} bytes")
    return bytes(data)


def _charset(content_type: str) -> str:
    """The charset a Content-Type declares, UTF-8 when it declares none; an unknown one raises."""
    import codecs
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            name = value.strip().strip('"')
            try:
                return codecs.lookup(name).name
            except LookupError:
                raise ValueError(f"the answer declares an unknown charset: {name!r}") from None
    return "utf-8"
