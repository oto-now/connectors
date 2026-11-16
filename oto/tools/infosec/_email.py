"""Email authentication of a domain: SPF walk (bounded), DMARC, DKIM, MTA-STS, TLS-RPT, BIMI."""
from __future__ import annotations

from typing import Optional

from ._probe import Probe

# Common DKIM selectors (mail and sending providers): DKIM is not enumerable, these are
# tested — plus the one the caller gives.
DKIM_SELECTORS = ("default", "google", "selector1", "selector2", "k1", "k2", "k3",
                  "dkim", "mail", "s1", "s2", "smtp", "mandrill", "mxvault",
                  "zoho", "protonmail", "protonmail2", "protonmail3", "sib",
                  "brevo", "mailjet", "resend", "pm", "everlytickey1", "turbo-smtp")

# SPF mechanisms that cost a DNS lookup — RFC 7208 §4.6.4 caps the total at 10.
SPF_MAX_LOOKUPS = 10


def _spf_terms(record: str) -> list[str]:
    return [t for t in record.split()[1:] if t]


def _costs_lookup(term: str) -> bool:
    t = term.lstrip("+-~?").lower()
    return (t in ("a", "mx", "ptr")
            or t.startswith(("include:", "exists:", "redirect=", "a:", "a/",
                             "mx:", "mx/", "ptr:")))


class _Walk:
    """Shared state of ONE SPF walk: domains read, lookups counted, stop.

    The counter is global to the walk (breadth AND depth), and the walk STOPS as soon as
    it exceeds the RFC 7208 limit — the SPF is then in `permerror`, the detail beyond
    that teaches nothing, and a fanned-out SPF cannot make the resolver get hammered."""

    def __init__(self):
        self.seen: set = set()
        self.lookups = 0
        self.truncated = False

    def over(self) -> bool:
        return self.lookups > SPF_MAX_LOOKUPS


def _spf_empty(errors: Optional[list] = None) -> dict:
    return {"lookups": 0, "ipv4": [], "provider_ipv4": [], "duplicates": [],
            "errors": errors or [], "unread": False, "truncated": False}


def spf(probe: Probe, domain: str) -> dict:
    """Full, bounded SPF walk of a domain; `unread` = the root record could NOT be read
    (≠ "no SPF")."""
    state = _Walk()
    walk = _spf_walk(probe, domain, state)
    walk["lookups"] = state.lookups
    walk["truncated"] = state.truncated
    return walk


def _spf_walk(probe: Probe, domain: str, state: _Walk, depth: int = 0,
              path: tuple = ()) -> dict:
    """Counts an SPF's DNS lookups by following include/redirect (bounded), and collects
    the literal IPv4s it authorizes: `ipv4` = those of the domain's OWN record only,
    `provider_ipv4` = those of its includes (a provider's infrastructure, which says
    nothing about the domain)."""
    # `path` = the include chain leading here (a REAL loop); `seen` = everything already
    # walked (a duplicate: counted once, flagged by the caller).
    if depth > 10 or domain in path:
        return _spf_empty([f"loop or excessive depth on {domain}"])
    state.seen.add(domain)
    try:
        txt = probe.query(domain, "TXT")
    except Exception as e:  # rendered in the result (errors, unread), not swallowed
        out = _spf_empty([f"{domain}: {type(e).__name__}"])
        out["unread"] = depth == 0
        return out
    recs = [t for t in txt if t.lower().startswith("v=spf1")]
    if not recs:
        return _spf_empty([f"{domain}: no SPF"] if depth else [])
    ipv4, provider, duplicates, errors = [], [], [], []
    if len(recs) > 1:
        errors.append(f"{domain}: {len(recs)} SPF records (permerror)")
    for term in _spf_terms(recs[0]):
        low = term.lstrip("+-~?").lower()
        if low.startswith("ip4:"):
            ipv4.append(low[4:])
        if not _costs_lookup(term):
            continue
        state.lookups += 1
        if state.over():
            state.truncated = True
            continue
        target = None
        if low.startswith("include:"):
            target = low[8:]
        elif low.startswith("redirect="):
            target = low[9:]
        if not target:
            continue
        if target in path + (domain,):
            errors.append(f"include loop: {' → '.join(path + (domain, target))}")
            continue
        if target in state.seen:
            duplicates.append(target)   # the lookup is due, the content already read
            continue
        sub = _spf_walk(probe, target, state, depth + 1, path + (domain,))
        # a `redirect=` replaces the record: its IPs remain the domain's
        if low.startswith("redirect="):
            ipv4 += sub["ipv4"]
        else:
            provider += sub["ipv4"]
        provider += sub["provider_ipv4"]
        duplicates += sub["duplicates"]
        errors += sub["errors"]
    return {"lookups": state.lookups, "ipv4": ipv4, "provider_ipv4": provider,
            "duplicates": duplicates, "errors": errors, "unread": False, "truncated": False}


