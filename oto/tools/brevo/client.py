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
launching a mass send and those deletions stay in the UI. Deleting a contact
or a list is exposed (`delete_contact`, `delete_list`), so that a test or a
user can clean up behind itself; the description asks for a confirmation.

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


class BrevoClient(ContactsMixin, TransactionalEmailMixin, CampaignsMixin,
                  CrmMixin, _BrevoBase):
    """Brevo v3 client — contacts, transactional, campaigns, CRM."""

    def get_account(self) -> Dict[str, Any]:
        """Brevo account: company, plan(s), remaining email/SMS credits."""
        return self._request("GET", "/account")

    def list_senders(self, ip: Optional[str] = None,
                     domain: Optional[str] = None) -> Dict[str, Any]:
        """The account's verified senders — their `email`/`id` is required to send."""
        params = self._clean({"ip": ip, "domain": domain})
        return self._request("GET", "/senders", params=params or None)
