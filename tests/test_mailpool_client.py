"""Mailpool client contract (v1, `X-Api-Authorization`, cold-email infrastructure).

Mocks `requests.Session.request`: verbs and paths, auth header, bounds, retries
on reads only — and Mailpool's own traps, each locked by a test that would fail
if the fix were removed:

- no mailbox credential ever leaves the client, at any depth (spam checks embed
  a full mailbox), error bodies included; a mailbox is an allowlist;
- redirects are never followed (the key header would go with them);
- every list sends `limit` and `offset`, even when the caller omits them;
- `update_domain_dns` replaces the whole set: it re-reads `expected`, refuses a
  set that loses an MX, SPF or DKIM, reads back and restores on a loss;
- ids placed in the path are positive integers only.
"""
from __future__ import annotations

import pytest

from oto.tools.common.errors import UpstreamHTTPError
from oto.tools import mailpool as mp_pkg
from oto.tools.mailpool import MailpoolCreateRefused
from oto.tools.mailpool import client as mp


class _Resp:
    def __init__(self, status_code: int = 200, body=None, headers=None, raw=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.content = b"x" if raw is None else raw
        self.text = str(self._body) if raw is None else raw.decode()
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {"calls": [], "responses": []}

    def fake_request(self, method, url, **kwargs):
        seen.update(method=method, url=url, kwargs=kwargs,
                    headers=dict(self.headers))
        seen["calls"].append((method, url))
        seen.setdefault("all", []).append(kwargs)
        if seen["responses"]:
            return seen["responses"].pop(0)
        return _Resp(200)

    monkeypatch.setattr(mp.requests.Session, "request", fake_request)
    seen["slept"] = []
    monkeypatch.setattr(mp.time, "sleep", seen["slept"].append)
    return seen


@pytest.fixture()
def cli():
    return mp.MailpoolClient(api_key="mp_test")


BASE = "https://app.mailpool.io/v1/api"

MAILBOX = {
    "id": 5301, "email": "anna@example-outreach.com", "type": "shared",
    "status": "active", "password": "p1", "imapHost": "imap.example.net",
    "imapPassword": "p2", "smtpPassword": "p3", "secret": "s1",
    "admin": {"email": "admin@example-outreach.com", "password": "p4", "secret": "s2"},
    "domain": {"id": 1201, "domain": "example-outreach.com"},
}


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_requires_key():
    from oto.tools.common.credentials import MissingCredential
    with pytest.raises(MissingCredential):
        mp.MailpoolClient(api_key=None)


def test_auth_header(capture, cli):
    cli.get_subscription_slots()
    assert capture["headers"]["X-Api-Authorization"] == "mp_test"
    assert capture["url"] == f"{BASE}/subscriptions/slots"


SECRET_VALUES = {"p1", "p2", "p3", "p4", "s1", "s2"}


def _values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _values(v)
    else:
        yield obj


@pytest.mark.parametrize("call,body,mailbox_at", [
    (lambda c: c.list_mailboxes(), {"data": [MAILBOX], "total": 1}, lambda o: o["data"][0]),
    (lambda c: c.get_mailbox(5301), MAILBOX, lambda o: o),
    (lambda c: c.get_spam_check(880011), {"id": 1, "mailbox": MAILBOX}, lambda o: o["mailbox"]),
    (lambda c: c.create_spam_check(5301), {"id": 1, "mailbox": MAILBOX}, lambda o: o["mailbox"]),
    (lambda c: c.list_spam_checks(), {"data": [{"id": 1, "mailbox": MAILBOX}]},
     lambda o: o["data"][0]["mailbox"]),
])
def test_no_credential_is_ever_returned(capture, cli, call, body, mailbox_at):
    capture["responses"].append(_Resp(200, body))
    out = call(cli)
    assert not SECRET_VALUES & set(_values(out))
    assert [k for k in _keys(out) if mp.is_secret_key(k)] == []
    box = mailbox_at(out)
    assert box["admin"] == {"email": "admin@example-outreach.com"}
    assert box["imapHost"] == "imap.example.net"


def test_mailbox_is_an_allowlist():
    out = mp.project_mailbox({**MAILBOX, "newField": "x", "recoveryHint": "y"})
    assert "newField" not in out and "recoveryHint" not in out
    assert set(out) <= set(mp.MAILBOX_FIELDS) | {"admin"}


def test_strip_secrets_keeps_everything_else():
    assert mp.strip_secrets({"a": [{"b": 1, "smtpPassword": "x"}], "c": None}) == {
        "a": [{"b": 1}], "c": None}


@pytest.mark.parametrize("key", [
    "password", "imapPassword", "smtp_password", "SMTP_PASS", "pwd", "secret",
    "credentials", "dkimPrivateKey", "privateKey", "authCode", "auth-code",
    "auth_code", "apiKey", "accessKey", "token", "refreshToken",
    "recoveryCodes", "backupCodes", "otp", "totpSeed", "cookie", "sessionId",
    "appPassword",
])
def test_secret_keys_are_recognised(key):
    assert mp.is_secret_key(key)


@pytest.mark.parametrize("key", [
    "passed", "bypassWarmup", "tokenCount", "email", "imapHost", "status",
    "dailyLimit", "authenticated", "keyword", "score",
])
def test_ordinary_keys_are_kept(key):
    assert not mp.is_secret_key(key)


@pytest.mark.parametrize("call,path", [
    (lambda c: c.list_domains(), "/domains/"),
    (lambda c: c.list_mailboxes(), "/mailboxes"),
    (lambda c: c.list_spam_checks(), "/spam-checks/"),
    (lambda c: c.list_warmup_inboxes(), "/warmup"),
    (lambda c: c.list_available_warmup_inboxes(), "/warmup/available-inboxes"),
])
def test_lists_always_send_limit_and_offset(capture, cli, call, path):
    call(cli)
    assert capture["url"] == BASE + path
    assert capture["kwargs"]["params"] == {"limit": mp.DEFAULT_LIMIT, "offset": 0}


def test_mailboxes_domain_filter(capture, cli):
    cli.list_mailboxes(limit=10, offset=20, domain_id=1201)
    assert capture["kwargs"]["params"] == {"limit": 10, "offset": 20, "domainId": 1201}


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (10, -1), ("10", 0), (True, 0)])
def test_page_bounds(cli, limit, offset):
    with pytest.raises(ValueError):
        cli.list_domains(limit=limit, offset=offset)


