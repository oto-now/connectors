"""Infosec — the public footprint of a domain, read passively.

Whois (RDAP), DNS, email authentication (SPF/DMARC/DKIM…), DNS blocklists and a scored
deliverability report, subdomains (Certificate Transparency, crt.sh), TLS certificate and
HTTP security headers.

Passive only: RDAP, DNS-over-HTTPS, public CT logs, one TLS handshake, one HTTP GET. No
port scan, no vulnerability probing. Open data: no credential.

The DNS reads of one check go through ONE probe (`_probe.Probe`): a reused session, a cap
on requests in flight and a global deadline. A read past the deadline is an ERROR named in
the result, never an absence. `user_agent` is the consumer's own (with its contact
address), sent to RDAP, crt.sh and the checked site: the lib does not present itself.
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
import time
from typing import Optional
from urllib.parse import urlsplit

import requests

from ..common.errors import raise_for_upstream
from ._blocklist import blocklist as _blocklist
from ._deliverability import deliverability as _deliverability
from ._email import email_security as _email_security
from ._probe import BUDGET_S, Probe

# Single source of the checks: the dispatch AND the error message derive from it.
OPS = ("whois", "dns", "email_security", "subdomains", "tls", "headers",
       "blocklist", "deliverability")

_STACK_HINTS = (("google", "Google Workspace"), ("outlook", "Microsoft 365"),
                ("protonmail", "Proton"), ("zoho", "Zoho"),
                ("mailgun", "Mailgun"), ("sendgrid", "SendGrid"),
                ("amazonses", "Amazon SES"), ("ovh", "OVH"),
                ("atlassian", "Atlassian"), ("hubspot", "HubSpot"))
_SECURITY_HEADERS = (("hsts", "strict-transport-security"), ("csp", "content-security-policy"),
                     ("x_frame_options", "x-frame-options"),
                     ("x_content_type_options", "x-content-type-options"),
                     ("referrer_policy", "referrer-policy"),
                     ("permissions_policy", "permissions-policy"))


def normalize_domain(value: str) -> str:
    """A URL, email address or hostname reduced to a bare domain (no scheme, port, path)."""
    v = (value or "").strip().lower()
    if "@" in v:
        v = v.split("@", 1)[1]
    if "://" not in v:
        v = "//" + v
    try:
        host = urlsplit(v).hostname or ""
    except ValueError:
        return ""
    return host.strip(".")


def _ipv4(ip: str) -> str:
    try:
        parsed = ipaddress.ip_address(ip.strip())
    except ValueError:
        raise ValueError(f"invalid ip: {ip!r} (IPv4 expected)") from None
    if parsed.version != 4:
        raise ValueError("only IPv4s can be checked on DNS blocklists")
    return str(parsed)


def _vcard_field(vcard: list, field: str) -> Optional[str]:
    """A field of an RDAP jCard (vcardArray[1] = list of [name, _, _, value])."""
    try:
        for entry in vcard[1]:
            if entry[0] == field:
                val = entry[3]
                return val if isinstance(val, str) else " ".join(map(str, val))
    except (IndexError, TypeError):
        pass
    return None


class InfosecClient:
    """Passive checks of a domain. `user_agent`: the consumer's identity, with a contact."""

    RDAP_URL = "https://rdap.org/domain/{domain}"
    CRTSH_URL = "https://crt.sh/"

    def __init__(self, user_agent: str, *, budget_s: float = BUDGET_S):
        if not (user_agent or "").strip():
            raise ValueError("user_agent is required: the consumer's identity, with a contact")
        self.user_agent = user_agent
        self.budget_s = budget_s

    def check(self, op: str, domain: str = "", ip: Optional[str] = None,
              dkim_selector: Optional[str] = None, limit: int = 100, port: int = 443) -> dict:
        """One check of a domain, picked by `op` (see `OPS`). `domain` may be a URL or an
        email address; `ip` (an IPv4) is read by `blocklist` and `deliverability`, and alone
        suffices for `blocklist`. Raises `ValueError` before any call on a bad argument."""
        if op not in OPS:
            raise ValueError("op must be one of: " + ", ".join(OPS))
        d = normalize_domain(domain)
        addr = _ipv4(ip) if ip else None
        if not d and not (op == "blocklist" and addr):
            raise ValueError(f"invalid domain: {domain!r}")
        if op == "whois":
            return self.whois(d)
        if op == "dns":
            return self.dns(d)
        if op == "email_security":
            return self.email_security(d, dkim_selector)
        if op == "subdomains":
            return self.subdomains(d, limit)
        if op == "tls":
            return self.tls(d, port)
        if op == "headers":
            return self.headers(d)
        if op == "blocklist":
            return self.blocklist(d or None, addr)
        return self.deliverability(d, addr, dkim_selector)

    # --- DNS checks, under one probe ---------------------------------------------------

    def _probe(self) -> Probe:
        return Probe(self.budget_s)

    def dns(self, domain: str) -> dict:
        """A/AAAA/MX/NS/TXT records + stack hints. A failed read is named in `errors`."""
        types = ("A", "AAAA", "MX", "NS", "TXT")
        with self._probe() as probe:
            res = dict(zip(types, probe.gather(*[(lambda t=t: probe.query(domain, t))
                                                 for t in types])))
        records = {t: (v if isinstance(v, list) else []) for t, v in res.items()}
        errors = {t: type(v).__name__ for t, v in res.items() if isinstance(v, BaseException)}
        joined = " ".join(records["MX"] + records["TXT"]).lower()
        hints = {label for needle, label in _STACK_HINTS if needle in joined}
        return {"domain": domain, **records, "stack_hints": sorted(hints), "errors": errors}

    def email_security(self, domain: str, dkim_selector: Optional[str] = None) -> dict:
        """SPF/DMARC/DKIM/MTA-STS/TLS-RPT/BIMI posture + MX. DKIM is tested on common
        selectors, plus `dkim_selector`: an empty find does not prove DKIM is absent."""
        with self._probe() as probe:
            return _email_security(probe, domain, dkim_selector)

    def blocklist(self, domain: Optional[str] = None, ip: Optional[str] = None) -> dict:
        """The domain and its sending IPv4s (`ip`, else the SPF's `ip4:`) on DNS blocklists."""
        with self._probe() as probe:
            return _blocklist(probe, domain, ip)

    def deliverability(self, domain: str, ip: Optional[str] = None,
                       dkim_selector: Optional[str] = None) -> dict:
        """Scored email-deliverability report, with its coverage and ranked recommendations."""
        with self._probe() as probe:
            return _deliverability(probe, domain, ip, dkim_selector)

    # --- other checks --------------------------------------------------------------------

    def whois(self, domain: str) -> dict:
        """Domain registration via RDAP."""
        r = requests.get(self.RDAP_URL.format(domain=domain), timeout=20,
                         headers={"user-agent": self.user_agent}, allow_redirects=True)
        if r.status_code == 404:
            return {"domain": domain, "found": False,
                    "note": "not registered or TLD not covered by RDAP"}
        raise_for_upstream(r, service="rdap")
        j = r.json()
        events = {e.get("eventAction"): e.get("eventDate") for e in j.get("events", []) or []}
        registrar = registrant = None
        for ent in j.get("entities", []) or []:
            roles = ent.get("roles", []) or []
            name = _vcard_field(ent.get("vcardArray", []), "fn")
            if "registrar" in roles:
                registrar = name or ent.get("handle")
            if "registrant" in roles:
                registrant = name or ent.get("handle")
        return {
            "domain": j.get("ldhName", domain),
            "found": True,
            "registrar": registrar,
            "registrant": registrant,
            "statuses": j.get("status", []),
            "created": events.get("registration"),
            "expires": events.get("expiration"),
            "last_changed": events.get("last changed"),
            "nameservers": [ns.get("ldhName") for ns in j.get("nameservers", []) or []],
        }

    def subdomains(self, domain: str, limit: int = 100) -> dict:
        """Known names read from the Certificate Transparency logs. `count` = all distinct
        found, `subdomains` = truncated to `limit`. crt.sh often fails transiently: three
        tries, then the result carries `error`."""
        rows, last_err = None, "unknown"
        for attempt in range(3):
            if attempt:
                time.sleep(1.5 * attempt)
            try:
                r = requests.get(self.CRTSH_URL, params={"q": f"%.{domain}", "output": "json"},
                                 headers={"user-agent": self.user_agent}, timeout=40)
            except requests.RequestException as e:
                last_err = type(e).__name__
                continue
            if r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
                continue
            raise_for_upstream(r, service="crt.sh")
            rows = r.json()
            break
        if rows is None:
            return {"domain": domain, "error": f"crt.sh unavailable ({last_err})", "subdomains": []}
        names: set[str] = set()
        for row in rows:
            for n in (row.get("name_value") or "").splitlines():
                n = n.strip().lower().lstrip("*.")
                if n.endswith(domain) and n != domain:
                    names.add(n)
        ordered = sorted(names)
        return {"domain": domain, "count": len(ordered), "subdomains": ordered[:limit]}

    def tls(self, host: str, port: int = 443) -> dict:
        """Certificate presented by the host (issuer, validity, SANs, protocol). A failed
        validation is reported (`validated=False` + `error`); a network failure too."""
        try:
            return _tls_probe(host, port)
        except Exception as e:  # rendered in the result (error), not swallowed
            return {"host": host, "port": port, "error": f"{type(e).__name__}: {e}"}

    def headers(self, domain: str) -> dict:
        """HTTP security headers + server fingerprint (a single GET)."""
        try:
            r = requests.get(f"https://{domain}", timeout=15, allow_redirects=True,
                             headers={"user-agent": self.user_agent})
        except Exception as e:  # rendered in the result (error), not swallowed
            return {"domain": domain, "error": f"{type(e).__name__}: {e}"}
        h = {k.lower(): v for k, v in r.headers.items()}
        checks = {key: header in h for key, header in _SECURITY_HEADERS}
        return {
            "domain": domain,
            "final_url": str(r.url),
            "status": r.status_code,
            "security_headers": checks,
            "security_headers_score": f"{sum(checks.values())}/{len(checks)}",
            "server": h.get("server"),
            "x_powered_by": h.get("x-powered-by"),
        }


def _tls_probe(host: str, port: int) -> dict:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=15) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert() or {}
                version, cipher = ss.version(), ss.cipher()
        validated, err = True, None
    except ssl.SSLCertVerificationError as e:
        # An INSPECTION: reconnect without verification ONLY to read the protocol and
        # cipher of a host whose certificate is invalid — no data exchanged, and
        # `validated=False` is returned as is. Not a trust channel.
        cert, validated, err = {}, False, str(e)
        uctx = ssl._create_unverified_context()
        with socket.create_connection((host, port), timeout=15) as sock:
            with uctx.wrap_socket(sock, server_hostname=host) as ss:
                version, cipher = ss.version(), ss.cipher()
    subject = {k: v for t in cert.get("subject", ()) for k, v in t}
    issuer = {k: v for t in cert.get("issuer", ()) for k, v in t}
    return {
        "host": host, "port": port, "validated": validated, "error": err,
        "protocol": version,
        "cipher": cipher[0] if cipher else None,
        "issuer": issuer.get("organizationName") or issuer.get("commonName"),
        "subject_cn": subject.get("commonName"),
        "valid_from": cert.get("notBefore"),
        "valid_until": cert.get("notAfter"),
        "subject_alt_names": [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"],
    }
