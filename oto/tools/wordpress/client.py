"""WordPress REST API client (core `wp/v2` + any namespace the site exposes).

Auth = **Application Password** (WordPress ≥ 5.6), sent as HTTP Basic over
HTTPS only — an `http://` site is refused (the password would travel in clear),
unless the caller passes `allow_http=True` for a local development site. The
only built-in way for a third party to call the REST API without
a plugin. The credential is three values: the site URL, the WordPress
username, and the application password (spaces are part of the display format
and are accepted as-is by WordPress).

The API root is DISCOVERED, never assumed: `/wp-json/` on sites with pretty
permalinks, `/?rest_route=/` on the others (both are core, same routes). The
first call probes `/wp-json/` and falls back to `?rest_route=` when it does not
answer the REST index — cached for the client's lifetime.

Redirects are NOT followed (`allow_redirects=False`): `requests` drops the
Authorization header on a cross-host redirect (the call would come back as an
anonymous 401 that blames the password), and an upstream redirect is also the
classic way to turn a vetted public URL into a request to an internal host.
Any 3xx is raised: a redirect as an actionable error naming the target — the
user fixes the site URL once (http→https, www, a moved domain) —, the other 3xx
(300, 304, 305…) as an upstream error, never read as an empty success.

A 429 is retried after a short wait (`Retry-After` in seconds or as an HTTP
date, else 1 s then 2 s); a wait over 10 s is raised with its delay
(`WordPressRateLimited`), never slept through — the caller is not blocked.

Pagination headers (`X-WP-Total`, `X-WP-TotalPages`) carry the totals, so the
list call returns `{"items", "total", "total_pages"}` rather than a bare list.
"""
from __future__ import annotations

import re
import time
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ..common.errors import UpstreamHTTPError

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never wait indefinitely
_USER_AGENT = "oto-wordpress/1 (+https://oto.cx)"
_REDIRECTS = (301, 302, 303, 307, 308)
_MAX_RETRY_WAIT = 10  # s — beyond that, the 429 is raised with its delay instead of slept through
# A route = segments `[A-Za-z0-9_-]` separated by `/` (`wp/v2/posts`, `wc/v3/products`):
# no `..`, no query, no fragment — nothing that escapes the requested route.
_ROUTE_RE = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")


class WordPressRedirect(ValueError):
    """The site answered with a redirect — the stored site URL is not the one
    WordPress serves. Carries the target so the caller can say which URL to use."""

    def __init__(self, location: str):
        self.location = location
        super().__init__(
            f"the WordPress site redirects to {location or '(target missing)'} — "
            "store that address as the site URL instead (https, with or "
            "without www depending on what the site serves).")


class WordPressRateLimited(UpstreamHTTPError):
    """429 whose `Retry-After` asks for more than the client waits in-line:
    raised with the delay (`retry_after`, seconds) instead of blocking."""

    def __init__(self, retry_after: Optional[float]):
        wait = f"retry in {int(retry_after)} s" if retry_after is not None else "retry later"
        super().__init__(429, f"WordPress rate limit reached — {wait}.",
                         service="wordpress", retry_after=retry_after)


class WordPressMediaFieldsError(UpstreamHTTPError):
    """The media was uploaded, but setting its fields (alt text, caption…)
    failed. Carries the created `media_id`: retrying the upload would create a
    duplicate — update that media instead."""

    def __init__(self, media_id: int, cause: UpstreamHTTPError):
        self.media_id = media_id
        self.cause = cause
        super().__init__(
            cause.status_code,
            f"media {media_id} created, but its fields were not set "
            f"({cause.body!r}) — update media {media_id} instead of "
            "uploading it again.",
            service="wordpress")