@pytest.mark.parametrize("bad", [0, -3, "12", "../x", True, None])
def test_path_ids_are_positive_integers(cli, bad):
    with pytest.raises(ValueError):
        cli.get_domain(bad)


MX = {"type": "MX", "key": None, "value": "mx.example.net", "priority": 10}
SPF = {"type": "TXT", "key": None, "value": "v=spf1 include:_spf.example.net ~all"}
DKIM = {"type": "TXT", "key": "s1._domainkey", "value": "v=DKIM1; k=rsa; p=AAAA"}
SRV = {"type": "SRV", "key": "_sip._tcp", "value": "sip.example.net",
       "priority": 10, "port": 5060, "weight": 5}
CURRENT = [MX, SPF, DKIM]
TRACK = {"type": "CNAME", "key": "track", "value": "t.example.net"}


def _dns(capture, *reads):
    """Queue the reads of an update: the re-read of `expected`, then the read-back."""
    for r in reads:
        capture["responses"].append(_Resp(200, [dict(x) for x in r]))


def test_update_dns_rereads_writes_and_reads_back(capture, cli):
    new = CURRENT + [TRACK]
    capture["responses"] += [_Resp(200, list(CURRENT)), _Resp(200, {}),
                             _Resp(200, list(new))]
    out = cli.update_domain_dns(1201, new, expected=CURRENT)
    assert [m for m, _ in capture["calls"]] == ["GET", "PUT", "GET"]
    assert capture["calls"][1][1] == f"{BASE}/domains/1201/dns"
    assert capture["all"][1]["json"] == new
    assert out == new


def test_update_dns_refuses_when_the_set_changed(capture, cli):
    _dns(capture, CURRENT + [TRACK])
    with pytest.raises(ValueError, match="changed"):
        cli.update_domain_dns(1201, CURRENT, expected=CURRENT)
    assert [m for m, _ in capture["calls"]] == ["GET"]


