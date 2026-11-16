"""A domain and its sending IPv4s on DNS blocklists (DNSBL), queried over DNS-over-HTTPS.

Lists that refuse public resolvers are named under `not_checked`, never queried: a refusal
answer (`127.255.255.x`…) is not a verdict, and an `error` is never a clean result.
"""
from __future__ import annotations

import ipaddress
from typing import Optional

from ._email import spf
from ._probe import Probe

# Blocklists queryable for free via a public resolver, AND whose terms allow automated
# commercial use (read on 04/10/2026). `listed` = the codes that count as a listing; any
# other 127.x code is returned as `unexpected_answer`, never interpreted.
IP_LISTS = (
    {"zone": "bl.spamcop.net", "name": "SpamCop", "listed": ("127.0.0.2",),
     "info": "https://www.spamcop.net/bl.shtml"},
    {"zone": "psbl.surriel.com", "name": "PSBL", "listed": ("127.0.0.2",),
     "info": "https://psbl.org/"},
    {"zone": "bl.nordspam.com", "name": "NordSpam", "listed": ("127.0.0.2",),
     "info": "https://www.nordspam.com/"},
    {"zone": "all.s5h.net", "name": "s5h", "listed": ("127.0.0.2",),
     "info": "https://www.usenix.org.uk/content/rbl.html"},
)
DOMAIN_LISTS = (
    {"zone": "dbl.nordspam.com", "name": "NordSpam DBL", "listed": ("127.0.0.2",),
     "info": "https://www.nordspam.com/"},
)
# Major lists that refuse public resolvers (or whose terms forbid automated commercial
# use): named so that the absence of a verdict is visible, not queried.
NOT_CHECKED = (
    {"name": "Spamhaus (ZEN / DBL)", "reason": "refuses public resolvers; commercial "
     "access by key (Data Query Service) not configured"},
    {"name": "SURBL", "reason": "refuses public resolvers; commercial feed required"},
    {"name": "URIBL", "reason": "refuses public resolvers; commercial feed required"},
    {"name": "Barracuda (BRBL)", "reason": "free but the resolver's IP must be "
     "registered with Barracuda; a shared public resolver is not"},
    {"name": "Mailspike", "reason": "public-resolver access is reserved for "
     "low-volume non-commercial sites"},
    {"name": "UCEPROTECT", "reason": "only allows manual lookups; an "
     "automated query gets the source blocked"},
)
MAX_IPS = 10


def _dnsbl(probe: Probe, query: str, lst: dict) -> dict:
    """One DNSBL query: `listed` / `clean` / `error` — never `clean` on anything other
    than a clear NXDOMAIN."""
    row = {"list": lst["name"], "zone": lst["zone"]}
    try:
        status, answers = probe.raw(f"{query}.{lst['zone']}", "A")
    except Exception as e:  # rendered in the result (status=error), not swallowed
        return {**row, "status": "error", "detail": type(e).__name__}
    if status == 3:
        return {**row, "status": "clean"}
    if status != 0:
        return {**row, "status": "error", "detail": f"DNS status {status}"}
    if not answers:
        # NOERROR with no record: this is not the NXDOMAIN of an unlisted IP.
        return {**row, "status": "error", "detail": "empty response (NOERROR without A)"}
    if any(a in lst["listed"] for a in answers):
        return {**row, "status": "listed", "codes": answers, "delist_info": lst["info"]}
    return {**row, "status": "error", "detail": f"unexpected_answer {answers}"}


def _ptr(probe: Probe, ip: str) -> dict:
    """Reverse DNS of an IPv4 + forward confirmation (FCrDNS): a sender without a
    consistent PTR is penalized by most filters."""
    rev = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
    try:
        names = [n.rstrip(".") for n in probe.query(rev, "PTR")]
        fwd = probe.query(names[0], "A") if names else []
    except Exception as e:  # rendered in the result (error), not swallowed
        return {"ptr": None, "fcrdns": False, "error": type(e).__name__}
    return {"ptr": names[0] if names else None, "fcrdns": bool(names) and ip in fwd}


