"""Posts, engagement, home feed and a member's activity.

Extracted from `client.py` (split by domain, frozen public surface):
the bodies are unchanged. This mixin is never instantiated on its own — it is
composed into `UnipileClient`, which provides the transport (`_request`,
`_acct`, `_norm`, `_by_shape`, `session`).
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from ..const import FEED_QUERY_ID
from ..feed import _unpack_cursor, parse_feed
from ..open_addresses import without_open_addresses


class _ContentMixin:
    """Posts, engagement, home feed and a member's activity."""

    def _member_id(self, identifier: str) -> str:
        """Resolve a member identifier to the **provider_id (URN, `ACoAA…`)**
        expected by the v2 posts/comments/reactions endpoints: the public slug
        returns 400 "Invalid User ID" there (v2 delta seen live 2026-07-06). Already
        opaque URN → as is; numeric id of a company page → as is (the v2 reference
        lists a page's posts under its numeric id); slug → resolved via the
        profile (1 call)."""
        ident = str(identifier).strip()
        if ident.startswith(("ACoA", "urn:")) or ident.isdigit():
            return ident
        prof = self.get_profile(ident)
        return str((prof or {}).get("provider_id") or (prof or {}).get("id") or ident)

    def list_member_posts(self, identifier: str, cursor: Optional[str] = None,
                          limit: Optional[int] = None) -> dict:
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = limit
        return without_open_addresses("list_member_posts", self._norm(self._request(
            "GET", self._acct(f"/users/{quote(self._member_id(identifier), safe='')}/posts"),
            params=params,
        )), listed=True)

    def get_post(self, post_id: str) -> dict:
        return without_open_addresses("get_post", self._request(
            "GET", self._acct(f"/posts/{quote(post_id, safe='')}")
        ))

    def list_comments(self, post_id: str, offset: Optional[int] = None,
                      limit: Optional[int] = None,
                      comment_id: Optional[str] = None) -> dict:
        """ONE page of a post's comments — or of the replies to a comment
        (`comment_id` → `/posts/{id}/comments/{comment_id}/comments`).

        ⚠️ Paginated by `offset` ALONE (Unipile v2 doc: "Pagination on the following
        endpoints uses exclusively the `offset` parameter"): a `cursor` never
        had any effect here, hence the cap at the first page (oto#177). Empty page = end.
        The page loop is up to the caller, who bounds volume and duration."""
        return self._engagement_page("comments", post_id, offset, limit, comment_id)

    def list_reactions(self, post_id: str, offset: Optional[int] = None,
                       limit: Optional[int] = None,
                       comment_id: Optional[str] = None) -> dict:
        """ONE page of a post's reactions — or of a comment's (`comment_id` →
        `/posts/{id}/comments/{comment_id}/reactions`). Same `offset`-only
        pagination as `list_comments`."""
        return self._engagement_page("reactions", post_id, offset, limit, comment_id)

    def _engagement_page(self, what: str, post_id: str, offset: Optional[int],
                         limit: Optional[int], comment_id: Optional[str]) -> dict:
        path = f"/posts/{quote(post_id, safe='')}"
        if comment_id:
            path += f"/comments/{quote(comment_id, safe='')}"
        params: dict[str, Any] = {}
        if offset:
            params["offset"] = offset
        if limit:
            params["limit"] = limit
        return self._norm(self._request(
            "GET", self._acct(f"{path}/{what}"), params=params,
        ))

    def create_post(self, text: str) -> dict:
        return self._request("POST", self._acct("/posts"), json={"text": text})

    def comment_post(self, post_id: str, text: str) -> dict:
        return self._request(
            "POST", self._acct(f"/posts/{quote(post_id, safe='')}/comments"),
            json={"text": text},
        )

    def react_post(self, post_id: str, value: str = "LIKE") -> dict:
        """React to a post. v2: body `{reaction}`."""
        return self._request(
            "POST", self._acct(f"/posts/{quote(post_id, safe='')}/reactions"),
            json={"reaction": value},
        )

    # ---- feed (Voyager passthrough via proxyRequest v2) -----------------

    def linkedin_raw(
        self,
        request_url: str,
        method: str = "GET",
        body: Optional[dict] = None,
        headers: Optional[dict] = None,
        encoding: bool = False,
        force_api: bool = False,
    ) -> dict:
        """Relay a raw Voyager request — v2: `POST /v2/{account}/linkedin/`
        (proxyRequest), body `{url, method, bypass_url_encoding, …}`."""
        payload: dict[str, Any] = {
            "url": request_url,
            "method": method,
            "bypass_url_encoding": not encoding,
        }
        if body is not None:
            payload["body"] = body
        if headers:
            payload["headers"] = headers
        return self._request("POST", self._acct("/linkedin/"), json=payload)

    def get_feed(
        self,
        count: int = 20,
        cursor: Optional[str] = None,
        raw: bool = False,
        sort_order: str = "MEMBER_SETTING",
    ) -> dict:
        """LinkedIn home feed via the Voyager Magic Route."""
        start, token = _unpack_cursor(cursor)
        if token:
            variables = (
                f"(start:{start},count:{count},"
                f"paginationToken:{token},sortOrder:{sort_order})"
            )
            request_url = (
                "https://www.linkedin.com/voyager/api/graphql"
                f"?variables={variables}&queryId={FEED_QUERY_ID}"
            )
        else:
            request_url = (
                "https://www.linkedin.com/voyager/api/graphql"
                f"?queryId={FEED_QUERY_ID}"
            )
        resp = self.linkedin_raw(request_url, method="GET", encoding=False)
        if raw:
            return resp
        return parse_feed(resp, count=count, start=start)

    # ---- me / followers / a member's activity ---------------------------

    def get_own_profile(self) -> dict:
        """Profile of the connected account. v2: `GET /users/me` (no #153 guard:
        the returned id ≠ the literal "me")."""
        return without_open_addresses("get_me", self._request("GET", self._acct("/users/me")))

    def list_followers(self, user_id: Optional[str] = None,
                      cursor: Optional[str] = None,
                      limit: Optional[int] = None) -> dict:
        uid = user_id or "me"
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = limit
        return self._norm(self._request(
            "GET", self._acct(f"/users/{quote(uid, safe='')}/followers"),
            params=params,
        ))

    def list_following(self, user_id: Optional[str] = None,
                      cursor: Optional[str] = None,
                      limit: Optional[int] = None) -> dict:
        uid = user_id or "me"
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = limit
        return self._norm(self._request(
            "GET", self._acct(f"/users/{quote(uid, safe='')}/following"),
            params=params,
        ))

    def list_member_comments(self, identifier: str,
                            cursor: Optional[str] = None,
                            limit: Optional[int] = None) -> dict:
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = limit
        return self._norm(self._request(
            "GET", self._acct(f"/users/{quote(self._member_id(identifier), safe='')}/comments"),
            params=params,
        ))

    def list_member_reactions(self, identifier: str,
                             cursor: Optional[str] = None,
                             limit: Optional[int] = None) -> dict:
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = limit
        return self._norm(self._request(
            "GET", self._acct(f"/users/{quote(self._member_id(identifier), safe='')}/reactions"),
            params=params,
        ))

