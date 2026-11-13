"""
Attio CRM API Client.

Requires: requests

No address that opens content without a login leaves the client: the call
recordings drop `video_url` (a signed download link), the workspace members
drop `avatar_url`.
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

import requests

from ..common.credentials import require
from ..common.errors import UpstreamHTTPError

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never wait indefinitely

# No address that opens content without a login leaves the client: a call
# recording's `video_url` is a signed download link (one hour) that opens the
# video to anyone holding it; a workspace member's `avatar_url` opens the
# picture to anyone.
VIDEO_ADDRESS_FIELDS = ("video_url",)
AVATAR_ADDRESS_FIELDS = ("avatar_url",)


def _without_addresses(answer: Any, fields: tuple) -> Any:
    """The answer whose `data` (one object, or each one of a list) lost `fields`."""
    if isinstance(answer, dict):
        data = answer.get("data")
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict):
                for name in fields:
                    item.pop(name, None)
    return answer


@dataclass
class Company:
    """Company record."""
    id: str
    name: str
    domain: str = None
    industry: str = None
    employee_count: int = None
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Person:
    """Person record."""
    id: str
    name: str
    email: str = None
    phone: str = None
    company_id: str = None
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Deal:
    """Deal record."""
    id: str
    name: str
    value: float = None
    stage: str = None
    company_id: str = None
    attributes: Dict[str, Any] = field(default_factory=dict)


class AttioResource:
    """Base class for Attio resources."""

    def __init__(self, client: "AttioClient", object_type: str):
        self.client = client
        self.object_type = object_type

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        sort: List[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """List records. Attio v2 has no GET records: we go through records/query."""
        body: Dict[str, Any] = {"limit": limit, "offset": offset}
        if sort:
            body["sorts"] = sort

        return self.client._request("POST", f"objects/{self.object_type}/records/query", json=body)

    def get(self, record_id: str) -> Dict[str, Any]:
        """Get a specific record."""
        return self.client._request("GET", f"objects/{self.object_type}/records/{record_id}")

    def create(self, **attributes) -> Dict[str, Any]:
        """Create a new record."""
        data = {"data": {"values": attributes}}
        return self.client._request("POST", f"objects/{self.object_type}/records", json=data)

    def update(self, record_id: str, overwrite_multiselect: bool = False,
               **attributes) -> Dict[str, Any]:
        """Update a record.

        PATCH (default) APPENDS the multiselect values passed to those that
        already exist, and an empty list changes nothing; PUT REPLACES them, and `[]`
        empties them — "Use the PUT endpoint to overwrite or remove multiselect attribute
        values" (Attio docs). Same switch as `AttioEntries.update`: without it, a
        unique value (domain, address) stuck on a record to be merged could not be
        freed by any call (signal #887).
        """
        method = "PUT" if overwrite_multiselect else "PATCH"
        data = {"data": {"values": attributes}}
        return self.client._request(method, f"objects/{self.object_type}/records/{record_id}", json=data)

    def merge(self, primary_record_id: str, secondary_record_id: str) -> Dict[str, Any]:
        """Merge two records of the SAME object (beta endpoint at Attio, signal #886).

        ⚠️ Irreversible and not idempotent: BOTH original records are marked
        merged and can no longer be read; Attio creates a THIRD one, whose id
        (`data.new_record_id`) matches neither. Where both carry a value, the
        primary's wins. Replaying returns 404; a 202 response says the merge is
        asynchronous (404 `merge_in_progress` until it finishes).
        Scopes: `record_permission:read-write` + `object_configuration:read`.
        """
        data = {"data": {"primary_record_id": primary_record_id,
                         "secondary_record_id": secondary_record_id}}
        return self.client._request("POST", f"objects/{self.object_type}/records/merge", json=data)

    def delete(self, record_id: str) -> Dict[str, Any]:
        """Delete a record."""
        return self.client._request("DELETE", f"objects/{self.object_type}/records/{record_id}")

    def search(
        self,
        query: str = None,
        filters: Dict[str, Any] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Search records.

        `records/query` does not support full-text search: a `query`
        is translated into a `{"name": {"$contains": ...}}` filter. An explicit
        `filters` (Attio `filter` format) takes precedence.
        """
        data: Dict[str, Any] = {"limit": limit}
        if query:
            data["filter"] = {"name": {"$contains": query}}
        if filters:
            data["filter"] = filters

        return self.client._request("POST", f"objects/{self.object_type}/records/query", json=data)


class AttioNotes:
    """Notes resource."""

    def __init__(self, client: "AttioClient"):
        self.client = client

    def create(
        self,
        parent_object: str,
        parent_record_id: str,
        title: str,
        content: str,
    ) -> Dict[str, Any]:
        """
        Create a note.

        Args:
            parent_object: Object type (companies, people, deals)
            parent_record_id: Record ID to attach note to
            title: Note title
            content: Note content (markdown)

        Returns:
            Created note
        """
        data = {
            "data": {
                "parent_object": parent_object,
                "parent_record_id": parent_record_id,
                "title": title,
                "format": "markdown",
                "content": content,
            }
        }
        return self.client._request("POST", "notes", json=data)

    # Attio's `limit` ceiling on /v2/notes: 50 accepted, 51 → HTTP 400
    # "Query params validation error" (with no further detail). Recorded on
    # 27/08/2026 by bisection against the real API, consistent with the docs.
    MAX_LIMIT = 50

    def list(self, parent_object: str = None, parent_record_id: str = None,
             limit: int = None, offset: int = None) -> List[Dict[str, Any]]:
        """List notes. `GET /v2/notes` can only do TWO things:
        paginate (`limit` 1-50, default **10**; `offset`) and restrict to a
        parent record (`parent_object` AND `parent_record_id` together — one
        without the other is refused with 400).

        ⚠️ **No sort and no date filter, and the API does not say so**: it SWALLOWS
        parameters it does not know, returning 200. Verified on 27/08/2026
        by differential — `sort=nonexistent_field:desc`, `created_at[gte]`,
        `created_after` and even `zzz_unknown=x` all come back 200 unchanged,
        whereas `/tasks` refuses an invalid `sort` with 400. This is why we
        do NOT expose a date bound here: it would bound nothing.

        Consequence for the caller, and the reason for signals #586/#597:
        notes come out from OLDEST to most recent, so today's are at the END of
        the collection. Without `limit`/`offset`, you only saw the ten oldest
        of the workspace. To reach the recent ones: advance `offset` in pages
        of 50 until a page shorter than `limit` (end of collection) — there is
        no total count. Cheaper alternative when the record is known: scope on
        `parent_object`+`parent_record_id`.
        """
        if limit is not None and not 1 <= limit <= self.MAX_LIMIT:
            raise ValueError(
                f"limit must be between 1 and {self.MAX_LIMIT} (Attio's ceiling on /notes); got {limit}")
        params = {}
        if parent_object:
            params["parent_object"] = parent_object
        if parent_record_id:
            params["parent_record_id"] = parent_record_id
        # No bound provided ⟹ no invented parameter: Attio's default
        # (10, the oldest) applies and the docstring says so.
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset

        return self.client._request("GET", "notes", params=params)

    def get(self, note_id: str) -> Dict[str, Any]:
        """Get a single note by ID."""
        return self.client._request("GET", f"notes/{note_id}")

    def delete(self, note_id: str) -> Dict[str, Any]:
        """Delete a note by ID. Attio API does not support updating note body."""
        return self.client._request("DELETE", f"notes/{note_id}")


class AttioTasks:
    """Tasks resource."""

    def __init__(self, client: "AttioClient"):
        self.client = client

    def _get_default_assignee(self) -> str:
        """Get first workspace member ID as default assignee."""
        data = self.client._request("GET", "workspace_members")
        members = data.get("data", [])
        if not members:
            raise Exception("No workspace members found")
        return members[0]["id"]["workspace_member_id"]

    def create(
        self,
        content: str,
        deadline: str = None,
        assignee_id: str = None,
        linked_object: str = None,
        linked_record_id: str = None,
    ) -> Dict[str, Any]:
        """
        Create a task.

        Args:
            content: Task description (max 2000 chars)
            deadline: ISO date or YYYY-MM-DD deadline
            assignee_id: Workspace member ID (defaults to first member)
            linked_object: Object type to link (companies, people)
            linked_record_id: Record ID to link

        Returns:
            Created task
        """
        if not assignee_id:
            assignee_id = self._get_default_assignee()

        task_data = {
            "content": content,
            "format": "plaintext",
            "is_completed": False,
            "assignees": [{"referenced_actor_type": "workspace-member", "referenced_actor_id": assignee_id}],
        }
        if deadline:
            if len(deadline) == 10:  # YYYY-MM-DD
                deadline = f"{deadline}T00:00:00.000Z"
            task_data["deadline_at"] = deadline
        if linked_object and linked_record_id:
            task_data["linked_records"] = [{
                "target_object": linked_object,
                "target_record_id": linked_record_id,
            }]

        return self.client._request("POST", "tasks", json={"data": task_data})

    # `GET /v2/tasks`: limit accepted up to 1000, 1001 → HTTP 400 (bisection
    # of 27/08/2026); default 500. `sort` is a REAL parameter, unlike
    # /notes: a value outside this set is refused with 400.
    MAX_LIMIT = 1000
    SORTS = ("created_at:asc", "created_at:desc", "completed_at:asc", "completed_at:desc")

    def list(self, completed: bool = None, limit: int = None, offset: int = None,
             sort: str = None) -> List[Dict[str, Any]]:
        """List tasks. Pagination `limit` (1-1000, default 500) + `offset`,
        sort `sort` among `created_at:asc|desc` and `completed_at:asc|desc`
        (default `created_at:asc` — OLDEST first, hence the truncated page
        "that stops in July" of signal #586).

        ⚠️ The completion filter is called `is_completed` at Attio, NOT
        `completed`. This client sent `completed`: an unknown name, silently
        swallowed (verified on 27/08/2026 by differential — `is_completed=NOTABOOL`
        → 400, `completed=NOTABOOL` → 200), so the filter advertised to the tool
        filtered nothing. The Python parameter keeps its name, only the wire name changes.

        No date filter here either: sorting `created_at:desc` and
        stopping is the only way to read a recent window.
        """
        if limit is not None and not 1 <= limit <= self.MAX_LIMIT:
            raise ValueError(
                f"limit must be between 1 and {self.MAX_LIMIT} (Attio's ceiling on /tasks); got {limit}")
        if sort is not None and sort not in self.SORTS:
            raise ValueError(f"sort must be one of {', '.join(self.SORTS)}; got {sort!r}")
        params = {}
        if completed is not None:
            params["is_completed"] = completed
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if sort is not None:
            params["sort"] = sort

        return self.client._request("GET", "tasks", params=params)

    def update(
        self,
        task_id: str,
        deadline: str = None,
        is_completed: bool = None,
        assignee_id: str = None,
        linked_object: str = None,
        linked_record_id: str = None,
    ) -> Dict[str, Any]:
        """Update a task. Attio API only allows updating deadline_at, is_completed, assignees, linked_records."""
        task_data: Dict[str, Any] = {}
        if deadline is not None:
            if len(deadline) == 10:
                deadline = f"{deadline}T00:00:00.000Z"
            task_data["deadline_at"] = deadline
        if is_completed is not None:
            task_data["is_completed"] = is_completed
        if assignee_id is not None:
            task_data["assignees"] = [{"referenced_actor_type": "workspace-member", "referenced_actor_id": assignee_id}]
        if linked_object is not None and linked_record_id is not None:
            task_data["linked_records"] = [{
                "target_object": linked_object,
                "target_record_id": linked_record_id,
            }]
        if not task_data:
            raise ValueError("Nothing to update — pass at least one updatable field")
        return self.client._request("PATCH", f"tasks/{task_id}", json={"data": task_data})

    def get(self, task_id: str) -> Dict[str, Any]:
        """Get a single task by ID."""
        return self.client._request("GET", f"tasks/{task_id}")

    def delete(self, task_id: str) -> Dict[str, Any]:
        """Delete a task by ID."""
        return self.client._request("DELETE", f"tasks/{task_id}")


class AttioLists:
    """Lists resource — CRUD on Attio lists (saved collections of records)."""

    def __init__(self, client: "AttioClient"):
        self.client = client

    def list(self) -> Dict[str, Any]:
        """List all lists accessible to the access token."""
        return self.client._request("GET", "lists")

    def get(self, list_id_or_slug: str) -> Dict[str, Any]:
        """Get a single list by ID or slug."""
        return self.client._request("GET", f"lists/{list_id_or_slug}")

    def create(
        self,
        name: str,
        parent_object: str,
        api_slug: str = None,
        workspace_access: str = "full-access",
        workspace_member_access: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new list.

        Args:
            name: Display name.
            parent_object: Object slug or ID the list targets (e.g. "companies").
            api_slug: API slug (optional, derived from name if omitted).
            workspace_access: "full-access" | "read-and-write" | "read-only" | None.
            workspace_member_access: per-member overrides (list of dicts with
                `workspace_member_id` + `level`).
        """
        # api_slug and workspace_member_access are REQUIRED by POST /v2/lists
        # (400 otherwise) — slug derived from the name, empty member access by default.
        if not api_slug:
            api_slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        data: Dict[str, Any] = {
            "name": name,
            "api_slug": api_slug,
            "parent_object": parent_object,
            "workspace_access": workspace_access,
            "workspace_member_access": workspace_member_access or [],
        }
        return self.client._request("POST", "lists", json={"data": data})

    def update(self, list_id_or_slug: str, **attributes) -> Dict[str, Any]:
        """Update an existing list (name, api_slug, access controls)."""
        return self.client._request("PATCH", f"lists/{list_id_or_slug}", json={"data": attributes})

    def views(self, list_id_or_slug: str) -> Dict[str, Any]:
        """List saved views for a list."""
        return self.client._request("GET", f"lists/{list_id_or_slug}/views")


class AttioEntries:
    """List entries — records added to a specific list."""

    def __init__(self, client: "AttioClient"):
        self.client = client

    def query(
        self,
        list_id_or_slug: str,
        filter: Dict[str, Any] = None,
        sorts: List[Dict[str, Any]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Query entries in a list with optional filter/sort."""
        body: Dict[str, Any] = {"limit": limit, "offset": offset}
        if filter is not None:
            body["filter"] = filter
        if sorts is not None:
            body["sorts"] = sorts
        return self.client._request("POST", f"lists/{list_id_or_slug}/entries/query", json=body)

    def get(self, list_id_or_slug: str, entry_id: str) -> Dict[str, Any]:
        """Get a single list entry by ID."""
        return self.client._request("GET", f"lists/{list_id_or_slug}/entries/{entry_id}")

    def create(
        self,
        list_id_or_slug: str,
        parent_record_id: str,
        parent_object: str,
        entry_values: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Add a record to a list as a new entry.

        `entry_values` is required by the API (400 otherwise) — empty object accepted.
        """
        data: Dict[str, Any] = {
            "parent_record_id": parent_record_id,
            "parent_object": parent_object,
            "entry_values": entry_values if entry_values is not None else {},
        }
        return self.client._request("POST", f"lists/{list_id_or_slug}/entries", json={"data": data})

    def update(
        self,
        list_id_or_slug: str,
        entry_id: str,
        entry_values: Dict[str, Any],
        overwrite_multiselect: bool = False,
    ) -> Dict[str, Any]:
        """Update entry values.

        PATCH (default) appends multiselect values; PUT overwrites.
        """
        method = "PUT" if overwrite_multiselect else "PATCH"
        return self.client._request(
            method,
            f"lists/{list_id_or_slug}/entries/{entry_id}",
            json={"data": {"entry_values": entry_values}},
        )

    def delete(self, list_id_or_slug: str, entry_id: str) -> Dict[str, Any]:
        """Delete a list entry (removes the record from the list)."""
        return self.client._request("DELETE", f"lists/{list_id_or_slug}/entries/{entry_id}")


class AttioWorkspaceMembers:
    """Workspace members — humans with access to the Attio workspace."""

    def __init__(self, client: "AttioClient"):
        self.client = client

    def list(self) -> Dict[str, Any]:
        """List all workspace members, without their avatar address."""
        return _without_addresses(self.client._request("GET", "workspace_members"), AVATAR_ADDRESS_FIELDS)

    def get(self, workspace_member_id: str) -> Dict[str, Any]:
        """Get a single workspace member by ID, without its avatar address."""
        return _without_addresses(self.client._request("GET", f"workspace_members/{workspace_member_id}"),
                                  AVATAR_ADDRESS_FIELDS)


class AttioComments:
    """Comments — threaded discussions on records, entries, or threads."""

    def __init__(self, client: "AttioClient"):
        self.client = client

    def get(self, comment_id: str) -> Dict[str, Any]:
        """Get a single comment by ID."""
        return self.client._request("GET", f"comments/{comment_id}")

    def create(
        self,
        content: str,
        author_id: str,
        thread_id: str = None,
        parent_object: str = None,
        parent_record_id: str = None,
        entry_id: str = None,
        list_id: str = None,
    ) -> Dict[str, Any]:
        """Create a comment, either on an existing thread or attached to a record/entry."""
        data: Dict[str, Any] = {
            "format": "plaintext",
            "content": content,
            "author": {"type": "workspace-member", "id": author_id},
        }
        if thread_id:
            data["thread_id"] = thread_id
        elif parent_object and parent_record_id:
            data["parent_object"] = parent_object
            data["parent_record_id"] = parent_record_id
        elif list_id and entry_id:
            data["list_id"] = list_id
            data["entry_id"] = entry_id
        else:
            raise ValueError("Provide thread_id, or (parent_object + parent_record_id), or (list_id + entry_id)")
        return self.client._request("POST", "comments", json={"data": data})

    def delete(self, comment_id: str) -> Dict[str, Any]:
        """Delete a comment. Deletes the whole thread if comment is the head."""
        return self.client._request("DELETE", f"comments/{comment_id}")


class AttioThreads:
    """Threads — collections of comments on a record/entry."""

    def __init__(self, client: "AttioClient"):
        self.client = client

    def list(
        self,
        parent_object: str = None,
        parent_record_id: str = None,
        list_id: str = None,
        entry_id: str = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List threads, optionally filtered by parent record or entry."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if parent_object:
            params["parent_object"] = parent_object
        if parent_record_id:
            params["parent_record_id"] = parent_record_id
        if list_id:
            params["list_id"] = list_id
        if entry_id:
            params["entry_id"] = entry_id
        return self.client._request("GET", "threads", params=params)

    def get(self, thread_id: str) -> Dict[str, Any]:
        """Get a thread with all its comments."""
        return self.client._request("GET", f"threads/{thread_id}")


class AttioMeetings:
    """Meetings — calendar events synced into Attio."""

    def __init__(self, client: "AttioClient"):
        self.client = client

    # `GET /v2/meetings`: limit 1-200 (201 → 400), default 50. CURSOR
    # pagination only — `offset` does not exist in its contract and the API
    # swallows it (`offset=-1` and `offset=NOTANINTEGER` come back 200, whereas
    # /notes refuses `offset=-1` with 400). Recorded on 27/08/2026.
    MAX_LIMIT = 200
    SORTS = ("start_asc", "start_desc")

    def list(self, limit: int = None, cursor: str = None, sort: str = None,
             ends_from: str = None, starts_before: str = None) -> Dict[str, Any]:
        """List meetings. The only endpoint of the connector to carry a REAL
        date window: `ends_from` (meetings ending from, inclusive) and
        `starts_before` (starting before, exclusive) — ISO 8601 timestamps, refused
        with 400 if they do not parse. Sort `sort` = `start_asc` (default) or
        `start_desc`. `limit` 1-200, default 50.

        ⚠️ **Cursor pagination, not offset** — signal #586: the client
        sent `offset`, which Attio ignores (`offset=2000` returned the same two
        meetings of January 2023); only `pagination.next_cursor` advances, and it
        was not accepted as an argument. Pass this `next_cursor` back as
        `cursor` for the next page; null `next_cursor` = end of collection.
        """
        if limit is not None and not 1 <= limit <= self.MAX_LIMIT:
            raise ValueError(
                f"limit must be between 1 and {self.MAX_LIMIT} (Attio's ceiling on /meetings); got {limit}")
        if sort is not None and sort not in self.SORTS:
            raise ValueError(f"sort must be {' or '.join(self.SORTS)}; got {sort!r}")
        params = {}
        for key, value in (("limit", limit), ("cursor", cursor), ("sort", sort),
                           ("ends_from", ends_from), ("starts_before", starts_before)):
            if value is not None:
                params[key] = value
        return self.client._request("GET", "meetings", params=params)

    def get(self, meeting_id: str) -> Dict[str, Any]:
        """Get a single meeting by ID."""
        return self.client._request("GET", f"meetings/{meeting_id}")


class AttioCallRecordings:
    """Call recordings — audio recordings attached to a meeting."""

    def __init__(self, client: "AttioClient"):
        self.client = client

    def list(self, meeting_id: str) -> Dict[str, Any]:
        """List recordings for a meeting, without any video address."""
        return _without_addresses(self.client._request("GET", f"meetings/{meeting_id}/call_recordings"),
                                  VIDEO_ADDRESS_FIELDS)

    def get(self, meeting_id: str, call_recording_id: str) -> Dict[str, Any]:
        """Get a single call recording, without its video address (a signed
        download link, valid one hour, that opens the video to anyone holding it)."""
        return _without_addresses(
            self.client._request("GET", f"meetings/{meeting_id}/call_recordings/{call_recording_id}"),
            VIDEO_ADDRESS_FIELDS)

    def transcript(self, meeting_id: str, call_recording_id: str, cursor: str = None) -> Dict[str, Any]:
        """Get one page of the transcript of a call recording.

        Attio pages the speech segments by cursor: pass the response's
        `pagination.next_cursor` back as `cursor` for the next page; a null
        `next_cursor` is the last page.
        """
        kwargs = {"params": {"cursor": cursor}} if cursor is not None else {}
        return self.client._request(
            "GET",
            f"meetings/{meeting_id}/call_recordings/{call_recording_id}/transcript",
            **kwargs,
        )


class AttioObjects:
    """Objects (meta) — system + custom objects in the workspace."""

    def __init__(self, client: "AttioClient"):
        self.client = client

    def list(self) -> Dict[str, Any]:
        """List all objects (system + custom)."""
        return self.client._request("GET", "objects")

    def get(self, object_id_or_slug: str) -> Dict[str, Any]:
        """Get a single object by ID or slug."""
        return self.client._request("GET", f"objects/{object_id_or_slug}")

    def views(self, object_id_or_slug: str) -> Dict[str, Any]:
        """List saved views for an object."""
        return self.client._request("GET", f"objects/{object_id_or_slug}/views")


class AttioAttributes:
    """Attributes (meta) — schema attributes on an object or list.

    `target` is "objects" or "lists"; `identifier` is the object/list ID or slug.
    """

    def __init__(self, client: "AttioClient"):
        self.client = client

    def list(self, target: str, identifier: str) -> Dict[str, Any]:
        """List attributes on an object or list."""
        return self.client._request("GET", f"{target}/{identifier}/attributes")

    def get(self, target: str, identifier: str, attribute: str) -> Dict[str, Any]:
        """Get a single attribute."""
        return self.client._request("GET", f"{target}/{identifier}/attributes/{attribute}")

    def options(self, target: str, identifier: str, attribute: str) -> Dict[str, Any]:
        """List select options for a select attribute."""
        return self.client._request("GET", f"{target}/{identifier}/attributes/{attribute}/options")

    def statuses(self, target: str, identifier: str, attribute: str) -> Dict[str, Any]:
        """List statuses for a status attribute."""
        return self.client._request("GET", f"{target}/{identifier}/attributes/{attribute}/statuses")

    # --- SCHEMA writes -------------------------------------------------------
    # The API has no DELETE on an attribute, an option or a stage: what is
    # created here cannot be erased through the API.

    def create(self, target: str, identifier: str, definition: Dict[str, Any]) -> Dict[str, Any]:
        """Create an attribute on an object or list (POST /{target}/{identifier}/attributes).

        `definition` is Attio's attribute object, sent verbatim as `data`: `title`,
        `description`, `api_slug`, `type`, `is_required`, `is_unique`,
        `is_multiselect`, `config`. Scope `object_configuration:read-write`
        (`list_configuration:read-write` for a list).
        """
        return self.client._request("POST", f"{target}/{identifier}/attributes",
                                    json={"data": definition})

    def create_option(self, target: str, identifier: str, attribute: str,
                      title: str) -> Dict[str, Any]:
        """Add a select option to a select attribute (POST …/attributes/{attribute}/options)."""
        return self.client._request("POST", f"{target}/{identifier}/attributes/{attribute}/options",
                                    json={"data": {"title": title}})

    def create_status(self, target: str, identifier: str, attribute: str,
                      title: str) -> Dict[str, Any]:
        """Add a status (e.g. a deal stage) to a status attribute (POST …/attributes/{attribute}/statuses)."""
        return self.client._request("POST", f"{target}/{identifier}/attributes/{attribute}/statuses",
                                    json={"data": {"title": title}})


class AttioClient:
    """
    Attio CRM API client.

    Usage:
        client = AttioClient()
        companies = client.companies.list()
        client.companies.create(name="Acme Inc", domain="acme.com")
    """

    BASE_URL = "https://api.attio.com/v2"

    def __init__(self, api_key: str = None):
        """
        Initialize Attio client.

        Args:
            api_key: Attio API key
        """
        self.api_key = require(api_key, "ATTIO_API_KEY")

        # Initialize resources
        self.companies = AttioResource(self, "companies")
        self.people = AttioResource(self, "people")
        self.deals = AttioResource(self, "deals")
        self.notes = AttioNotes(self)
        self.tasks = AttioTasks(self)
        self.lists = AttioLists(self)
        self.entries = AttioEntries(self)
        self.workspace_members = AttioWorkspaceMembers(self)
        self.comments = AttioComments(self)
        self.threads = AttioThreads(self)
        self.meetings = AttioMeetings(self)
        self.call_recordings = AttioCallRecordings(self)
        self.objects = AttioObjects(self)
        self.attributes = AttioAttributes(self)

    def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        """Make API request."""
        url = f"{self.BASE_URL}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response = requests.request(method, url, headers=headers, timeout=_HTTP_TIMEOUT, **kwargs)

        if not response.ok:
            # Attio's JSON body holds the real reason (validation,
            # missing scope…) — losing it makes the 400/404s undecipherable.
            # A 429 takes the same path: its status and `retry_after` say what to do.
            try:
                body = response.json()
            except ValueError:
                body = response.text[:2000]
            raise UpstreamHTTPError.from_response(response, service="attio", body=body,
                                                  method=method, url=url)

        if response.content:
            return response.json()
        return {}
