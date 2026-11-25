"""An injected `session=`: every request of the client goes out through it.

Five clients take a destination the consumer configures (a base URL, a login URL,
a token URL) and used to call module-level `requests`, which leaves the consumer no
way to choose the transport — for instance a session whose transport checks each
destination at connection time. They now take an optional `requests.Session`:

- given, EVERY request goes through it (tokens included), and module-level
  `requests` is never touched;
- absent, nothing changes (the existing tests of each client pin that path).

Transport stubbed: a recording `requests.Session` subclass, and module-level
`requests` verbs replaced by a failure. No network, no real credential.
"""
from __future__ import annotations

import json

import pytest
import requests

from oto.tools.http.client import HttpConnectorClient
from oto.tools.lighton.client import LightOnClient
from oto.tools.salesforce import client as salesforce_client
from oto.tools.salesforce.client import SalesforceClient
from oto.tools.threecx import ThreeCXClient
from oto.tools.threecx import auth as threecx_auth
from oto.tools.wordpress.client import WordPressClient


class _Resp:
    def __init__(self, payload=None, status_code=200, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode() if payload is not None else b""
        self.text = self.content.decode()
        self.headers = headers or {"Content-Type": "application/json"}
        self.url = ""
        self.reason = "OK"

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def close(self):
        pass

    def iter_content(self, *a, **k):
        yield self.content


class _Recording(requests.Session):
    """Records (method, url) of each request and answers from `answer(method, url)`."""

    def __init__(self, answer):
        super().__init__()
        self.seen: list[tuple[str, str]] = []
        self._answer = answer

    def request(self, method, url, *a, **k):
        self.seen.append((method.upper(), url))
        return self._answer(method.upper(), url)


@pytest.fixture(autouse=True)
def no_module_requests(monkeypatch):
    """Module-level `requests` is off limits once a session is injected."""
    def forbidden(*a, **k):
        pytest.fail(f"module-level requests called: {a!r}")
    for verb in ("request", "get", "post", "put", "patch", "delete", "head"):
        monkeypatch.setattr(requests, verb, forbidden)


def test_lighton_goes_through_the_given_session():
    s = _Recording(lambda m, u: _Resp({"results": []}))
    LightOnClient(api_key="k", base_url="https://lighton.example.test",
                  session=s).search("q")
    assert s.seen and all(u.startswith("https://lighton.example.test/api/v3/")
                          for _, u in s.seen)


def test_wordpress_probe_index_and_calls_go_through_the_given_session():
    def answer(m, u):
        return _Resp({"namespaces": ["wp/v2"], "name": "x"})
    s = _Recording(answer)
    wp = WordPressClient("https://blog.example.test", "u", "pw", session=s)
    wp.public_index()
    wp.index()
    assert len(s.seen) >= 3, "probe, public index and authenticated index"
    assert all(u.startswith("https://blog.example.test/") for _, u in s.seen)


def test_salesforce_token_exchange_and_api_go_through_the_given_session(monkeypatch):
    monkeypatch.setattr(salesforce_client, "_TOKEN_CACHE", {})

    def answer(m, u):
        if u.endswith("/services/oauth2/token"):
            return _Resp({"access_token": "a", "instance_url": "https://acme.my.salesforce.com"})
        return _Resp({"records": [], "done": True, "totalSize": 0})
    s = _Recording(answer)
    sf = SalesforceClient("id", "secret", "refresh", login_url="https://login.salesforce.com",
                          session=s)
    sf._request("GET", "query/", params={"q": "SELECT Id FROM Account"})
    assert s.seen[0] == ("POST", "https://login.salesforce.com/services/oauth2/token")
    assert s.seen[1][1].startswith("https://acme.my.salesforce.com/services/data/")


def test_threecx_token_and_api_go_through_the_given_session(monkeypatch):
    monkeypatch.setattr(threecx_auth, "_TOKEN_CACHE", {})
    # The client's own public-address check resolves the host: stubbed public.
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])

    def answer(m, u):
        if u.endswith("/connect/token"):
            return _Resp({"access_token": "a", "expires_in": 3600})
        return _Resp({"value": []})
    s = _Recording(answer)
    client = ThreeCXClient("https://pbx.example.test", client_id="c", client_secret="s",
                           session=s)
    assert client.session is s
    client._request("/Users")
    assert s.seen[0] == ("POST", "https://pbx.example.test/connect/token")
    assert s.seen[1][1].startswith("https://pbx.example.test/xapi/v1/Users")


def test_http_oauth2_token_goes_through_its_own_session_and_calls_through_the_other():
    token = _Recording(lambda m, u: _Resp({"access_token": "t", "expires_in": 3600}))
    api = _Recording(lambda m, u: _Resp({"ok": True}))
    client = HttpConnectorClient(
        "https://api.example.test", "oauth2",
        {"token_url": "https://auth.example.test/token", "client_id": "c",
         "client_secret": "s"}, session=api, token_session=token)
    assert client.get("/v1/x") == {"ok": True}
    assert token.seen == [("POST", "https://auth.example.test/token")]
    assert api.seen == [("GET", "https://api.example.test/v1/x")]
    assert api.headers["Authorization"] == "Bearer t", "the auth configured the given session"
    assert "Authorization" not in token.headers, "the token request carries no bearer"


def test_http_session_mode_logs_in_through_the_given_session():
    """The session mode logs in when the session is configured: the given session is
    the one it logs in through, hops guarded by `guard` as before."""
    steps = json.dumps({"login": {"path": "/api/login"}})
    seen_hops = []

    def answer(m, u):
        if u.endswith("/api/login"):
            api.cookies.set("sid", "x", domain="svc.example.test")
        return _Resp({"ok": True})
    api = _Recording(answer)
    client = HttpConnectorClient(
        "https://svc.example.test", "session",
        {"username": "u", "password": "p", "session": steps},
        guard=seen_hops.append, session=api, on_jar=lambda jar: None)
    assert client._ready() is api
    assert api.seen and api.seen[0][1] == "https://svc.example.test/api/login"


def test_without_a_session_nothing_changes_for_the_http_node(monkeypatch):
    """The default keeps creating its own session (existing behaviour)."""
    client = HttpConnectorClient("https://api.example.test", "bearer", {"token": "t"})
    s = client._ready()
    assert type(s) is requests.Session and s.headers["Authorization"] == "Bearer t"
