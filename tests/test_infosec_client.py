"""Contrat du client infosec — sans réseau.

Le transport est remplacé : `Probe.raw` (DNS-over-HTTPS) par une table (nom, type) →
(statut, réponses), `requests.get` par une réponse fixe, `socket.create_connection` par
une panne. Ce qui est vérifié : le routage de `check` (une op = une vérification), le
refus d'un argument invalide AVANT tout appel, et le comportement de chaque vérification,
en particulier qu'une lecture manquée n'est jamais comptée comme une absence.
"""
from __future__ import annotations

import pytest

from oto.tools.infosec import OPS, InfosecClient, normalize_domain
from oto.tools.infosec import _blocklist, _email, _probe
from oto.tools.infosec import client as C

UA = "test-infosec/1.0 (+https://example.org)"


@pytest.fixture
def infosec():
    return InfosecClient(UA)


# --- routage et refus ------------------------------------------------------------------

_METHODS = ("whois", "dns", "email_security", "subdomains", "tls", "headers",
            "blocklist", "deliverability")


@pytest.fixture
def spies(monkeypatch):
    seen: dict[str, tuple] = {}
    for name in _METHODS:
        def fn(self, *args, _name=name):
            seen[_name] = args
            return {"check": _name}
        monkeypatch.setattr(InfosecClient, name, fn)
    return seen


@pytest.mark.parametrize("op", OPS)
def test_each_op_routes_to_its_own_check(infosec, spies, op):
    assert infosec.check(op, "example.com") == {"check": op}
    assert set(spies) == {op}


def test_domain_is_normalised_before_the_check(infosec, spies):
    for raw in ("https://Example.com/path?q=1", "contact@example.com", "example.com:8443", "EXAMPLE.COM."):
        spies.clear()
        infosec.check("dns", raw)
        assert spies["dns"] == ("example.com",), raw


def test_limit_port_and_their_defaults(infosec, spies):
    infosec.check("subdomains", "example.com", limit=7)
    assert spies["subdomains"] == ("example.com", 7)
    infosec.check("tls", "example.com", port=8443)
    assert spies["tls"] == ("example.com", 8443)
    infosec.check("subdomains", "example.com")
    assert spies["subdomains"] == ("example.com", 100)
    infosec.check("tls", "example.com")
    assert spies["tls"] == ("example.com", 443)


def test_unknown_op_is_refused_with_the_allowed_list(infosec, spies):
    with pytest.raises(ValueError, match="op must be") as e:
        infosec.check("portscan", "example.com")
    assert all(op in str(e.value) for op in OPS)
    assert spies == {}


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("bad", ["", "   ", "@", "://"])
def test_invalid_domain_is_refused_before_any_call(infosec, spies, op, bad):
    with pytest.raises(ValueError, match="invalid domain"):
        infosec.check(op, bad)
    assert spies == {}


def test_ip_alone_suffices_for_blocklist_only(infosec, spies):
    infosec.check("blocklist", ip="1.2.3.4")
    assert spies["blocklist"] == (None, "1.2.3.4")
    with pytest.raises(ValueError, match="invalid domain"):
        infosec.check("dns", ip="1.2.3.4")
    with pytest.raises(ValueError, match="IPv4"):
        infosec.check("blocklist", ip="2001:db8::1")
    with pytest.raises(ValueError, match="invalid ip"):
        infosec.check("blocklist", ip="not-an-ip")


def test_user_agent_is_required():
    with pytest.raises(ValueError, match="user_agent"):
        InfosecClient("")


def test_normalize_domain():
    assert normalize_domain("https://a.example.com:8443/x") == "a.example.com"
    assert normalize_domain("x@Example.com") == "example.com"
    assert normalize_domain("") == ""


# --- transport remplacé ------------------------------------------------------------------

def _fake_dns(monkeypatch, table: dict):
    """`Probe.raw` : clé (nom, type) → (statut, réponses) ; absent = NXDOMAIN. Une valeur
    exception est levée."""
    seen = []

    def raw(self, name, rtype):
        seen.append((name, rtype))
        value = table.get((name, rtype), (3, []))
        if isinstance(value, BaseException):
            raise value
        return value
    monkeypatch.setattr(_probe.Probe, "raw", raw)
    return seen


class _Resp:
    def __init__(self, payload=None, *, status=200, headers=None, url=""):
        self._payload, self.status_code = payload, status
        self.headers, self.url, self.text = headers or {}, url, str(payload)

    def json(self):
        return self._payload


