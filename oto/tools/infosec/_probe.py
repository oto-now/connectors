"""DNS reads of one check: DNS-over-HTTPS through ONE bounded probe.

A probe carries a reused HTTP session, a cap on requests in flight and a global
deadline. A request that would start past the deadline, or that times out once it is
reached, raises `BudgetExhausted`: the read did NOT happen, which is never an absence.
The caller renders it as an error in the coverage; the score only counts what was read.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import requests

from ..common.errors import raise_for_upstream

DOH_URL = "https://cloudflare-dns.com/dns-query"

# Bounds of a DNS check: global deadline, per-request timeout, and requests in flight at
# the same time (DKIM tests ~25 selectors, an IP list ~9 requests per IP).
BUDGET_S = 25.0
REQUEST_S = 8.0
IN_FLIGHT = 10

_RTYPES = {"A": 1, "NS": 2, "CNAME": 5, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28}


class BudgetExhausted(Exception):
    """The deadline of a DNS check is reached: the read did NOT happen (≠ absence)."""


class Probe:
    """One probe per check: reused session, semaphore, deadline."""

    def __init__(self, budget_s: float = BUDGET_S):
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_maxsize=IN_FLIGHT)
        self.session.mount("https://", adapter)
        self._sem = threading.BoundedSemaphore(IN_FLIGHT)
        self._end = time.monotonic() + budget_s
        self.exhausted = False

    def __enter__(self) -> "Probe":
        return self

    def __exit__(self, *exc) -> None:
        self.session.close()

    def remaining(self) -> float:
        return self._end - time.monotonic()

    def raw(self, name: str, rtype: str) -> tuple[int, list[str]]:
        """(DNS status, answers). Status 0 = NOERROR, 3 = NXDOMAIN, 2 = SERVFAIL — a
        blocklist needs to tell them apart (absent from a list ≠ list unreachable). Only
        answers of the requested type are returned (a CNAME chain is not an answer)."""
        want = _RTYPES.get(rtype)
        with self._sem:
            left = self.remaining()
            if left <= 0:
                self.exhausted = True
                raise BudgetExhausted(name)
            try:
                r = self.session.get(DOH_URL, params={"name": name, "type": rtype},
                                     headers={"accept": "application/dns-json"},
                                     timeout=min(left, REQUEST_S))
            except requests.Timeout:
                if self.remaining() <= 0:
                    self.exhausted = True
                    raise BudgetExhausted(name)
                raise
        raise_for_upstream(r, service="doh")
        data = r.json()
        out = []
        for ans in data.get("Answer", []) or []:
            if want is not None and ans.get("type") not in (want, None):
                continue
            d = (ans.get("data") or "").strip()
            if rtype == "TXT":
                # concatenate the chunks and strip the escaping quotes
                d = d.replace('" "', "").strip('"')
            out.append(d)
        return int(data.get("Status", 2)), out

    def query(self, name: str, rtype: str) -> list[str]:
        """Answers of a record type."""
        return self.raw(name, rtype)[1]

    @staticmethod
    def gather(*calls: Callable[[], Any]) -> list[Any]:
        """Runs the calls in parallel; each slot holds its result or its exception.

        Requests in flight are bounded by the probe's semaphore, not by the pool: a call
        may gather in turn without starving its parent."""
        if not calls:
            return []

        def run(call):
            try:
                return call()
            except Exception as e:  # rendered by the caller, never swallowed
                return e

        with ThreadPoolExecutor(max_workers=min(len(calls), 32)) as pool:
            return list(pool.map(run, calls))
