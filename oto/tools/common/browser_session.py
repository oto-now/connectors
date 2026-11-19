"""Connectors executed in the host's browser, on a session the host keeps.

Some services answer only a logged-in browser: their API refuses a bare HTTP client,
and their session does not survive a cookie export. A person logs in once in a browser
window the host opens (login, SSO, captcha, second factor), and every call then runs as
JavaScript in a page of that logged-in browser.

The lib does not drive a browser and stores no session. The host provides both:

- `run(jar, page_url, page_function, arg)`: opens `page_url` in a browser carrying the
  session `jar`, evaluates `page_function` (the source of an async JavaScript function
  of one argument) with `arg`, and returns its value. What `jar` designates (a profile
  of a hosted browser, for instance) is the host's business: the lib passes it on;
- `jar` and `on_jar`, the contract of `oto.tools.common.host_session`: the session the
  host keeps, and the callback that hands back the one a login established.

The connector carries what is proper to the service: the pages, the requests, the
probe that tells whether a session is logged in, and what a refusal means. A refused
session raises `SessionRejected`: no automatic login exists, a person must log in again.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple

from .credentials import require
from .host_session import HostJar

Runner = Callable[[str, str, str, Any], Any]
# A runner bound to one session: (page_url, page_function, arg) -> value.
Evaluate = Callable[[str, str, Any], Any]

# Reasons of a probe verdict. Each one calls for a different conduct from whoever reads
# it: finish the login in the window, log in again, or stop retrying.
LOGGED_IN = "logged_in"
NO_SESSION = "no_session"          # nothing proves a login: the person has not finished
AUTH_REJECTED = "auth_rejected"    # the service refused the session: log in again


class Verdict(NamedTuple):
    """What a login probe concludes, with its reason. `retry=False`: logging in again
    cannot change the outcome."""
    connected: bool
    reason: str
    detail: str = ""
    retry: bool = True


class ProbeUnavailable(RuntimeError):
    """The probe gave no verdict (its route answered something that says nothing about
    authentication). It is not "logged out": the host decides what to do with a session
    it cannot confirm."""


# A `fetch` of a JSON API from the page, with the session cookies. The body is read once
# as text, then parsed: a second read of a Response body raises and would hide the real answer.
FETCH_JS = """async ({url, method, body}) => {
    const r = await fetch(url, {
        method, credentials: "include",
        headers: {"content-type": "application/json"},
        body: body ? JSON.stringify(body) : undefined,
    });
    const txt = await r.text();
    let data;
    try { data = txt ? JSON.parse(txt) : null; }
    catch (e) { data = {raw: txt.slice(0, 400)}; }
    return {status: r.status, data};
}"""


class BrowserSession:
    """The session of a browser-executed connector: the host's runner, the session it
    keeps (`jar`) and its callback (`on_jar`). `name` is the credential name a missing
    session raises (`MissingCredential`)."""

    def __init__(self, run: Runner, *, name: str, jar: str | None = None,
                 on_jar: Callable[[str], None] | None = None):
        if run is None:
            raise ValueError(f"{name}: the host's browser runner is required")
        self._run = run
        self._name = name
        self._host = HostJar(jar, on_jar)

    def _bound(self, jar: str | None) -> Evaluate:
        jar = require(jar, self._name)
        return lambda page_url, page_function, arg=None: self._run(jar, page_url, page_function, arg)

    def evaluate(self, page_url: str, page_function: str, arg: Any = None) -> Any:
        """Evaluate `page_function` in `page_url`, in the kept session."""
        return self._bound(self._host.text)(page_url, page_function, arg)

    def fetch(self, page_url: str, url: str, method: str = "GET", body: dict | None = None) -> dict:
        """One `fetch(url)` from `page_url`, with the session cookies (`credentials:
        "include"`): `page_url` is of the origin of `url`, or of an origin the API
        answers with its cookies. Returns `{status, data}`."""
        res = self.evaluate(page_url, FETCH_JS, {"url": url, "method": method, "body": body})
        if not isinstance(res, dict) or not isinstance(res.get("status"), int):
            raise ValueError(f"{self._name}: the browser runner did not return the fetch result")
        return res

    def finish_login(self, jar: str, probe: Callable[[Evaluate], Verdict]) -> Verdict:
        """Probe `jar`, the session a login window established, with the connector's
        `probe`. Logged in: the session is kept and handed to the host through `on_jar`.
        Otherwise nothing is handed back, and the verdict says why. `ProbeUnavailable`
        propagates: it is no verdict."""
        verdict = probe(self._bound(jar))
        if verdict.connected:
            self._host.save(jar)
        return verdict