def _fake_get(monkeypatch, resp):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return resp
    monkeypatch.setattr(C.requests, "get", get)
    return calls


def test_dns_reports_records_stack_hints_and_errors(monkeypatch, infosec):
    _fake_dns(monkeypatch, {
        ("example.com", "A"): (0, ["1.2.3.4"]),
        ("example.com", "MX"): (0, ["10 aspmx.l.GOOGLE.com."]),
        ("example.com", "TXT"): (0, ["v=spf1 include:_spf.google.com ~all",
                                     "hubspot-domain-verification=xyz"]),
        ("example.com", "NS"): RuntimeError("down"),
    })
    out = infosec.check("dns", "https://example.com/")
    assert out["A"] == ["1.2.3.4"]
    assert out["stack_hints"] == ["Google Workspace", "HubSpot"]
    assert out["NS"] == [] and out["errors"] == {"NS": "RuntimeError"}


def test_email_security_grades_the_posture(monkeypatch, infosec):
    _fake_dns(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 -all"]),
        ("_dmarc.example.com", "TXT"): (0, ["v=DMARC1; p=reject; rua=mailto:x@example.com"]),
        ("_mta-sts.example.com", "TXT"): (0, ["v=STSv1; id=1"]),
        ("google._domainkey.example.com", "TXT"): (0, ["v=DKIM1; k=rsa; p=MIIB"]),
    })
    out = infosec.check("email_security", "example.com")
    assert out["dmarc_policy"] == "reject"
    assert out["mta_sts"] is True
    assert out["dkim_selectors_found"] == ["google"]
    assert out["posture"] == "forte"
    assert "common selectors" in out["note"]


def test_email_security_weak_when_dmarc_is_none(monkeypatch, infosec):
    _fake_dns(monkeypatch, {("_dmarc.example.com", "TXT"): (0, ["v=DMARC1; p=none"])})
    out = infosec.check("email_security", "example.com")
    assert (out["spf"], out["dmarc_policy"], out["posture"]) == (None, "none", "faible")


def test_email_security_tests_the_requested_dkim_selector(monkeypatch, infosec):
    _fake_dns(monkeypatch, {("acme2026._domainkey.example.com", "TXT"): (0, ["v=DKIM1; p=MIIB"])})
    out = infosec.check("email_security", "example.com", dkim_selector="acme2026")
    assert out["dkim_selectors_found"] == ["acme2026"]


def test_spf_lookup_count_follows_includes(monkeypatch, infosec):
    many = " ".join(f"include:s{i}.example.net" for i in range(6))
    table = {("example.com", "TXT"): (0, [f"v=spf1 {many} mx a ~all"])}
    for i in range(6):
        table[(f"s{i}.example.net", "TXT")] = (0, ["v=spf1 include:deep.example.net ~all"])
    table[("deep.example.net", "TXT")] = (0, ["v=spf1 ip4:1.2.3.4 -all"])
    _fake_dns(monkeypatch, table)
    out = infosec.check("email_security", "example.com")
    assert out["spf_dns_lookups"] > _email.SPF_MAX_LOOKUPS
    assert out["spf_lookup_limit_exceeded"] is True
    assert out["spf_walk_truncated"] is True
    assert out["spf_all"] == "~all"


def test_a_repeated_include_is_a_duplicate_not_a_loop(monkeypatch, infosec):
    _fake_dns(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 include:_spf.p.net include:_spf.p.net ~all"]),
        ("_spf.p.net", "TXT"): (0, ["v=spf1 ip4:1.2.3.4 -all"]),
    })
    out = infosec.check("email_security", "example.com")
    assert out["spf_duplicate_includes"] == ["_spf.p.net"]
    assert out["spf_errors"] == []
    assert out["spf_dns_lookups"] == 2


def test_a_real_include_loop_is_still_reported(monkeypatch, infosec):
    _fake_dns(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 include:a.example.net ~all"]),
        ("a.example.net", "TXT"): (0, ["v=spf1 include:example.com -all"]),
    })
    out = infosec.check("email_security", "example.com")
    assert any("loop" in e for e in out["spf_errors"])


# --- listes noires --------------------------------------------------------------------------

