"""Salesforce REST API Client — https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/"""

import base64
import hashlib
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import requests

from ..common.credentials import require
from ..common import raise_for_upstream
from ..http.client import refuse_redirect, safe_location

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never wait indefinitely


class SalesforceAuthError(ValueError):
    """Salesforce OAuth refusal (invalid_client / invalid_grant…).

    `UpstreamHTTPError` contract: carries a synthetic `status_code` 401 so that
    consumers classify this credential refusal as a handled error, not a
    bug. Subclasses `ValueError`: existing `except ValueError` handlers still work.
    """

    status_code = 401


class SalesforceHostRefused(ValueError):
    """An address the client would send a secret to (`login_url`: client id and
    secret, refresh token; `instance_url`: the access token) is not a Salesforce
    host. Raised BEFORE any request to it, never followed by a fallback."""

    def __init__(self, what: str, url: str):
        self.what = what
        # Only scheme, host, port and path: a userinfo never reaches a message or a log.
        self.url = safe_location(url)
        super().__init__(
            f"Salesforce {what} refused: {self.url or '?'} is not an https address "
            f"of a Salesforce domain ({', '.join('*.' + s for s in SALESFORCE_DOMAINS)})")


# Closed list of the domains a Salesforce org is served from — login, token and REST
# API alike. A host matches when it ENDS with "." + one of them (never a bare
# domain, never a substring). Not listed on purpose: force.com (Visualforce,
# Lightning and file content, not the API), site.com and salesforce-sites.com
# (public sites), cloudforce.com (not a current instance host).
SALESFORCE_DOMAINS = (
    # login.salesforce.com, test.salesforce.com, My Domain <name>.my.salesforce.com
    # and its variants (<name>--<sandbox>.sandbox.my.salesforce.com, <name>.develop.,
    # .scratch., …): help.salesforce.com, "My Domain URL Formats" and "Allow the
    # Required Domains" (*.salesforce.com, login authentication).
    "salesforce.com",
    # Government Cloud Plus - Defense: "orgs in Government Cloud Plus - Defense
    # leverage salesforce.mil FQDNs" (help.salesforce.com, "IP Addresses to Allow for
    # Government Cloud Plus", and Salesforce's Government Cloud Plus - Defense datasheet).
    "salesforce.mil",
    # Salesforce on Alibaba Cloud: login.sfcrmproducts.cn and My Domain
    # <name>.my.sfcrmproducts.cn (help.aliyun.com, Salesforce on Alibaba Cloud docs).
    "sfcrmproducts.cn",
)


_HOSTNAME = re.compile(r"^[a-z0-9.-]+$")