def normalize_site_url(site_url: str, *, allow_http: bool = False) -> str:
    """`https://example.com/blog` form: scheme kept (defaults to https), no
    trailing slash, no query/fragment. A WordPress installed in a sub-directory
    keeps its path — the REST root lives under it.

    `http://` is refused unless `allow_http=True` (a local development site):
    the application password would be sent in clear. Credentials embedded in
    the URL (`https://user:pass@host`) are refused — they would end up in the
    stored URL and in error messages."""
    raw = (site_url or "").strip()
    if not raw:
        raise ValueError("WordPress site URL missing.")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Invalid WordPress site URL: http(s) scheme and host name expected.")
    if parts.username is not None or parts.password is not None:
        raise ValueError(
            "Invalid WordPress site URL: it must not contain credentials "
            "(user:password@). The application password is entered separately.")
    if parts.scheme == "http" and not allow_http:
        raise ValueError(
            "WordPress site URL over http:// refused: the application password "
            "would be sent in clear. Use the site's https:// address.")
    path = parts.path.rstrip("/")
    # Pasting the admin or the API URL is common — keep the site root.
    for suffix in ("/wp-admin", "/wp-json", "/wp-login.php"):
        idx = path.find(suffix)
        if idx != -1:
            path = path[:idx]
    return f"{parts.scheme}://{parts.netloc}{path}"


