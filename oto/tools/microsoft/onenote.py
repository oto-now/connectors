"""Microsoft Graph client — OneNote notebooks, sections and pages the signed-in person
can reach (delegated access, scope `scopes.NOTES`).

## Protocol facts that shape a caller

- **A notebook lives somewhere**: the person's own (`/me/onenote`, the default), a
  Microsoft 365 group's — a team's notebook — (`group_id`), or a SharePoint site's
  (`site_id`). Every id is only valid under the root it came from: a page of a team
  notebook is not found under `/me`, so the client is built for ONE root.
- **A page is HTML in, HTML out.** It is created from an HTML document (its
  `<title>` is the page title) and read back as HTML that OneNote rewrites (absolute
  positioning, its own `data-id` / `id` attributes, images as Graph resource URLs
  that need the same token).
- **A page is changed by commands, not replaced** (`PATCH …/content`): each command
  names a `target` (`body`, an element's `#id` or `data-id`), an `action` (`append`,
  `prepend`, `insert`, `replace`) and HTML `content`. Element ids come from reading
  the content with `include_ids=True`.
- **Listing every page across notebooks is fragile**: `GET /onenote/pages` without
  a section fails (error 20266) once the person has too many sections — list a
  section's pages. A notebook's `sections` are its top-level ones only; the
  root-wide `list_sections()` also returns those inside section groups.
- A created page is indexed with a delay: it can be missing from a listing for a few
  seconds.
"""
from __future__ import annotations

from html import escape
from typing import Any, Dict, Iterable, List, Optional

import requests

from ..common.credentials import require
from ._transport import GraphBase, segment
from .files import _odata_string

#: Graph's `$top` ceiling on OneNote collections.
ONENOTE_PAGE_SIZE = 100
#: The `action` values a page update accepts.
UPDATE_ACTIONS = frozenset({"append", "prepend", "insert", "replace"})