def _sending_ips(walk: dict, notes: list) -> list[tuple[str, str]]:
    """The single IPv4s of the domain's own SPF; ranges and providers' IPs are named in
    `notes`, not checked."""
    ips, ranges = [], []
    if walk.get("unread"):
        notes.append("the SPF could not be read (DNS error): its sending IPs are "
                     "not checked — this is not the absence of SPF")
    for raw in walk["ipv4"]:
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            continue
        if net.num_addresses == 1:
            ips.append((str(net.network_address), "spf"))
        else:
            ranges.append(raw)
    if ranges:
        notes.append(f"{len(ranges)} IP range(s) of the SPF not checked — only "
                     f"single IPs are: {', '.join(ranges[:5])}"
                     + (" …" if len(ranges) > 5 else ""))
    if walk["provider_ipv4"]:
        notes.append(f"{len(walk['provider_ipv4'])} IP(s)/range(s) of the providers included "
                     "in the SPF not checked: they belong to their "
                     "infrastructure, not to the domain")
    if not ips and not walk.get("unread"):
        notes.append("no dedicated sending IP found in the SPF: sending probably goes "
                     "through shared infrastructure (Google, Microsoft, "
                     "an emailing tool) whose IPs say nothing about the domain — "
                     "pass `ip` to check a dedicated IP")
    return ips


def blocklist(probe: Probe, d: Optional[str], ip: Optional[str],
              walk: Optional[dict] = None) -> dict:
    """The domain on the domain lists, and the sending IPv4s (`ip` if given, else the
    literal `ip4:` entries of the SPF) on the IP lists. `walk`: the SPF walk already done
    by the caller, otherwise done here."""
    notes: list[str] = []
    if ip:
        ips = [(ip, "argument")]
    elif d:
        ips = _sending_ips(walk if walk is not None else spf(probe, d), notes)
    else:
        ips = []
    seen, uniq = set(), []
    for addr, src in ips:
        if addr not in seen:
            seen.add(addr)
            uniq.append((addr, src))
    if len(uniq) > MAX_IPS:
        notes.append(f"{len(uniq)} IPs found, only the first {MAX_IPS} are checked")
        uniq = uniq[:MAX_IPS]

    def check_ip(addr: str, src: str) -> dict:
        q = ".".join(reversed(addr.split(".")))
        *rows, ptr = _raise_first(probe.gather(
            *[(lambda lst=lst: _dnsbl(probe, q, lst)) for lst in IP_LISTS],
            lambda: _ptr(probe, addr)))
        return {"ip": addr, "source": src, **ptr,
                "listed_on": [r["list"] for r in rows if r["status"] == "listed"],
                "results": list(rows)}

    ip_results = _raise_first(probe.gather(*[(lambda a=a, s=s: check_ip(a, s)) for a, s in uniq]))
    domain_results = (_raise_first(probe.gather(*[(lambda lst=lst: _dnsbl(probe, d, lst))
                                                  for lst in DOMAIN_LISTS]))
                      if d else [])
    every = domain_results + [r for x in ip_results for r in x["results"]]
    listed = sorted({r["list"] for r in every if r["status"] == "listed"})
    return {
        "domain": d,
        "ips": ip_results,
        "domain_results": domain_results,
        "listed": bool(listed),
        "listed_on": listed,
        "checked": sum(1 for r in every if r["status"] in ("clean", "listed")),
        "errors": sum(1 for r in every if r["status"] == "error"),
        "not_checked": list(NOT_CHECKED),
        "notes": notes,
    }


def _raise_first(results: list) -> list:
    """These calls render their own failures: an exception here is a bug, raised as is."""
    for r in results:
        if isinstance(r, BaseException):
            raise r
    return results
