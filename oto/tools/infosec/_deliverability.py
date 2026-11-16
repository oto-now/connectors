"""Email-deliverability report: authentication + blocklists, scored out of 100, with
ranked recommendations. What could not be read is neither penalized nor declared present."""
from __future__ import annotations

from typing import Optional

from ._blocklist import blocklist
from ._email import email_security, spf
from ._probe import Probe

_ORDER = {"high": 0, "medium": 1, "low": 2}


def recommend(sec: dict, bl: dict) -> tuple[int, list[dict]]:
    """Score out of 100 and ranked recommendations (high > medium > low).

    What could not be read (`sec["unread"]`) is NEITHER penalized NOR declared present:
    a DNS error on `_dmarc` is not "No DMARC"."""
    recs: list[dict] = []
    score = 100
    unread = set(sec.get("unread") or ())

    def add(priority: str, cost: int, issue: str, fix: str) -> None:
        nonlocal score
        score -= cost
        recs.append({"priority": priority, "issue": issue, "fix": fix})

    if "mx" in unread:
        pass
    elif sec.get("null_mx"):
        add("medium", 5, "Null MX (RFC 7505): the domain accepts no mail",
            "Replies and bounces to this domain are lost — publish real MX records if it sends mail.")
    elif not sec.get("mx"):
        add("medium", 5, "No MX record", "Publish MX records so replies and bounces can be received.")
    if "spf" in unread:
        pass
    elif not sec.get("spf"):
        add("high", 25, "No SPF record", "Publish a TXT `v=spf1 … ~all` listing every service that sends for this domain.")
    else:
        if sec.get("spf_record_count", 1) > 1:
            add("high", 20, "Several SPF records (permerror)", "Merge them into a single `v=spf1` record.")
        if sec.get("spf_lookup_limit_exceeded"):
            needed = ("more than 10" if sec.get("spf_walk_truncated")
                      else str(sec.get("spf_dns_lookups")))
            add("high", 15, f"SPF needs {needed} DNS lookups (max 10)",
                "Remove unused includes or flatten the record.")
        if sec.get("spf_duplicate_includes"):
            add("low", 2, "SPF repeats include(s): " + ", ".join(sec["spf_duplicate_includes"]),
                "Keep each include once — every repeat still costs a DNS lookup.")
        if sec.get("spf_all") in ("+all", "?all"):
            add("high", 15, f"SPF ends with `{sec.get('spf_all')}`: anyone can send as this domain",
                "End the record with `~all` (or `-all` once all senders are listed).")
        elif not sec.get("spf_all") and "redirect=" not in (sec.get("spf") or "").lower():
            add("low", 3, "SPF has no `all` qualifier", "End the record with `~all` or `-all`.")
    if "dmarc" in unread:
        pass
    elif not sec.get("dmarc"):
        add("high", 20, "No DMARC record",
            "Publish `_dmarc` TXT `v=DMARC1; p=none; rua=mailto:…`, then move to quarantine. "
            "Gmail and Yahoo require DMARC from bulk senders.")
    else:
        if sec.get("dmarc_policy") == "none":
            add("medium", 8, "DMARC policy is `p=none` (monitoring only)",
                "Once reports are clean, move to `p=quarantine` then `p=reject`.")
        if not sec.get("dmarc_rua"):
            add("low", 3, "DMARC has no `rua` report address", "Add `rua=mailto:…` to receive aggregate reports.")
        pct = sec.get("dmarc_pct")
        if pct is not None and pct < 100:
            # `pct=0`: the policy applies to NO message — the biggest gap, not an absence.
            add("medium" if pct == 0 else "low", 8 if pct == 0 else 2,
                f"DMARC applies to {pct}% of mail only", "Raise `pct` to 100.")
    if "dkim" in unread:
        pass
    elif not sec.get("dkim_selectors_found"):
        add("medium", 10, "No DKIM key found on common selectors",
            "Enable DKIM signing at the sending provider; pass `dkim_selector` to check a custom selector.")
    if not sec.get("mta_sts") and "mta_sts" not in unread:
        add("low", 0, "No MTA-STS", "Optional: publish MTA-STS to enforce TLS on inbound mail.")
    if not sec.get("tls_rpt") and "tls_rpt" not in unread:
        add("low", 0, "No TLS-RPT", "Optional: publish `_smtp._tls` TXT to receive TLS failure reports.")
    if not sec.get("bimi") and "bimi" not in unread:
        add("low", 0, "No BIMI", "Optional: BIMI shows the brand logo in Gmail/Yahoo once DMARC is enforced.")
    for r in bl.get("domain_results", []):
        if r["status"] == "listed":
            add("high", 25, f"Domain listed on {r['list']}", f"Find the cause, then request removal: {r['delist_info']}")
    for x in bl.get("ips", []):
        for r in x["results"]:
            if r["status"] == "listed":
                add("high", 15, f"IP {x['ip']} listed on {r['list']}",
                    f"Stop the source of spam, then request removal: {r['delist_info']}")
        if not x.get("ptr"):
            add("medium", 5, f"IP {x['ip']} has no reverse DNS (PTR)", "Ask the IP owner to set a PTR record.")
        elif not x.get("fcrdns"):
            add("low", 3, f"IP {x['ip']} PTR does not resolve back to it", "Align the PTR host's A record with the IP.")
    recs.sort(key=lambda r: _ORDER[r["priority"]])
    return max(score, 0), recs


def deliverability(probe: Probe, d: str, ip: Optional[str], dkim_selector: Optional[str]) -> dict:
    """Full report: authentication + blocklists, scored out of 100, ranked recommendations."""
    walk = spf(probe, d)   # ONE SPF walk, shared by both halves
    sec, bl = probe.gather(lambda: email_security(probe, d, dkim_selector, walk=walk),
                           lambda: blocklist(probe, d, ip, walk=walk))
    for half in (sec, bl):
        if isinstance(half, BaseException):
            raise half
    score, recs = recommend(sec, bl)
    grade = "good" if score >= 85 else "fair" if score >= 65 else "poor"
    # Coverage travels WITH the score: a "good" on a single list queried must not read
    # as a domain verified everywhere. What was not READ is not scored, and says so.
    coverage = {
        "blocklists_checked": bl["checked"],
        "blocklist_errors": bl["errors"],
        "own_ips_checked": len(bl["ips"]),
        "not_checked": [x["name"] for x in bl["not_checked"]],
        "unread_records": sec.get("unread", []),
        "spf_walk_truncated": sec.get("spf_walk_truncated", False),
        "budget_exhausted": probe.exhausted,
        "partial": bool(sec.get("unread") or bl["errors"] or probe.exhausted),
    }
    return {
        "domain": d,
        "score": score,
        "grade": grade,
        "coverage": coverage,
        "recommendations": recs,
        "authentication": sec,
        "blocklists": bl,
        "limits": ("Blocklists that refuse public resolvers (Spamhaus, SURBL, URIBL) are not "
                   "checked; inbox placement and sender reputation at Gmail/Microsoft are not "
                   "visible from DNS."),
    }