class OneNoteClient(GraphBase):
    """The OneNote notebooks under ONE root: the person's (default), a group's
    (`group_id`) or a SharePoint site's (`site_id`)."""

    def __init__(self, access_token: Optional[str] = None, *,
                 group_id: Optional[str] = None, site_id: Optional[str] = None,
                 session: Optional[requests.Session] = None):
        if group_id and site_id:
            raise ValueError("name group_id or site_id, not both: a notebook has one root")
        super().__init__(access_token, session=session)
        if group_id:
            self.root = f"/groups/{segment(group_id, 'group_id')}/onenote"
        elif site_id:
            self.root = f"/sites/{segment(site_id, 'site_id')}/onenote"
        else:
            self.root = "/me/onenote"

    def _page(self, page_id: str) -> str:
        return f"{self.root}/pages/{segment(page_id, 'page_id')}"

    def _list(self, path: str, params: Optional[Dict[str, Any]], limit: int
              ) -> List[Dict[str, Any]]:
        return self._paged(path, params, limit=limit, page_size=ONENOTE_PAGE_SIZE)

    def list_notebooks(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        """GET …/notebooks — the notebooks (`id`, `displayName`, `isShared`,
        `userRole`, `links`), most recently modified first."""
        return self._list(f"{self.root}/notebooks",
                          {"$orderby": "lastModifiedDateTime desc"}, limit)

    def list_sections(self, *, notebook_id: Optional[str] = None, limit: int = 100
                      ) -> List[Dict[str, Any]]:
        """GET …/sections — a notebook's top-level sections, or every section of the
        root (section groups included) when `notebook_id` is omitted, each with its
        `parentNotebook`."""
        path = (f"{self.root}/notebooks/{segment(notebook_id, 'notebook_id')}/sections"
                if notebook_id else f"{self.root}/sections")
        return self._list(path, {"$expand": "parentNotebook($select=id,displayName)"},
                          limit)

    def list_pages(self, *, section_id: Optional[str] = None,
                   title_contains: Optional[str] = None, limit: int = 50
                   ) -> List[Dict[str, Any]]:
        """GET …/pages — a section's pages (or the root's, see the protocol facts),
        most recently modified first, metadata only (`id`, `title`, `createdDateTime`,
        `lastModifiedDateTime`, `links`, `parentSection`). `title_contains` filters on
        the title, case-insensitively — not on the page text."""
        path = (f"{self.root}/sections/{segment(section_id, 'section_id')}/pages"
                if section_id else f"{self.root}/pages")
        params: Dict[str, Any] = {"$orderby": "lastModifiedDateTime desc",
                                  "$expand": "parentSection($select=id,displayName)"}
        if title_contains:
            params["$filter"] = (
                f"contains(tolower(title),'{_odata_string(title_contains.lower())}')")
        return self._list(path, params, limit)

    def get_page(self, page_id: str) -> Dict[str, Any]:
        """GET …/pages/{id} — the page's metadata, with its section and notebook."""
        return self._json("GET", self._page(page_id), params={
            "$expand": "parentSection($select=id,displayName),"
                       "parentNotebook($select=id,displayName)"})

    def get_page_content(self, page_id: str, *, include_ids: bool = False) -> str:
        """GET …/pages/{id}/content — the page as HTML. `include_ids=True` adds the
        element ids that `update_page` targets."""
        resp = self._request("GET", f"{self._page(page_id)}/content",
                             params={"includeIDs": "true"} if include_ids else None,
                             headers={"Accept": "text/html"})
        return resp.text

    def create_page(self, section_id: str, *, title: str, body_html: str = ""
                    ) -> Dict[str, Any]:
        """POST …/sections/{id}/pages — a new page titled `title` (plain text, escaped
        here) whose body is `body_html`. Returns the created page's metadata."""
        document = (f"<!DOCTYPE html><html><head><title>{escape(require(title, 'title'))}"
                    f"</title></head><body>{body_html or ''}</body></html>")
        return self._json(
            "POST", f"{self.root}/sections/{segment(section_id, 'section_id')}/pages",
            data=document.encode("utf-8"),
            headers={"Content-Type": "text/html; charset=utf-8"})

    def update_page(self, page_id: str, commands: Iterable[Dict[str, Any]]) -> None:
        """PATCH …/pages/{id}/content — applies `commands` (`target`, `action`,
        `content`, optional `position` `before`/`after`), in order.

        Raises `ValueError` before any call for an empty list or an unknown action."""
        commands = [dict(c) for c in commands]
        if not commands:
            raise ValueError("commands is empty: name what to change on the page")
        for c in commands:
            if c.get("action") not in UPDATE_ACTIONS:
                raise ValueError(f"action {c.get('action')!r} is not one of "
                                 f"{sorted(UPDATE_ACTIONS)}")
            require(c.get("target"), "target")
        self._json("PATCH", f"{self._page(page_id)}/content", json=commands)

    def append_to_page(self, page_id: str, html: str, *, target: str = "body") -> None:
        """Adds `html` at the end of the page (`target="body"`) or of one of its
        elements."""
        self.update_page(page_id, [{"target": target, "action": "append",
                                    "content": require(html, "html")}])

    def create_section(self, notebook_id: str, name: str) -> Dict[str, Any]:
        """POST …/notebooks/{id}/sections — a new section in the notebook."""
        return self._json(
            "POST", f"{self.root}/notebooks/{segment(notebook_id, 'notebook_id')}/sections",
            json={"displayName": require(name, "name")})

    def create_notebook(self, name: str) -> Dict[str, Any]:
        """POST …/notebooks — a new notebook under the root."""
        return self._json("POST", f"{self.root}/notebooks",
                          json={"displayName": require(name, "name")})

    def delete_page(self, page_id: str) -> str:
        """DELETE …/pages/{id}. Returns the page id."""
        self._json("DELETE", self._page(page_id))
        return page_id
