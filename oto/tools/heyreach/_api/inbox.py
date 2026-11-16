"""The LinkedIn inbox, the sender accounts and their network, the webhooks.

A mixin composed into `HeyReachClient`, which provides the transport (`_call`).
"""

from __future__ import annotations

from typing import Any, List, Optional
from urllib.parse import quote

from . import checks as c


def _segment(value: Any, name: str) -> str:
    """A path segment, percent-encoded whole (a conversation id is base64: `/`, `+`, `=`)."""
    value = c.text(value, name, required=True)
    if not value.strip("."):
        raise ValueError(f"`{name}` cannot be made of dots only; got {value!r}.")
    return quote(value, safe="")


class InboxMixin:

    # --- conversations ------------------------------------------------------

    def list_conversations(self, limit: Optional[int] = None, cursor: Optional[str] = None, *,
                           last_message_from: Optional[str] = None,
                           last_message_to: Optional[str] = None,
                           linkedin_account_ids: Optional[List[int]] = None,
                           campaign_ids: Optional[List[int]] = None,
                           search: Optional[str] = None,
                           lead_linkedin_id: Optional[str] = None,
                           lead_profile_url: Optional[str] = None,
                           tags: Optional[List[str]] = None,
                           latest_auto_tag_names: Optional[List[str]] = None,
                           seen: Optional[bool] = None) -> Any:
        """POST /inbox/GetConversationsV3 — one page of conversations, newest message
        first; next page: `cursor` = nextCursor while hasNextPage."""
        c.not_both("lead_linkedin_id", lead_linkedin_id, "lead_profile_url", lead_profile_url)
        return self._call("POST", "/inbox/GetConversationsV3", body={
            "limit": c.limit(limit, required=False), "cursor": c.text(cursor, "cursor"),
            "from": c.date_time(last_message_from, "last_message_from"),
            "to": c.date_time(last_message_to, "last_message_to"),
            "filters": {
                "linkedInAccountIds": c.idents(linkedin_account_ids, "linkedin_account_ids"),
                "campaignIds": c.idents(campaign_ids, "campaign_ids"),
                "searchString": c.text(search, "search"),
                "leadLinkedInId": c.text(lead_linkedin_id, "lead_linkedin_id"),
                "leadProfileUrl": c.text(lead_profile_url, "lead_profile_url"),
                "tags": c.texts(tags, "tags"),
                "latestAutoTagNames": c.texts(latest_auto_tag_names, "latest_auto_tag_names"),
                "seen": c.flag(seen, "seen")}}, strip="list_conversations")

    def get_conversation(self, linkedin_account_id: int, conversation_id: str) -> Any:
        """GET /inbox/GetChatroom/{linkedin_account_id}/{conversation_id} — one thread
        with all its messages."""
        account = c.ident(linkedin_account_id, "linkedin_account_id")
        return self._call("GET", f"/inbox/GetChatroom/{account}/"
                                 f"{_segment(conversation_id, 'conversation_id')}",
                          strip="get_conversation")

    def send_message(self, linkedin_account_id: int, conversation_id: str, message: str, *,
                     subject: Optional[str] = None) -> Any:
        """POST /inbox/SendMessage — reaches the person at once; never retried here."""
        return self._call("POST", "/inbox/SendMessage", body={
            "linkedInAccountId": c.ident(linkedin_account_id, "linkedin_account_id"),
            "conversationId": c.text(conversation_id, "conversation_id", required=True),
            "message": c.text(message, "message", required=True),
            "subject": c.text(subject, "subject")})

    def set_seen_status(self, linkedin_account_id: int, conversation_id: str,
                        seen: bool) -> Any:
        """POST /inbox/SetSeenStatus — mark a conversation read or unread."""
        return self._call("POST", "/inbox/SetSeenStatus", body={
            "linkedInAccountId": c.ident(linkedin_account_id, "linkedin_account_id"),
            "conversationId": c.text(conversation_id, "conversation_id", required=True),
            "seen": c.flag(seen, "seen", required=True)})

    # --- sender accounts ----------------------------------------------------

    def list_linkedin_accounts(self, limit: int, offset: Optional[int] = None, *,
                               keyword: Optional[str] = None) -> Any:
        """POST /li_account/GetAll — one page of the workspace's LinkedIn senders."""
        return self._call("POST", "/li_account/GetAll", body={
            "limit": c.limit(limit), "offset": c.offset(offset),
            "keyword": c.text(keyword, "keyword")})

    def get_linkedin_account(self, account_id: int) -> Any:
        """GET /li_account/GetById — one sender, its session state and daily limits."""
        return self._call("GET", "/li_account/GetById",
                          query={"accountId": c.ident(account_id, "account_id")})

    def check_connection(self, sender_account_id: int, *, lead_profile_url: Optional[str] = None,
                         lead_linkedin_id: Optional[str] = None) -> Any:
        """POST /MyNetwork/IsConnection — {isConnection}; the lead by exactly one key."""
        c.not_both("lead_profile_url", lead_profile_url, "lead_linkedin_id", lead_linkedin_id)
        c.one_lead_key(lead_profile_url=lead_profile_url, lead_linkedin_id=lead_linkedin_id)
        return self._call("POST", "/MyNetwork/IsConnection", body={
            "senderAccountId": c.ident(sender_account_id, "sender_account_id"),
            "leadProfileUrl": c.text(lead_profile_url, "lead_profile_url"),
            "leadLinkedInId": c.text(lead_linkedin_id, "lead_linkedin_id")})

    # --- webhooks -----------------------------------------------------------

    def list_webhooks(self, limit: int, offset: Optional[int] = None) -> Any:
        """POST /webhooks/GetAllWebhooks — one page of webhooks, never their custom headers."""
        return self._call("POST", "/webhooks/GetAllWebhooks", body={
            "limit": c.limit(limit), "offset": c.offset(offset),
            "includeCustomHeaders": False}, strip="list_webhooks")

    def get_webhook(self, webhook_id: int) -> Any:
        """GET /webhooks/GetWebhookById — one webhook, never its custom headers."""
        return self._call("GET", "/webhooks/GetWebhookById", query={
            "webhookId": c.ident(webhook_id, "webhook_id"), "includeCustomHeaders": "false"},
            strip="get_webhook")

    def create_webhook(self, webhook_name: str, webhook_url: str, event_type: str, *,
                       campaign_ids: Optional[List[int]] = None) -> Any:
        """POST /webhooks/CreateWebhook — HeyReach POSTs this event type to an https
        address; `campaign_ids` empty: every campaign."""
        return self._call("POST", "/webhooks/CreateWebhook", body={
            "webhookName": c.text(webhook_name, "webhook_name", required=True),
            "webhookUrl": c.https(webhook_url, "webhook_url"),
            "eventType": c.choice(event_type, "event_type", c.EVENT_TYPES, required=True),
            "campaignIds": c.idents(campaign_ids, "campaign_ids", allow_empty=True)})

    def delete_webhook(self, webhook_id: int) -> Any:
        """DELETE /webhooks/DeleteWebhook — its events stop."""
        return self._call("DELETE", "/webhooks/DeleteWebhook",
                          query={"webhookId": c.ident(webhook_id, "webhook_id")})