@pytest.mark.parametrize("new,lost", [
    ([TRACK], "MX"),
    ([MX, DKIM], "SPF"),
    ([MX, SPF], "DKIM"),
])
def test_update_dns_refuses_losing_mx_spf_dkim(capture, cli, new, lost):
    _dns(capture, CURRENT)
    with pytest.raises(ValueError, match=lost):
        cli.update_domain_dns(1201, new, expected=CURRENT)
    assert "PUT" not in [m for m, _ in capture["calls"]]


def test_update_dns_allows_changing_a_protected_value(capture, cli):
    hard = {**SPF, "value": "v=spf1 include:_spf.example.net -all"}
    new = [MX, hard, DKIM]
    _dns(capture, CURRENT)
    capture["responses"].append(_Resp(200, {}))
    _dns(capture, new)
    assert cli.update_domain_dns(1201, new, expected=CURRENT) == new


def test_update_dns_removal_with_explicit_consent(capture, cli):
    _dns(capture, CURRENT)
    capture["responses"].append(_Resp(200, {}))
    _dns(capture, [MX, SPF])
    assert cli.update_domain_dns(1201, [MX, SPF], expected=CURRENT,
                                 allow_removing_protected=True) == [MX, SPF]


def test_update_dns_restores_when_a_record_is_missing(capture, cli):
    new = CURRENT + [TRACK]
    _dns(capture, CURRENT)
    capture["responses"].append(_Resp(200, {}))
    _dns(capture, CURRENT)                 # read-back: TRACK missing
    capture["responses"].append(_Resp(200, {}))
    _dns(capture, CURRENT)                 # read-back of the restore
    with pytest.raises(mp.MailpoolDnsWriteError) as e:
        cli.update_domain_dns(1201, new, expected=CURRENT)
    assert e.value.restored is True and e.value.missing == [TRACK]
    assert [m for m, _ in capture["calls"]] == ["GET", "PUT", "GET", "PUT", "GET"]
    assert capture["all"][3]["json"] == CURRENT
    assert "put back" in str(e.value)


def test_update_dns_reports_a_failed_restore(capture, cli):
    new = CURRENT + [TRACK]
    _dns(capture, CURRENT)
    capture["responses"].append(_Resp(200, {}))
    _dns(capture, [TRACK])                 # read-back lost everything else
    capture["responses"].append(_Resp(500, {"message": "down"}))
    with pytest.raises(mp.MailpoolDnsWriteError) as e:
        cli.update_domain_dns(1201, new, expected=CURRENT)
    assert e.value.restored is False and isinstance(e.value.restore_error, UpstreamHTTPError)
    assert "unknown state" in str(e.value)


def test_srv_records_round_trip(capture, cli):
    new = CURRENT + [SRV]
    _dns(capture, CURRENT)
    capture["responses"].append(_Resp(200, {}))
    _dns(capture, new)
    assert cli.update_domain_dns(1201, new, expected=CURRENT) == new
    assert capture["all"][1]["json"][-1] == SRV


@pytest.mark.parametrize("records", [
    [],
    "MX",
    [{"type": "XX", "value": "v"}],
    [{"type": "A", "value": ""}],
    [{"type": "A", "value": "1.2.3.4", "ttl": 60}],
    [{"type": "A", "key": "a b", "value": "1.2.3.4"}],
    [{"type": "A", "key": "../x", "value": "1.2.3.4"}],
    [{"type": "MX", "value": "mx.example.net"}],
    [{"type": "MX", "value": "mx.example.net", "priority": True}],
    [{"type": "MX", "value": "mx.example.net", "priority": 70000}],
    [{"type": "SRV", "key": "_sip._tcp", "value": "s.example.net", "priority": 1}],
    [{"type": "A", "value": "1.2.3.4", "port": 80}],
])
def test_update_dns_refuses_bad_sets(capture, cli, records):
    with pytest.raises(ValueError):
        cli.update_domain_dns(1201, records, expected=CURRENT)
    assert capture["calls"] == []


