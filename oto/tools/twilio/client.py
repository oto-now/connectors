"""Twilio REST API client (https://www.twilio.com/docs/usage/api) — numbers,
calls, recordings, messages.

HTTP Basic auth, the Account SID (`AC…`) as user name and the account's Auth
Token as password. Every resource lives under
`https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/…`; the SID given at
construction is the account acted on (a subaccount's SID with the parent's
credentials works on these v2010 resources too).

## Protocol facts that shape a caller

- **Writes are form-encoded** (`application/x-www-form-urlencoded`), with
  PascalCase names; a list parameter (`StatusCallbackEvent`, `MediaUrl`) is the
  same name repeated. Reads answer JSON.
- **Pagination**: `PageSize` (1-1000, default 50); each page carries
  `next_page_uri`, a path to GET as is, null on the last page. `next_page`
  follows it, refusing any path outside this account.
- **Phone numbers are E.164** (`+33612345678`).
- **Recording media needs the credentials**: `recording_media` returns the
  audio bytes; the media URL is never handed out.
- **A number is billed for the whole month at purchase**, without proration
  on release.
- Errors carry `code` (a Twilio error number), `message` and `more_info`;
  they surface as `UpstreamHTTPError`.
"""
from __future__ import annotations

import base64
import re
from typing import Any, Optional

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ._api._common import Form, form, page_size  # noqa: F401 — re-exported
from ._api.calls import CallsMixin
from ._api.messages import MessagesMixin
from ._api.numbers import NumbersMixin

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait
BASE_URL = "https://api.twilio.com"
API_ROOT = "/2010-04-01"

_ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
#: Keys of an Account resource that are credentials, removed from every return.
SECRET_FIELDS = ("auth_token",)

def basic_signature(account_sid: str, auth_token: str) -> str:
    """`base64(account_sid:auth_token)` — the value that follows « Basic »."""
    return base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")


class TwilioClient(NumbersMixin, CallsMixin, MessagesMixin):
    """Twilio REST API (v2010). Basic auth `account_sid:auth_token`."""

    BASE_URL = BASE_URL

    def __init__(self, account_sid: Optional[str] = None, auth_token: Optional[str] = None):
        """
        Args:
            account_sid: the Account SID (`AC` + 32 hex), Basic auth user name
                and the account acted on.
            auth_token: the account's Auth Token (Basic auth password). It also
                signs the webhooks Twilio sends for this account.
        """
        self.account_sid = require(account_sid, "TWILIO_ACCOUNT_SID").strip()
        self.auth_token = require(auth_token, "TWILIO_AUTH_TOKEN")
        if not _ACCOUNT_SID.match(self.account_sid):
            raise ValueError("`account_sid` must be `AC` followed by 32 hexadecimal characters.")
        self.session = requests.Session()
        # Credentials in the HEADER only — never in the URL, which ends up in
        # exception messages, logs and error tracking.
        self.session.headers.update({
            "Authorization": f"Basic {basic_signature(self.account_sid, self.auth_token)}",
            "Accept": "application/json",
        })

    # --- transport ----------------------------------------------------------

    @property
    def account_path(self) -> str:
        return f"{API_ROOT}/Accounts/{self.account_sid}"

    def _send(self, method: str, path: str, *, params: Optional[dict] = None,
              data: Optional[Form] = None, raw: bool = False) -> Any:
        resp = self.session.request(method, self.BASE_URL + path, params=params, data=data,
                                    timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="twilio")
        if raw:
            return resp.content
        if resp.status_code == 204 or not resp.content:
            return {"deleted": True} if method == "DELETE" else {}
        return resp.json()

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        return self._send("GET", self.account_path + path, params=clean or None)

    def _post(self, path: str, data: Form) -> Any:
        return self._send("POST", self.account_path + path, data=list(data))

    def _delete(self, path: str) -> Any:
        return self._send("DELETE", self.account_path + path)

    # --- account ------------------------------------------------------------

    def get_account(self) -> dict:
        """The account: `friendly_name`, `status` (active, suspended, closed),
        `type` (Trial or Full). Free; the connection probe. The resource carries the
        account's `auth_token`: it is removed here, never returned."""
        account = self._send("GET", self.account_path + ".json")
        if isinstance(account, dict):
            for key in SECRET_FIELDS:
                account.pop(key, None)
        return account

    def get_balance(self) -> dict:
        """The account balance: `balance` and `currency`. Subaccounts share
        their parent's balance."""
        return self._get("/Balance.json")

    def next_page(self, next_page_uri: str) -> dict:
        """The next page of a list, from its `next_page_uri`. Refused when the
        path leaves this account (the URI is data, never a free address)."""
        uri = (next_page_uri or "").strip()
        if not uri.startswith(self.account_path + "/") or "://" in uri or ".." in uri:
            raise ValueError("`next_page_uri` must be a path of this account returned by a list.")
        return self._send("GET", uri)
