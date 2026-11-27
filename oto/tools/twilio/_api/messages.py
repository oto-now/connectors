"""SMS and MMS.

Reference: Message resource, https://www.twilio.com/docs/messaging/api/message-resource
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

from ._common import e164, form, sid
from ._common import page_size as _page_size

MESSAGE_STATUSES = ("queued", "sending", "sent", "failed", "delivered", "undelivered",
                    "receiving", "received", "accepted", "scheduled", "read", "canceled")
MAX_BODY = 1600
MAX_MEDIA = 10
# An alphanumeric sender ID: 1 to 11 characters, letters, digits and spaces, at least one letter.
_ALPHA_SENDER = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9 ]{1,11}$")


def _sender(value: str) -> str:
    text = (value or "").strip()
    if text.startswith("+"):
        return e164(text, "from_")
    if _ALPHA_SENDER.match(text):
        return text
    raise ValueError("`from_` must be an E.164 number of this account or an alphanumeric "
                     f"sender ID (1-11 letters, digits, spaces); got {value!r}.")


class MessagesMixin:
    def send_message(self, to: str, *, body: Optional[str] = None, from_: Optional[str] = None,
                     messaging_service_sid: Optional[str] = None,
                     media_urls: Optional[Sequence[str]] = None,
                     status_callback: Optional[str] = None) -> dict:
        """Send an SMS (or MMS with `media_urls`) to `to`. The sender is
        `from_` (a number of this account, or an alphanumeric sender ID where
        the country allows one: such a message cannot be replied to) or
        `messaging_service_sid`. BILLED per segment (160 GSM-7 characters, 70
        when the text needs Unicode). Returns the message (`sid`, `status`,
        `num_segments`)."""
        if (from_ is None) == (messaging_service_sid is None):
            raise ValueError("send_message: give exactly one of `from_` or `messaging_service_sid`.")
        if not body and not media_urls:
            raise ValueError("send_message: give `body`, `media_urls`, or both.")
        if body and len(body) > MAX_BODY:
            raise ValueError(f"`body` exceeds {MAX_BODY} characters.")
        if media_urls and len(media_urls) > MAX_MEDIA:
            raise ValueError(f"`media_urls`: at most {MAX_MEDIA} files.")
        data = form(To=e164(to, "to"), From=_sender(from_) if from_ else None,
                    MessagingServiceSid=(sid(messaging_service_sid, "MG", "messaging_service_sid")
                                         if messaging_service_sid else None),
                    Body=body, MediaUrl=list(media_urls or []) or None,
                    StatusCallback=status_callback)
        return self._post("/Messages.json", data)

    def get_message(self, message_sid: str) -> dict:
        """One message: `status`, `direction`, `from`, `to`, `body`,
        `num_segments`, `price`, `error_code`."""
        return self._get(f"/Messages/{_message_sid(message_sid)}.json")

    def list_messages(self, *, to: Optional[str] = None, from_: Optional[str] = None,
                      date_sent_after: Optional[str] = None, date_sent_before: Optional[str] = None,
                      page_size: Optional[int] = None) -> dict:
        """Messages of this account, most recent first, both directions.
        Dates are `YYYY-MM-DD` (UTC). A thread with one person is the union
        of `to=them` and `from_=them`."""
        return self._get("/Messages.json", {
            "To": to, "From": from_, "DateSent>": date_sent_after, "DateSent<": date_sent_before,
            "PageSize": _page_size(page_size)})


def _message_sid(value: str) -> str:
    text = (value or "").strip()
    if text[:2] in ("SM", "MM"):
        return sid(text, text[:2], "message_sid")
    raise ValueError(f"`message_sid` must start with SM or MM; got {value!r}.")