def test_blocklist_reports_listed_clean_and_error_distinctly(monkeypatch, infosec):
    _fake_dns(monkeypatch, {
        ("4.3.2.1.bl.spamcop.net", "A"): (0, ["127.0.0.2"]),
        ("4.3.2.1.psbl.surriel.com", "A"): (2, []),
        ("4.3.2.1.in-addr.arpa", "PTR"): (0, ["mail.example.com."]),
        ("mail.example.com", "A"): (0, ["1.2.3.4"]),
    })
    out = infosec.check("blocklist", ip="1.2.3.4")
    by_list = {r["list"]: r["status"] for r in out["ips"][0]["results"]}
    assert by_list["SpamCop"] == "listed"
    assert by_list["PSBL"] == "error"
    assert by_list["NordSpam"] == "clean"
    assert out["listed"] is True and out["listed_on"] == ["SpamCop"]
    assert out["errors"] == 1
    assert (out["ips"][0]["ptr"], out["ips"][0]["fcrdns"]) == ("mail.example.com", True)


@pytest.mark.parametrize("answer", [(0, ["127.255.255.254"]), (0, [])])
def test_a_refusal_code_or_an_empty_answer_is_never_clean(monkeypatch, infosec, answer):
    _fake_dns(monkeypatch, {("4.3.2.1.bl.spamcop.net", "A"): answer})
    out = infosec.check("blocklist", ip="1.2.3.4")
    row = next(r for r in out["ips"][0]["results"] if r["list"] == "SpamCop")
    assert row["status"] == "error"


def test_blocklist_names_the_lists_it_cannot_check(monkeypatch, infosec):
    _fake_dns(monkeypatch, {})
    out = infosec.check("blocklist", "example.com")
    assert {"Spamhaus (ZEN / DBL)", "SURBL", "URIBL", "Barracuda (BRBL)", "Mailspike",
            "UCEPROTECT"} <= {x["name"] for x in out["not_checked"]}
    queried = {lst["zone"] for lst in _blocklist.IP_LISTS + _blocklist.DOMAIN_LISTS}
    assert not queried & {"b.barracudacentral.org", "bl.mailspike.net", "dnsbl-1.uceprotect.net"}


def test_blocklist_takes_sending_ips_from_the_domain_s_own_spf(monkeypatch, infosec):
    _fake_dns(monkeypatch, {("example.com", "TXT"): (0, ["v=spf1 ip4:1.2.3.4 ip4:10.0.0.0/24 ~all"])})
    out = infosec.check("blocklist", "example.com")
    assert [x["ip"] for x in out["ips"]] == ["1.2.3.4"]
    assert len(out["notes"]) == 1 and "10.0.0.0/24" in out["notes"][0]
    _fake_dns(monkeypatch, {("example.com", "TXT"): (0, ["v=spf1 include:_spf.google.com ~all"])})
    out = infosec.check("blocklist", "example.com")
    assert out["ips"] == [] and any("shared infrastructure" in n for n in out["notes"])


def test_blocklist_does_not_check_ips_of_included_providers(monkeypatch, infosec):
    _fake_dns(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 ip4:1.2.3.4 include:spf.provider.net ~all"]),
        ("spf.provider.net", "TXT"): (0, ["v=spf1 ip4:5.6.7.8 -all"]),
    })
    out = infosec.check("blocklist", "example.com")
    assert [x["ip"] for x in out["ips"]] == ["1.2.3.4"]
    assert any("providers included" in n for n in out["notes"])


def test_spf_redirect_keeps_the_ips_as_the_domain_s_own(monkeypatch, infosec):
    _fake_dns(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 redirect=_spf.example.com"]),
        ("_spf.example.com", "TXT"): (0, ["v=spf1 ip4:1.2.3.4 -all"]),
    })
    assert [x["ip"] for x in infosec.check("blocklist", "example.com")["ips"]] == ["1.2.3.4"]


def test_blocklist_checks_the_domain_on_domain_lists(monkeypatch, infosec):
    _fake_dns(monkeypatch, {("example.com.dbl.nordspam.com", "A"): (0, ["127.0.0.2"])})
    out = infosec.check("blocklist", "example.com")
    assert out["domain_results"][0]["status"] == "listed"
    assert out["listed_on"] == ["NordSpam DBL"]


# --- délivrabilité ------------------------------------------------------------------------------

def test_deliverability_scores_and_ranks_recommendations(monkeypatch, infosec):
    _fake_dns(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 include:_spf.google.com ~all"]),
        ("_dmarc.example.com", "TXT"): (0, ["v=DMARC1; p=none"]),
        ("example.com", "MX"): (0, ["1 smtp.google.com."]),
        ("google._domainkey.example.com", "TXT"): (0, ["v=DKIM1; p=MIIB"]),
    })
    out = infosec.check("deliverability", "example.com")
    assert 0 < out["score"] < 100
    prios = [r["priority"] for r in out["recommendations"]]
    assert prios == sorted(prios, key={"high": 0, "medium": 1, "low": 2}.get)
    assert "p=none" in " ".join(r["issue"] for r in out["recommendations"])
    assert out["blocklists"]["not_checked"]
    cov = out["coverage"]
    assert cov["own_ips_checked"] == 0
    assert cov["blocklists_checked"] == len(_blocklist.DOMAIN_LISTS)
    assert "SURBL" in cov["not_checked"]


