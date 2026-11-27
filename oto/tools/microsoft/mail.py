"""Microsoft Graph client — the signed-in person's Outlook mailbox (delegated access,
scopes `scopes.MAIL`).

Covers what an agent needs to triage and answer mail: folders, search, read a
message and its attachments, write a draft (new or reply), send it, move or delete
a message. Nothing is sent without an explicit `send_draft`: every write produces
a draft first.

## Protocol facts that shape a caller

- **`$search` and `$filter`/`$orderby` exclude each other** on messages: a
  searched list comes back in Graph's own order (most recent first), and the
  `unread` filter cannot be combined with a `query` — put the condition in the
  KQL query instead, or list without a query.
- **Filtering and sorting together** on messages requires the sorted property
  to appear first in the filter (otherwise Graph refuses the request as an
  "InefficientFilter"): the unread filter is written `receivedDateTime ge … and
  isRead eq false` for that reason.
- **An attachment travels inline only up to 3 MB**: larger files need an
  upload session, not covered here — `create_draft` refuses them.
- **An image shown in the body is an attachment with a `contentId`** and
  `isInline: true`; the HTML body points at it with `<img src="cid:<id>">`.
  The id is written without the angle brackets of the MIME `Content-ID`
  header.
- **Every request asks for IMMUTABLE ids** (`Prefer: IdType="ImmutableId"`,
  set once in `MailClient._send`, alongside a call's own preference): an
  id given back survives a move to another folder — `move` returns the SAME
  id — and the move of a sent draft to Sent Items, so the sent copy is read
  back by the draft's id (`sent_message`). It changes only if the message
  goes to an archive mailbox, or is exported and imported again.
- **Sending is asynchronous** (202): the copy in Sent Items appears once the
  message has actually left, which can take a moment.
- Folder arguments accept a folder id or a well-known name (`inbox`,
  `archive`, `deleteditems`, `drafts`, `sentitems`, `junkemail`).

## Shared and delegated mailboxes

The reading methods take an optional `mailbox` (an address or a user id): they
then read `/users/{mailbox}/…` instead of `/me/…`, still on behalf of the
signed-in person. Graph serves it only if that person was given access to the
mailbox in Exchange (Full Access, or read permission on the folders read) AND
the grant carries the delegated scope `Mail.Read.Shared` (`scopes.MAIL_SHARED`,
consentable by the person, no administrator required by Microsoft). It is read
only: writing, drafting and sending always act on the person's own mailbox
(`/me`). A 403 or 404 from Graph on such a call raises `MailboxAccessError`,
which names the mailbox and Graph's error code — never a silent fallback on the
person's own mailbox.
"""
from __future__ import annotations

import base64
import html
import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional
from urllib.parse import quote

from ..common import UpstreamHTTPError
from ..common.credentials import require
from ._transport import GraphBase, segment

#: Graph's ceiling for an attachment sent in the body of a request.
MAX_INLINE_ATTACHMENT = 3 * 1024 * 1024
BODY_TYPES = ("text", "html")
# The fields of a list: everything but the body, which `get_message` reads.
_LIST_FIELDS = ("id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,"
                "isRead,isDraft,hasAttachments,importance,bodyPreview,conversationId,"
                "parentFolderId,webLink")
_ATTACHMENT_FIELDS = "id,name,contentType,size,isInline,lastModifiedDateTime"
_FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"
_CHUNK = 64 * 1024
#: The most read of an error body, to take Graph's code and message from it.
_ERROR_BODY_MAX = 16 * 1024
_BODY_TAG = re.compile(r"<body[^>]*>", re.IGNORECASE)
#: Every request of the mailbox asks for immutable ids (see the module doc).
_IMMUTABLE_IDS = 'IdType="ImmutableId"'
_SENT_FIELDS = "id,webLink,sentDateTime"
#: Waits before each read of a just-sent message — about 10 s in all.
_SENT_POLL_DELAYS = (0.5, 1.0, 1.5, 2.0, 2.5, 2.5)


