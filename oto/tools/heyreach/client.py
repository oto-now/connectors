"""HeyReach public API client (https://api.heyreach.io/api/public) — LinkedIn outreach
automation: campaigns and the leads they work on, lead lists, leads and their tags,
the LinkedIn inbox of the sender accounts, statistics and webhooks.

The client of the `heyreach` description (`connectors/heyreach/connector.yaml`): one
method per function, named like it, with its arguments; answers are returned as parsed
JSON, without the addresses an agent must not see (`STRIP`).

## Account

One API key per HeyReach workspace, sent in the `X-API-KEY` header. It acts on every
sender account, campaign, list and conversation of the workspace. Keys never expire but
can be deleted or deactivated (401).

## Protocol facts that shape a caller

- **Most lists are POST with `{offset, limit}`** (up to 100 per page, 1000 for the
  leads of a list): next page = offset + limit while offset < totalCount. The inbox
  (`list_conversations`, GetConversationsV3) pages by cursor: pass `nextCursor` back
  as `cursor` while `hasNextPage`.
- **Leads are sent with HeyReach's own keys** (`profileUrl`, `firstName`,
  `customUserFields`…), as given, at most 100 per call; a custom field name is
  letters, digits and `_`.
- **Several gestures answer 200 with an empty body** (pause, resume, stop a lead, send
  a message, set the seen status, the key check): `{}` is returned.
- **Never redirected, never retried**: the key travels in a custom header, which
  `requests` would forward to another host, and a send must not be repeated.
- **Rate limit: 300 requests per minute for the whole workspace** (429 above).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ..http.client import refuse_redirect
from ._api.campaigns import CampaignsMixin
from ._api.checks import compact
from ._api.inbox import InboxMixin
from ._api.lists import ListsMixin

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait
BASE_URL = "https://api.heyreach.io/api/public"

#: What each answer loses before it reaches the caller, by dotted path (relative to each
#: element of `items` for a list): a member's picture (a LinkedIn media address that opens
#: without a login) and a webhook's custom headers (the receiver's own secrets). The
#: description strips the same paths (`output.strip`).
STRIP: Dict[str, tuple] = {
    "list_campaign_leads": ("items", ("linkedInUserProfile.imageUrl",)),
    "list_list_leads": ("items", ("imageUrl",)),
    "get_lead": (None, ("imageUrl",)),
    "list_conversations": ("items", ("correspondentProfile.imageUrl",)),
    "get_conversation": (None, ("correspondentProfile.imageUrl",)),
    "list_webhooks": ("items", ("customHeaders",)),
    "get_webhook": (None, ("customHeaders",)),
}


class HeyReachUnreadable(RuntimeError):
    """HeyReach answered a success whose body is not JSON: nothing can be read from it."""


def _drop(node: Any, path: list) -> None:
    """Remove the key at `path` in `node`, through lists; absent or scalar: nothing to do."""
    if isinstance(node, list):
        for item in node:
            _drop(item, path)
        return
    if not isinstance(node, dict):
        return
    if len(path) == 1:
        node.pop(path[0], None)
        return
    _drop(node.get(path[0]), path[1:])


def without_hidden(function: str, answer: Any) -> Any:
    """`answer` of `function` without the paths of `STRIP` (in place, and returned)."""
    items, paths = STRIP.get(function, (None, ()))
    root = answer.get(items) if items and isinstance(answer, dict) else answer
    for path in paths:
        _drop(root, path.split("."))
    return answer


class HeyReachClient(CampaignsMixin, ListsMixin, InboxMixin):
    """HeyReach public API on one workspace, authenticated by its API key."""

    BASE_URL = BASE_URL

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: the workspace's HeyReach API key, sent as `X-API-KEY`.
        """
        self.api_key = require(api_key, "HEYREACH_API_KEY").strip()
        self.session = requests.Session()
        # The key in the HEADER only — never in the URL, which ends up in exception
        # messages, logs and error tracking.
        self.session.headers.update({"X-API-KEY": self.api_key, "Accept": "application/json"})

    # --- transport ----------------------------------------------------------

    def _call(self, method: str, path: str, *, query: Optional[Dict[str, Any]] = None,
              body: Optional[Dict[str, Any]] = None, strip: Optional[str] = None) -> Any:
        """One request under the API root, absent values dropped (a nested table absent
        when empty). A refusal raises `UpstreamHTTPError`, a redirect `RedirectRefused`,
        a success that is not JSON `HeyReachUnreadable`."""
        params = compact(query or {})
        resp = self.session.request(method, f"{self.BASE_URL}{path}", params=params or None,
                                    json=compact(body) if body is not None else None,
                                    timeout=_HTTP_TIMEOUT, allow_redirects=False)
        refuse_redirect(resp)
        raise_for_upstream(resp, service="heyreach")
        if not resp.content:
            return {}
        try:
            answer = resp.json()
        except ValueError:
            raise HeyReachUnreadable(
                f"HeyReach answered HTTP {resp.status_code} on {method} {path} with a body that "
                f"is not JSON: {(resp.text or '').strip()[:200]!r}") from None
        return without_hidden(strip, answer) if strip else answer

    # --- key ----------------------------------------------------------------

    def check_api_key(self) -> Any:
        """GET /auth/CheckApiKey — `{}` when the key works, a 401 when it does not."""
        return self._call("GET", "/auth/CheckApiKey")


__all__ = ["HeyReachClient", "HeyReachUnreadable", "BASE_URL", "STRIP", "without_hidden"]
