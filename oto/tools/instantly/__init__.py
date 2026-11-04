"""Instantly — cold email outreach: campaigns and their analytics, leads and lead lists, the
Unibox (replies) and sending accounts, the block list, webhooks."""

from .checks import without_attachment_urls, without_webhook_headers
from .client import InstantlyClient

__all__ = ["InstantlyClient", "without_attachment_urls", "without_webhook_headers"]