class MailboxAccessError(UpstreamHTTPError):
    """Graph refused (403) or did not find (404) what was read in another mailbox
    than the person's own. `mailbox` is the mailbox asked for, `graph_code` the code
    Graph gave (`ErrorAccessDenied`, `ErrorItemNotFound`…, `None` if absent)."""

    def __init__(self, mailbox: str, error: UpstreamHTTPError):
        super().__init__(error.status_code, error.body, service=error.service,
                         method=error.method, url=error.url, retry_after=error.retry_after)
        detail = (error.body or {}).get("error") if isinstance(error.body, dict) else None
        detail = detail if isinstance(detail, dict) else {}
        self.mailbox = mailbox
        self.graph_code = detail.get("code")
        graph_message = detail.get("message") or error.body
        self.args = (f"No access to mailbox {mailbox}: Microsoft Graph answered HTTP "
                     f"{error.status_code} {self.graph_code or '(no code)'}: {graph_message}. "
                     "Reading it needs the signed-in person to have access to this mailbox "
                     "in Exchange and the delegated permission Mail.Read.Shared.",)


class AttachmentTooLarge(ValueError):
    """An attachment above the `max_bytes` its reader accepts. `size` is the size
    Graph declared, or `None` when the content was cut off while read."""

    def __init__(self, name: str, max_bytes: int, *, size: Optional[int] = None):
        self.name = name
        self.max_bytes = max_bytes
        self.size = size
        found = f"is {size} bytes" if size is not None else "goes beyond it"
        super().__init__(f"attachment {name!r} {found}: the limit is {max_bytes} bytes")


class UnsupportedAttachment(ValueError):
    """An attachment that is not a file: an attached Outlook item
    (`#microsoft.graph.itemAttachment`) or a link to a cloud file
    (`#microsoft.graph.referenceAttachment`). `kind` is Graph's `@odata.type`."""

    def __init__(self, attachment_id: str, kind: str):
        self.attachment_id = attachment_id
        self.kind = kind
        super().__init__(f"attachment is a {kind}, not a file attachment: it has no "
                         "file content to read")


@dataclass(frozen=True)
class AttachmentContent:
    """The content of a file attachment: its `name`, its `content_type` (as Graph
    serves it, else as declared on the attachment) and its raw bytes."""
    name: str
    content_type: str
    content: bytes


def _read_bounded(resp: Any, name: str, max_bytes: int) -> bytes:
    """A streamed body, refused as soon as it goes beyond `max_bytes`."""
    declared = resp.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise AttachmentTooLarge(name, max_bytes, size=int(declared))
    data = bytearray()
    for chunk in resp.iter_content(_CHUNK):
        data += chunk
        if len(data) > max_bytes:
            raise AttachmentTooLarge(name, max_bytes)
    return bytes(data)


def _upstream_error(resp: Any) -> UpstreamHTTPError:
    """Graph's refusal of a streamed request: its error code and message only (a
    bounded read of the body), never the body as received."""
    detail: Dict[str, Any] = {}
    try:
        raw = next(resp.iter_content(_ERROR_BODY_MAX), b"")
        error = json.loads(raw).get("error") if raw else None
        if isinstance(error, dict):
            detail = {k: error[k] for k in ("code", "message") if isinstance(error.get(k), str)}
    except (ValueError, AttributeError, StopIteration):
        pass
    return UpstreamHTTPError.from_response(resp, service="microsoft",
                                           body={"error": detail} if detail else None)


def _recipients(addresses: Iterable[str], name: str) -> List[Dict[str, Any]]:
    if isinstance(addresses, str):
        raise TypeError(f"{name} is a list of addresses, not a string")
    return [{"emailAddress": {"address": require(a, name)}} for a in addresses]


def _content_id(name: str, value: Any) -> str:
    """The `content_id` of an inline attachment: what `cid:` points at in the body."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"attachment {name!r}: content_id must be a non-empty string")
    if any(c in value for c in "<> \t\r\n"):
        raise ValueError(
            f"attachment {name!r}: content_id {value!r} must be written without angle "
            "brackets or whitespace — the body refers to it as `cid:<content_id>`")
    return value


def _attachment(spec: Mapping[str, Any]) -> Dict[str, Any]:
    """`{name, content_type, content_bytes[, content_id]}` (raw bytes) → a Graph
    fileAttachment; with `content_id`, an inline one (`<img src="cid:…">`)."""
    name = require(spec.get("name"), "attachment name")
    content = spec.get("content_bytes")
    if not isinstance(content, (bytes, bytearray)):
        raise TypeError(f"attachment {name!r}: content_bytes must be the raw bytes")
    if len(content) > MAX_INLINE_ATTACHMENT:
        raise ValueError(
            f"attachment {name!r} is {len(content)} bytes: Graph accepts at most "
            f"{MAX_INLINE_ATTACHMENT} bytes per attachment sent inline")
    file = {"@odata.type": "#microsoft.graph.fileAttachment", "name": name,
            "contentType": spec.get("content_type") or "application/octet-stream",
            "contentBytes": base64.b64encode(bytes(content)).decode("ascii")}
    if spec.get("content_id") is not None:
        file.update(isInline=True, contentId=_content_id(name, spec["content_id"]))
    return file


def _attachments(specs: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Every attachment checked BEFORE anything is written, inline ids unique."""
    files = [_attachment(a) for a in specs]
    seen: Dict[str, str] = {}
    for file in files:
        cid = file.get("contentId")
        if cid is None:
            continue
        if cid in seen:
            raise ValueError(f"content_id {cid!r} is given to two attachments "
                             f"({seen[cid]!r} and {file['name']!r}): each `cid:` must "
                             "designate one file")
        seen[cid] = file["name"]
    return files


