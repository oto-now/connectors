"""Instantly API V2 client (https://developer.instantly.ai) — cold email outreach: the
workspace, campaigns and their analytics, leads, lead lists and labels, the Unibox (emails
sent and received, a reply), sending accounts, the block list and webhooks.

The client of the `instantly` description (`connectors/instantly/connector.yaml`): one method
per function, named like it, with its arguments; answers are Instantly's JSON, two removals
aside (below). API V1 is deprecated and never called.

## Account

One workspace API key, sent as `Authorization: Bearer <key>`, on
`https://api.instantly.ai/api/v2`. A key carries SCOPES chosen when it is created
(`resource:read|create|update|delete|all`, and the wildcards `all:read`, `all:all`): a read-only
key answers 403 to every action. The key is never put in an address.

## Protocol facts that shape a caller

- **Cursor paging**: `limit` (1 to 100) and `starting_after`, the previous answer's
  `next_starting_after`; a page without it is the last.
- **Query booleans** are sent `true` / `false`; a list argument repeats its key (`ids=a&ids=b`).
- **No body, no Content-Type**: activating or pausing a campaign and marking a thread read are
  POSTs without a body, sent without a JSON content type (an empty JSON body is refused).
- **Never retried, never redirected**: a write (a reply, leads added to a campaign) may have
  gone through on a 5xx or a timeout. `GET /emails` is limited to 20 requests per minute.
- **Removed from every answer**: a webhook's `headers` (delivered to its destination, they may
  carry its secret) and an email attachment's `url` (a download address that opens without a
  login). Nothing else is changed.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ..common.url_guard import refuse_redirect
from . import checks
from ._api import _CampaignsMixin, _LeadsMixin, _UniboxMixin, _WebhooksMixin
from ._api.campaigns import CAMPAIGN_STATUSES, SORT_ORDERS
from ._api.leads import LABEL_INTERESTS, LEAD_FIELDS, LEAD_FILTERS
from ._api.unibox import ACCOUNT_STATUSES, EMAIL_TYPES, MODES, PROVIDER_CODES
from ._api.webhooks import EVENT_TYPES

BASE_URL = "https://api.instantly.ai/api/v2"
HTTP_TIMEOUT = (10, 60)  # (connect, read): analytics with lead totals answer slowly
SERVICE = "instantly"


class InstantlyClient(_CampaignsMixin, _LeadsMixin, _UniboxMixin, _WebhooksMixin):
    """Instantly API V2 on one workspace, with its API key."""

    BASE_URL = BASE_URL

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: an Instantly API key (Settings → Integrations → API keys), with the
                scopes of the calls to make (`all:read` reads everything, `all:all` acts too).
        """
        self.api_key = require(api_key, "INSTANTLY_API_KEY")
        self.session = requests.Session()
        # The key in the HEADER only: never in an address, which ends up in exception
        # messages, logs and error tracking.
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}",
                                     "Accept": "application/json"})

    # --- transport ----------------------------------------------------------------------------

    def _call(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
              json: Optional[Dict[str, Any]] = None) -> Any:
        """One request, never retried nor redirected; a refusal raises `UpstreamHTTPError`."""
        resp = self.session.request(method, f"{self.BASE_URL}{path}", params=params or None,
                                    json=json, timeout=HTTP_TIMEOUT, allow_redirects=False)
        refuse_redirect(resp, service=SERVICE)
        raise_for_upstream(resp, service=SERVICE)
        return resp.json() if resp.content else {}

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._call("GET", path, params=checks.query(params or {}))

    def _send(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        """A POST, PATCH or DELETE; `body=None` sends no body at all."""
        return self._call(method, path, json=body)

    # --- workspace ----------------------------------------------------------------------------

    def get_workspace(self) -> Any:
        """GET /workspaces/current — the workspace the key reaches (the connection probe)."""
        return self._get("/workspaces/current")


__all__ = [
    "InstantlyClient", "BASE_URL", "HTTP_TIMEOUT", "CAMPAIGN_STATUSES", "SORT_ORDERS",
    "LEAD_FILTERS", "LEAD_FIELDS", "LABEL_INTERESTS", "EMAIL_TYPES", "MODES", "ACCOUNT_STATUSES",
    "PROVIDER_CODES", "EVENT_TYPES",
]