class WordPressClient:
    def __init__(self, site_url: str, username: str, application_password: str,
                 *, rest_mode: Optional[str] = None, allow_http: bool = False,
                 session: Optional[requests.Session] = None):
        """`session`: the `requests.Session` every request (probe, public index,
        authenticated calls) goes out through, when the consumer supplies one —
        e.g. a session whose transport checks each destination at connection
        time. Absent, module-level `requests`, as before."""
        self.site_url = normalize_site_url(site_url, allow_http=allow_http)
        self._auth = (require((username or "").strip(), "WORDPRESS_USERNAME"),
                      require((application_password or "").strip(),
                              "WORDPRESS_APPLICATION_PASSWORD"))
        # "pretty" (/wp-json/<route>) | "query" (/?rest_route=/<route>) — probed lazily.
        self._rest_mode = rest_mode
        self._http = session if session is not None else requests

    # --- transport -------------------------------------------------------------

    @staticmethod
    def _route(route: str) -> str:
        """The REST route, checked: `wp/v2/posts`-like segments only (empty =
        the index). A `?`, `#`, `..` or encoded character would let a route
        reach another endpoint or smuggle a parameter (`7?force=true`)."""
        route = (route or "").strip("/")
        if route and not _ROUTE_RE.match(route):
            raise ValueError(f"route REST WordPress invalide : {route!r}.")
        return route

    @staticmethod
    def _item(route: str, item_id: Any) -> str:
        """`<route>/<id>` with a strictly positive integer id."""
        if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id < 1:
            raise ValueError(f"identifiant WordPress invalide : {item_id!r} (entier > 0 attendu).")
        return f"{route.strip('/')}/{item_id}"

    def _url(self, route: str, mode: str) -> Tuple[str, Dict[str, str]]:
        route = self._route(route)
        if mode == "pretty":
            return f"{self.site_url}/wp-json/{route}", {}
        return f"{self.site_url}/", {"rest_route": f"/{route}"}

    @property
    def rest_mode(self) -> str:
        if self._rest_mode is None:
            self._rest_mode = self._probe_rest_mode()
        return self._rest_mode

    def _probe_rest_mode(self) -> str:
        """`/wp-json/` if it answers the REST index, else `?rest_route=`. The
        probe is unauthenticated on purpose: the index is public, and a wrong
        mode must not be mistaken for a wrong password.

        A redirect on `/wp-json/` alone is not conclusive (some hosts rewrite
        that path while `?rest_route=` answers directly): it is raised only if
        the other form does not answer either."""
        redirect: Optional[str] = None
        for mode in ("pretty", "query"):
            url, params = self._url("", mode)
            resp = self._http.get(url, params=params, timeout=_HTTP_TIMEOUT,
                                  allow_redirects=False,
                                headers={"User-Agent": _USER_AGENT,
                                         "Accept": "application/json"})
            if resp.status_code in _REDIRECTS:
                redirect = redirect or resp.headers.get("Location", "")
                continue
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except ValueError:
                    continue
                if isinstance(body, dict) and "namespaces" in body:
                    return mode
        if redirect is not None:
            raise WordPressRedirect(redirect)
        raise ValueError(
            f"{self.site_url} does not answer like a WordPress site: neither "
            "/wp-json/ nor ?rest_route=/ serves the REST API. Check the URL, or "
            "that a security plugin is not disabling the REST API.")

    def request(self, method: str, route: str, *, params: Optional[dict] = None,
                json: Any = None, data: Optional[bytes] = None,
                headers: Optional[dict] = None) -> Tuple[Any, Dict[str, str]]:
        """One REST call → `(parsed body, response headers)`. `route` is the path
        after the REST root, namespace included (`wp/v2/posts`)."""
        url, base_params = self._url(route, self.rest_mode)
        all_params = {**base_params, **(params or {})}
        hdrs = {"User-Agent": _USER_AGENT, "Accept": "application/json",
                **(headers or {})}
        for attempt in range(3):
            resp = self._http.request(
                method, url, params=all_params, json=json, data=data,
                headers=hdrs, auth=self._auth, timeout=_HTTP_TIMEOUT,
                allow_redirects=False)
            if resp.status_code == 429:
                asked = _retry_after(resp)
                wait = float(2 ** attempt) if asked is None else asked
                if attempt == 2 or wait > _MAX_RETRY_WAIT:
                    raise WordPressRateLimited(asked)
                time.sleep(wait)
                continue
            return _parse(resp), dict(resp.headers)
        raise AssertionError("unreachable")

    def _get(self, route: str, **params) -> Any:
        return self.request("GET", route, params=params or None)[0]

    # --- site ------------------------------------------------------------------

    def index(self) -> Dict:
        """The REST index: name, description, url, `namespaces` (what plugins are
        installed: `yoast/v1`, `rankmath/v1`, `wc/v3`, `acf/v3`…), and
        `authentication` (application-passwords authorization endpoint)."""
        return self._get("")

    def public_index(self) -> Dict:
        """The REST index fetched WITHOUT credentials. What a caller needs
        before it has a working password (the application-passwords
        authorization endpoint): WordPress checks Basic credentials on EVERY
        REST route, the public index included, so a placeholder login would
        turn this public read into a 401."""
        url, params = self._url("", self.rest_mode)
        resp = self._http.get(url, params=params, timeout=_HTTP_TIMEOUT,
                              allow_redirects=False,
                              headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
        body = _parse(resp)
        if not isinstance(body, dict):
            raise UpstreamHTTPError(502, "index REST WordPress inattendu (objet attendu).",
                                    service="wordpress")
        return body

    def me(self) -> Dict:
        """The authenticated user, `context=edit` so `roles` and `capabilities`
        come back — the only way to know what this password is allowed to do."""
        return self._get("wp/v2/users/me", context="edit")

    def settings(self) -> Dict:
        """Site settings (title, timezone, default category…). Admin only (403
        otherwise) — callers treat a 403 as "not available", not as an error."""
        return self._get("wp/v2/settings")

    def types(self) -> Dict:
        """Post types keyed by slug, with `rest_base` and `rest_namespace`."""
        return self._get("wp/v2/types", context="edit")

    def taxonomies(self) -> Dict:
        return self._get("wp/v2/taxonomies", context="edit")

    # --- generic collection CRUD (posts, pages, CPTs, terms, media) --------------

    def list(self, route: str, *, page: int = 1, per_page: int = 20,
             **params) -> Dict:
        """One page of a collection. `per_page` is capped at 100 by WordPress."""
        q = {"page": page, "per_page": max(1, min(per_page, 100)),
             **{k: v for k, v in params.items() if v is not None}}
        body, headers = self.request("GET", route, params=q)
        if not isinstance(body, list):
            # An object here = a route that is not a collection, or a plugin
            # answering its own shape — never read it as an empty page.
            raise UpstreamHTTPError(
                502, f"{route} did not return a list (received: {type(body).__name__}).",
                service="wordpress")
        lower = {k.lower(): v for k, v in headers.items()}
        def _int(name):
            try:
                return int(lower.get(name))
            except (TypeError, ValueError):
                return None
        return {"items": body,
                "total": _int("x-wp-total"),
                "total_pages": _int("x-wp-totalpages"),
                "page": page}

    def get(self, route: str, item_id: int, *, context: str = "edit") -> Dict:
        return self._get(self._item(route, item_id), context=context)

    def create(self, route: str, body: Dict) -> Dict:
        return self.request("POST", route, json=body)[0]

    def update(self, route: str, item_id: int, body: Dict) -> Dict:
        return self.request("POST", self._item(route, item_id), json=body)[0]

    def delete(self, route: str, item_id: int, *, force: bool = False) -> Dict:
        """`force=False` moves a post to the trash (restorable). Terms and media
        do not support trashing — WordPress refuses them without `force=True`
        (`rest_trash_not_supported`), which the caller must ask for explicitly."""
        params = {"force": "true"} if force else None
        return self.request("DELETE", self._item(route, item_id), params=params)[0]

    # --- media -------------------------------------------------------------------

    def upload_media(self, content: bytes, filename: str, mime_type: str,
                     **fields) -> Dict:
        """Raw-body upload (`Content-Disposition` carries the filename), then the
        optional fields (`alt_text`, `caption`, `title`, `description`, `post`)
        in a second call — the raw-body form cannot carry them. If that second
        call fails, `WordPressMediaFieldsError` carries the created media id."""
        media, _ = self.request(
            "POST", "wp/v2/media", data=content,
            headers={"Content-Type": mime_type or "application/octet-stream",
                     "Content-Disposition": f'attachment; filename="{_ascii_filename(filename)}"'})
        extra = {k: v for k, v in fields.items() if v is not None}
        if extra:
            media_id = media.get("id") if isinstance(media, dict) else None
            if not isinstance(media_id, int):
                raise UpstreamHTTPError(502, "upload returned no media id.",
                                        service="wordpress")
            try:
                media = self.update("wp/v2/media", media_id, extra)
            except UpstreamHTTPError as e:
                raise WordPressMediaFieldsError(media_id, e) from e
        return media


def _ascii_filename(filename: str) -> str:
    """A `Content-Disposition` filename WordPress reads back intact: ASCII
    (accents folded: `café.png` → `cafe.png`; `requests` would send other
    characters as latin-1 or fail), without the characters its header parser
    splits or unescapes on (`;`, `\\`, `"`) nor control characters."""
    folded = unicodedata.normalize("NFKD", filename or "").encode("ascii", "ignore").decode()
    safe = re.sub(r'[;\\"\x00-\x1f\x7f]', "", folded).strip()
    return safe or "upload"


def _retry_after(resp: Any) -> Optional[float]:
    """`Retry-After` in seconds — delta-seconds or an HTTP date; `None` when it
    is absent or unreadable."""
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _parse(resp: Any) -> Any:
    """A response → its JSON body. A redirect raises `WordPressRedirect`, any
    other 3xx or an error status `UpstreamHTTPError`; a non-JSON success is a
    502 (a cache/WAF page or a PHP notice printed before the payload) — never
    read as parsed."""
    status = resp.status_code
    if status in _REDIRECTS:
        raise WordPressRedirect(resp.headers.get("Location", ""))
    if 300 <= status < 400:
        raise UpstreamHTTPError(502, f"unexpected HTTP {status} response.", service="wordpress")
    raise_for_upstream(resp, service="wordpress")
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        raise UpstreamHTTPError(
            502, f"non-JSON response (start: {resp.text[:200]!r})", service="wordpress")
