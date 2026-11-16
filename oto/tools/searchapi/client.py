"""SearchApi.io — one search endpoint, the vertical picked by `engine`.

`engine` stays open: any SearchApi engine id is accepted, including one this module does
not name — that is the service's capability, not closed by an allowlist. The typed fields
are the params common to most engines; `params` carries any engine-specific one and is
merged last. The key travels in `Authorization: Bearer`, never in the query.
"""
from __future__ import annotations

from typing import Any, Optional

import requests

from ..common.credentials import require
from ..common.errors import raise_for_upstream

_TIMEOUT = (10, 30)  # (connect, read)


class SearchApiClient:
    """SearchApi.io client: `search` (billed per request) and `account` (free usage read)."""

    BASE_URL = "https://www.searchapi.io/api/v1"

    def __init__(self, api_key: str):
        self.api_key = require(api_key, "SEARCHAPI_API_KEY")
        self.session = requests.Session()

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        r = self.session.get(f"{self.BASE_URL}{path}", params=params, timeout=_TIMEOUT,
                             headers={"Authorization": f"Bearer {self.api_key}"})
        raise_for_upstream(r, service="searchapi")
        return r.json()

    def search(
        self,
        engine: str,
        query: Optional[str] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
        location: Optional[str] = None,
        num: Optional[int] = None,
        page: Optional[int] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict:
        """One SearchApi call; returns the raw JSON payload.

        Args:
            engine: SearchApi engine id (`google`, `google_news`, `google_jobs`,
                `google_maps`, `youtube`, `bing`…). Required, no default vertical.
            query: the engine's `q`.
            country: 2-letter country code (`gl`).
            language: UI language (`hl`).
            location: geographic anchor, e.g. "Paris, France".
            num: results per page.
            page: result page (1-based).
            params: engine-specific params, merged LAST: a key here overrides the typed
                field it duplicates. Needed for an engine whose input is not `q`.

        Either `query` or `params` is required. An engine that does not support a typed
        field rejects the call upstream (`UpstreamHTTPError`).
        """
        if not (engine or "").strip():
            raise ValueError("`engine` is required (the SearchApi vertical, e.g. 'google', "
                             "'google_news', 'google_jobs', 'google_maps', 'youtube')")
        if query is None and not params:
            raise ValueError(f"engine '{engine}' requires `query` (the engine's `q`) — or "
                             "`params` for an engine whose input is not `q`")
        payload: dict[str, Any] = {"q": query, "gl": country, "hl": language,
                                   "location": location, "num": num, "page": page}
        if params:
            payload.update(params)
        payload = {k: v for k, v in payload.items() if v is not None}
        payload["engine"] = engine
        return self._get("/search", payload)

    def account(self) -> dict:
        """Account usage (remaining credits, hourly limit): free, unlike `search`. Serves as
        the connection test; it authenticates, it does not say the account can search."""
        return self._get("/me")
