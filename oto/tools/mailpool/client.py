"""Mailpool API client — cold-email infrastructure: domains, DNS, mailboxes,
spam checks, warmup.

API v1 (`https://app.mailpool.io/v1/api`, reference https://api.mailpool.ai),
authenticated by the **`X-Api-Authorization: <key>`** header. A key belongs to
a workspace and is created in Mailpool → Settings → API Keys. One method = one
endpoint.

Scope: reading domains, their DNS records and owners, mailboxes, warmup and
subscription slots; editing a domain's DNS and DMARC settings; running spam
checks; creating mailboxes on subscription slots already paid for
(`_mailboxes.py`: refused, before anything is sent, when no slot of the type
is free). Everything else that is BILLED is deliberately absent: registering,
transferring or renewing a domain, buying subscription slots, adding an inbox
to warmup, inbox-placement tests. Exports to a sending platform are served
(`_exports.py`: Mailpool sends the mailbox credentials itself); saving the
platform's own login is not (a secret, typed in the dashboard).

What the caller needs to know, and cannot guess:

- ⚠️ **This client never returns a mailbox credential.** Mailbox payloads —
  including the one nested in a spam check — carry the mailbox password, the
  IMAP/SMTP passwords, the admin account's password and secret. A mailbox is
  served as an ALLOWLIST (`MAILBOX_FIELDS`, the closed schema of
  `connectors/mailpool/connector.yaml`; the admin account reduced to its
  address); every other payload loses, at any depth, each key named like a
  credential (`strip_secrets`). Error bodies go through the same filter
  before they reach `UpstreamHTTPError`.

- **Every list requires `limit` and `offset`** (a list called without them is
  refused with 400 "numeric string is expected"). They are always sent.

- ⚠️ **`update_domain_dns` REPLACES the whole record set** of the domain: a
  record left out of the list is deleted. The method therefore carries the
  read-modify-write itself: the caller passes the set it read (`expected`);
  the set is read again and the write refused if it changed meanwhile; a host
  that would lose its MX, SPF or DKIM is refused unless
  `allow_removing_protected=True`; after the write the set is read back, and
  if a record sent is missing the previous set is put back and
  `MailpoolDnsWriteError` raised. Changes reach the public nameservers within
  minutes.

- **The `_dmarc` TXT record is published from the domain's DMARC settings**, not
  from the record set: an edited `_dmarc` record is stored by
  `update_domain_dns` but only goes live after `set_dmarc_email` or
  `set_dmarc_policy`. `set_dmarc_email` writes the address as both `rua` and
  `ruf`.

- `get_domain_dns` returns the configuration stored by Mailpool, not a live DNS
  lookup.

- **Some features are enabled per workspace**: the Google Workspace / Microsoft
  365 availability checks answer 403 "not enabled for this workspace" otherwise.

- A spam check is free and completes within seconds (`state` goes from
  `pending` to `completed`); it is refused with 404 for mailbox types it does
  not support.

Requires: requests
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from ..common.credentials import require
from ..common.errors import UpstreamHTTPError
from ._exports import MailboxExportMixin
from ._mailboxes import MailboxCreateMixin

# (connect, read) per attempt — never an unbounded wait.
HTTP_TIMEOUT = (10, 30)
#: Whole budget of one method call, retries and waits included: a consumer
#: with a ~45 s budget per call gets an answer (a domain lookup can hang).
CALL_BUDGET_S = 40.0

MIN_LIMIT, MAX_LIMIT = 1, 100
DEFAULT_LIMIT = 50

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3

DMARC_POLICIES = ("none", "quarantine", "reject")
DNS_RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "SRV")

#: A key is a credential when one of its WORDS (camelCase, snake_case,
#: kebab-case split) is one of these: `smtpPassword`, `auth-code`,
#: `dkimPrivateKey`, `recoveryCodes`, `totpSeed`… — while `passed`,
#: `bypassWarmup` or `tokenCount` are kept.
SECRET_WORDS = frozenset({
    "pass", "passwd", "password", "passwords", "pwd", "passphrase",
    "secret", "secrets", "credential", "credentials", "token", "tokens",
    "otp", "totp", "hotp", "cookie", "cookies",
})
#: Word pairs that make a credential together (`authCode`, `apiKey`,
#: `privateKey`, `accessKey`, `recoveryCodes`, `totpSeed`…).
SECRET_PAIRS = (("auth", "code"), ("api", "key"), ("private", "key"),
                ("access", "key"), ("secret", "key"), ("recovery", "code"),
                ("recovery", "codes"), ("backup", "code"), ("backup", "codes"),
                ("totp", "seed"), ("otp", "seed"), ("session", "id"))
#: A key ending in one of these counts credentials, it does not hold one
#: (`tokenCount`).
_COUNT_WORDS = frozenset({"count", "counts", "total"})

#: Fields of a mailbox served by this client — the closed `mailbox` schema of
#: connectors/mailpool/connector.yaml. The admin account is reduced to its
#: address (`admin: {email}`); everything else is dropped.
MAILBOX_FIELDS = ("id", "email", "firstName", "lastName", "signature", "forwardTo",
                  "status", "avatar", "type", "isAdmin", "domain", "imapHost",
                  "imapPort", "imapTLS", "imapUsername", "smtpHost", "smtpPort",
                  "smtpTLS", "smtpUsername", "scheduledDeletionAt", "error")

_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _words(key: str) -> List[str]:
    return [w.lower() for w in _WORD_RE.findall(str(key))]


def is_secret_key(key: Any) -> bool:
    """Whether a payload key names a credential (see `SECRET_WORDS`)."""
    words = _words(key)
    if words and words[-1] in _COUNT_WORDS:
        return False
    if any(w in SECRET_WORDS for w in words):
        return True
    return any(pair == tuple(words[i:i + 2])
               for i in range(len(words) - 1) for pair in SECRET_PAIRS)


def strip_secrets(payload: Any) -> Any:
    """`payload` without any key naming a credential (`is_secret_key`), at any
    depth."""
    if isinstance(payload, dict):
        return {k: strip_secrets(v) for k, v in payload.items()
                if not is_secret_key(k)}
    if isinstance(payload, list):
        return [strip_secrets(v) for v in payload]
    return payload


def project_mailbox(mailbox: Any) -> Any:
    """A mailbox as the allowlist `MAILBOX_FIELDS`, credentials-free whatever
    Mailpool adds tomorrow; the admin account reduced to its address."""
    if not isinstance(mailbox, dict):
        return mailbox
    out = {k: strip_secrets(mailbox[k]) for k in MAILBOX_FIELDS if k in mailbox}
    admin = mailbox.get("admin")
    if isinstance(admin, dict) and admin.get("email"):
        out["admin"] = {"email": admin["email"]}
    return out


class MailpoolDnsWriteError(RuntimeError):
    """A DNS write whose read-back did not show every record sent. `restored`
    says whether the previous set was put back (and read back intact);
    `missing` lists the records absent after the write, `previous` the set
    before it. The domain may be in an intermediate state when `restored` is
    false: read it with `get_domain_dns` before anything else."""

    def __init__(self, domain_id: int, missing: List[Dict[str, Any]],
                 previous: List[Dict[str, Any]], restored: bool,
                 restore_error: Optional[BaseException] = None):
        self.domain_id = domain_id
        self.missing = missing
        self.previous = previous
        self.restored = restored
        self.restore_error = restore_error
        state = ("the previous records were put back" if restored else
                 "putting the previous records back FAILED — the domain is in an "
                 "unknown state, read it before anything else")
        super().__init__(
            f"Mailpool did not keep the full record set of domain {domain_id} "
            f"(missing: {[_label(r) for r in missing]}); {state}.")


class MailpoolClient(MailboxCreateMixin, MailboxExportMixin):
    """Mailpool v1 client (https://app.mailpool.io/v1/api), `X-Api-Authorization` auth."""

    BASE_URL = "https://app.mailpool.io/v1/api"

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Mailpool workspace API key, passed by the consumer
                (Mailpool → Settings → API Keys).
        """
        self.api_key = require(api_key, "MAILPOOL_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "X-Api-Authorization": self.api_key,
            "Accept": "application/json",
        })

    # --- validation ---------------------------------------------------------

    @staticmethod
    def _page(limit: Optional[int], offset: Optional[int]) -> Dict[str, int]:
        limit = DEFAULT_LIMIT if limit is None else limit
        offset = 0 if offset is None else offset
        for name, value in (("limit", limit), ("offset", offset)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"`{name}` must be an integer.")
        if not (MIN_LIMIT <= limit <= MAX_LIMIT):
            raise ValueError(
                f"`limit` must be between {MIN_LIMIT} and {MAX_LIMIT}; got "
                f"{limit}. Beyond that, paginate with `offset`.")
        if offset < 0:
            raise ValueError("`offset` must be >= 0.")
        return {"limit": limit, "offset": offset}

    @staticmethod
    def _id(name: str, value: Any) -> int:
        """A numeric id placed IN the path: an int, never a string that could
        reach another endpoint."""
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"`{name}` must be a positive integer.")
        return value

    @staticmethod
    def _domain_name(value: Any, *, require_tld: bool = True) -> str:
        """A domain name (`example.com`), or with `require_tld=False` a bare
        name to start suggestions from (`example`)."""
        name = value.strip().lower() if isinstance(value, str) else ""
        if not _DOMAIN_RE.match(name) or (require_tld and "." not in name):
            raise ValueError(
                "`domain` must be a domain name such as example.com"
                + ("." if require_tld else ", or a name such as example."))
        return name

    @staticmethod
    def _check_records(records: Iterable[Dict[str, Any]], *,
                       name: str = "records") -> List[Dict[str, Any]]:
        if isinstance(records, (str, bytes, dict)):
            raise ValueError(f"`{name}` must be a list of records.")
        records = list(records)
        if not records:
            raise ValueError(
                f"`{name}` is empty: the call replaces the whole record set and "
                "would delete every record of the domain.")
        out = []
        for i, r in enumerate(records):
            if not isinstance(r, dict):
                raise ValueError(f"{name}[{i}] must be an object.")
            if r.get("type") not in DNS_RECORD_TYPES:
                raise ValueError(
                    f"{name}[{i}].type must be one of {', '.join(DNS_RECORD_TYPES)}.")
            if not isinstance(r.get("value"), str) or not r["value"]:
                raise ValueError(f"{name}[{i}].value must be a non-empty string.")
            unknown = set(r) - _RECORD_FIELDS
            if unknown:
                raise ValueError(
                    f"{name}[{i}] has unknown fields: {', '.join(sorted(unknown))}.")
            key = r.get("key")
            if key is not None and (not isinstance(key, str) or not _LABEL_RE.match(key)):
                raise ValueError(
                    f"{name}[{i}].key must be null (the apex) or a host label such as "
                    "`track`, `_dmarc` or `s1._domainkey`.")
            for num in ("priority", "port", "weight"):
                v = r.get(num)
                if v is not None and (isinstance(v, bool) or not isinstance(v, int)
                                      or not 0 <= v <= 65535):
                    raise ValueError(f"{name}[{i}].{num} must be an integer from 0 to 65535.")
            if r["type"] in ("MX", "SRV") and r.get("priority") is None:
                raise ValueError(f"{name}[{i}]: a {r['type']} record needs `priority`.")
            if r["type"] == "SRV" and (r.get("port") is None or r.get("weight") is None):
                raise ValueError(f"{name}[{i}]: an SRV record needs `port` and `weight`.")
            if r["type"] != "SRV" and (r.get("port") is not None or r.get("weight") is not None):
                raise ValueError(f"{name}[{i}]: `port` and `weight` belong to SRV records only.")
            out.append(dict(r))
        return out

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json: Any = None) -> Any:
        """One call, within `CALL_BUDGET_S`. Redirects are never followed (the
        key header would go with them, and a PUT would come back as a GET):
        any 3xx is an error. 429/5xx are retried for READS only — a replayed
        POST could act twice — after `Retry-After` (seconds or HTTP date) or
        1 s then 2 s, as long as the budget allows; otherwise the error is
        raised. Every body, error bodies included, loses its credentials."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        retryable = method.upper() in ("GET", "HEAD")
        deadline = time.monotonic() + CALL_BUDGET_S
        last = None
        for attempt in range(MAX_ATTEMPTS):
            remaining = max(1.0, deadline - time.monotonic())
            last = self.session.request(
                method, f"{self.BASE_URL}{path}", params=params or None,
                json=json, allow_redirects=False,
                timeout=(min(HTTP_TIMEOUT[0], remaining), min(HTTP_TIMEOUT[1], remaining)))
            if (last.status_code not in RETRY_STATUSES
                    or not retryable or attempt == MAX_ATTEMPTS - 1):
                break
            asked = _retry_after(last)
            wait = float(2 ** attempt) if asked is None else asked
            if time.monotonic() + wait >= deadline:
                break
            time.sleep(wait)
        return _parse(last)

    # --- probe --------------------------------------------------------------

    def probe(self) -> Any:
        """Check that the key authenticates, for the cost of one call (401 if
        wrong): the subscription slots, no parameter, no personal data."""
        return self.get_subscription_slots()

    # --- domains ------------------------------------------------------------

    def list_domains(self, limit: Optional[int] = None,
                     offset: Optional[int] = None) -> Any:
        """`GET /domains/` — `{data: [...], total}`."""
        return self._request("GET", "/domains/", params=self._page(limit, offset))

    def get_domain(self, domain_id: int) -> Any:
        """`GET /domains/{id}`."""
        return self._request("GET", f"/domains/{self._id('domain_id', domain_id)}")

    def get_domain_dns(self, domain_id: int) -> List[Dict[str, Any]]:
        """`GET /domains/{id}/dns` — list of `{type, key, value, priority?,
        port?, weight?}`; `key` is the host label (`null` for the apex).
        Anything but a list of records is raised, never read as an empty set
        (an empty set sent back would delete every record)."""
        records = self._request(
            "GET", f"/domains/{self._id('domain_id', domain_id)}/dns")
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise UpstreamHTTPError(
                502, "Mailpool did not return a list of DNS records.", service="mailpool")
        return records

    def update_domain_dns(self, domain_id: int,
                          records: Iterable[Dict[str, Any]], *,
                          expected: Iterable[Dict[str, Any]],
                          allow_removing_protected: bool = False) -> List[Dict[str, Any]]:
        """`PUT /domains/{id}/dns` — ⚠️ REPLACES the whole record set; returns
        the set read back after the write.

        Args:
            domain_id: Mailpool domain id.
            records: the WHOLE new set.
            expected: the set the caller read and changed (`get_domain_dns`).
                It is read again first; the write is refused if it changed.
            allow_removing_protected: allow a host to lose its MX, SPF or DKIM
                (refused otherwise — a domain without them cannot send).

        After the write the set is read back. If a record sent is missing,
        the previous set is put back and `MailpoolDnsWriteError` raised.
        """
        domain_id = self._id("domain_id", domain_id)
        records = self._check_records(records)
        expected = self._check_records(expected, name="expected")
        current = self.get_domain_dns(domain_id)
        if sorted(map(_identity, current)) != sorted(map(_identity, expected)):
            raise ValueError(
                "The DNS records of the domain changed since `expected` was read: "
                "read them again with get_domain_dns and redo the change.")
        if not allow_removing_protected:
            lost = sorted(_protected(current) - _protected(records))
            if lost:
                raise ValueError(
                    "Refused: the new set removes " + ", ".join(
                        f"the {kind} of {host}" for kind, host in lost)
                    + ". A domain needs them to send; pass "
                    "allow_removing_protected=True only if that is the intent.")
        path = f"/domains/{domain_id}/dns"
        self._request("PUT", path, json=records)
        after = self.get_domain_dns(domain_id)
        missing = _missing(records, after)
        if not missing:
            return after
        try:
            self._request("PUT", path, json=current)
            restored = not _missing(current, self.get_domain_dns(domain_id))
            error = None
        except Exception as e:  # noqa: BLE001 — reported in the raised error
            restored, error = False, e
        raise MailpoolDnsWriteError(domain_id, missing, current, restored, error)

    def set_dmarc_email(self, domain_id: int, email: str) -> Any:
        """`POST /domains/{id}/dmarc-email` — report address (`rua` and `ruf`);
        also publishes the stored `_dmarc` record."""
        if not isinstance(email, str) or not _EMAIL_RE.match(email):
            raise ValueError(
                "`email` must be a single email address such as dmarc@example.com.")
        return self._request(
            "POST", f"/domains/{self._id('domain_id', domain_id)}/dmarc-email",
            json={"email": email})

    def set_dmarc_policy(self, domain_id: int, policy: str) -> Any:
        """`POST /domains/{id}/dmarc-policy` — `none`, `quarantine` or `reject`."""
        if policy not in DMARC_POLICIES:
            raise ValueError(
                f"`policy` must be one of {', '.join(DMARC_POLICIES)}.")
        return self._request(
            "POST", f"/domains/{self._id('domain_id', domain_id)}/dmarc-policy",
            json={"policy": policy})

    def set_redirect_url(self, domain_id: int, url: str) -> Any:
        """`POST /domains/{id}/redirect-url` — where a visit to the domain goes."""
        if not isinstance(url, str) or not url.strip():
            raise ValueError("`url` must be a non-empty string.")
        return self._request(
            "POST", f"/domains/{self._id('domain_id', domain_id)}/redirect-url",
            json={"url": url.strip()})

    def get_domain_info(self, domain: str) -> Any:
        """`GET /domains/info` — `{available, price}` of a domain to register."""
        return self._request("GET", "/domains/info",
                             params={"domain": self._domain_name(domain)})

    def get_domain_suggestions(self, domain: str, limit: Optional[int] = None,
                               offset: Optional[int] = None) -> Any:
        """`GET /domains/suggestions` — available variants of a name, with price."""
        return self._request(
            "GET", "/domains/suggestions",
            params={"domain": self._domain_name(domain, require_tld=False),
                    **self._page(limit, offset)})

    def check_google_workspace_availability(self, domain: str) -> Any:
        """`POST /domains/google-workspace-availability` (enabled per workspace)."""
        return self._request("POST", "/domains/google-workspace-availability",
                             json={"domain": self._domain_name(domain)})

    def check_microsoft_365_availability(self, domain: str) -> Any:
        """`POST /domains/microsoft-365-availability` (enabled per workspace)."""
        return self._request("POST", "/domains/microsoft-365-availability",
                             json={"domain": self._domain_name(domain)})

    def list_domain_owners(self) -> Any:
        """`GET /domains/owners` — saved registrant contacts."""
        return self._request("GET", "/domains/owners")

    # --- mailboxes ----------------------------------------------------------

    def list_mailboxes(self, limit: Optional[int] = None,
                       offset: Optional[int] = None,
                       domain_id: Optional[int] = None) -> Any:
        """`GET /mailboxes` — `{data: [...], total}`, each mailbox as
        `MAILBOX_FIELDS`."""
        params: Dict[str, Any] = dict(self._page(limit, offset))
        if domain_id is not None:
            params["domainId"] = self._id("domain_id", domain_id)
        out = self._request("GET", "/mailboxes", params=params)
        if isinstance(out, dict) and isinstance(out.get("data"), list):
            out["data"] = [project_mailbox(m) for m in out["data"]]
        return out

    def get_mailbox(self, mailbox_id: int) -> Any:
        """`GET /mailboxes/{id}` — as `MAILBOX_FIELDS`."""
        return project_mailbox(self._request(
            "GET", f"/mailboxes/{self._id('mailbox_id', mailbox_id)}"))

    # --- spam checks --------------------------------------------------------

    def list_spam_checks(self, limit: Optional[int] = None,
                         offset: Optional[int] = None) -> Any:
        """`GET /spam-checks/` — the embedded mailbox as `MAILBOX_FIELDS`."""
        out = self._request("GET", "/spam-checks/",
                            params=self._page(limit, offset))
        if isinstance(out, dict) and isinstance(out.get("data"), list):
            out["data"] = [_with_mailbox(c) for c in out["data"]]
        return out

    def create_spam_check(self, mailbox_id: int) -> Any:
        """`POST /spam-checks/` — sends a test email from the mailbox; free."""
        return _with_mailbox(self._request(
            "POST", "/spam-checks/",
            json={"mailboxId": self._id("mailbox_id", mailbox_id)}))

    def get_spam_check(self, spam_check_id: int) -> Any:
        """`GET /spam-checks/{id}` — the embedded mailbox as `MAILBOX_FIELDS`."""
        return _with_mailbox(self._request(
            "GET", f"/spam-checks/{self._id('spam_check_id', spam_check_id)}"))

    def delete_spam_check(self, spam_check_id: int) -> Any:
        """`DELETE /spam-checks/{id}`."""
        return self._request(
            "DELETE", f"/spam-checks/{self._id('spam_check_id', spam_check_id)}")

    # --- warmup -------------------------------------------------------------

    def list_warmup_inboxes(self, limit: Optional[int] = None,
                            offset: Optional[int] = None) -> Any:
        """`GET /warmup` — inboxes enrolled in warmup."""
        return self._request("GET", "/warmup", params=self._page(limit, offset))

    def get_warmup_inbox(self, warmup_id: int) -> Any:
        """`GET /warmup/{id}` — settings and daily counters."""
        return self._request("GET", f"/warmup/{self._id('warmup_id', warmup_id)}")

    def list_available_warmup_inboxes(self, limit: Optional[int] = None,
                                      offset: Optional[int] = None) -> Any:
        """`GET /warmup/available-inboxes` — active mailboxes not yet enrolled."""
        return self._request("GET", "/warmup/available-inboxes",
                             params=self._page(limit, offset))

    # --- subscription -------------------------------------------------------

    def get_subscription_slots(self) -> Any:
        """`GET /subscriptions/slots` — mailboxes used vs paid, per type."""
        return self._request("GET", "/subscriptions/slots")


# --- module helpers -----------------------------------------------------------

_RECORD_FIELDS = frozenset({"type", "key", "value", "priority", "port", "weight"})
#: A host label relative to the domain: `track`, `_dmarc`, `s1._domainkey`, `*`.
_LABEL_RE = re.compile(r"^(?:\*|[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)"
                       r"(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*$")
_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                        r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")
#: One address, nothing that could add a tag to the published `_dmarc` record
#: (`;`, `,`, `!`, spaces) — local part, `@`, a domain with a TLD.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def _host(r: Dict[str, Any]) -> str:
    key = r.get("key")
    return "@" if key in (None, "", "@") else str(key).lower()


def _identity(r: Dict[str, Any]) -> Tuple[Any, ...]:
    return (r.get("type"), _host(r), r.get("value"), r.get("priority"),
            r.get("port"), r.get("weight"))


def _label(r: Dict[str, Any]) -> str:
    prio = f" ({r['priority']})" if r.get("priority") is not None else ""
    return f"{r.get('type')} {_host(r)} {r.get('value')}{prio}"


def _missing(sent: List[Dict[str, Any]], read: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    present = {_identity(r) for r in read}
    return [r for r in sent if _identity(r) not in present]


def _protected(records: List[Dict[str, Any]]) -> set:
    """`(kind, host)` of the records a domain needs to send: MX, SPF
    (`v=spf1` TXT), DKIM (`v=DKIM1` TXT or a `_domainkey` host)."""
    out = set()
    for r in records:
        host, value = _host(r), (r.get("value") or "").strip().strip('"').lower()
        if r.get("type") == "MX":
            out.add(("MX", host))
        elif r.get("type") == "TXT" and value.startswith("v=spf1"):
            out.add(("SPF", host))
        elif "_domainkey" in host.split(".") or (
                r.get("type") == "TXT" and value.startswith("v=dkim1")):
            out.add(("DKIM", host))
    return out


def _with_mailbox(check: Any) -> Any:
    if isinstance(check, dict) and "mailbox" in check:
        check = {**check, "mailbox": project_mailbox(check["mailbox"])}
    return check


def _retry_after(resp: Any) -> Optional[float]:
    """`Retry-After` in seconds — delta-seconds or an HTTP date; `None` when it
    is absent or unreadable."""
    raw = ((getattr(resp, "headers", None) or {}).get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _parse(resp: Any) -> Any:
    """A response → its body, credentials removed. A 3xx or an error status is
    raised (`UpstreamHTTPError`, body filtered by `strip_secrets`); a success
    that is not JSON is a 502 — a proxy page is never read as a result."""
    status = resp.status_code
    if 300 <= status < 400:
        raise UpstreamHTTPError(
            502, f"Mailpool answered with a redirect (HTTP {status}); it is not followed.",
            service="mailpool")
    if status >= 400:
        try:
            body = strip_secrets(resp.json())
        except ValueError:
            body = (resp.text or "")[:200]
        raise UpstreamHTTPError(status, body, service="mailpool")
    if not resp.content:
        return {}
    try:
        body = resp.json()
    except ValueError:
        raise UpstreamHTTPError(
            502, f"Mailpool returned a non-JSON answer (HTTP {status}).", service="mailpool")
    return strip_secrets(body)
