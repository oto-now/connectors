"""The `session` auth mode of the http node: a login and password, a session cookie.

For a service with no API key: the node logs in with the account's login and password,
follows the steps the card declares, and reads the API with the session cookie the
service set. Three declared steps, in this order:

1. **login**: a POST of a JSON body (the login, the password, the card's constants) to
   a path of the base URL. The only POST the session mode ever sends.
2. **follow** (optional): the login answer names an URL in one of its fields; the node
   GETs it, with constant query parameters the card declares.
3. **redirects**: the node follows the 3xx of that GET one by one, up to a bound, to
   collect the cookies they set.

Every hop goes to an allowed host only: the base URL's host, a host the card lists, or
a host under the registrable domain the card declares (no public suffix list guessed:
the card names the domain). Before each hop, the host's egress guard (`guard`) checks
the URL; a guard that refuses stops the chain before the request leaves.

The cookies live in a jar the host keeps (`jar`, `on_jar`): the node hands it over as
text after a login and whenever the service changes a cookie, the host stores it
encrypted, and gives it back at the next build, so a call does not log in each time.
The jar is a secret: a session cookie is worth the password for its lifetime.

A 401, or a redirect to the login page, triggers one full login and one replay; if the
replay is refused again, `SessionRejected` (`credential_rejected`). Nothing is retried
beyond that, nothing falls back to another mode.

The `visitor` mode (`VisitorSession`) reads a site that has no account: no login nor
password, the "login" is a GET of an entry page whose answer sets the visitor's cookies.
The card declares its method (`GET`, explicitly: no default) and constant query
parameters; the node follows that GET's redirects under the same host rules and the
same guard, keeps the jar the same way, and refuses with `VisitorRefused`
(`visitor_refused`) when the visit sets no cookie or the session is refused again
after a fresh visit.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests

# What the card may declare under `session`, and nothing else.
_KEYS = {"login", "follow", "login_pages", "domain", "hosts", "max_redirects"}
_LOGIN_KEYS = {"path", "user_field", "password_field", "constants"}
_FOLLOW_KEYS = {"field", "query"}
# The visitor mode has no login nor password, and no answer to follow: its visit is a
# GET whose redirects are followed.
_VISITOR_KEYS = _KEYS - {"follow"}
_VISITOR_LOGIN_KEYS = {"path", "method", "query"}
VISITOR_METHODS = ("GET",)
MAX_REDIRECTS = 5


class SessionRejected(ValueError):
    """The service refused the account: the login was refused, or the session was
    refused again right after a fresh login. `code` is the account mark the host
    applies (`credential_rejected`). The message never carries the login, the password
    nor a cookie."""

    code = "credential_rejected"


class VisitorRefused(ValueError):
    """The site gave the visitor no session: the visit set no cookie, was refused
    (401, 403), or the session was refused again right after a fresh visit. No account
    is involved: `code` (`visitor_refused`) is not a credential mark."""

    code = "visitor_refused"


class SessionHopRefused(ValueError):
    """A step of the login would leave the allowed hosts (base URL's host, the card's
    hosts, its declared domain). Nothing was sent to that host."""


def _object(value, what: str) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f"{what}: invalid JSON ({e.msg})") from None
    if not isinstance(value, dict):
        raise ValueError(f"{what}: an object is expected")
    return value


def _closed(obj: dict, allowed: set[str], what: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ValueError(f"{what}: unknown keys {', '.join(unknown)} "
                         f"(expected: {', '.join(sorted(allowed))})")


def _strings(value, what: str) -> dict[str, str]:
    obj = _object(value or {}, what)
    if not all(isinstance(v, (str, int, float, bool)) for v in obj.values()):
        raise ValueError(f"{what}: scalar values only")
    return obj


class SessionConfig:
    """The `session` field of the card, read strictly: an unknown key, a missing path or
    a wrong type raises `ValueError`, never a default chosen in silence."""

    def __init__(self, raw, *, visitor: bool = False):
        cfg = _object(raw, "session")
        _closed(cfg, _VISITOR_KEYS if visitor else _KEYS, "session")
        login = _object(cfg.get("login"), "session.login")
        _closed(login, _VISITOR_LOGIN_KEYS if visitor else _LOGIN_KEYS, "session.login")
        path = login.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("session.login.path: a path starting with / is required")
        self.login_path = path
        self.visitor = visitor
        if visitor:
            self._visit(cfg, login)
        else:
            self._account(cfg, login)
        self._bounds(cfg)

    def _visit(self, cfg: dict, login: dict) -> None:
        method = login.get("method")
        if method not in VISITOR_METHODS:
            raise ValueError(f"session.login.method: the visitor mode requires an explicit "
                             f"method ({'|'.join(VISITOR_METHODS)})")
        self.login_method = method
        self.login_query = _strings(login.get("query"), "session.login.query")
        self.user_field = self.password_field = None
        self.constants: dict = {}
        self.follow_field: str | None = None
        self.follow_query: dict = {}
        # No default login page: the entry page often is `/`, and every redirect would
        # then read as a return to the login.
        self.login_pages = self._pages(cfg, [])

    def _account(self, cfg: dict, login: dict) -> None:
        self.login_method = "POST"
        self.login_query: dict = {}
        self.user_field = login.get("user_field", "username")
        self.password_field = login.get("password_field", "password")
        if not all(isinstance(f, str) and f for f in (self.user_field, self.password_field)):
            raise ValueError("session.login: user_field and password_field are names")
        self.constants = _strings(login.get("constants"), "session.login.constants")
        clash = {self.user_field, self.password_field} & set(self.constants)
        if clash:
            raise ValueError(f"session.login.constants: {', '.join(sorted(clash))} "
                             f"is the login or the password field")

        self.follow_field: str | None = None
        self.follow_query: dict = {}
        if "follow" in cfg:
            follow = _object(cfg["follow"], "session.follow")
            _closed(follow, _FOLLOW_KEYS, "session.follow")
            field = follow.get("field")
            if not isinstance(field, str) or not field:
                raise ValueError("session.follow.field: the field of the login answer is required")
            self.follow_field = field
            self.follow_query = _strings(follow.get("query"), "session.follow.query")

        self.login_pages = self._pages(cfg, [self.login_path])

    @staticmethod
    def _pages(cfg: dict, default: list) -> tuple:
        pages = cfg.get("login_pages", default)
        if not isinstance(pages, list) or not all(isinstance(p, str) and p.startswith("/") for p in pages):
            raise ValueError("session.login_pages: a list of paths starting with /")
        return tuple(pages)

    def _bounds(self, cfg: dict) -> None:
        domain = cfg.get("domain")
        if domain is not None and (not isinstance(domain, str) or "." not in domain.strip(".")):
            raise ValueError("session.domain: a registrable domain such as example.org")
        self.domain = domain.strip(".").lower() if domain else None
        hosts = cfg.get("hosts", [])
        if not isinstance(hosts, list) or not all(isinstance(h, str) and h for h in hosts):
            raise ValueError("session.hosts: a list of host names")
        self.hosts = frozenset(h.lower() for h in hosts)
        bound = cfg.get("max_redirects", MAX_REDIRECTS)
        if not isinstance(bound, int) or isinstance(bound, bool) or not 0 <= bound <= 10:
            raise ValueError("session.max_redirects: an integer from 0 to 10")
        self.max_redirects = bound


def _dotted(answer, path: str):
    node = answer
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def _with_query(url: str, query: dict) -> str:
    if not query:
        return url
    p = urlsplit(url)
    pairs = parse_qsl(p.query, keep_blank_values=True) + [(k, str(v)) for k, v in query.items()]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(pairs), p.fragment))


def dump_jar(jar: requests.cookies.RequestsCookieJar) -> str:
    """The jar as text, for the host to store encrypted."""
    return json.dumps([
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
         "secure": c.secure, "expires": c.expires}
        for c in jar
    ], sort_keys=True)


def load_jar(text: str, jar: requests.cookies.RequestsCookieJar) -> None:
    """Fill `jar` from a text `dump_jar` produced. A malformed text raises."""
    try:
        cookies = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("session jar: not the text dump_jar produces") from None
    if not isinstance(cookies, list):
        raise ValueError("session jar: not the text dump_jar produces")
    for c in cookies:
        jar.set(c["name"], c["value"], domain=c["domain"], path=c["path"],
                secure=c["secure"], expires=c["expires"])


class SessionLogin:
    """The session auth: logs in, keeps the cookies, says when a response asks for a
    new login. Built by `build_auth` from the card's `username`, `password` and
    `session` fields, with the host's egress guard and its stored jar."""

    mode = "session"
    refused = SessionRejected

    def __init__(self, base_url: str, username: str, password: str, config: SessionConfig, *,
                 guard: Callable[[str], None], jar: str | None = None,
                 on_jar: Callable[[str], None] | None = None, timeout: int = 45):
        if guard is None:
            raise ValueError(f"auth_mode={self.mode!r} requires the host's egress guard")
        self._base_url = base_url
        self._base_host = (urlsplit(base_url).hostname or "").lower()
        self._username, self._password = username, password
        self._cfg = config
        self._guard = guard
        self._jar_text = jar
        self._on_jar = on_jar
        self._timeout = timeout
        self._lock = threading.Lock()
        self._saved: str | None = None
        if config.domain and not self._under_domain(self._base_host):
            raise ValueError("session.domain: the base URL's host is not under it")

    # -- the UpstreamAuth interface ------------------------------------------------

    def configure(self, session: requests.Session) -> None:
        if self._jar_text:
            load_jar(self._jar_text, session.cookies)
            self._saved = self._jar_text
        else:
            self._login(session)

    def query_params(self) -> dict:
        return {}

    def refresh(self, session: requests.Session) -> None:
        self._login(session)

    def needs_login(self, r: requests.Response) -> bool:
        return r.status_code == 401 or self.is_login_redirect(r)

    def check_replay(self, r: requests.Response) -> None:
        if self.needs_login(r):
            raise self.refused("the service refused the session right after a fresh login")

    def after_response(self, session: requests.Session) -> None:
        self._save(session)

    # -- the login -----------------------------------------------------------------

    def is_login_redirect(self, r: requests.Response) -> bool:
        if not 300 <= r.status_code < 400:
            return False
        path = urlsplit(urljoin(r.url or self._base_url, r.headers.get("Location") or "")).path
        return any(path == p or path.startswith(p.rstrip("/") + "/") for p in self._cfg.login_pages)

    def _under_domain(self, host: str) -> bool:
        d = self._cfg.domain
        return bool(d) and (host == d or host.endswith("." + d))

    def _allowed(self, url: str) -> None:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        if p.scheme not in ("http", "https") or not host:
            raise SessionHopRefused("session login: a step leads to a non-http(s) address")
        if host == self._base_host or host in self._cfg.hosts or self._under_domain(host):
            self._guard(url)
            return
        raise SessionHopRefused(f"session login: a step leads to {host}, outside the "
                                f"base URL's host, the card's hosts and its domain")

    def _send(self, session: requests.Session, method: str, url: str, **kw) -> requests.Response:
        self._allowed(url)
        return session.request(method, url, timeout=self._timeout, allow_redirects=False, **kw)

    def _login(self, session: requests.Session) -> None:
        with self._lock:
            session.cookies.clear()
            self._authenticate(session)
            if not session.cookies:
                raise self.refused("the login set no session cookie")
            self._save(session)

    def _authenticate(self, session: requests.Session) -> None:
        """The account's login: a POST of the login and the password, then the URL its
        answer names, if the card says to follow it."""
        body = {**self._cfg.constants, self._cfg.user_field: self._username,
                self._cfg.password_field: self._password}
        r = self._send(session, "POST", self._base_url + self._cfg.login_path, json=body)
        if r.status_code in (401, 403):
            raise self.refused(f"the service refused the login ({r.status_code})")
        if not 200 <= r.status_code < 300:
            raise ValueError(f"session login: the login answered {r.status_code}")
        if self._cfg.follow_field:
            self._follow(session, r)

    def _follow(self, session: requests.Session, login: requests.Response) -> None:
        field = self._cfg.follow_field
        try:
            answer = login.json()
        except ValueError:
            raise ValueError("session login: the login answer is not JSON") from None
        target = _dotted(answer, field)
        if not isinstance(target, str) or not target:
            raise ValueError(f"session login: the login answer has no URL in {field}")
        self._get_through(session, _with_query(urljoin(login.url, target), self._cfg.follow_query))

    def _get_through(self, session: requests.Session, url: str) -> None:
        """GET `url` and follow its redirects, each hop checked, up to the card's bound."""
        r = self._send(session, "GET", url)
        for _ in range(self._cfg.max_redirects):
            if not 300 <= r.status_code < 400:
                break
            if self._back_to_login(r):
                raise self.refused("the service sent the login back to its login page")
            url = urljoin(r.url, r.headers.get("Location") or "")
            r.close()
            r = self._send(session, "GET", url)
        r.close()
        if 300 <= r.status_code < 400:
            raise ValueError(f"session login: more than {self._cfg.max_redirects} redirects")
        if r.status_code in (401, 403):
            raise self.refused(f"the service refused the login ({r.status_code})")
        if not 200 <= r.status_code < 300:
            raise ValueError(f"session login: a step answered {r.status_code}")

    def _back_to_login(self, r: requests.Response) -> bool:
        return self.is_login_redirect(r)

    def _save(self, session: requests.Session) -> None:
        text = dump_jar(session.cookies)
        if text != self._saved:
            self._saved = text
            if self._on_jar is not None:
                self._on_jar(text)


class VisitorSession(SessionLogin):
    """The visitor auth: no account; a GET of the entry page the card declares sets the
    cookies, which then serve every call like a session's. Built by `build_auth` from
    the card's `session` field (read with `visitor=True`), with the host's egress guard
    and its stored jar. Its refusals are `VisitorRefused`, never a credential mark."""

    mode = "visitor"
    refused = VisitorRefused

    def __init__(self, base_url: str, config: SessionConfig, *,
                 guard: Callable[[str], None], jar: str | None = None,
                 on_jar: Callable[[str], None] | None = None, timeout: int = 45):
        if not config.visitor:
            raise ValueError("VisitorSession: the session field was not read for the visitor mode")
        super().__init__(base_url, "", "", config, guard=guard, jar=jar, on_jar=on_jar,
                         timeout=timeout)

    def _authenticate(self, session: requests.Session) -> None:
        self._get_through(session, _with_query(self._base_url + self._cfg.login_path,
                                               self._cfg.login_query))

    def _back_to_login(self, r: requests.Response) -> bool:
        # The visit goes through the entry page: its own redirects are the visit.
        return False
