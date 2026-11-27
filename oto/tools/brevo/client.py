"""Client for the Brevo v3 PUBLIC API (formerly Sendinblue).

Auth = **v3 API key** in the `api-key` header, created in Brevo:
Settings → SMTP & API → API Keys. A key covers the whole account (no scope).

Covers the core of the platform: contacts/lists/segments, transactional email
and templates, email campaigns, native CRM (deals/companies/tasks/notes).

⚠️ **Not to be confused with the `brevoauto` connector** (`workflow-apis.brevo.com`),
which drives *automations* through the vendor's private API and a browser session.
The two surfaces are disjoint: the v3 API key gives no access to automation
authoring, and the browser session is not used here.

**Deliberately absent writes** (an unlucky LLM call would be costly):
sending a campaign (`sendNow`, switching the status to `sent`), deleting a
campaign/template, purging hard bounces. Design and measurement are exposed;
launching a mass send stays in the UI. Deleting a list or a contact is here
(`delete_list`, `delete_contact`): a caller confirms it first.

`get_account` never returns the login of the SMTP relay nor the marketing
automation key: both are removed from the answer (`ACCOUNT_STRIPPED`).

Docs: https://developers.brevo.com/reference

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ._base import _BrevoBase
from .campaigns import CampaignsMixin
from .contacts import ContactsMixin
from .crm import CrmMixin
from .email import TransactionalEmailMixin


#: What `get_account` removes from Brevo's answer, each by its path of keys: a login or a key never leaves the client.
ACCOUNT_STRIPPED = (("relay", "data", "userName"), ("marketingAutomation", "key"))


def _strip_account(account: Any) -> Any:
    """Remove the paths of `ACCOUNT_STRIPPED` from the account; an absent parent has nothing to remove."""
    if not isinstance(account, dict):
        return account
    for path in ACCOUNT_STRIPPED:
        node: Any = account
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(path[-1], None)
    return account


class BrevoClient(ContactsMixin, TransactionalEmailMixin, CampaignsMixin,
                  CrmMixin, _BrevoBase):
    """Brevo v3 client — contacts, transactional, campaigns, CRM."""

    def get_account(self) -> Dict[str, Any]:
        """Brevo account: company, plan(s), remaining email/SMS credits.

        Without the SMTP relay login nor the marketing automation key (`ACCOUNT_STRIPPED`).
        """
        return _strip_account(self._request("GET", "/account"))

    def list_senders(self, ip: Optional[str] = None,
                     domain: Optional[str] = None) -> Dict[str, Any]:
        """The account's verified senders — their `email`/`id` is required to send."""
        params = self._clean({"ip": ip, "domain": domain})
        return self._request("GET", "/senders", params=params or None)
