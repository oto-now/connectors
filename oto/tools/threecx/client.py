"""3CX phone system client — call log, call recordings and configuration (3CX v20,
XAPI).

Reference: 3CX's Configuration REST API (`/xapi/v1`, OData v4), whose
`$metadata` document is served by every PBX.

## Auth

Either an API client (`client_id`/`client_secret`) or a user account
(`username`/`password`); both yield a Bearer token. See `auth.py`. What a call
returns depends on the rights behind that token: a group manager reads the
call log of the PBX and downloads its recordings, but listing the
`Recordings` collection needs a system administrator.

## Protocol facts that shape a caller

- The call log is the bound function `ReportCallLogData/Pbx.GetCallLogData`,
  one row per call segment. A recorded segment carries `SrcRecId`/`DstRecId`
  (the recording id) and `RecordingUrl` (a relative path, not a download URL).
- Its paging (`$top`/`$skip`) applies before any `$filter`, and its `$count`
  counts the page: a page is the last one when it holds fewer rows than
  asked. Recorded rows are therefore filtered here, after paging.
- A recording downloads from `Recordings/Pbx.DownloadRecording(recId=…)`,
  as `audio/x-wav`.

## Configuration

`read` / `create` / `update` / `delete` / `invoke` reach any resource of the
XAPI. Nothing is hard-coded: every path is checked against the PBX's own
`$metadata` (`schema.Schema`, cached one hour per PBX) before the request, a
body may only name properties of the target type, and a credential
(passwords, SIP secrets, provisioning links, API and licence keys) is never
written, nor handed out by an operation. Masking what is READ is the caller's
choice (`schema.mask`).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

from ..common import raise_for_upstream
from ..common.url_guard import assert_public_https_url, refuse_redirect
from ..common.credentials import require
from . import auth
from .schema import Schema, check_identifier

_HTTP_TIMEOUT = (10, 120)  # (connect, read) — never an unbounded wait
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")
_MAX_TOP = 500
_SCHEMA_TTL = 3600  # seconds — a PBX's schema changes only with its version
# {base_url: (Schema, fetched_at)} — process-wide, like the token cache.
_SCHEMA_CACHE: Dict[str, Tuple[Schema, float]] = {}


def _base_url(value: Optional[str]) -> str:
    parts = urlsplit(value or "")
    if parts.scheme != "https" or not parts.netloc or parts.path.strip("/"):
        raise ValueError(f"base_url must be the https address of the phone system, "
                         f"e.g. https://example.3cx.fr — received {value!r}.")
    if "@" in parts.netloc:
        raise ValueError("base_url must not carry credentials (user:pass@).")
    return f"https://{parts.netloc}"


def _instant(value: str, name: str) -> str:
    """An OData DateTimeOffset literal in UTC — the call log refuses any other
    offset. A bare date is midnight UTC; an instant with an offset is converted."""
    if isinstance(value, str) and _DATE.match(value):
        return f"{value}T00:00:00Z"
    if isinstance(value, str) and _DATETIME.match(value):
        # Fraction completed to 6 digits: Python 3.10 parses no other width.
        texte = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"),
                       value.replace("Z", "+00:00"))
        instant = datetime.fromisoformat(texte)
        return instant.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    raise ValueError(f"{name} must be a yyyy-MM-dd date or an ISO 8601 instant "
                     f"with a timezone — received {value!r}.")


def _filename(resp, rec_id: int) -> str:
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?',
                      resp.headers.get("Content-Disposition", ""))
    return match.group(1) if match else f"recording-{rec_id}.wav"


class ThreeCXClient:
    """3CX XAPI client, Bearer auth (API client or user account)."""

    def __init__(self, base_url: str, *,
                 client_id: Optional[str] = None, client_secret: Optional[str] = None,
                 username: Optional[str] = None, password: Optional[str] = None):
        """
        Args:
            base_url: the PBX's https address.
            client_id / client_secret: an API client of the PBX.
            username / password: a user account, used when no `client_id` is
                given.

        A missing credential raises `MissingCredential`: the library never
        reads one from the environment.
        """
        self.base_url = _base_url(require(base_url, "THREECX_BASE_URL"))
        if client_id:
            self.client_id, self.username = client_id, None
            secret = require(client_secret, "THREECX_CLIENT_SECRET")
            self._creds = {"client_id": client_id, "client_secret": secret}
            identity = f"client:{client_id}"
        else:
            self.client_id = None
            self.username = require(username, "THREECX_USERNAME")
            secret = require(password, "THREECX_PASSWORD")
            self._creds = {"username": self.username, "password": secret}
            identity = f"user:{self.username}"
        self._key = auth.cred_key(self.base_url, identity, secret)
        self.session = requests.Session()

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _token(self) -> str:
        return auth.get_access_token(self.base_url, key=self._key, **self._creds)

    def _request(self, path: str, *, params: Optional[Dict[str, Any]] = None,
                 accept: str = "application/json", method: str = "GET",
                 body: Optional[Any] = None) -> requests.Response:
        renewed = False
        while True:
            assert_public_https_url(self.base_url)
            resp = self.session.request(
                method, f"{self.base_url}/xapi/v1{path}", params=params, json=body,
                headers={"Authorization": f"Bearer {self._token()}", "Accept": accept},
                timeout=_HTTP_TIMEOUT, allow_redirects=False)
            refuse_redirect(resp, service="3cx")
            if resp.status_code == 401 and not renewed:
                # A cached token can be revoked before its expiry: renew once.
                auth.invalidate(self._key)
                renewed = True
                continue
            raise_for_upstream(resp, service="3cx")
            return resp

    # ------------------------------------------------------------------
    # call log
    # ------------------------------------------------------------------

    def list_calls(self, date_from: str, date_to: str, *, top: int = 100, skip: int = 0,
                   recorded_only: bool = False) -> Dict[str, Any]:
        """One page of the call log, every extension and direction.

        Args:
            date_from / date_to: `yyyy-MM-dd` (midnight UTC) or an ISO 8601
                instant with its offset.
            top / skip: paging over call-log rows (at most 500 per page).
            recorded_only: keep only the segments that have a recording; the
                page may then hold fewer rows than `top` without being the last.

        Returns:
            `{"calls": [rows], "next_skip": int | None}` — `next_skip` is None
            on the last page.
        """
        if not 1 <= int(top) <= _MAX_TOP:
            raise ValueError(f"top must be between 1 and {_MAX_TOP} — received {top!r}.")
        if int(skip) < 0:
            raise ValueError(f"skip must be non-negative — received {skip!r}.")
        args = ",".join([
            f"periodFrom={_instant(date_from, 'date_from')}",
            f"periodTo={_instant(date_to, 'date_to')}",
            "sourceType=0", "sourceFilter=''",
            "destinationType=0", "destinationFilter=''",
            "callsType=0", "callTimeFilterType=0",
            "callTimeFilterFrom='0:00:0'", "callTimeFilterTo='0:00:0'",
            "hidePcalls=true",
        ])
        rows: List[dict] = self._request(
            f"/ReportCallLogData/Pbx.GetCallLogData({args})",
            params={"$top": int(top), "$skip": int(skip)}).json().get("value") or []
        next_skip = int(skip) + len(rows) if len(rows) == int(top) else None
        if recorded_only:
            rows = [r for r in rows if r.get("SrcRecId") or r.get("DstRecId")]
        return {"calls": rows, "next_skip": next_skip}

    # ------------------------------------------------------------------
    # recordings
    # ------------------------------------------------------------------

    def download_recording(self, rec_id: int) -> Dict[str, Any]:
        """The audio of a recording, by the `SrcRecId`/`DstRecId` of a
        call-log row.

        Returns:
            `{"content": bytes, "content_type": str, "filename": str}`.
        """
        rec_id = int(rec_id)
        resp = self._request(f"/Recordings/Pbx.DownloadRecording(recId={rec_id})",
                             accept="audio/*")
        return {"content": resp.content,
                "content_type": resp.headers.get("Content-Type") or "audio/x-wav",
                "filename": _filename(resp, rec_id)}

    # ------------------------------------------------------------------
    # configuration — any resource of the XAPI, checked against its schema
    # ------------------------------------------------------------------

    def schema(self) -> Schema:
        """The PBX's own `$metadata`, parsed; cached one hour per PBX."""
        hit = _SCHEMA_CACHE.get(self.base_url)
        if hit and time.monotonic() - hit[1] < _SCHEMA_TTL:
            return hit[0]
        schema = Schema.parse(self._request("/$metadata", accept="application/xml").content)
        _SCHEMA_CACHE[self.base_url] = (schema, time.monotonic())
        return schema

    @staticmethod
    def _json(resp: requests.Response) -> Any:
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return {"content_type": resp.headers.get("Content-Type"), "size": len(resp.content)}

    def read(self, resource: str, *, key: Any = None, navigation: Optional[str] = None,
             filter: Optional[str] = None, select: Optional[List[str]] = None,
             expand: Optional[str] = None, orderby: Optional[str] = None,
             top: Optional[int] = None, skip: int = 0) -> Dict[str, Any]:
        """A collection page, one entity, a navigation of it, or a singleton.

        Returns:
            a collection: `{"value": [rows], "next_skip": int | None}` (a page
            is the last when it holds fewer rows than `top`); otherwise
            `{"value": entity}`.
        """
        schema = self.schema()
        path = schema.path(check_identifier(resource, "resource"), key=key,
                           navigation=navigation)
        params: Dict[str, Any] = {}
        if filter:
            params["$filter"] = filter
        if select:
            params["$select"] = ",".join(check_identifier(s, "select") for s in select)
        if expand:
            params["$expand"] = expand
        if orderby:
            params["$orderby"] = orderby
        collection = key is None and not schema.is_singleton(resource)
        if navigation is not None:
            nav_type = schema.resource_type(resource).navigations[navigation]
            collection = nav_type.startswith("Collection(")
        if collection:
            top = 100 if top is None else int(top)
            if not 1 <= top <= _MAX_TOP:
                raise ValueError(f"top must be between 1 and {_MAX_TOP} — received {top!r}.")
            if int(skip) < 0:
                raise ValueError(f"skip must be non-negative — received {skip!r}.")
            params.update({"$top": top, "$skip": int(skip)})
        payload = self._json(self._request(path, params=params or None))
        if not collection:
            return {"value": payload}
        rows = (payload or {}).get("value") or []
        return {"value": rows, "next_skip": int(skip) + len(rows) if len(rows) == top else None}

    def create(self, resource: str, body: Dict[str, Any]) -> Any:
        """POST a new entity to a collection; returns what the PBX answers."""
        schema = self.schema()
        if schema.is_singleton(check_identifier(resource, "resource")):
            raise ValueError(f"{resource} is a singleton: update it, it cannot be created.")
        schema.check_body(resource, body)
        return self._json(self._request(schema.path(resource), method="POST", body=body))

    def update(self, resource: str, body: Dict[str, Any], *, key: Any = None) -> Any:
        """PATCH the given properties of one entity, or of a singleton."""
        schema = self.schema()
        check_identifier(resource, "resource")
        if key is None and not schema.is_singleton(resource):
            raise ValueError(f"updating {resource} needs the key of the entity.")
        schema.check_body(resource, body)
        return self._json(self._request(schema.path(resource, key=key), method="PATCH",
                                        body=body))

    def delete(self, resource: str, *, key: Any) -> Any:
        """DELETE one entity of a collection."""
        schema = self.schema()
        check_identifier(resource, "resource")
        if key is None:
            raise ValueError(f"deleting from {resource} needs the key of the entity.")
        return self._json(self._request(schema.path(resource, key=key), method="DELETE"))

    def invoke(self, operation: str, *, resource: Optional[str] = None, key: Any = None,
               arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call a bound or unbound operation: a function is a GET (it reads), an
        action a POST (it acts), its arguments in the body."""
        schema = self.schema()
        if resource is not None:
            check_identifier(resource, "resource")
        op = schema.operation(check_identifier(operation, "operation"), resource,
                              on_entity=key is not None or (
                                  resource is not None and schema.is_singleton(resource)))
        path = schema.path(resource, key=key, operation=op, arguments=arguments)
        if op.kind == "function":
            return self._json(self._request(path))
        return self._json(self._request(path, method="POST", body=dict(arguments or {})))
