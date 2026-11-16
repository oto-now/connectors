"""The transport of a request to the vendor, over `requests`, as oto-core's HTTP client sends it.

- No redirect is followed: a 3xx is an answer like any status outside 2xx.
- The delay of the function (`timeout_s`, else the connector's, else 30 s) bounds the connection and each read
  (`requests`' timeout: each wait, not the whole exchange).
- The pace of the vendor (`connector.rate_limit`), per credential and per process: a request waits for a place in the
  window of its credential.
- A 429 is replayed twice at most, after the vendor's `Retry-After` when it is 10 s or less (1 s when unreadable); a
  401 is replayed once on a fresh signature when the authentication renews (a token exchange).
- An answer is read up to 4 MiB; a success is JSON (empty: `None`), or `UnreadableAnswer`.
- A guarded address (`address.guard`) is checked before each request.

No secret, no address and no header leaves here in a message or a cause: a failure of `requests` (its message carries
the address, a key in its query included) becomes `Unreachable`, raised without its cause.
"""
from __future__ import annotations

import collections
import email.utils
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import quote_plus

import requests
import urllib3

from .address import guard
from .errors import Unreachable, UnreadableAnswer
from .request import NO_BODY, Form, Request, json_text

#: A larger answer is not read: what an agent receives is bounded anyway.
MAX_ANSWER_BYTES = 4 * 1024 * 1024
#: After a 429: two more tries at most, each after the vendor's `Retry-After` when it is 10 s or less.
MAX_RETRIES = 2
MAX_RETRY_WAIT_S = 10.0
DEFAULT_RETRY_WAIT_S = 1.0
#: The delay of a call when neither the function nor the connector declares one.
DEFAULT_TIMEOUT_S = 30

_CHUNK = 64 * 1024

# Read at the call, never bound at import: a test replaces them.
_clock: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class Target:
    """Where and how requests of a call go: the vendor's `label` (in messages), the resolved `base_url`, the delay,
    the pace (`rate`: `{requests, seconds}` or `None`) and its key (a fingerprint of the credential), and whether the
    address is `guarded`."""

    label: str
    base_url: str
    timeout_s: float
    rate: Optional[dict] = None
    pace_key: str = ""
    guarded: bool = False


class _Pace:
    """The instants of recent requests, per credential, in this process: a request waits for a place in the window."""

    def __init__(self) -> None:
        self._sent: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def take(self, key: str, rate: Optional[dict]) -> None:
        if not rate:
            return
        window = float(rate["seconds"])
        while True:
            with self._lock:
                now = _clock()
                sent = self._sent[key]
                while sent and sent[0] <= now - window:
                    sent.popleft()
                if len(sent) < rate["requests"]:
                    sent.append(now)
                    return
                wait = sent[0] + window - now
            _sleep(wait)


PACE = _Pace()


def _encode(pairs: tuple) -> str:
    """Pairs as a form-encoded text (`URLSearchParams`): a space as `+`, `*` kept."""
    return "&".join(f"{quote_plus(name, safe='*')}={quote_plus(value, safe='*')}" for name, value in pairs)


def url_of(target: Target, request: Request) -> str:
    """The full address of a request: the base, the path, then its query."""
    query = f"?{_encode(request.query)}" if request.query else ""
    return f"{target.base_url}{request.path}{query}"


def _unreachable(target: Target, error: BaseException) -> Unreachable:
    # A delay passed while reading the body reaches here as a `ConnectionError` around urllib3's `ReadTimeoutError`.
    timed_out = isinstance(error, requests.Timeout) or any(
        isinstance(arg, urllib3.exceptions.TimeoutError) for arg in getattr(error, "args", ()))
    if timed_out:
        return Unreachable(f"{target.label} did not answer within {target.timeout_s:g} s. Retry later.", timed_out=True)
    return Unreachable(f"{target.label} could not be reached. Retry later.", timed_out=False)


def send(session: requests.Session, target: Target, request: Request) -> requests.Response:
    """One request, under the delay, without redirect, at the pace of the credential."""
    url = url_of(target, request)
    if target.guarded:
        guard(url)
    headers = {"accept": "application/json", **request.headers}
    data: Optional[bytes] = None
    if isinstance(request.body, Form):
        headers["content-type"] = "application/x-www-form-urlencoded"
        data = _encode(request.body.pairs).encode("ascii")
    elif request.body is not NO_BODY:
        headers["content-type"] = "application/json"
        data = json_text(request.body).encode("utf-8")
    PACE.take(target.pace_key, target.rate)
    try:
        return session.request(request.method, url, headers=headers, data=data, timeout=target.timeout_s,
                               allow_redirects=False, stream=True)
    except requests.RequestException as error:
        raise _unreachable(target, error) from None


def retry_wait(response: requests.Response) -> Optional[float]:
    """The wait a 429 asks for (`Retry-After`, seconds or an HTTP date), `None` beyond the bound."""
    header = response.headers.get("retry-after")
    if header is None:
        return DEFAULT_RETRY_WAIT_S
    try:
        seconds = float(header)
    except ValueError:
        try:
            seconds = email.utils.parsedate_to_datetime(header).timestamp() - time.time()
        except (TypeError, ValueError):
            return DEFAULT_RETRY_WAIT_S
    if seconds != seconds:
        return DEFAULT_RETRY_WAIT_S
    return max(seconds, 0.0) if seconds <= MAX_RETRY_WAIT_S else None


def respond(session: requests.Session, target: Target, request: Request,
            resign: Optional[Callable[[], Request]] = None) -> requests.Response:
    """The vendor's answer to the request: a 401 replayed once on `resign()` when given, a 429 twice at most."""
    response = send(session, target, request)
    if response.status_code == 401 and resign is not None:
        response.close()
        request = resign()
        response = send(session, target, request)
    for _ in range(MAX_RETRIES):
        if response.status_code != 429:
            break
        wait = retry_wait(response)
        if wait is None:
            break
        response.close()
        _sleep(wait)
        response = send(session, target, request)
    return response


def read_bytes(target: Target, response: requests.Response) -> Optional[bytes]:
    """The body of an answer, `None` beyond 4 MiB; a failure while reading is `Unreachable`."""
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in response.iter_content(_CHUNK):
            size += len(chunk)
            if size > MAX_ANSWER_BYTES:
                response.close()
                return None
            chunks.append(chunk)
    except requests.RequestException as error:
        raise _unreachable(target, error) from None
    return b"".join(chunks)


def _text(raw: bytes) -> str:
    """UTF-8, a byte order mark dropped and an invalid sequence replaced (`TextDecoder`)."""
    return raw.decode("utf-8-sig", errors="replace")


def read_json(target: Target, response: requests.Response) -> Any:
    """The JSON of a success, `None` when empty; too large or not JSON: `UnreadableAnswer`."""
    raw = read_bytes(target, response)
    if raw is None:
        raise UnreadableAnswer(f"{target.label} sent an answer too large to read. Narrow the request.")
    if not raw:
        return None
    try:
        return json.loads(_text(raw))
    except ValueError:
        raise UnreadableAnswer(f"{target.label} sent an answer that is not JSON. Retry later.") from None


def read_error_body(target: Target, response: requests.Response) -> Any:
    """The body of an answer outside 2xx: its JSON, else its text; `None` when empty, too large or cut short (an
    unreadable error body says nothing more than its status)."""
    try:
        raw = read_bytes(target, response)
    except Unreachable:
        return None
    if not raw:
        return None
    try:
        return json.loads(_text(raw))
    except ValueError:
        return _text(raw)