def test_deliverability_flags_a_missing_dmarc_as_high(monkeypatch, infosec):
    _fake_dns(monkeypatch, {("example.com", "TXT"): (0, ["v=spf1 -all"])})
    top = infosec.check("deliverability", "example.com")["recommendations"][0]
    assert top["priority"] == "high" and "DMARC" in top["issue"]


def test_deliverability_does_not_ask_for_all_when_spf_redirects(monkeypatch, infosec):
    _fake_dns(monkeypatch, {("example.com", "TXT"): (0, ["v=spf1 redirect=_spf.example.net"])})
    out = infosec.check("deliverability", "example.com")
    assert not any("qualifier" in r["issue"] for r in out["recommendations"])


def test_deliverability_walks_the_spf_once(monkeypatch, infosec):
    seen = _fake_dns(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 include:_spf.p.net ~all"]),
        ("_spf.p.net", "TXT"): (0, ["v=spf1 ip4:1.2.3.4 -all"]),
    })
    infosec.check("deliverability", "example.com")
    assert seen.count(("_spf.p.net", "TXT")) == 1


def test_a_fanned_out_spf_is_bounded_globally(monkeypatch, infosec):
    seen = []

    def raw(self, name, rtype):
        seen.append((name, rtype))
        if rtype == "TXT" and (name == "example.com" or name.endswith(".trap.net")):
            kids = " ".join(f"include:{i}.{name.split('.')[0]}.trap.net" for i in range(10))
            return 0, [f"v=spf1 {kids} -all"]
        return 3, []
    monkeypatch.setattr(_probe.Probe, "raw", raw)
    out = infosec.check("deliverability", "example.com")
    spf_reads = [n for n, t in seen if t == "TXT" and (n == "example.com" or n.endswith(".trap.net"))]
    assert len(spf_reads) <= 2 * (_email.SPF_MAX_LOOKUPS + 1)
    assert out["authentication"]["spf_lookup_limit_exceeded"] is True
    assert out["coverage"]["spf_walk_truncated"] is True
    assert any("more than 10" in r["issue"] for r in out["recommendations"])


def test_dmarc_pct_zero_is_penalised(monkeypatch, infosec):
    _fake_dns(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 -all"]),
        ("_dmarc.example.com", "TXT"): (0, ["v=DMARC1; p=reject; pct=0; rua=mailto:a@b.c"]),
        ("example.com", "MX"): (0, ["1 mx.example.com."]),
        ("google._domainkey.example.com", "TXT"): (0, ["v=DKIM1; p=MIIB"]),
    })
    out = infosec.check("deliverability", "example.com")
    assert out["score"] < 100
    assert any("0% of mail" in r["issue"] for r in out["recommendations"])


def test_a_dns_error_is_never_scored_as_an_absence(monkeypatch, infosec):
    def raw(self, name, rtype):
        if name == "_dmarc.example.com" or rtype == "MX":
            raise RuntimeError("DoH unreachable")
        if (name, rtype) == ("example.com", "TXT"):
            return 0, ["v=spf1 -all"]
        return 3, []
    monkeypatch.setattr(_probe.Probe, "raw", raw)
    out = infosec.check("deliverability", "example.com")
    issues = " ".join(r["issue"] for r in out["recommendations"])
    assert "No DMARC" not in issues and "No MX" not in issues
    assert {"dmarc", "mx"} <= set(out["coverage"]["unread_records"])
    assert out["coverage"]["partial"] is True


def test_a_null_mx_is_scored(monkeypatch, infosec):
    _fake_dns(monkeypatch, {("example.com", "TXT"): (0, ["v=spf1 -all"]),
                            ("example.com", "MX"): (0, ["0 ."])})
    issues = [r["issue"] for r in infosec.check("deliverability", "example.com")["recommendations"]]
    assert any("Null MX" in i for i in issues) and "No MX record" not in issues