def test_update_dns_requires_expected(cli):
    with pytest.raises(TypeError):
        cli.update_domain_dns(1201, CURRENT)


@pytest.mark.parametrize("body", [{}, {"data": []}, ["x"]])
def test_dns_read_that_is_not_a_record_list_is_an_error(capture, cli, body):
    capture["responses"].append(_Resp(200, body))
    with pytest.raises(UpstreamHTTPError) as e:
        cli.get_domain_dns(1201)
    assert e.value.status_code == 502


def test_dmarc_and_redirect_bodies(capture, cli):
    cli.set_dmarc_email(1201, "dmarc@example.com")
    assert capture["kwargs"]["json"] == {"email": "dmarc@example.com"}
    cli.set_dmarc_policy(1201, "reject")
    assert capture["url"] == f"{BASE}/domains/1201/dmarc-policy"
    cli.set_redirect_url(1201, " example.com ")
    assert capture["kwargs"]["json"] == {"url": "example.com"}
    with pytest.raises(ValueError):
        cli.set_dmarc_policy(1201, "strict")
    for bad in ("not-an-email", "dmarc@corp", "x@y.test; p=none", "a@b.test,c@d.test",
                 "a b@c.test"):
        with pytest.raises(ValueError):
            cli.set_dmarc_email(1201, bad)


def test_domain_lookups(capture, cli):
    cli.get_domain_info(" Example-Outreach.com ")
    assert capture["kwargs"]["params"] == {"domain": "example-outreach.com"}
    cli.get_domain_suggestions("example", limit=5)
    assert capture["kwargs"]["params"] == {"domain": "example", "limit": 5, "offset": 0}
    cli.check_microsoft_365_availability("example-outreach.com")
    assert capture["method"] == "POST"
    with pytest.raises(ValueError):
        cli.get_domain_info("nodot")
    for bad in ("exa mple", "", "a/b", "x?y"):
        with pytest.raises(ValueError):
            cli.get_domain_suggestions(bad)


def test_reads_are_retried_writes_are_not(capture, cli):
    capture["responses"] += [_Resp(503), _Resp(200, {"ok": 1})]
    assert cli.get_domain(1201) == {"ok": 1}
    assert len(capture["calls"]) == 2

    capture["calls"].clear()
    capture["responses"] += [_Resp(503, {"message": "down"})]
    with pytest.raises(UpstreamHTTPError) as e:
        cli.create_spam_check(5301)
    assert e.value.status_code == 503
    assert len(capture["calls"]) == 1


def test_upstream_error_is_typed(capture, cli):
    capture["responses"].append(_Resp(403, {"message": "not enabled for this workspace"}))
    with pytest.raises(UpstreamHTTPError) as e:
        cli.check_google_workspace_availability("example-outreach.com")
    assert e.value.status_code == 403


@pytest.mark.parametrize("status", [301, 302, 307, 308, 300, 304])
def test_redirects_are_never_followed(capture, cli, status):
    capture["responses"].append(_Resp(status, headers={"Location": "https://evil.example.net/"}))
    with pytest.raises(UpstreamHTTPError) as e:
        cli.set_redirect_url(1201, "example.com")
    assert e.value.status_code == 502
    assert capture["kwargs"]["allow_redirects"] is False
    assert len(capture["calls"]) == 1


def test_error_body_loses_its_credentials(capture, cli):
    capture["responses"].append(_Resp(400, {"message": "bad", "mailbox": MAILBOX}))
    with pytest.raises(UpstreamHTTPError) as e:
        cli.create_spam_check(5301)
    assert not SECRET_VALUES & set(_values(e.value.body))
    assert not any(v in str(e.value) for v in ("'p1'", "'p2'", "'s1'"))


def test_non_json_success_is_an_error(capture, cli):
    capture["responses"].append(_Resp(200, ValueError("html"), raw=b"<html>proxy</html>"))
    with pytest.raises(UpstreamHTTPError) as e:
        cli.get_domain(1201)
    assert e.value.status_code == 502


