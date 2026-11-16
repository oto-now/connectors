"""The Unibox (emails sent and received, a reply, a thread marked read) and the sending
accounts."""
from __future__ import annotations

from typing import Any, Optional

from .. import checks as c

EMAIL_TYPES = ("received", "sent", "manual")
MODES = ("emode_focused", "emode_others", "emode_all")
ACCOUNT_STATUSES = (1, 2, 3, -1, -2, -3)
PROVIDER_CODES = (1, 2, 3, 4, 8, 11)


class _UniboxMixin:
    def list_emails(self, *, campaign_id: Optional[str] = None, list_id: Optional[str] = None,
                    lead: Optional[str] = None, search: Optional[str] = None,
                    eaccount: Optional[str] = None, email_type: Optional[str] = None,
                    is_unread: Optional[bool] = None, i_status: Optional[int] = None,
                    mode: Optional[str] = None, latest_of_thread: Optional[bool] = None,
                    preview_only: Optional[bool] = None, company_domain: Optional[str] = None,
                    min_timestamp_created: Optional[str] = None,
                    max_timestamp_created: Optional[str] = None,
                    sort_order: Optional[str] = None, limit: Optional[int] = None,
                    starting_after: Optional[str] = None) -> Any:
        """GET /emails — the Unibox, one page (20 requests per minute). Attachments come
        without their download address; bodies are returned as sent."""
        if i_status is not None and (isinstance(i_status, bool) or not isinstance(i_status, int)):
            raise ValueError(f"`i_status` must be an integer; got {i_status!r}.")
        return c.without_attachment_urls(self._get("/emails", {
            "campaign_id": c.optional_uuid(campaign_id, "campaign_id"),
            "list_id": c.optional_uuid(list_id, "list_id"),
            "lead": None if lead is None else c.email(lead, "lead"),
            "search": c.text(search, "search"),
            "eaccount": c.text(eaccount, "eaccount"),
            "email_type": c.choice(email_type, "email_type", EMAIL_TYPES),
            "is_unread": is_unread,
            "i_status": i_status,
            "mode": c.choice(mode, "mode", MODES),
            "latest_of_thread": latest_of_thread,
            "preview_only": preview_only,
            "company_domain": c.text(company_domain, "company_domain"),
            "min_timestamp_created": c.text(min_timestamp_created, "min_timestamp_created"),
            "max_timestamp_created": c.text(max_timestamp_created, "max_timestamp_created"),
            "sort_order": c.choice(sort_order, "sort_order", ("asc", "desc")),
            "limit": c.limit(limit),
            "starting_after": c.text(starting_after, "starting_after"),
        }))

    def get_email(self, email_id: str) -> Any:
        """GET /emails/{id} — one email in full, attachments without their address."""
        return c.without_attachment_urls(self._get(f"/emails/{c.uuid(email_id, 'email_id')}"))

    def count_unread_emails(self) -> Any:
        """GET /emails/unread/count — {count}."""
        return self._get("/emails/unread/count")

    def reply_to_email(self, reply_to_uuid: str, eaccount: str, subject: str, *,
                       text: Optional[str] = None, html: Optional[str] = None,
                       cc_address_email_list: Optional[str] = None,
                       bcc_address_email_list: Optional[str] = None) -> Any:
        """POST /emails/reply — SENDS a reply to the sender of an email, at once."""
        body = c.present({"text": c.text(text, "text"), "html": c.text(html, "html")})
        if not body:
            raise ValueError("Give `text` or `html` (or both): the reply needs a body.")
        return c.without_attachment_urls(self._send("POST", "/emails/reply", {
            "reply_to_uuid": c.uuid(reply_to_uuid, "reply_to_uuid"),
            "eaccount": c.email(eaccount, "eaccount"),
            "subject": c.required_text(subject, "subject"),
            "body": body,
            **c.present({
                "cc_address_email_list": c.text(cc_address_email_list, "cc_address_email_list"),
                "bcc_address_email_list": c.text(bcc_address_email_list, "bcc_address_email_list"),
            }),
        }))

    def mark_thread_as_read(self, thread_id: str) -> Any:
        """POST /emails/threads/{thread_id}/mark-as-read — every email of the thread. No body."""
        return self._send("POST", f"/emails/threads/{c.uuid(thread_id, 'thread_id')}/mark-as-read")

    # --- sending accounts ---------------------------------------------------------------------

    def list_accounts(self, *, search: Optional[str] = None, status: Optional[int] = None,
                      provider_code: Optional[int] = None, tag_ids: Optional[str] = None,
                      limit: Optional[int] = None, starting_after: Optional[str] = None) -> Any:
        """GET /accounts — sending accounts, one page; no credential is in the answer."""
        return self._get("/accounts", {
            "search": c.text(search, "search"),
            "status": c.choice(status, "status", ACCOUNT_STATUSES),
            "provider_code": c.choice(provider_code, "provider_code", PROVIDER_CODES),
            "tag_ids": c.tag_ids(tag_ids),
            "limit": c.limit(limit),
            "starting_after": c.text(starting_after, "starting_after"),
        })

    def get_account(self, email: str) -> Any:
        """GET /accounts/{email} — one sending account."""
        return self._get(f"/accounts/{c.path_email(email, 'email')}")
