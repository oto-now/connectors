"""Webhooks: Instantly POSTs an event (a reply, an interest change…) to an address."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import checks as c

EVENT_TYPES = (
    "all_events", "email_sent", "email_opened", "email_link_clicked", "reply_received",
    "email_bounced", "lead_unsubscribed", "campaign_completed", "account_error", "lead_neutral",
    "lead_interested", "lead_not_interested", "lead_meeting_booked", "lead_meeting_completed",
    "lead_closed", "lead_out_of_office", "lead_wrong_person", "lead_no_show",
)


class _WebhooksMixin:
    def list_webhooks(self, *, campaign: Optional[str] = None, event_type: Optional[str] = None,
                      limit: Optional[int] = None, starting_after: Optional[str] = None) -> Any:
        """GET /webhooks — one page, without the delivery headers."""
        return c.without_webhook_headers(self._get("/webhooks", {
            "campaign": c.optional_uuid(campaign, "campaign"),
            "event_type": c.choice(event_type, "event_type", EVENT_TYPES),
            "limit": c.limit(limit),
            "starting_after": c.text(starting_after, "starting_after"),
        }))

    def list_webhook_event_types(self) -> Any:
        """GET /webhooks/event-types — the events a webhook can listen to."""
        return self._get("/webhooks/event-types")

    def create_webhook(self, target_hook_url: str, *, event_type: Optional[str] = None,
                       custom_interest_value: Optional[int] = None,
                       campaign: Optional[str] = None, name: Optional[str] = None,
                       headers: Optional[Dict[str, str]] = None) -> Any:
        """POST /webhooks — Instantly will POST each event to `target_hook_url` (https).

        `headers` (delivered with each event, such as the destination's `Authorization`)
        is a SECRET: it stays with the consumer's own code, set server side, and no
        description serves it to an agent. The answer never carries it back."""
        if event_type is None and custom_interest_value is None:
            raise ValueError("Give `event_type`, or `custom_interest_value` for a custom label.")
        if custom_interest_value is not None and (isinstance(custom_interest_value, bool)
                                                  or not isinstance(custom_interest_value, int)):
            raise ValueError("`custom_interest_value` must be an integer (a label's interest_status).")
        if headers is not None and not (isinstance(headers, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in headers.items())):
            raise ValueError("`headers` must map header names to text values.")
        return c.without_webhook_headers(self._send("POST", "/webhooks", c.present({
            "target_hook_url": c.https_url(target_hook_url, "target_hook_url"),
            "event_type": c.choice(event_type, "event_type", EVENT_TYPES),
            "custom_interest_value": custom_interest_value,
            "campaign": c.optional_uuid(campaign, "campaign"),
            "name": c.text(name, "name"),
            "headers": headers,
        })))

    def delete_webhook(self, webhook_id: str) -> Any:
        """DELETE /webhooks/{id} — Instantly stops posting its events."""
        return c.without_webhook_headers(
            self._send("DELETE", f"/webhooks/{c.uuid(webhook_id, 'webhook_id')}"))
