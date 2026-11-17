"""What a reply takes from the message it answers: recipients, subject, threading headers.

Pure functions over a message's headers (names lowercased) and a thread's messages, as
the Gmail API returns them. No call here: `GmailClient` fetches, these decide.
"""

from email.utils import getaddresses
from typing import Optional

#: The headers a reply reads from the message it answers.
REPLY_HEADERS = ['From', 'To', 'Cc', 'Reply-To', 'Subject', 'Message-ID', 'References', 'In-Reply-To']

#: Labels of thread messages that are not part of the conversation as sent and received.
_NOT_IN_CONVERSATION = {'DRAFT', 'TRASH'}


def _addresses(header: str) -> list[str]:
    """The addresses of a header, in order.

    ⚠️ Do NOT use `parseaddr` here: it reads only one address and returns
    `('', '')` on `To: a@x, b@y` — a thread with several recipients then became
    "Cannot determine reply recipient".
    """
    # `"@" in a`: on a header with no real address ("Name Without Address"),
    # getaddresses returns [('', 'Name')] — letting it through would send to "Name"
    # instead of raising the call guard.
    return [a for _, a in getaddresses([header]) if a and "@" in a]


def recipients(header: str, exclude: str = "") -> str:
    """The addresses of a header, as a list, minus `exclude`."""
    addrs = _addresses(header)
    if exclude:
        addrs = [a for a in addrs if a.lower() != exclude.lower()]
    return ", ".join(addrs)


def _primary(headers: dict, my_email: str) -> str:
    """Who a reply goes to by default: the Reply-To, otherwise the sender, otherwise —
    on a message we sent ourselves — all its To recipients."""
    if headers.get('reply-to'):
        return recipients(headers['reply-to'])
    if my_email.lower() in (a.lower() for a in _addresses(headers.get('from', ''))):
        return recipients(headers.get('to', ''))
    return recipients(headers.get('from', ''))


def reply_recipients(headers: dict, my_email: str, to: Optional[str] = None,
                     cc: Optional[str] = None, reply_all: bool = False) -> tuple[str, str]:
    """`(to, cc)` of a reply. An empty `to` means none could be determined.

    By default the reply goes to the default recipient (`_primary`) alone: nobody else
    receives it unless the call says so. `reply_all=True` copies everyone else on the
    message (its To and Cc), minus ourselves and anyone already in To. An explicit `to`
    or `cc` wins over both: the reply goes exactly to them, `to` falling back to the
    default recipient (a reply always has one).
    """
    primary = _primary(headers, my_email)
    if to or cc:
        return (to or primary), (cc or "")
    if not reply_all:
        return primary, ""
    leave_out = {my_email.lower()} | {a.lower() for a in _addresses(primary)}
    copied: list[str] = []
    for a in _addresses(headers.get('to', '')) + _addresses(headers.get('cc', '')):
        if a.lower() not in leave_out:
            leave_out.add(a.lower())
            copied.append(a)
    return primary, ", ".join(copied)


def reply_subject(headers: dict) -> str:
    subject = headers.get('subject', '')
    return subject if subject.lower().startswith('re:') else f"Re: {subject}"


def threading_headers(headers: dict) -> tuple[Optional[str], Optional[str]]:
    """`(In-Reply-To, References)` of a reply, per RFC 5322 §3.6.4.

    References = the parent's References (or, without them, its In-Reply-To when it
    names a single message) followed by the parent's Message-ID. Only the parent's id
    in References made some clients (Outlook) file the reply apart from the
    conversation. `(None, None)` when the parent has no Message-ID: Gmail's threadId
    still files the reply in the thread on our side.
    """
    parent_id = headers.get('message-id', '').strip()
    if not parent_id:
        return None, None
    chain = headers.get('references', '').split()
    if not chain:
        in_reply_to = headers.get('in-reply-to', '').split()
        chain = in_reply_to if len(in_reply_to) == 1 else []
    if parent_id not in chain:
        chain.append(parent_id)
    return parent_id, " ".join(chain)


def latest_of(messages: list[dict]) -> Optional[dict]:
    """The latest message of a thread (the API lists them oldest first), drafts and
    trashed messages left out. None when nothing is left."""
    kept = [m for m in messages if not _NOT_IN_CONVERSATION & set(m.get('labelIds', []))]
    return kept[-1] if kept else None
