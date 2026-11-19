"""Crunchbase — organizations, people and funding rounds, read with a person's session.

The data API of the web application (`www.crunchbase.com/v4/data`) mirrors the schema of
the public v4 API (`field_ids`, `card_ids`, `entities/organizations|people/{permalink}`,
`autocompletes`) without a `user_key`: the logged-in session authorizes it, and it only
answers a request from a page of the logged-in browser. Every call therefore runs in the
host's browser (`oto.tools.common.browser_session`): the host keeps the session and
gives it here (`jar`); a login establishes a new one, handed back through `on_jar`.

A refused session (401, 403) raises `SessionRejected`: only a person logging in again
renews it.
"""
from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote, urlencode

from ..common.browser_session import (
    AUTH_REJECTED,
    LOGGED_IN,
    NO_SESSION,
    BrowserSession,
    Evaluate,
    ProbeUnavailable,
    Runner,
    Verdict,
)
from ..common.errors import UpstreamHTTPError
from ..common.host_session import SessionRejected

APP_URL = "https://www.crunchbase.com/"
LOGIN_URL = "https://www.crunchbase.com/login"
API_URL = "https://www.crunchbase.com/v4/data"
SEARCH_LIMIT = 25

# The probe reads one known organization record: it answers 200 to a logged-in session
# only. A fetch that fails in the page returns status 0.
_PROBE_JS = """async (url) => {
    try {
        const r = await fetch(url, {credentials: "include",
            headers: {"content-type": "application/json"}});
        return r.status;
    } catch (e) { return 0; }
}"""
_PROBE_URL = f"{API_URL}/entities/organizations/crunchbase?field_ids=identifier"


def permalink(value: str, kind: str) -> str:
    """The permalink of an organization or a person (`kind`): the value itself, or the
    segment that follows `/organization/` or `/person/` in a Crunchbase URL."""
    v = (value or "").strip()
    marker = f"/{kind}/"
    if marker in v:
        v = v.split(marker, 1)[1]
    v = v.split("/")[0].split("?")[0]
    if not v:
        raise ValueError(f"a {kind} permalink (or its Crunchbase URL) is required")
    return v


def read_probe(status) -> Verdict:
    """Verdict of the login probe from its status. 200: logged in; 401 or 403: refused;
    0 (the fetch failed in the page): no session yet; anything else: no verdict."""
    if status == 200:
        return Verdict(True, LOGGED_IN)
    if status in (401, 403):
        return Verdict(False, AUTH_REJECTED,
                       f"Crunchbase refused the session ({status}): log in again.")
    if status == 0:
        return Verdict(False, NO_SESSION,
                       "Crunchbase did not recognize a session: finish logging in, "
                       "then check again.")
    raise ProbeUnavailable(f"the Crunchbase login probe answered {status}, which says "
                           "nothing about the session")


class CrunchbaseClient:
    """Crunchbase, read through the host's browser.

    `run` is the host's browser runner, `jar` the session it keeps (required to call),
    `on_jar` the callback that receives the session a login established."""

    def __init__(self, run: Runner, *, jar: str | None = None,
                 on_jar: Callable[[str], None] | None = None):
        self._session = BrowserSession(run, name="CRUNCHBASE_SESSION", jar=jar, on_jar=on_jar)

    # -- login ---------------------------------------------------------------------

    @staticmethod
    def probe(evaluate: Evaluate) -> Verdict:
        """Whether the session `evaluate` is bound to is logged in (`read_probe`)."""
        return read_probe(evaluate(APP_URL, _PROBE_JS, _PROBE_URL))

    def finish_login(self, jar: str) -> Verdict:
        """Probe the session a login window established (`jar`); logged in, it is handed
        to the host through `on_jar`."""
        return self._session.finish_login(jar, self.probe)

    # -- reads ---------------------------------------------------------------------

    def _get(self, path: str, query: dict | None = None) -> dict:
        url = API_URL + path + (f"?{urlencode(query)}" if query else "")
        res = self._session.fetch(APP_URL, url)
        status = res["status"]
        if status in (401, 403):
            raise SessionRejected(f"Crunchbase refused the session ({status}): a person "
                                  "must log in again")
        if not 200 <= status < 300:
            raise UpstreamHTTPError(status, res.get("data"), service="crunchbase",
                                    method="GET", url=API_URL + path)
        return res["data"]

    def get_company(self, slug: str) -> dict:
        """An organization by permalink (or URL): `properties` and the `founders` and
        `raised_funding_rounds` cards, raw."""
        return self._get(f"/entities/organizations/{quote(permalink(slug, 'organization'))}",
                         {"card_ids": "founders,raised_funding_rounds"})

    def get_person(self, slug: str) -> dict:
        """A person by permalink (or URL): their `properties`, raw."""
        return self._get(f"/entities/people/{quote(permalink(slug, 'person'))}")

    def get_funding_rounds(self, slug: str) -> dict:
        """The `raised_funding_rounds` card of an organization (permalink or URL), raw."""
        return self._get(f"/entities/organizations/{quote(permalink(slug, 'organization'))}",
                         {"card_ids": "raised_funding_rounds"})

    def search_companies(self, query: str, limit: int = 10) -> dict:
        """Organizations matching `query` (autocomplete): `{entities}`, raw; each carries
        an `identifier` with the `permalink`. `limit` from 1 to 25."""
        return self._search(query, "organization.companies", limit)

    def search_people(self, query: str, limit: int = 10) -> dict:
        """People matching `query` (autocomplete): `{entities}`, raw. `limit` from 1 to 25."""
        return self._search(query, "person.people", limit)

    def _search(self, query: str, collection: str, limit: int) -> dict:
        if not (query or "").strip():
            raise ValueError("`query` is required")
        if not 1 <= int(limit) <= SEARCH_LIMIT:
            raise ValueError(f"`limit` must be from 1 to {SEARCH_LIMIT}")
        return self._get("/autocompletes", {"query": query, "collection_ids": collection,
                                            "limit": int(limit)})