def _above_quote(body_html: str, draft_body: Mapping[str, Any]) -> str:
    """The new text above the quoted message that Graph put in a reply draft."""
    quoted = str(draft_body.get("content") or "")
    if str(draft_body.get("contentType") or "").lower() != "html":
        quoted = f'<div style="white-space:pre-wrap">{html.escape(quoted)}</div>'
    match = _BODY_TAG.search(quoted)
    if match:
        return quoted[:match.end()] + body_html + quoted[match.end():]
    return body_html + quoted


class MailClient(GraphBase):
    """The signed-in person's Outlook mailbox; read only, the shared or delegated
    mailboxes she was given access to (`mailbox`)."""

    def _send(self, method: str, path_or_url: str, **kwargs: Any) -> Any:
        """Every request asks for immutable ids, alongside the call's own `Prefer`."""
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Prefer"] = ", ".join(
            p for p in (_IMMUTABLE_IDS, headers.get("Prefer")) if p)
        return super()._send(method, path_or_url, headers=headers, **kwargs)

    @staticmethod
    def _root(mailbox: Optional[str]) -> str:
        """`/me`, or `/users/{mailbox}` for a shared or delegated mailbox."""
        if mailbox is None:
            return "/me"
        return f"/users/{quote(require(mailbox, 'mailbox'), safe='@')}"

    @classmethod
    def _message(cls, message_id: str, mailbox: Optional[str] = None) -> str:
        return f"{cls._root(mailbox)}/messages/{segment(message_id, 'message_id')}"

    @staticmethod
    @contextmanager
    def _reading(mailbox: Optional[str]) -> Iterator[None]:
        """A 403 or 404 on another mailbox becomes a `MailboxAccessError`."""
        try:
            yield
        except UpstreamHTTPError as e:
            if mailbox is not None and e.status_code in (403, 404):
                raise MailboxAccessError(mailbox, e) from e
            raise

    # ================================================================
    # Reading
    # ================================================================

    def list_folders(self, *, limit: int = 500, mailbox: Optional[str] = None
                     ) -> List[Dict[str, Any]]:
        """GET /me/mailFolders (or /users/{mailbox}/mailFolders) — the top-level
        folders (`id`, `displayName`, counts of items and unread items)."""
        with self._reading(mailbox):
            return self._paged(f"{self._root(mailbox)}/mailFolders", limit=limit)

    def search_messages(self, query: Optional[str] = None, *, folder: Optional[str] = None,
                        top: int = 25, unread: Optional[bool] = None,
                        mailbox: Optional[str] = None) -> List[Dict[str, Any]]:
        """GET /me/messages (or /me/mailFolders/{folder}/messages) — at most `top`
        messages, without their body (`bodyPreview` only).

        Args:
            query: a KQL search (`from:jane subject:invoice`, plain words…), sent as
                `$search`; without it, the most recent messages first.
            folder: a folder id or a well-known name; every folder when omitted.
            unread: `True` / `False` to keep only unread / read messages. Not
                combinable with `query` (Graph refuses `$filter` with `$search`).
            mailbox: a shared or delegated mailbox (address or user id), read
                under `/users/{mailbox}`; the person's own when omitted.
        """
        root = self._root(mailbox)
        path = (f"{root}/mailFolders/{segment(folder, 'folder')}/messages" if folder
                else f"{root}/messages")
        params: Dict[str, Any] = {"$select": _LIST_FIELDS}
        if query is not None:
            if unread is not None:
                raise ValueError("unread cannot be combined with query: Graph refuses "
                                 "$filter with $search on messages")
            params["$search"] = '"' + require(query, "query").replace('"', '\\"') + '"'
        else:
            params["$orderby"] = "receivedDateTime desc"
            if unread is not None:
                params["$filter"] = (f"receivedDateTime ge 1900-01-01T00:00:00Z and "
                                     f"isRead eq {'false' if unread else 'true'}")
        with self._reading(mailbox):
            return self._paged(path, params, limit=top)

    def get_message(self, message_id: str, *, body: str = "text",
                    mailbox: Optional[str] = None) -> Dict[str, Any]:
        """GET /me/messages/{id} (or /users/{mailbox}/messages/{id}) — the whole
        message, its body as `text` (default, what an agent reads) or `html`."""
        if body not in BODY_TYPES:
            raise ValueError(f"body must be one of {', '.join(BODY_TYPES)}")
        with self._reading(mailbox):
            return self._json("GET", self._message(message_id, mailbox),
                              headers={"Prefer": f'outlook.body-content-type="{body}"'})

    def list_attachments(self, message_id: str, *, mailbox: Optional[str] = None
                         ) -> List[Dict[str, Any]]:
        """GET /me/messages/{id}/attachments (or under /users/{mailbox}) — name,
        type and size of each attachment, without its content (see `get_attachment`)."""
        with self._reading(mailbox):
            return self._paged(f"{self._message(message_id, mailbox)}/attachments",
                               {"$select": _ATTACHMENT_FIELDS}, limit=500, page_size=None)

    def get_attachment(self, message_id: str, attachment_id: str, *,
                       mailbox: Optional[str] = None) -> Dict[str, Any]:
        """GET /me/messages/{id}/attachments/{id} (or under /users/{mailbox}) — one
        attachment; a file's content is in `contentBytes`, base64-encoded."""
        with self._reading(mailbox):
            return self._json("GET", f"{self._message(message_id, mailbox)}/attachments/"
                                     f"{segment(attachment_id, 'attachment_id')}")

    def get_attachment_content(self, message_id: str, attachment_id: str, *,
                               max_bytes: int, mailbox: Optional[str] = None
                               ) -> AttachmentContent:
        """The bytes of a file attachment, read as a stream and bounded by
        `max_bytes` — for a file to hand on (a scan, a PDF), never base64 in JSON.

        Two requests: GET …/attachments/{id}?$select=name,contentType,size (its
        name, type and size), then GET …/attachments/{id}/$value, read in chunks.

        Args:
            max_bytes: the largest content accepted, required (> 0). An attachment
                whose declared size is above it is refused before its content is
                asked for; a content found larger while read is cut off there.
            mailbox: a shared or delegated mailbox, as for `get_attachment`.

        Raises:
            AttachmentTooLarge: above `max_bytes` (declared, or read).
            UnsupportedAttachment: not a file (an attached Outlook item, or a link
                to a cloud file), which has no content of its own to read.
            UpstreamHTTPError: Graph refused (its error code and message only,
                never the body as received).
        """
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        path = (f"{self._message(message_id, mailbox)}/attachments/"
                f"{segment(attachment_id, 'attachment_id')}")
        with self._reading(mailbox):
            meta = self._attachment_meta(path)
            kind = meta.get("@odata.type")
            if kind != _FILE_ATTACHMENT:
                raise UnsupportedAttachment(attachment_id, kind or "(no @odata.type)")
            name = meta.get("name") or ""
            size = meta.get("size")
            if isinstance(size, int) and size > max_bytes:
                raise AttachmentTooLarge(name, max_bytes, size=size)
            resp = self._send("GET", f"{path}/$value", stream=True,
                              headers={"Accept": "*/*"})
            try:
                if resp.status_code >= 400:
                    raise _upstream_error(resp)
                content = _read_bounded(resp, name, max_bytes)
                content_type = (resp.headers.get("Content-Type") or meta.get("contentType")
                                or "application/octet-stream")
            finally:
                resp.close()
        return AttachmentContent(name=name, content_type=content_type, content=content)

    def _attachment_meta(self, path: str) -> Dict[str, Any]:
        """Name, type and size of an attachment; a refusal carries Graph's code and
        message only."""
        resp = self._send("GET", path, params={"$select": "name,contentType,size"},
                          stream=True)
        try:
            if resp.status_code >= 400:
                raise _upstream_error(resp)
            meta = resp.json()
        finally:
            resp.close()
        if not isinstance(meta, dict):
            raise ValueError("Microsoft Graph answered an attachment that is not an object")
        return meta

    # ================================================================
    # Writing — drafts first, sending is a separate step
    # ================================================================

    def create_draft(self, *, to: Iterable[str], subject: str, body_html: str,
                     cc: Iterable[str] = (), bcc: Iterable[str] = (),
                     attachments: Iterable[Mapping[str, Any]] = ()) -> Dict[str, Any]:
        """POST /me/messages — a new draft in the Drafts folder, not sent.

        Args:
            to / cc / bcc: email addresses.
            attachments: as `add_attachments` takes them — all checked before
                anything is written.

        Returns the draft as created (its `id` is the one `send_draft` takes, its
        `webLink` opens it in Outlook on the web).
        """
        files = _attachments(attachments)
        draft = self._json("POST", "/me/messages", json={
            "subject": subject or "",
            "body": {"contentType": "HTML", "content": body_html or ""},
            "toRecipients": _recipients(to, "to"),
            "ccRecipients": _recipients(cc, "cc"),
            "bccRecipients": _recipients(bcc, "bcc"),
        })
        self._post_attachments(draft["id"], files)
        return draft

    def create_reply_draft(self, message_id: str, *, body_html: str,
                           reply_all: bool = False,
                           attachments: Iterable[Mapping[str, Any]] = ()) -> Dict[str, Any]:
        """POST /me/messages/{id}/createReply (or createReplyAll), then PATCH of
        the draft's body — a reply draft, not sent, recipients and subject set
        by Graph, `body_html` placed above the quoted message; `attachments` as
        `add_attachments` takes them, all checked before anything is written.

        Returns the updated draft."""
        files = _attachments(attachments)
        action = "createReplyAll" if reply_all else "createReply"
        draft = self._json("POST", f"{self._message(message_id)}/{action}")
        updated = self._json("PATCH", self._message(draft["id"]), json={
            "body": {"contentType": "HTML",
                     "content": _above_quote(body_html or "", draft.get("body") or {})}})
        self._post_attachments(draft["id"], files)
        return updated

    def add_attachments(self, message_id: str,
                        attachments: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        """POST /me/messages/{id}/attachments, once per file — attachments added to
        an existing draft.

        Args:
            attachments: `{"name", "content_type", "content_bytes"}` each, the
                content as raw bytes, ≤ 3 MB each. Each one is added by its own
                request, so that their sum is not bound by Graph's request size.
                An entry with a `content_id` is an INLINE attachment (`isInline`),
                shown where the body says `<img src="cid:<content_id>">`; the id
                is non-empty, without angle brackets or whitespace, and unique
                among the attachments given. All are checked before anything is
                written.

        Returns the attachments as created."""
        files = _attachments(attachments)
        return self._post_attachments(message_id, files)

    def _post_attachments(self, message_id: str,
                          files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self._json("POST", f"{self._message(message_id)}/attachments", json=file)
                for file in files]

    def send_draft(self, message_id: str) -> str:
        """POST /me/messages/{id}/send — sends a draft (it moves to Sent Items).
        Graph answers 202: the message is accepted, not yet delivered. Returns the
        id passed in, which is also the id of its copy in Sent Items (`sent_message`)."""
        self._json("POST", f"{self._message(message_id)}/send")
        return message_id

    def sent_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """GET /me/messages/{id} — the copy in Sent Items of a draft
        just sent, `{id, webLink, sentDateTime}`. That copy appears only once the
        message has left: read a few times over about 10 s while Graph answers
        404, then `None` (not visible yet — the send itself is not in question).
        Any other error is raised."""
        for delay in _SENT_POLL_DELAYS:
            time.sleep(delay)
            try:
                return self._json("GET", self._message(message_id),
                                  params={"$select": _SENT_FIELDS})
            except UpstreamHTTPError as e:
                if e.status_code != 404:
                    raise
        return None

    def move(self, message_id: str, destination: str) -> Dict[str, Any]:
        """POST /me/messages/{id}/move — to a folder id or a well-known name
        (`archive`, `deleteditems`, `inbox`…). Returns the message, its id unchanged
        (immutable ids)."""
        return self._json("POST", f"{self._message(message_id)}/move",
                          json={"destinationId": require(destination, "destination")})

    def delete(self, message_id: str) -> str:
        """DELETE /me/messages/{id} — removes the message from its folder; the
        mailbox keeps it as recoverable for its retention period. To put it in the
        trash folder instead, `move(message_id, "deleteditems")`. Returns the id."""
        self._json("DELETE", self._message(message_id))
        return message_id
