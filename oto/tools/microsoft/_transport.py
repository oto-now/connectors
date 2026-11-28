"""Microsoft Graph transport, shared by every surface client (files, mail, calendar,
Teams): the person's Bearer token, the error contract, `@odata.nextLink` paging.

A delegated access token is obtained and refreshed by the consumer
(`auth.exchange_code` / `auth.refresh`); the clients only spend it. An expired one
surfaces as a 401 `UpstreamHTTPError`, a scope the grant lacks as a 403: refreshing
or reconnecting is the consumer's job. Throttling (429, `Retry-After`) surfaces the
same way and is not retried here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from ..common import raise_for_upstream
from ..common.credentials import require

_HTTP_TIMEOUT = (10, 120)  # (connect, read) — downloads can be large
BASE_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_PAGE_SIZE = 200


def segment(value: str, name: str) -> str:
    """An id as one URL path segment: required, every reserved character
    percent-encoded (Teams ids carry `:` and `@`, Outlook ids `=`)."""
    return quote(require(value, name), safe="")


class GraphBase:
    """Microsoft Graph v1.0 on behalf of one person."""

    def __init__(self, access_token: Optional[str] = None, *,
                 session: Optional[requests.Session] = None):
        """
        Args:
            access_token: a delegated access token of the signed-in person (see
                `auth.exchange_code` / `auth.refresh`).
            session: the `requests.Session` every request goes out through (a
                transport the consumer controls); a new one by default. The token
                travels with each request, never set on a session given here.

        A missing token raises `MissingCredential`: the library never reads
        secrets on its own.
        """
        self._headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {require(access_token, 'MICROSOFT_ACCESS_TOKEN')}"}
        self.session = session if session is not None else requests.Session()

    def _url(self, path_or_url: str) -> str:
        return path_or_url if path_or_url.startswith("https://") else f"{BASE_URL}{path_or_url}"

    def _send(self, method: str, path_or_url: str, **kwargs: Any) -> requests.Response:
        """One request through `self.session`, the person's token with it; the answer
        as is, whatever its status (the caller checks it and closes a stream)."""
        headers = {**self._headers, **(kwargs.pop("headers", None) or {})}
        return self.session.request(method, self._url(path_or_url), headers=headers,
                                    timeout=_HTTP_TIMEOUT, **kwargs)

    def _request(self, method: str, path_or_url: str, **kwargs: Any) -> requests.Response:
        resp = self._send(method, path_or_url, **kwargs)
        raise_for_upstream(resp, service="microsoft")
        return resp

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._request(method, path, **kwargs)
        if resp.status_code == 204 or not (resp.content or b"").strip():
            return None
        return resp.json()

    def _paged(self, path: str, params: Optional[Dict[str, Any]] = None, *, limit: int,
               page_size: Optional[int] = DEFAULT_PAGE_SIZE, **kwargs: Any
               ) -> List[Dict[str, Any]]:
        """Follows `@odata.nextLink` until `limit` items are collected.

        `page_size` is the endpoint's `$top` ceiling (50 on Teams messages and
        chats), `None` for an endpoint that refuses `$top`. Extra `kwargs`
        (headers) go with every page.
        """
        if limit < 1:
            raise ValueError("limit must be ≥ 1")
        first = dict(params or {})
        if page_size is not None:
            first["$top"] = min(limit, page_size)
        items: List[Dict[str, Any]] = []
        page = self._json("GET", path, params=first, **kwargs)
        while True:
            items.extend((page or {}).get("value") or [])
            next_link = (page or {}).get("@odata.nextLink")
            if len(items) >= limit or not next_link:
                return items[:limit]
            page = self._json("GET", next_link, **kwargs)

    def get_me(self) -> Dict[str, Any]:
        """GET /me — the signed-in person (`id`, `displayName`, `mail`,
        `userPrincipalName`)."""
        return self._json("GET", "/me")