def test_the_time_budget_yields_a_partial_named_result(monkeypatch):
    """Past the deadline no request leaves (the real transport is never reached)."""
    def boom(*a, **k):
        raise AssertionError("no request past the deadline")
    monkeypatch.setattr(_probe.requests.Session, "get", boom)
    out = InfosecClient(UA, budget_s=0.0).check("deliverability", "example.com")
    assert out["coverage"]["budget_exhausted"] is True
    assert out["coverage"]["partial"] is True
    issues = " ".join(r["issue"] for r in out["recommendations"])
    assert "No DMARC" not in issues and "No SPF" not in issues


def test_probe_parses_doh_answers_of_the_requested_type(monkeypatch):
    calls = []

    def get(self, url, **kwargs):
        calls.append((url, kwargs))
        return _Resp({"Status": 0, "Answer": [{"type": 5, "data": "alias.example.com."},
                                              {"type": 16, "data": '"v=spf1 " "-all"'}]})
    monkeypatch.setattr(_probe.requests.Session, "get", get)
    with _probe.Probe() as probe:
        assert probe.raw("example.com", "TXT") == (0, ["v=spf1 -all"])
    assert calls[0][0] == _probe.DOH_URL
    assert calls[0][1]["params"] == {"name": "example.com", "type": "TXT"}


# --- RDAP, CT, en-têtes, TLS ----------------------------------------------------------------------

def test_subdomains_counts_all_but_returns_at_most_limit(monkeypatch, infosec):
    calls = _fake_get(monkeypatch, _Resp([
        {"name_value": "api.example.com\n*.vpn.example.com"},
        {"name_value": "example.com"},
        {"name_value": "mail.other.com"},
        {"name_value": "staging.example.com"},
    ]))
    out = infosec.check("subdomains", "example.com", limit=2)
    assert out["count"] == 3
    assert out["subdomains"] == ["api.example.com", "staging.example.com"]
    assert calls[0][1]["headers"]["user-agent"] == UA


def test_subdomains_reports_a_crtsh_outage_instead_of_raising(monkeypatch, infosec):
    monkeypatch.setattr(C.time, "sleep", lambda *_: None)
    calls = _fake_get(monkeypatch, _Resp(None, status=502))
    out = infosec.check("subdomains", "example.com")
    assert out["subdomains"] == [] and "crt.sh unavailable" in out["error"]
    assert len(calls) == 3


def test_headers_scores_the_security_headers(monkeypatch, infosec):
    _fake_get(monkeypatch, _Resp(headers={"Strict-Transport-Security": "max-age=1",
                                          "Content-Security-Policy": "default-src 'self'",
                                          "Server": "nginx"}, url="https://example.com/"))
    out = infosec.check("headers", "example.com")
    assert out["security_headers"]["hsts"] is True
    assert out["security_headers"]["x_frame_options"] is False
    assert out["security_headers_score"] == "2/6"
    assert out["server"] == "nginx"


def test_whois_flags_an_unregistered_domain(monkeypatch, infosec):
    _fake_get(monkeypatch, _Resp(None, status=404))
    out = infosec.check("whois", "nope.example")
    assert out["found"] is False and "RDAP" in out["note"]


def test_whois_extracts_registrar_and_dates(monkeypatch, infosec):
    calls = _fake_get(monkeypatch, _Resp({
        "ldhName": "example.com",
        "status": ["client transfer prohibited"],
        "events": [{"eventAction": "registration", "eventDate": "1995-08-14"},
                   {"eventAction": "expiration", "eventDate": "2027-08-13"}],
        "entities": [{"roles": ["registrar"],
                      "vcardArray": ["vcard", [["fn", {}, "text", "RESERVED-IANA"]]]}],
        "nameservers": [{"ldhName": "a.iana-servers.net"}],
    }))
    out = infosec.check("whois", "example.com")
    assert out["registrar"] == "RESERVED-IANA"
    assert (out["created"], out["expires"]) == ("1995-08-14", "2027-08-13")
    assert out["nameservers"] == ["a.iana-servers.net"]
    assert calls[0][0] == "https://rdap.org/domain/example.com"


def test_tls_never_raises_on_an_unreachable_host(monkeypatch, infosec):
    def boom(*a, **k):
        raise OSError("unreachable")
    monkeypatch.setattr(C.socket, "create_connection", boom)
    out = infosec.check("tls", "example.com", port=8443)
    assert (out["host"], out["port"]) == ("example.com", 8443)
    assert "OSError" in out["error"]


def test_no_module_reaches_500_lines():
    import pathlib
    root = pathlib.Path(C.__file__).parent
    for f in root.glob("*.py"):
        assert len(f.read_text(encoding="utf-8").splitlines()) < 500, f.name