def _check_salesforce_url(url: str, what: str) -> str:
    """`url` itself when it is https, without userinfo nor port, nor query or
    fragment, on a host under `SALESFORCE_DOMAINS`; `SalesforceHostRefused`
    otherwise. The one check of every address a secret goes to."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError, AttributeError):
        raise SalesforceHostRefused(what, url if isinstance(url, str) else "") from None
    host = (parts.hostname or "").lower()
    if (parts.scheme.lower() != "https" or "@" in parts.netloc or port is not None
            or parts.query or parts.fragment or not _HOSTNAME.match(host)
            or not any(host.endswith("." + d) for d in SALESFORCE_DOMAINS)):
        raise SalesforceHostRefused(what, url)
    return url


# PROCESS-WIDE access-token cache, keyed by a hash of the credential — same pattern as
# `oto.tools.zoho.auth._TOKEN_CACHE`, and for the same reason: server-side, a
# client instance is created on EVERY MCP call.
# {key: (access_token, instance_url, expires_at)}
_TOKEN_CACHE: dict[str, tuple[str, str, float]] = {}


def _cred_key(login_url: str, client_id: str, refresh_token: str) -> str:
    """Isolates credentials from each other WITHOUT ever keeping a plaintext secret as
    the key. The refresh_token is part of it: after rotation, the key changes, so an
    entry tied to the old token is never served again."""
    return hashlib.sha256(
        f"{login_url}|{client_id}|{refresh_token}".encode()).hexdigest()


class SalesforceClient:
    API_VERSION = "v60.0"

    # Default fields per standard sObject (avoids requiring a describe() before
    # any read, like Zoho DEFAULT_FIELDS).
    DEFAULT_FIELDS = {
        "Contact": "Id,FirstName,LastName,Email,Phone,Title,AccountId",
        "Account": "Id,Name,Website,Industry,Phone,BillingCity,BillingCountry",
        "Lead": "Id,FirstName,LastName,Company,Email,Phone,Status",
        "Opportunity": "Id,Name,StageName,Amount,CloseDate,AccountId",
    }

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
        login_url: Optional[str] = None,
        on_refresh: Optional[Callable[[dict], None]] = None,
        *,
        session: Optional[requests.Session] = None,
    ):
        """Initialize the client.

        Credentials are always supplied by the consumer (multi-user server usage:
        each call builds a client with the user's resolved creds). The access
        token AND the `instance_url` (returned by the refresh — no fixed region
        table like Zoho) are cached **in memory** on the instance —
        never in a shared file (which would leak between users server-side).

        ⚠️ This instance cache isn't enough server-side: an instance is created on
        EVERY MCP call, so it is never used. The real cache is **process-wide**,
        keyed by a hash of the credential (`_TOKEN_CACHE`), same pattern as
        `oto.tools.zoho.auth` — without it we refresh on every tool call, which
        under rotation amounts to rotating the token on every call.

        `on_refresh(token_data)` is called after each successful refresh,
        with the token server's full response. It is the only way for the
        caller to see what Salesforce returns — notably a
        **renewed `refresh_token`**: under rotation (RTR, mandatory on External
        Client Apps), each exchange invalidates the previous token and returns a
        new one. Discarding it — what this class used to do — revokes the
        connection on first use.

        `session`: the `requests.Session` every request goes out through — the
        refresh-token exchange on `login_url` AND the API calls on the returned
        `instance_url` — when the consumer supplies one (e.g. a session whose
        transport checks each destination at connection time). Absent,
        module-level `requests`, as before.
        """
        self.client_id = require(client_id, "SALESFORCE_CLIENT_ID")
        self.client_secret = require(client_secret, "SALESFORCE_CLIENT_SECRET")
        self.refresh_token = require(refresh_token, "SALESFORCE_REFRESH_TOKEN")
        self.login_url = _check_salesforce_url(
            (login_url or "https://login.salesforce.com").rstrip("/"), "login_url")
        self._access_token: Optional[str] = None
        self._instance_url: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._on_refresh = on_refresh
        self._http = session if session is not None else requests

    # --- Auth ---

    def _get_access_token(self) -> tuple[str, str]:
        """Get a valid (access_token, instance_url), refreshing if needed (in-memory
        cache). The refresh_token grant doesn't report an `expires_in` — cache for a
        fixed window and re-refresh on 401, like the Zoho client."""
        if self._access_token and self._token_expires_at > time.time():
            return self._access_token, self._instance_url  # type: ignore[return-value]

        # PROCESS-WIDE cache (`oto.tools.zoho.auth` pattern): the server creates one
        # client per MCP call, so the instance cache above is never used. Without it we
        # refresh on every tool call — and under rotation (RTR), refreshing means
        # rotating the token. The key is a HASH of the credential, never a plaintext
        # secret, and it includes the refresh_token: a renewed token produces a new
        # key, so no stale entry is served after rotation.
        k = _cred_key(self.login_url, self.client_id, self.refresh_token)
        cached = _TOKEN_CACHE.get(k)
        if cached and cached[2] > time.time() + 60:
            # An entry written before the check, or by anything else: refused, not served.
            _check_salesforce_url(cached[1], "instance_url")
            self._access_token, self._instance_url, self._token_expires_at = cached
            return self._access_token, self._instance_url

        resp = self._http.post(
            f"{self.login_url}/services/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            },
            timeout=_HTTP_TIMEOUT,
            allow_redirects=False,  # a 307/308 would replay the secrets to another host
        )
        refuse_redirect(resp)
        # Salesforce follows standard OAuth2 (RFC 6749 §5.2): an auth refusal
        # (invalid_grant/invalid_client) returns HTTP 400 with the error in the
        # body — UNLIKE Zoho which returns 200 (see ZohoClient). The body is
        # therefore parsed BEFORE `raise_for_status()`: otherwise the 400 raises a
        # raw `requests.HTTPError` (no direct `.status_code`, body absent from the
        # message) and `SalesforceAuthError` — the 401 contract expected by the
        # upstream Sentry triage (`before_send`) — is never reached.
        try:
            token_data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise  # 2xx but unreadable body — re-raise the original parsing error

        if "error" in token_data:
            raise SalesforceAuthError(
                f"Salesforce OAuth error: {token_data['error']} — "
                f"{token_data.get('error_description', '')}")

        resp.raise_for_status()

        # The token answer names the host every call (and its bearer) goes to: checked
        # BEFORE it is kept or cached.
        instance_url = _check_salesforce_url(token_data["instance_url"], "instance_url")
        self._access_token = token_data["access_token"]
        self._instance_url = instance_url
        self._token_expires_at = time.time() + 3600
        _TOKEN_CACHE[k] = (self._access_token, self._instance_url,
                           self._token_expires_at)

        # ROTATION (RTR): under this regime — mandatory on External Client
        # Apps — Salesforce invalidates the token we just used and returns a
        # new one here. We adopt it for the rest of this instance's life, and we
        # let the caller persist it via `on_refresh`: without that, the stored
        # credential is revoked on the first call, and any later reuse makes
        # Salesforce revoke the current token AND the associated access
        # tokens — the connection drops and requires a human reconnect.
        rotated = token_data.get("refresh_token")
        if rotated:
            self.refresh_token = rotated
        if self._on_refresh is not None:
            # Best-effort: a persistence failure must not fail a call whose
            # access token is valid.
            try:
                self._on_refresh(token_data)
            except Exception:  # noqa: BLE001 — persistence is a side effect
                pass
        return self._access_token, self._instance_url

    def _invalidate_token(self):
        """Forget the cached token to force a refresh on next request.

        ALSO purges the process-wide entry: clearing only the instance cache
        would let the next MCP call serve again the token we just judged
        dead (a 401 would then replay the same request unchanged)."""
        _TOKEN_CACHE.pop(
            _cred_key(self.login_url, self.client_id, self.refresh_token), None)
        self._access_token = None
        self._token_expires_at = 0.0

    # --- HTTP ---

    def _url_for(self, instance_url: str, path: str) -> str:
        """`path` relative to `/services/data/{API_VERSION}/` (e.g. "sobjects/Account/",
        "query/") — or an absolute path starting with "/" (e.g. a `nextRecordsUrl`),
        used as-is against `instance_url`, which must be a Salesforce host."""
        _check_salesforce_url(instance_url, "instance_url")
        if path.startswith("/"):
            return f"{instance_url}{path}"
        return f"{instance_url}/services/data/{self.API_VERSION}/{path}"

    def _request(self, method: str, path: str, **kwargs) -> Any:
        token, instance_url = self._get_access_token()
        url = self._url_for(instance_url, path)
        headers = {"Authorization": f"Bearer {token}"}

        for attempt in range(2):
            resp = self._http.request(method, url, headers=headers, timeout=_HTTP_TIMEOUT,
                                      **kwargs)

            # Token expired — refresh once and retry
            if resp.status_code == 401 and attempt == 0:
                self._invalidate_token()
                token, instance_url = self._get_access_token()
                url = self._url_for(instance_url, path)
                headers["Authorization"] = f"Bearer {token}"
                continue

            # No 429 branch: unlike Zoho, Salesforce's REST API doesn't use HTTP 429
            # for rate limiting — exceeding the org's daily API request allotment
            # returns HTTP 403 with error code REQUEST_LIMIT_EXCEEDED (confirmed
            # against Salesforce's own error-codes reference). It's a rolling 24h
            # quota, not a per-second throttle, so an immediate retry wouldn't help
            # anyway — `raise_for_upstream` surfaces the 403 + REQUEST_LIMIT_EXCEEDED
            # body as-is, which is already actionable.
            raise_for_upstream(resp, service="salesforce")

            # create = 201 + body ; update/delete = 204 no body
            return resp.json() if resp.content else {}

        raise Exception("Request failed after retries")

    # --- sObjects (generic CRUD) ---

    def describe(self, sobject: str) -> dict:
        """Field metadata for an sObject type (Account, Contact, custom…)."""
        return self._request("GET", f"sobjects/{sobject}/describe/")

    def get_record(self, sobject: str, record_id: str, fields: Optional[str] = None) -> dict:
        """Get a single record by id."""
        params = {"fields": fields} if fields else None
        return self._request("GET", f"sobjects/{sobject}/{record_id}", params=params)

    def list_records(
        self,
        sobject: str,
        fields: Optional[str] = None,
        where: Optional[str] = None,
        limit: int = 200,
    ) -> dict:
        """List records of an sObject type (built as a SOQL SELECT)."""
        if not fields:
            fields = self.DEFAULT_FIELDS.get(sobject)
            if not fields:
                raise ValueError(
                    f"No default fields for sObject '{sobject}'. Pass fields "
                    f"explicitly. Known sObjects: {', '.join(self.DEFAULT_FIELDS)}"
                )
        soql = f"SELECT {fields} FROM {sobject}"
        if where:
            soql += f" WHERE {where}"
        soql += f" LIMIT {limit}"
        return self.query(soql)

    def create_record(self, sobject: str, data: dict) -> dict:
        """Create a record. Returns {id, success, errors}."""
        return self._request("POST", f"sobjects/{sobject}/", json=data)

    def update_record(self, sobject: str, record_id: str, data: dict) -> dict:
        """Update a record's fields. Salesforce returns 204 — synthesized
        {id, success} (the caller never sees the raw empty body)."""
        self._request("PATCH", f"sobjects/{sobject}/{record_id}", json=data)
        return {"id": record_id, "success": True}

    def delete_record(self, sobject: str, record_id: str) -> dict:
        """Delete a record. Salesforce returns 204 — synthesized {id, success}."""
        self._request("DELETE", f"sobjects/{sobject}/{record_id}")
        return {"id": record_id, "success": True}

    def upsert_record(self, sobject: str, external_id_field: str, external_id: str, data: dict) -> dict:
        """Upsert on an external id field — idempotent create-or-update."""
        return self._request(
            "PATCH", f"sobjects/{sobject}/{external_id_field}/{external_id}", json=data)

    # --- sObject Collections (bulk create/update, ONE sObject type per call) ---
    #
    # Three Salesforce mechanisms cover "many records" and they aren't
    # interchangeable: Bulk API 2.0 is a fully ASYNC job/upload/poll lifecycle
    # meant for 2000+ records (wrong shape for a synchronous tool call); Composite
    # Batch runs up to 25 arbitrary, independent sub-requests (built for
    # heterogeneous multi-step sequences, not bulk of one type). Collections is
    # the one that fits here: synchronous, ONE HTTP call, up to 200 records of
    # the SAME sObject type (confirmed against Salesforce's REST API guide).

    MAX_COLLECTION_RECORDS = 200

    def _collection_records(self, sobject: str, items: list[dict]) -> list[dict]:
        if len(items) > self.MAX_COLLECTION_RECORDS:
            raise ValueError(
                f"{len(items)} records — sObject Collections caps at "
                f"{self.MAX_COLLECTION_RECORDS} per call.")
        return [{"attributes": {"type": sobject}, **item} for item in items]

    def create_records(self, sobject: str, items: list[dict],
                        all_or_none: bool = False) -> list[dict]:
        """Bulk-create up to 200 records of the SAME sObject type in ONE call.

        Returns a list of `{id, success, errors}`, in the SAME ORDER as
        `items`. ⚠️ A 200 HTTP response can still carry PER-RECORD failures
        when `all_or_none` is false (the Salesforce default, kept as our
        default too) — unlike `create_record`, a raised exception here only
        means the whole request failed, not that every record did. Always
        inspect each entry.
        """
        return self._request("POST", "composite/sobjects", json={
            "allOrNone": all_or_none,
            "records": self._collection_records(sobject, items),
        })

    def update_records(self, sobject: str, items: list[dict],
                        all_or_none: bool = False) -> list[dict]:
        """Bulk-update up to 200 records of the SAME sObject type in ONE call
        — each item MUST carry its own `Id` (the record to update) alongside
        the fields to change. Same per-record `{id, success, errors}` contract
        as `create_records`."""
        return self._request("PATCH", "composite/sobjects", json={
            "allOrNone": all_or_none,
            "records": self._collection_records(sobject, items),
        })

    # --- SOQL / SOSL ---

    def query(self, soql: str) -> dict:
        """Run a SOQL query. Returns {totalSize, done, records: [...], nextRecordsUrl?}."""
        return self._request("GET", "query/", params={"q": soql})

    def query_more(self, next_records_url: str) -> dict:
        """Follow pagination — `next_records_url` = a previous query's `nextRecordsUrl`
        (an absolute path, e.g. "/services/data/v60.0/query/01g...-2000"). Routed
        through `_request` for the same refresh-and-retry-once behavior as any
        other call."""
        return self._request("GET", next_records_url)

    def search(self, sosl: str) -> dict:
        """Run a SOSL search, e.g. "FIND {Acme} IN ALL FIELDS RETURNING Account(Id, Name)"."""
        return self._request("GET", "search/", params={"q": sosl})

    # --- Notes (Enhanced Notes: ContentNote + ContentDocumentLink) ---
    #
    # Salesforce replaced the classic `Note` sObject (a simple ParentId-attached
    # record, like Zoho's Notes) with "Enhanced Notes" as the Lightning Experience
    # default years ago — Enhanced Notes are backed by `ContentNote` (a specialized
    # ContentDocument whose `Content` field is a directly SOQL-queryable base64 blob)
    # linked to a parent record via a separate `ContentDocumentLink` row
    # (`ContentDocumentId` = the ContentNote's own `Id`, `LinkedEntityId` = the
    # parent). Orgs still on classic Notes (admin never enabled Enhanced Notes)
    # aren't covered here — only the modern default is.

    @staticmethod
    def _soql_escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def list_notes(self, parent_id: str) -> list[dict]:
        """List Enhanced Notes attached to a record, `Content` decoded back to text.

        Two SOQL queries (ContentDocumentLink → ContentNote) because
        ContentDocumentLink doesn't support a semi-join directly into ContentNote.
        """
        escaped_id = self._soql_escape(parent_id)
        links = self.query(
            "SELECT ContentDocumentId FROM ContentDocumentLink "
            f"WHERE LinkedEntityId = '{escaped_id}'"
        ).get("records", [])
        doc_ids = [r["ContentDocumentId"] for r in links]
        if not doc_ids:
            return []
        ids_csv = ",".join(f"'{i}'" for i in doc_ids)
        notes = self.query(
            f"SELECT Id, Title, Content, CreatedDate FROM ContentNote WHERE Id IN ({ids_csv})"
        ).get("records", [])
        for note in notes:
            if note.get("Content"):
                try:
                    note["Content"] = base64.b64decode(note["Content"]).decode(
                        "utf-8", errors="replace")
                except (ValueError, TypeError):
                    pass  # malformed/non-base64 — leave as returned
        return notes

    def create_note(self, parent_id: str, title: str, body: str) -> dict:
        """Create an Enhanced Note and link it to a record.

        `body` is plain text/HTML, base64-encoded for ContentNote's `Content`
        field per the API contract.
        """
        content_b64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
        note = self.create_record("ContentNote", {"Title": title, "Content": content_b64})
        self.create_record("ContentDocumentLink", {
            "ContentDocumentId": note["id"],
            "LinkedEntityId": parent_id,
            "ShareType": "V",
            "Visibility": "AllUsers",
        })
        return note
