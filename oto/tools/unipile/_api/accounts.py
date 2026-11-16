"""Unipile accounts & hosted auth link.

Extracted from `client.py` (split by domain, frozen public surface):
the bodies are unchanged. This mixin is never instantiated on its own — it is
composed into `UnipileClient`, which provides the transport (`_request`,
`_acct`, `_norm`, `_by_shape`, `session`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote

import requests

from ..const import _REQUEST_TIMEOUT
from ..errors import UnipileError

logger = logging.getLogger(__name__)

# Guard for `list_accounts`: beyond it, we stop and SAY SO (log). At Unipile's
# default page size (20), that's 10,000 accounts — three orders of
# magnitude above today's platform key. It's only there so that
# an upstream answering `has_more: true` forever doesn't freeze the caller.
_ACCOUNTS_MAX_PAGES = 500


class _AccountsMixin:
    """Unipile accounts & hosted auth link."""

    def list_accounts(self) -> list[dict]:
        """ALL the accounts of the key, across all pages.

        ⚠️ `GET /v2/accounts` is PAGINATED by `offset` (`limit` default 20, `has_more`)
        and SORTED BY `name` (OpenAPI v2 "List all Accounts"). This client only read
        the first page: past 20 accounts on a key, any account whose
        name sorts after the 20th became INVISIBLE — with no error, since the returned
        page is perfectly valid. Seen on 2026-09-14: the platform key
        carried more than 20 accounts, and oto-backend's poll-and-bind reconciliation
        (which looks for the freshly connected account IN this list) could no
        longer bind anyone whose name falls after the 20th; the admin seat
        inventory lied by omission (live, in-use seats were missing from it).
        Names early in the alphabet got through: not reproducible
        for someone called Alessandro.

        We advance by the size of the RETURNED page (`limit` is not sent: we
        keep the upstream's default size rather than guess a maximum that
        the docs don't give) until `has_more` is false or a page is empty. Deduplicated
        by `id`: an account created during the walk shifts the alphabetical order
        and can serve an already-seen account again.

        ⚠️ MECHANICAL STOP, same reason as `list_invitations`: if upstream ignored
        `offset` and served the same page again, the loop would never end. Page
        identical to the previous one ⟹ we stop with what we have, and we
        log it — returning the first page stays the previous behavior, not a
        regression. A response without `has_more` (or a bare list) is read as
        a single page."""
        out: list[dict] = []
        seen_ids: set[str] = set()
        offset = 0
        previous: Optional[list] = None
        for _ in range(_ACCOUNTS_MAX_PAGES):
            data = self._request("GET", "/accounts",
                                 params={"offset": offset} if offset else None)
            if not isinstance(data, dict):
                return data or []
            page = data.get("data") or data.get("items") or []
            if not page:
                if offset:
                    # Upstream announced more (`has_more`) and returns an empty page:
                    # either the list shrank between two calls, or `offset` doesn't
                    # mean what we think here. The second case would silently
                    # re-truncate — so we say it.
                    logger.warning(
                        "unipile list_accounts: `has_more` announced more, the page "
                        "at offset=%s is empty — %d account(s) read.", offset, len(out))
                break
            ids = [a.get("id") if isinstance(a, dict) else a for a in page]
            if ids == previous:
                logger.warning(
                    "unipile list_accounts: upstream served the previous page again "
                    "identically (offset=%s) — pagination stopped at %d account(s).",
                    offset, len(out))
                break
            previous = ids
            for acc in page:
                aid = acc.get("id") if isinstance(acc, dict) else None
                if aid:
                    if aid in seen_ids:
                        continue
                    seen_ids.add(aid)
                out.append(acc)
            if not data.get("has_more"):
                break
            offset += len(page)
        else:
            logger.warning(
                "unipile list_accounts: cap of %d pages reached — list truncated "
                "at %d account(s).", _ACCOUNTS_MAX_PAGES, len(out))
        return out

    def delete_account(self, account_id: str) -> None:
        """Remove an account from the Unipile instance — this is what RELEASES the
        billed seat (a disconnect on the oto side only unwinds the binding, the seat
        keeps running).

        ⚠️ IRREVERSIBLE: the hosted session is destroyed. A reconnection of the
        same person will start from a NEW `account_id` — so the ownership history
        on the caller's side (dead bindings) will no longer rebind this account.
        204 expected; an unknown id bubbles up as a 404 `UnipileError`."""
        self._request("DELETE", f"/accounts/{quote(account_id, safe='')}")

    def account_id(self) -> str:
        """LinkedIn `account_id`: the one provided, otherwise the 1st LinkedIn account of the
        Unipile account.

        ⚠️ The provider casing CHANGED in v2: an account carries `provider:"linkedin"`
        (lowercase) and **no more `type` field** (v2 fields seen live:
        application_id, created_at, id, is_locked, metadata, name, object, provider,
        proxy, status, user_id). The old `== "LINKEDIN"` test could therefore never
        be true again → automatic discovery always fell into "no
        LinkedIn account connected" even though an operational account existed: a
        diagnostic that LIES costs hours. Case-insensitive comparison, on
        `provider` (v2) with `type` fallback (v1), and the failure message LISTS the
        providers actually seen."""
        if self._account_id:
            return self._account_id
        seen: list[str] = []
        for acc in self.list_accounts():
            if not isinstance(acc, dict):
                continue
            provider = str(acc.get("provider") or acc.get("type") or "").strip()
            if provider:
                seen.append(provider)
            if provider.lower() == "linkedin" and acc.get("id"):
                self._account_id = str(acc["id"])
                return self._account_id
        inventory = (f" Connected accounts: {', '.join(sorted(set(seen)))}."
                     if seen else " No account connected on this Unipile key.")
        raise UnipileError(
            "No LinkedIn account connected on Unipile "
            "(and UNIPILE_LINKEDIN_ACCOUNT_ID not set)." + inventory
        )

    def account_alive(self, account_id: str) -> bool:
        """Is the account's SESSION alive? `GET /v2/{account_id}/users/me`:
        200 = usable, 401 = disconnected (checkpoint / aborted login / dead cookie).
        Distinct from the account's `status:'running'`, which can lie on a
        stillborn account (abandoned wizard). Used to bind only an account that is ACTUALLY
        usable (a stillborn account preferred over the old healthy one = incident we've seen)."""
        try:
            resp = self.session.request(
                "GET", f"{self.base_url}/{quote(account_id, safe='')}/users/me",
                timeout=_REQUEST_TIMEOUT)
        except requests.RequestException:
            return False
        return resp.status_code == 200

    # ---- hosted auth -----------------------------------------------------

    # LinkedIn products that can be enabled on the hosted-auth link (`config.linkedin.products`).
    # `classic` = the base, always included. The two PREMIUM ones are EXCLUSIVE: an
    # account can only enable ONE (documented Unipile constraint).
    LINKEDIN_PREMIUM_PRODUCTS = ("recruiter", "sales_navigator")

    def hosted_auth_link(
        self,
        notify_url: Optional[str] = None,
        providers: Optional[list[str]] = None,
        name: Optional[str] = None,
        success_redirect_url: Optional[str] = None,
        failure_redirect_url: Optional[str] = None,
        ttl_minutes: int = 60,
        premium: Optional[str] = None,
        allow_cookies: bool = False,
        reconnect_account: Optional[str] = None,
    ) -> str:
        """Hosted auth URL (v2: `POST /v2/auth/link`, createAuthLink).

        v2 schema (createAuthLink, API 2.67): an `anyOf` of TWO bodies, both
        `additionalProperties: false` — no `type` discriminator, the variant is
        picked by which key is present:
        - create: `expires_on`, `redirect_uri`, `providers` (+ `state`, `config`);
        - reconnect: `expires_on`, `redirect_uri`, `account_id` (+ `state`,
          `config`) — and NO `providers`.
        v1 leftovers (`type`, `api_url`, `name`, `notify_url`, `reconnect_account`)
        are now REJECTED with a 400 ("must NOT have additional properties … must have
        required property 'account_id'") — they used to be tolerated. `name` and
        `notify_url` stay in the signature but are no longer sent.
        `providers` = list of **lowercase** codes (`["linkedin"]`) or `"*"` (all);
        **a single** `redirect_uri`; the response carries the link on **`link`**.

        ⚠️ **It's up to the app to enable premium products**: without
        `config.linkedin.products`, Unipile only connects `classic` → the
        Recruiter/Sales Navigator endpoints answer 403 "out of your scope" and
        the wizard offers NO premium checkbox (confirmed by Unipile support).
        - `premium`: `"recruiter"` | `"sales_navigator"` | None. **Exclusive** — an
          account can only enable one of the two.
        - `allow_cookies`: adds cookie-based connection to the wizard's methods
          (without it, only login/password is offered). **Recommended by
          Unipile for premium products.**
        - `reconnect_account`: `account_id` of an EXISTING account → reconnect body
          (`account_id`, attaches the product/repairs the session ON that account) instead of `create`
          (which would make a DUPLICATE). To be used to enable a premium on an
          already connected account."""
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        body: dict[str, Any] = {
            "expires_on": expires.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }
        # The variant is chosen by the key: `account_id` (reconnect) XOR `providers`
        # (create) — sending both, or a `type`, fails the anyOf.
        if reconnect_account:
            body["account_id"] = reconnect_account
        else:
            body["providers"] = [str(p).lower() for p in providers] if providers else "*"
        # v2 = a single redirect_uri (failure no longer has a dedicated URL); we take the
        # success one, otherwise the failure one as a fallback.
        redirect = success_redirect_url or failure_redirect_url
        if redirect:
            body["redirect_uri"] = redirect
        # `name` / `notify_url`: v1 fields, rejected by the v2 schema — kept in the
        # signature only, never sent. Nothing reads a correlation value back from
        # Unipile (the pending row is matched by `reconcile_pending`, not by the link).
        # config.linkedin: products to enable + offered connection methods.
        # Only set if we ask for something non-default (otherwise Unipile
        # keeps its original behavior: classic + credentials).
        if premium or allow_cookies:
            if premium and premium not in self.LINKEDIN_PREMIUM_PRODUCTS:
                raise UnipileError(
                    f"invalid premium: {premium!r} (expected "
                    f"{' or '.join(map(repr, self.LINKEDIN_PREMIUM_PRODUCTS))}). "
                    "An account can only enable ONE premium product."
                )
            cfg: dict[str, Any] = {}
            if premium:
                cfg["products"] = ["classic", premium]
            if allow_cookies:
                cfg["allow_methods"] = ["credentials", "cookies"]
            body["config"] = {"linkedin": cfg}
        data = self._request("POST", "/auth/link", json=body)
        return (data or {}).get("link") or (data or {}).get("url", "")
