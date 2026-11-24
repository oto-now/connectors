"""Gmail API client using OAuth2 user credentials."""

import base64
import mimetypes
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from oto.tools.common.credentials import require

from ._reply import (
    REPLY_HEADERS, latest_of, reply_recipients, reply_subject, threading_headers,
)

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']


def _markdown_to_html_fragment(text: str) -> str:
    """Render markdown to an HTML fragment suitable for Gmail's text/html part.

    No <html>/<body> wrapping — Gmail accepts the fragment directly inside a
    multipart/alternative message. A list glued to the preceding paragraph
    gets its missing blank line (`fix_glued_lists`): the author of a mail body
    is often an agent, who never sees a server-side warning, and the draft
    would otherwise show its bullets run together. Supports tables, fenced
    code, inline attributes.
    """
    import markdown as _md
    from oto.tools.markdown_lint import fix_glued_lists
    return _md.markdown(
        fix_glued_lists(text),
        extensions=['tables', 'fenced_code', 'sane_lists', 'attr_list'],
        output_format='html',
    )


class GmailClientError(Exception):
    """Gmail API error."""


class GmailClient:
    """Gmail API client.

    Args:
        credentials: OAuth2 user credentials, provided by the consumer (required).
    """

    def __init__(self, credentials: Optional[Credentials] = None):
        self.service = build('gmail', 'v1', credentials=require(credentials, 'GOOGLE_CREDENTIALS'))

    def list_messages(
        self,
        query: Optional[str] = None,
        label_ids: Optional[list[str]] = None,
        max_results: int = 20,
    ) -> list[dict]:
        """List messages with metadata (id, snippet, from, subject, date)."""
        kwargs = {'userId': 'me', 'maxResults': max_results}
        if query:
            kwargs['q'] = query
        if label_ids:
            kwargs['labelIds'] = label_ids

        resp = self.service.users().messages().list(**kwargs).execute()
        messages = resp.get('messages', [])

        results = []
        for msg in messages:
            meta = self.service.users().messages().get(
                userId='me', id=msg['id'], format='metadata',
                metadataHeaders=['From', 'Subject', 'Date'],
            ).execute()
            headers = {h['name']: h['value'] for h in meta.get('payload', {}).get('headers', [])}
            results.append({
                'id': meta['id'],
                'threadId': meta['threadId'],
                'snippet': meta.get('snippet', ''),
                'from': headers.get('From', ''),
                'subject': headers.get('Subject', ''),
                'date': headers.get('Date', ''),
                'labelIds': meta.get('labelIds', []),
            })

        return results

    def get_message(self, message_id: str) -> dict:
        """Get full message content with attachment metadata."""
        msg = self.service.users().messages().get(
            userId='me', id=message_id, format='full',
        ).execute()

        # Case-insensitive lookup (cf. `reply`): the API returns header names AS
        # WRITTEN by the sender. A `To:`/`Cc:`/`Subject:` looked up to the letter
        # came back empty on any message written in lowercase — including ours before
        # this fix: re-reading a draft showed neither recipient nor cc,
        # so it was impossible to verify before sending (signal #342). Also applies to
        # existing messages, which capitalizing at write time does not fix.
        headers = {h['name'].lower(): h['value'] for h in msg.get('payload', {}).get('headers', [])}
        body = self._extract_body(msg.get('payload', {}))
        attachments = self._list_attachments(msg.get('payload', {}))

        result = {
            'id': msg['id'],
            'threadId': msg['threadId'],
            'subject': headers.get('subject', ''),
            'from': headers.get('from', ''),
            'to': headers.get('to', ''),
            'cc': headers.get('cc', ''),
            'date': headers.get('date', ''),
            'body': body,
            # `body` is the text/plain — hence the SOURCE markdown when the message
            # also carries an HTML part. Without this flag, re-reading a well-rendered
            # mail shows `**bold**` and leads to the conclusion "rendering is broken"
            # (signal #341). A boolean rather than the HTML itself: dispel the doubt
            # without dumping a page of tags into the agent's context.
            'has_html': self._find_part(msg.get('payload', {}).get('parts', []), 'text/html') is not None,
            'labelIds': msg.get('labelIds', []),
        }
        if attachments:
            result['attachments'] = attachments
        return result

    def download_attachments(self, message_id: str, output_dir: str) -> list[dict]:
        """Download all attachments from a message.

        Returns list of {filename, path, size_bytes}.
        """
        msg = self.service.users().messages().get(
            userId='me', id=message_id, format='full',
        ).execute()

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        downloaded = []

        for part in self._iter_parts(msg.get('payload', {})):
            filename = part.get('filename')
            att_id = part.get('body', {}).get('attachmentId')
            if not filename or not att_id:
                continue

            att = self.service.users().messages().attachments().get(
                userId='me', messageId=message_id, id=att_id,
            ).execute()
            data = base64.urlsafe_b64decode(att['data'])
            path = self._unique_path(out, filename)
            path.write_bytes(data)
            downloaded.append({
                'filename': path.name,
                'path': str(path),
                'size_bytes': len(data),
            })

        return downloaded

    @staticmethod
    def _unique_path(out: Path, filename: str) -> Path:
        """Return a path under `out` that doesn't collide with an existing file.

        Outlook inline images all share names like `image.png`, so without
        disambiguation the second attachment would silently overwrite the
        first. Suffix an index before the extension on collision:
        `image.png`, `image_1.png`, `image_2.png`, …
        """
        candidate = out / filename
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        i = 1
        while True:
            candidate = out / f'{stem}_{i}{suffix}'
            if not candidate.exists():
                return candidate
            i += 1

    def get_signature(self) -> str:
        """Get the primary Gmail signature (HTML)."""
        result = self.service.users().settings().sendAs().list(userId='me').execute()
        for alias in result.get('sendAs', []):
            if alias.get('isPrimary'):
                return alias.get('signature', '')
        return ''

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        html: Optional[str] = None,
        cc: Optional[str] = None,
        bcc: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        from_name: Optional[str] = None,
        markdown: bool = True,
    ) -> dict:
        """Send an email. Returns the sent message metadata.

        If `html` is not provided and `markdown=True` (default), the body is
        rendered from markdown to an HTML fragment so Gmail wraps paragraphs
        naturally instead of dumping hard line breaks in text/plain mode.
        """
        if html is None and markdown:
            html = _markdown_to_html_fragment(body)
        message = self._build_message(to, subject, body, html, cc, bcc, attachments, from_name=from_name)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = self.service.users().messages().send(
            userId='me', body={'raw': raw},
        ).execute()
        return {'id': sent['id'], 'threadId': sent.get('threadId', '')}

    def reply(
        self,
        message_id: Optional[str],
        body: str,
        html: Optional[str] = None,
        cc: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        from_name: Optional[str] = None,
        markdown: bool = True,
        to: Optional[str] = None,
        thread_id: Optional[str] = None,
        reply_all: bool = False,
    ) -> dict:
        """Reply to a message (`message_id`) or to the latest message of a thread
        (`thread_id`) — exactly one of them. Keeps the thread, subject and threading
        headers; recipients per `_reply.reply_recipients` (the author alone unless
        `reply_all` or an explicit `to`/`cc`).

        If `html` is not provided and `markdown=True` (default), the body is
        rendered from markdown to an HTML fragment. Returns the sent message's `id`
        and `threadId`, plus what `_reply_context` names (recipients, the message
        answered, `warning` when it is not the latest of its thread).
        """
        if html is None and markdown:
            html = _markdown_to_html_fragment(body)
        ctx = self._reply_context(message_id, thread_id, to, cc, reply_all)
        message = self._build_message(ctx['to'], ctx['subject'], body, html, ctx['cc'] or None,
                                      None, attachments, from_name=from_name)
        self._thread(message, ctx)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = self.service.users().messages().send(
            userId='me', body={'raw': raw, 'threadId': ctx['thread_id']},
        ).execute()
        return {'id': sent['id'], 'threadId': sent.get('threadId', ''), **ctx['answer']}

    def _reply_context(self, message_id: Optional[str], thread_id: Optional[str],
                       to: Optional[str], cc: Optional[str], reply_all: bool) -> dict:
        """Everything a reply takes from the message it answers, in two reads: the
        thread (headers of each message), after the message's own threadId.

        `answer` is what the caller gets back: `to`, `cc`, `replied_to` (the message
        answered) and, when `message_id` is not the latest message of its thread,
        `warning` with the latest one's id — a reply to an older message is filed
        under it by the recipient's client.
        """
        if (message_id is None) == (thread_id is None):
            raise GmailClientError("A reply needs exactly one of message_id (the message "
                                   "to answer) or thread_id (answers its latest message).")
        messages = self.service.users().messages()
        if message_id is not None:
            thread_id = messages.get(userId='me', id=message_id, format='minimal',
                                     ).execute()['threadId']
        thread = self.service.users().threads().get(
            userId='me', id=thread_id, format='metadata', metadataHeaders=REPLY_HEADERS,
        ).execute().get('messages', [])
        latest = latest_of(thread)
        if message_id is None:
            if latest is None:
                raise GmailClientError(f"Thread {thread_id!r} has no message to answer "
                                       "(only drafts or trashed messages).")
            parent = latest
        else:
            parent = next((m for m in thread if m['id'] == message_id), None)
            if parent is None:
                raise GmailClientError(f"Message {message_id!r} is not in its thread "
                                       f"{thread_id!r} as Gmail returns it.")
        headers = {h['name'].lower(): h['value'] for h in parent['payload']['headers']}
        my_email = self.service.users().getProfile(userId='me').execute()['emailAddress']
        reply_to, reply_cc = reply_recipients(headers, my_email, to=to, cc=cc,
                                              reply_all=reply_all)
        if not reply_to:
            raise GmailClientError("Cannot determine reply recipient (from="
                                   f"{headers.get('from', '')!r}, to={headers.get('to', '')!r}).")
        in_reply_to, references = threading_headers(headers)
        answer = {'to': reply_to, 'cc': reply_cc, 'replied_to': parent['id']}
        if latest is not None and latest['id'] != parent['id']:
            answer['warning'] = {
                'code': 'not_latest_in_thread',
                'latest_message_id': latest['id'],
                'message': (f"Message {parent['id']} is not the latest of its thread: "
                            f"{latest['id']} is. The reply is filed under the older one."),
            }
        return {'thread_id': thread_id, 'subject': reply_subject(headers),
                'in_reply_to': in_reply_to, 'references': references,
                'to': reply_to, 'cc': reply_cc, 'answer': answer}

    @staticmethod
    def _thread(message, ctx: dict) -> None:
        if ctx['in_reply_to']:
            message['In-Reply-To'] = ctx['in_reply_to']
            message['References'] = ctx['references']

    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        html: Optional[str] = None,
        cc: Optional[str] = None,
        bcc: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        thread_id: Optional[str] = None,
        in_reply_to: Optional[str] = None,
        markdown: bool = True,
    ) -> dict:
        """Create a draft email. Pass thread_id + in_reply_to for threaded replies.

        If `html` is not provided and `markdown=True` (default), the body is
        rendered from markdown to an HTML fragment — **same contract as `send` and
        `reply`**, which already did so. Only the draft did not: an agent
        composing in markdown got a text body where `**bold**` and the
        bullets stayed literal, visible as-is to the recipient (#341/#343).
        """
        if html is None and markdown:
            html = _markdown_to_html_fragment(body)
        message = self._build_message(to, subject, body, html, cc, bcc, attachments)
        if in_reply_to:
            message['In-Reply-To'] = in_reply_to
            message['References'] = in_reply_to
        return self._save_draft(message, thread_id)

    def _save_draft(self, message, thread_id: Optional[str]) -> dict:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        msg_body: dict = {'raw': raw}
        if thread_id:
            msg_body['threadId'] = thread_id
        draft = self.service.users().drafts().create(
            userId='me', body={'message': msg_body},
        ).execute()
        msg = draft['message']
        return {'id': draft['id'], 'message_id': msg['id'], 'threadId': msg.get('threadId', '')}

    def update_draft(
        self,
        draft_id: str,
        body: str,
        html: Optional[str] = None,
        to: Optional[str] = None,
        subject: Optional[str] = None,
        cc: Optional[str] = None,
        bcc: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        markdown: bool = True,
    ) -> dict:
        """Replace the content of an existing draft, which stays the same draft (same id).

        The body is replaced whole, attachments included: a draft's former
        attachments are not carried over. `to`, `subject`, `cc`, `bcc` left at None
        keep the draft's current value. The thread is kept: a draft written as a
        reply stays filed in its conversation, with its threading headers.
        Same markdown contract as `create_draft`.
        """
        current = self.service.users().drafts().get(
            userId='me', id=draft_id, format='metadata').execute()
        msg = current.get('message', {})
        headers = {h['name'].lower(): h['value']
                   for h in msg.get('payload', {}).get('headers', [])}
        if html is None and markdown:
            html = _markdown_to_html_fragment(body)
        pick = lambda value, name: headers.get(name) if value is None else value
        message = self._build_message(pick(to, 'to') or '', pick(subject, 'subject') or '',
                                      body, html, pick(cc, 'cc') or None,
                                      pick(bcc, 'bcc') or None, attachments)
        if headers.get('in-reply-to'):
            message['In-Reply-To'] = headers['in-reply-to']
            message['References'] = headers.get('references') or headers['in-reply-to']
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        msg_body: dict = {'raw': raw}
        if msg.get('threadId'):
            msg_body['threadId'] = msg['threadId']
        draft = self.service.users().drafts().update(
            userId='me', id=draft_id, body={'id': draft_id, 'message': msg_body},
        ).execute()
        out = draft['message']
        return {'id': draft['id'], 'message_id': out['id'], 'threadId': out.get('threadId', '')}

    def list_drafts(self, max_results: int = 20) -> list[dict]:
        """List drafts with metadata (id, message_id, to, subject, snippet)."""
        resp = self.service.users().drafts().list(userId='me', maxResults=max_results).execute()
        drafts = resp.get('drafts', [])
        out = []
        for d in drafts:
            msg_id = d['message']['id']
            msg = self.service.users().messages().get(
                userId='me', id=msg_id, format='metadata',
                metadataHeaders=['To', 'Subject', 'Date'],
            ).execute()
            headers = {h['name'].lower(): h['value'] for h in msg.get('payload', {}).get('headers', [])}
            out.append({
                'id': d['id'],
                'message_id': msg_id,
                'to': headers.get('to', ''),
                'subject': headers.get('subject', ''),
                'date': headers.get('date', ''),
                'snippet': msg.get('snippet', ''),
            })
        return out

    def send_draft(self, draft_id: str) -> dict:
        """Send an existing draft as it stands. The draft leaves the drafts list."""
        sent = self.service.users().drafts().send(
            userId='me', body={'id': draft_id},
        ).execute()
        return {'id': sent['id'], 'threadId': sent.get('threadId', '')}

    def delete_draft(self, draft_id: str) -> dict:
        """Delete a draft by its draft ID."""
        self.service.users().drafts().delete(userId='me', id=draft_id).execute()
        return {'deleted': draft_id}

    def create_draft_reply(
        self,
        message_id: Optional[str],
        body: str,
        html: Optional[str] = None,
        cc: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        markdown: bool = True,
        to: Optional[str] = None,
        thread_id: Optional[str] = None,
        reply_all: bool = False,
    ) -> dict:
        """Create a draft reply — same targets, recipients and headers as `reply`.

        If `html` is not provided and `markdown=True` (default), the body is
        rendered from markdown to an HTML fragment — same contract as `reply`.
        """
        if html is None and markdown:
            html = _markdown_to_html_fragment(body)
        ctx = self._reply_context(message_id, thread_id, to, cc, reply_all)
        message = self._build_message(ctx['to'], ctx['subject'], body, html, ctx['cc'] or None,
                                      None, attachments)
        self._thread(message, ctx)
        return {**self._save_draft(message, ctx['thread_id']), **ctx['answer']}

    def _build_message(self, to, subject, body, html=None, cc=None, bcc=None, attachments=None, from_name=None):
        """Build a MIME message."""
        has_attachments = attachments and len(attachments) > 0

        if has_attachments:
            message = MIMEMultipart('mixed')
            if html:
                text_part = MIMEMultipart('alternative')
                text_part.attach(MIMEText(body, 'plain'))
                text_part.attach(MIMEText(html, 'html'))
                message.attach(text_part)
            else:
                message.attach(MIMEText(body, 'plain'))
            for filepath in attachments:
                message.attach(self._make_attachment(filepath))
        elif html:
            message = MIMEMultipart('alternative')
            message.attach(MIMEText(body, 'plain'))
            message.attach(MIMEText(html, 'html'))
        else:
            message = MIMEText(body)

        # Headers in CANONICAL form (RFC 5322): the Gmail API returns names AS
        # WRITTEN — a lowercase `cc:` stays `cc` in `payload.headers`, while
        # the ones Gmail sets itself arrive capitalized (`From`, `Date`). Any
        # reader looking for `Cc` (ours included, cf. `get_message`) therefore saw
        # our own messages with no recipient or cc — hence the diagnosis
        # "the cc is not applied" when it actually was (signals #340/#342).
        message['To'] = to
        message['Subject'] = subject
        if cc:
            message['Cc'] = cc
        if bcc:
            message['Bcc'] = bcc
        if from_name:
            profile = self.service.users().getProfile(userId='me').execute()
            message['From'] = f'{from_name} <{profile["emailAddress"]}>'
        return message

    @staticmethod
    def _make_attachment(filepath: str) -> MIMEBase:
        """Create a MIME attachment from a file path."""
        path = Path(filepath)
        content_type, _ = mimetypes.guess_type(str(path))
        if content_type is None:
            content_type = 'application/octet-stream'
        main_type, sub_type = content_type.split('/', 1)

        part = MIMEBase(main_type, sub_type)
        part.set_payload(path.read_bytes())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename=path.name)
        return part

    def search(self, query: str, max_results: int = 20) -> list[dict]:
        """Search messages using Gmail query syntax."""
        return self.list_messages(query=query, max_results=max_results)

    def archive_messages(self, message_ids: list[str]) -> list[dict]:
        """Archive messages (remove INBOX label). Returns list of results."""
        results = []
        for mid in message_ids:
            res = self.service.users().messages().modify(
                userId='me', id=mid,
                body={'removeLabelIds': ['INBOX']},
            ).execute()
            results.append({'id': res['id'], 'labelIds': res.get('labelIds', [])})
        return results

    def trash_message(self, message_id: str) -> dict:
        """Move a message to trash."""
        return self.service.users().messages().trash(
            userId='me', id=message_id,
        ).execute()

    def _list_attachments(self, payload: dict) -> list[dict]:
        """List attachment metadata from message payload.

        Returns {filename, mimeType, size}. **No `attachmentId`**: Gmail
        regenerates it on every `messages.get` (verified — two successive reads of
        the same message give different ids) → it is not a stable handle
        across calls. The content is fetched via `get_attachment(message_id,
        filename)`, which resolves the fresh id in a single `messages.get`.
        """
        attachments = []
        for part in self._iter_parts(payload):
            filename = part.get('filename')
            att_id = part.get('body', {}).get('attachmentId')
            if filename and att_id:
                attachments.append({
                    'filename': filename,
                    'mimeType': part.get('mimeType', ''),
                    'size': part.get('body', {}).get('size', 0),
                })
        return attachments

    def get_attachment(self, message_id: str, filename: str, index: int = 0) -> dict:
        """Fetch the CONTENT of an attachment by its file NAME.

        Returns {filename, mimeType, size, data: bytes}. The **filename** is the
        (stable) handle — not the Gmail attachmentId, which is volatile across calls. The
        name→id resolution and the download happen in the SAME `messages.get`
        (so the id is always fresh). `index` (default 0) disambiguates when several
        attachments share the same name (e.g. multiple inline `image.png`). Pure client:
        returns the bytes, writes nothing to disk (≠ `download_attachments`,
        reserved for the CLI). Raises `GmailClientError` if not found.
        """
        msg = self.service.users().messages().get(
            userId='me', id=message_id, format='full',
        ).execute()
        matches = []
        for part in self._iter_parts(msg.get('payload', {})):
            att_id = part.get('body', {}).get('attachmentId')
            if att_id and part.get('filename') == filename:
                matches.append((att_id, part.get('mimeType', '')))
        if not matches:
            raise GmailClientError(
                f"Attachment {filename!r} not found in message {message_id!r}."
            )
        if index < 0 or index >= len(matches):
            raise GmailClientError(
                f"index {index} out of range ({len(matches)} attachments named {filename!r})."
            )
        att_id, mime = matches[index]
        att = self.service.users().messages().attachments().get(
            userId='me', messageId=message_id, id=att_id,
        ).execute()
        data = base64.urlsafe_b64decode(att['data'])
        return {
            'filename': filename,
            'mimeType': mime or 'application/octet-stream',
            'size': len(data),
            'data': data,
        }

    def _iter_parts(self, payload: dict):
        """Recursively yield all parts from a message payload."""
        parts = payload.get('parts', [])
        for part in parts:
            yield part
            yield from self._iter_parts(part)

    def _extract_body(self, payload: dict) -> str:
        """Extract plain text body from message payload."""
        # Simple single-part message
        if payload.get('body', {}).get('data'):
            return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='replace')

        # Multipart: look for text/plain first, then text/html
        parts = payload.get('parts', [])
        for mime_type in ('text/plain', 'text/html'):
            text = self._find_part(parts, mime_type)
            if text:
                return text

        return ''

    def _find_part(self, parts: list, mime_type: str) -> Optional[str]:
        """Recursively find a part by MIME type."""
        for part in parts:
            if part.get('mimeType') == mime_type and part.get('body', {}).get('data'):
                return base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='replace')
            # Recurse into nested parts
            nested = part.get('parts', [])
            if nested:
                result = self._find_part(nested, mime_type)
                if result:
                    return result
        return None