def test_retry_after_is_respected(capture, cli):
    capture["responses"] += [_Resp(429, headers={"Retry-After": "3"}), _Resp(200, {"ok": 1})]
    assert cli.get_domain(1201) == {"ok": 1}
    assert capture["slept"] == [3.0]


def test_long_retry_after_is_raised_within_the_budget(capture, cli):
    capture["responses"] += [_Resp(429, {"message": "slow down"},
                                   headers={"Retry-After": "120"})]
    with pytest.raises(UpstreamHTTPError) as e:
        cli.get_domain(1201)
    assert e.value.status_code == 429
    assert capture["slept"] == [] and len(capture["calls"]) == 1


def test_retry_after_http_date(capture, cli):
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    soon = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=2), usegmt=True)
    capture["responses"] += [_Resp(503, headers={"Retry-After": soon}), _Resp(200, {"ok": 1})]
    assert cli.get_domain(1201) == {"ok": 1}
    assert len(capture["slept"]) == 1 and 0 <= capture["slept"][0] <= 2


def test_every_attempt_has_a_bounded_timeout(capture, cli):
    cli.get_domain(1201)
    connect, read = capture["kwargs"]["timeout"]
    assert connect <= mp.HTTP_TIMEOUT[0] and read <= mp.HTTP_TIMEOUT[1]


# --- mailbox creation --------------------------------------------------------

SLOTS = {"mailboxes": {"google": 9, "shared": 4, "outlook": 4},
         "slots": {"google": 17, "shared": 10, "outlook": 4}}
DOMAINS = {"data": [
    {"id": 38494, "domain": "new-outreach.com", "type": "unassigned", "status": "active"},
    {"id": 38495, "domain": "pending-outreach.com", "type": "unassigned", "status": "pending"},
    {"id": 32057, "domain": "old-outreach.com", "type": "google", "status": "active"},
], "total": 3}
NEW = {"email": "Anna@new-outreach.com", "firstName": "Anna", "lastName": "Lee",
       "type": "google", "forwardTo": "anna@example.com"}


def _plan_responses(capture, existing=()):
    capture["responses"] += [
        _Resp(200, SLOTS), _Resp(200, DOMAINS),
        _Resp(200, {"data": [{"id": 1, "email": e} for e in existing],
                    "total": len(existing)})]


def test_plan_sends_nothing_and_reports_slots(capture, cli):
    _plan_responses(capture)
    plan = cli.plan_mailboxes([NEW])
    assert [m for m, _ in capture["calls"]] == ["GET", "GET", "GET"]
    assert plan["mailboxes"][0]["email"] == "anna@new-outreach.com"
    assert plan["slots"] == {"google": {"free": 8, "used_by_this": 1, "left_after": 7}}
    assert plan["domains"]["new-outreach.com"] == {"id": 38494, "type": "unassigned",
                                                   "becomes": "google"}


def test_create_posts_the_array_and_strips_credentials(capture, cli):
    _plan_responses(capture)
    capture["responses"].append(_Resp(200, {"emails": [dict(MAILBOX, type="google")]}))
    out = cli.create_mailboxes([NEW])
    assert capture["calls"][-1] == ("POST", f"{BASE}/mailboxes")
    assert capture["kwargs"]["json"] == [{
        "email": "anna@new-outreach.com", "firstName": "Anna", "lastName": "Lee",
        "type": "google", "forwardTo": "anna@example.com"}]
    keys = set(_keys(out["created"]))
    assert not keys & {"password", "imapPassword", "smtpPassword", "secret"}
    assert out["created"][0]["admin"] == {"email": "admin@example-outreach.com"}


def test_create_is_never_retried(capture, cli):
    _plan_responses(capture)
    capture["responses"].append(_Resp(503, {"message": "down"}))
    with pytest.raises(UpstreamHTTPError):
        cli.create_mailboxes([NEW])
    assert [m for m, _ in capture["calls"]].count("POST") == 1