def _tags(record: Optional[str]) -> dict:
    out = {}
    for part in (record or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _ok(x) -> list:
    return x if isinstance(x, list) else []


def email_security(probe: Probe, d: str, dkim_selector: Optional[str] = None,
                   walk: Optional[dict] = None) -> dict:
    """SPF/DMARC/DKIM/MTA-STS/TLS-RPT/BIMI posture + MX.

    `walk`: the SPF walk already done by the caller (deliverability shares it with the
    blocklist check); otherwise it is done here. `unread` names what could NOT be read
    (DNS error, deadline): the score never counts it as absent."""
    root_txt, dmarc_txt, mta, tlsrpt, bimi, mx = probe.gather(
        lambda: probe.query(d, "TXT"), lambda: probe.query(f"_dmarc.{d}", "TXT"),
        lambda: probe.query(f"_mta-sts.{d}", "TXT"), lambda: probe.query(f"_smtp._tls.{d}", "TXT"),
        lambda: probe.query(f"default._bimi.{d}", "TXT"), lambda: probe.query(d, "MX"),
    )
    dns_errors = {k: type(v).__name__ for k, v in
                  (("spf", root_txt), ("dmarc", dmarc_txt), ("mta_sts", mta),
                   ("tls_rpt", tlsrpt), ("bimi", bimi), ("mx", mx))
                  if isinstance(v, BaseException)}
    spf_records = [t for t in _ok(root_txt) if t.lower().startswith("v=spf1")]
    spf_record = spf_records[0] if spf_records else None
    spf_all = None
    if spf_record:
        for term in _spf_terms(spf_record):
            if term.lower().lstrip("+-~?") == "all":
                spf_all = term if term[0] in "+-~?" else "+" + term
    if not spf_record:
        walk = _spf_empty()
    elif walk is None:
        walk = spf(probe, d)
    dmarc = next((t for t in _ok(dmarc_txt) if t.lower().startswith("v=dmarc1")), None)
    dt = _tags(dmarc)
    dmarc_policy = (dt.get("p", "").lower() or None) if dmarc else None
    selectors = list(DKIM_SELECTORS)
    if dkim_selector and dkim_selector.strip().lower() not in selectors:
        selectors.insert(0, dkim_selector.strip().lower())
    results = probe.gather(*[(lambda s=s: probe.query(f"{s}._domainkey.{d}", "TXT"))
                             for s in selectors])
    dkim_found = [sel for sel, res in zip(selectors, results)
                  if isinstance(res, list)
                  and any("v=dkim1" in t.lower() or "p=" in t.lower() for t in res)]
    dkim_unread = [sel for sel, res in zip(selectors, results) if isinstance(res, BaseException)]
    if dkim_unread and not dkim_found:
        # No selector found BUT some could not be read: nothing to conclude.
        dns_errors["dkim"] = f"{len(dkim_unread)} selector(s) not read"
    mx_list = _ok(mx)
    null_mx = any(m.strip().rstrip(".") in ("0", "0 .") or m.strip().endswith(" .") for m in mx_list)
    score = sum([bool(spf_record), dmarc_policy in ("quarantine", "reject"),
                 bool(_ok(mta)), bool(dkim_found)])
    grade = ["faible", "faible", "moyenne", "bonne", "forte"][score]
    return {
        "domain": d,
        "spf": spf_record,
        "spf_record_count": len(spf_records),
        "spf_all": spf_all,
        "spf_dns_lookups": walk["lookups"],
        "spf_lookup_limit_exceeded": walk["lookups"] > SPF_MAX_LOOKUPS,
        "spf_duplicate_includes": sorted(set(walk["duplicates"])),
        "spf_errors": walk["errors"],
        "dmarc": dmarc,
        "dmarc_policy": dmarc_policy,
        "dmarc_subdomain_policy": dt.get("sp", "").lower() or None,
        "dmarc_pct": int(dt["pct"]) if dt.get("pct", "").isdigit() else (100 if dmarc else None),
        "dmarc_rua": dt.get("rua") or None,
        "dmarc_alignment": {"dkim": dt.get("adkim", "r"), "spf": dt.get("aspf", "r")} if dmarc else None,
        "mta_sts": bool(_ok(mta)),
        "tls_rpt": bool([t for t in _ok(tlsrpt) if t.lower().startswith("v=tlsrptv1")]),
        "bimi": bool([t for t in _ok(bimi) if t.lower().startswith("v=bimi1")]),
        "mx": mx_list,
        "null_mx": null_mx,
        "dkim_selectors_found": dkim_found,
        "dkim_selector_requested": dkim_selector or None,
        "posture": grade,
        "unread": sorted(dns_errors),
        "dns_errors": dns_errors,
        "spf_walk_truncated": bool(walk.get("truncated")),
        "note": ("DKIM tested on common selectors only (enumeration impossible) — "
                 "pass `dkim_selector` to test the domain's own"),
    }
