"""SSRF guard for a base URL supplied by an account administrator.

`assert_public_https_url` accepts only an `https://` URL without credentials
whose host resolves, for every address returned, to a public IP. It is meant to
be called before **each** outgoing request (not only at construction), so a
host whose DNS changes later is still caught. Redirects must be disabled by the
caller (`allow_redirects=False`) and a 3xx turned into an error with
`refuse_redirect`.

Limit: the check resolves the host separately from the connection, so a DNS
answer that changes between the two is not covered.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from .errors import UpstreamHTTPError


def non_public(ip: ipaddress._BaseAddress) -> bool:
    """An address a request never goes to: private, loopback, link-local, reserved, multicast, unspecified or not
    global (an IPv4-mapped IPv6 address judged as its IPv4). Also read without DNS by the Custom API profile
    (`oto.connectors.custom_api`), on a host written as an address."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or not ip.is_global)


def assert_public_https_url(url: str) -> None:
    """Raise `ValueError` unless `url` is a credential-free https URL whose host
    resolves only to public addresses."""
    parts = urlsplit(url or "")
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError(f"https URL required — received {url!r}.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError("URL must not carry credentials (user:pass@).")
    host = parts.hostname
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, ValueError, UnicodeError) as exc:
        raise ValueError(f"host {host!r} does not resolve ({exc}).") from exc
    if not infos:
        raise ValueError(f"host {host!r} does not resolve.")
    for info in infos:
        address = info[4][0].split("%", 1)[0]  # strip an IPv6 zone id
        if non_public(ipaddress.ip_address(address)):
            raise ValueError(f"host {host!r} resolves to a non-public address; refused.")


def refuse_redirect(resp, *, service: str) -> None:
    """Turn a 3xx answer into a clear error (redirects are never followed)."""
    if 300 <= resp.status_code < 400:
        raise UpstreamHTTPError(
            502, {"error": f"upstream answered a redirect (HTTP {resp.status_code}); "
                           "redirects are not followed"}, service=service)