@pytest.mark.parametrize("mailboxes,existing,needle", [
    ([dict(NEW, email=f"a{i}@new-outreach.com") for i in range(9)], (), "8 google slot"),
    ([dict(NEW, email="a@pending-outreach.com")], (), "not active"),
    ([dict(NEW, email="a@old-outreach.com", type="shared")], (), "google domain"),
    ([dict(NEW, email="a@elsewhere.com")], (), "not a domain"),
    ([NEW], ("anna@new-outreach.com",), "already exists"),
    ([NEW, dict(NEW, email="b@new-outreach.com", type="shared")], (), "one mailbox type"),
])
def test_plan_refuses_before_sending(capture, cli, mailboxes, existing, needle):
    _plan_responses(capture, existing)
    capture["responses"].append(_Resp(200, {"data": [], "total": 0}))
    with pytest.raises(MailpoolCreateRefused) as e:
        cli.create_mailboxes(mailboxes)
    assert needle in str(e.value)
    assert "POST" not in [m for m, _ in capture["calls"]]


def test_no_slot_of_a_type_is_refused(capture, cli):
    _plan_responses(capture)
    with pytest.raises(ValueError, match="0 outlook slot"):
        cli.plan_mailboxes([dict(NEW, type="outlook")])


@pytest.mark.parametrize("bad", [
    [], {}, "x", [dict(NEW, type="private")], [dict(NEW, password="x")],
    [dict(NEW, firstName="")], [dict(NEW, email="no-at")], [NEW, NEW],
    [dict(NEW, forwardTo="a@b.com, c@d.com")],
    [dict(NEW, email=f"a{i}@new-outreach.com") for i in range(51)],
])
def test_create_input_is_checked_offline(capture, cli, bad):
    with pytest.raises(ValueError):
        cli.plan_mailboxes(bad)
    assert capture["calls"] == []


TWO_DOMAINS = {"data": DOMAINS["data"] + [
    {"id": 38497, "domain": "other-outreach.com", "type": "unassigned", "status": "active"}],
    "total": 4}
ON_OTHER = dict(NEW, email="anna@other-outreach.com", type="shared")


def _two_domain_plan(capture):
    capture["responses"] += [_Resp(200, SLOTS), _Resp(200, TWO_DOMAINS),
                             _Resp(200, {"data": [], "total": 0}),
                             _Resp(200, {"data": [], "total": 0})]


def test_create_sends_one_call_per_domain(capture, cli):
    _two_domain_plan(capture)
    capture["responses"] += [_Resp(200, {"emails": [dict(MAILBOX, id=1)]}),
                             _Resp(200, {"emails": [dict(MAILBOX, id=2)]})]
    out = cli.create_mailboxes([NEW, ON_OTHER])
    posts = [kw["json"] for kw in capture["all"] if isinstance(kw.get("json"), list)]
    assert [[m["email"] for m in p] for p in posts] == [
        ["anna@new-outreach.com"], ["anna@other-outreach.com"]]
    assert [m["id"] for m in out["created"]] == [1, 2]


def test_create_failing_after_a_domain_landed_is_partial(capture, cli):
    _two_domain_plan(capture)
    capture["responses"] += [_Resp(200, {"emails": [dict(MAILBOX, id=1)]}),
                             _Resp(400, {"message": "Mailboxes count limit exceeded"})]
    with pytest.raises(mp_pkg.MailpoolCreatePartial) as e:
        cli.create_mailboxes([NEW, ON_OTHER])
    assert e.value.done == ["new-outreach.com"] and e.value.not_done == ["other-outreach.com"]
    assert [m["id"] for m in e.value.created] == [1]
    assert not set(_keys(e.value.created)) & {"password", "secret"}


def test_a_second_domain_accepted_without_a_list_is_partial(capture, cli):
    """The second POST is accepted but its answer lists no mailbox: the first domain's
    mailboxes are kept for the caller, and the second domain is named as uncertain."""
    _two_domain_plan(capture)
    capture["responses"] += [_Resp(200, {"emails": [dict(MAILBOX, id=1)]}), _Resp(200, {"ok": True})]
    with pytest.raises(mp_pkg.MailpoolCreatePartial) as e:
        cli.create_mailboxes([NEW, ON_OTHER])
    assert e.value.done == ["new-outreach.com"] and e.value.not_done == ["other-outreach.com"]
    assert [m["id"] for m in e.value.created] == [1]
    assert isinstance(e.value.cause, RuntimeError)
    assert "accepted the mailboxes of other-outreach.com" in str(e.value.cause)
    assert "other-outreach.com" in str(e.value)


# --- exports -----------------------------------------------------------------

BATCH = {"id": 42, "createdAt": "2026-11-08T10:00:00Z", "status": "in_progress",
         "platform": "lemlist", "token": "t1",
         "exports": [{"id": 101, "mailboxId": 5301, "mailboxEmail": "anna@example-outreach.com",
                      "status": "in_progress", "domainType": "shared", "password": "p"}],
         "errors": []}


def test_credentials_status_never_serves_the_login(capture, cli):
    capture["responses"].append(_Resp(200, {"platform": "lemlist", "createdAt": "2026-11-08",
                                            "data": {"email": "a@b.com", "apiKey": "****abcd"}}))
    assert cli.export_credentials_status("lemlist") == {
        "platform": "lemlist", "saved": True, "saved_at": "2026-11-08"}
    capture["responses"].append(_Resp(404, {"message": "Credentials for platform lemlist not found"}))
    assert cli.export_credentials_status("lemlist")["saved"] is False


def test_export_posts_ids_after_checks(capture, cli):
    capture["responses"] += [_Resp(200, {"platform": "lemlist", "createdAt": "x", "data": {}}),
                             _Resp(200, {"data": [MAILBOX], "total": 1}), _Resp(200, BATCH)]
    out = cli.export_mailboxes("lemlist", [5301])
    assert capture["calls"][-1] == ("POST", f"{BASE}/exports/mailboxes")
    assert capture["kwargs"]["json"] == {"platform": "lemlist", "mailboxIds": [5301]}
    assert "token" not in out and "password" not in out["exports"][0]


@pytest.mark.parametrize("cred,mailboxes,needle", [
    (_Resp(404, {"message": "not found"}), [MAILBOX], "no lemlist login"),
    (_Resp(200, {"data": {}}), [dict(MAILBOX, status="pending")], "not active"),
    (_Resp(200, {"data": {}}), [], "not in this workspace"),
])
def test_export_refuses_before_sending(capture, cli, cred, mailboxes, needle):
    capture["responses"] += [cred, _Resp(200, {"data": mailboxes, "total": len(mailboxes)})]
    with pytest.raises(mp_pkg.MailpoolExportRefused, match=needle):
        cli.export_mailboxes("lemlist", [5301])
    assert "POST" not in [m for m, _ in capture["calls"]]


@pytest.mark.parametrize("platform,ids", [("gmail", [1]), ("lemlist", []), ("lemlist", [1, 1]),
                                          ("lemlist", ["1"]), ("lemlist", "1")])
def test_export_input_is_checked_offline(capture, cli, platform, ids):
    with pytest.raises(ValueError):
        cli.export_mailboxes(platform, ids)
    assert capture["calls"] == []


def test_batches_are_allowlisted(capture, cli):
    capture["responses"] += [_Resp(200, {"data": [BATCH], "total": 1}), _Resp(200, BATCH)]
    assert "token" not in cli.list_export_batches()["data"][0]
    assert cli.get_export_batch(42)["exports"][0]["status"] == "in_progress"
    assert capture["calls"][-1] == ("GET", f"{BASE}/exports/batches/42")


def test_une_creation_acceptee_sans_liste_en_retour_leve_au_lieu_de_rendre_vide(monkeypatch):
    """Mailpool a accepté le POST mais sa réponse ne liste aucune boîte : on ne rend pas
    `created: []` (les boîtes existent peut-être), on le dit."""
    c = mp.MailpoolClient(api_key="k")
    monkeypatch.setattr(c, "plan_mailboxes", lambda boxes: {"mailboxes": [
        {"email": "anna@exemple.test", "firstName": "A", "lastName": "B", "type": "google"}]})
    monkeypatch.setattr(c, "_request", lambda *a, **kw: {"ok": True})
    with pytest.raises(RuntimeError, match="exemple.test"):
        c.create_mailboxes([{}])
