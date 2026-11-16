"""Le moteur de la lib (`oto.connectors.engine`) : ce que les cas communs ne disent pas, contre un faux tiers.

Les cas communs (`connectors/_cases/`) se jouent contre le moteur dans leurs propres tests (`test_body_cases.py`,
`test_nested_body_cases.py`, `test_auth_cases.py`, `test_error_cases.py`, `test_strip_cases.py`). Ici : ce qu'il
refuse avant tout envoi, la query et les en-têtes, l'adresse et ses réglages, le transport (redirection, délai, corps
illisible, 429, rythme), la pagination, les contrôles, le secret masqué, et des requêtes de vraies descriptions du dépôt.
Aucun réseau : chaque requête part vers `engine_fake.Vendor`, la résolution d'un hôte est remplacée.
"""
from __future__ import annotations

import copy
import socket

import pytest
import requests

import engine_fake
from engine_fake import answer, play, sent
from oto.connectors import descriptions, engine
from oto.connectors.engine import run, transport
from oto.tools.common import url_guard

KEY = "test-key-not-real-0123456789"
BASE = "https://api.example.test/v1"


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(transport, "_sleep", waits.append)
    return waits


def _resolve(address: str):
    def resolver(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]
    return resolver


def connector(**extra) -> dict:
    block = {"name": "engine_cases", "label": "Engine cases", "base_url": BASE,
             "auth": {"kind": "bearer", "token": "api_key"},
             "credential": [{"name": "api_key", "label": "API key", "secret": True}],
             "errors": [{"status": 404, "code": "not_found", "retryable": False, "message": "Not found."}]}
    block.update(extra)
    return block


def described(*functions: dict, **extra) -> dict:
    return {"connector": connector(**extra), "functions": list(functions)}


def function(name: str = "get_records", **extra) -> dict:
    fn = {"name": name, "class": "read", "call": {"method": "GET", "path": "/records"}}
    fn.update(extra)
    return fn


def one(vendor: engine_fake.Vendor) -> dict:
    (request,) = vendor.requests
    return sent(request, BASE)


# --- ce que le moteur refuse, avant tout envoi -------------------------------------------------------------------------

@pytest.mark.parametrize("name, fn_name, form", [
    ("pennylane_firm", "upload_dms_file", "call.files"),
    ("aircall", "get_call_recording", "output.file"),
    ("gmail", "get_profile", "auth oauth2_user"),
    ("linkedin_unipile", "get_me", "account_identity"),
], ids=["files", "output_file", "oauth2_user", "account_identity"])
def test_a_form_the_engine_does_not_run_is_refused_before_anything_is_sent(name, fn_name, form):
    vendor = engine_fake.Vendor()
    with pytest.raises(engine.Unsupported, match=form.replace(".", r"\.")):
        engine.call(descriptions.load(name), fn_name, {}, credential={}, session=engine_fake.session(vendor))
    assert vendor.requests == []


def test_a_background_function_is_refused():
    with pytest.raises(engine.Unsupported, match="background"):
        play(described(function(background=True, timeout_s=300)), "get_records", {})


def test_a_function_without_call_is_refused():
    with pytest.raises(engine.Unsupported, match="handwritten"):
        play(described({"name": "by_hand", "class": "read"}), "by_hand", {})


def test_a_missing_credential_field_is_named_and_nothing_is_sent():
    vendor = engine_fake.Vendor()
    with pytest.raises(engine.MissingCredential, match="api_key"):
        engine.call(described(function()), "get_records", {}, credential={"api_key": ""},
                    session=engine_fake.session(vendor))
    assert vendor.requests == []


def test_a_missing_path_argument_is_refused():
    with pytest.raises(engine.InvalidArguments, match="argument id is required"):
        play(described(function(call={"method": "GET", "path": "/records/{id}"})), "get_records", {})


# --- la query, les en-têtes, l'encodage --------------------------------------------------------------------------------

@pytest.mark.parametrize("arrays, query", [
    ("repeat", [["tag", "a"], ["tag", "b c"]]),
    ("brackets", [["tag[]", "a"], ["tag[]", "b c"]]),
    ("comma", [["tag", "a,b c"]]),
], ids=["repeat", "brackets", "comma"])
def test_a_list_in_the_query_follows_query_arrays(arrays, query):
    fn = function(call={"method": "GET", "path": "/records", "query": {"tag": "tags"}})
    _, vendor = play(described(fn, query_arrays=arrays), "get_records", {"tags": ["a", "b c"]})
    assert one(vendor)["query"] == query


def test_a_list_in_the_query_without_query_arrays_is_refused():
    fn = function(call={"method": "GET", "path": "/records", "query": {"tag": "tags"}})
    with pytest.raises(engine.InvalidArguments, match="query_arrays"):
        play(described(fn), "get_records", {"tags": ["a"]})


def test_constants_then_arguments_then_authentication():
    fn = function(call={"method": "GET", "path": "/records", "query": {"q": "q", "filter": "filter", "n": "n"},
                        "headers": {"X-Request-Key": "request_key", "Authorization": "forged"},
                        "constants": {"query": {"view": "full", "draft": True}, "headers": {"X-Mode": "strict"}},
                        "encode": {"filter": "json"}})
    description = described(fn, headers={"Api-Version": "2026-01-01"})
    _, vendor = play(description, "get_records",
                     {"q": "é & co", "filter": {"a": [1, "é"]}, "n": 2.0, "request_key": 7, "forged": "x"},
                     credential={"api_key": KEY})
    parts = one(vendor)
    assert parts["query"] == [["view", "full"], ["draft", "true"], ["q", "é & co"], ["filter", '{"a":[1,"é"]}'],
                              ["n", "2"]]
    assert parts["headers"]["api-version"] == "2026-01-01"
    assert parts["headers"]["x-mode"] == "strict"
    assert parts["headers"]["x-request-key"] == "7"
    assert parts["headers"]["authorization"] == f"Bearer {KEY}"
    assert parts["headers"]["accept"] == "application/json"
    assert parts["body"] is None and "content-type" not in parts["headers"]


def test_an_absent_argument_places_nothing_and_a_null_is_sent():
    fn = function(call={"method": "PATCH", "path": "/records", "query": {"q": "q"}, "body": {"name": "name", "note": "note"}})
    _, vendor = play(described(fn), "get_records", {"note": None})
    parts = one(vendor)
    assert parts["query"] == [] and parts["body"] == {"note": None}
    assert parts["headers"]["content-type"] == "application/json"


def test_an_api_key_in_the_query_is_sent_last_and_masked_everywhere(monkeypatch):
    fn = function(call={"method": "GET", "path": "/records", "query": {"q": "q"}})
    auth = {"kind": "api_key", "in": "query", "name": "key", "key": "api_key"}
    description = described(fn, auth=auth, errors=[])
    with pytest.raises(engine.UpstreamHTTPError) as raised:
        play(description, "get_records", {"q": "x"}, answer(500, {"error": f"bad key {KEY}", KEY: 1}),
             credential={"api_key": KEY})
    error = raised.value
    assert KEY not in str(error) and KEY not in repr(error.body) and error.url == f"{BASE}/records"
    assert error.body == {"error": "bad key [secret]", "[secret]": 1}
    vendor = engine_fake.Vendor(requests.ConnectionError(f"failed to reach {BASE}/records?q=x&key={KEY}"))
    with pytest.raises(engine.Unreachable) as raised:
        engine.call(description, "get_records", {"q": "x"}, credential={"api_key": KEY}, session=engine_fake.session(vendor))
    assert KEY not in str(raised.value) and raised.value.__cause__ is None and raised.value.__suppress_context__
    assert sent(vendor.requests[0], BASE)["query"] == [["q", "x"], ["key", KEY]]


# --- l'adresse et les réglages -----------------------------------------------------------------------------------------

def test_an_address_by_setting_takes_the_default_of_its_choice():
    description = descriptions.load("typeform")
    fn = description["functions"][0]
    args = copy.deepcopy(fn["examples"][0]["input"])
    _, vendor = play(description, fn["name"], args, answer(200, {"items": []}))
    assert sent(vendor.requests[0])["url"].startswith("https://api.typeform.com/")
    _, vendor = play(description, fn["name"], args, answer(200, {"items": []}), settings={"region": "eu"})
    assert sent(vendor.requests[0])["url"].startswith("https://api.eu.typeform.com/")


@pytest.mark.parametrize("settings, reason", [
    ({}, "is not set"),
    ({"environment": "staging"}, "must be one of"),
    ({"environment": "sandbox", "region": "eu"}, "is not a setting"),
], ids=["missing", "not_a_choice", "unknown"])
def test_a_setting_missing_or_invalid_is_refused(settings, reason):
    description = descriptions.load("yousign")
    fn = description["functions"][0]
    with pytest.raises(engine.AccountSettingError, match=reason):
        play(description, fn["name"], fn["examples"][0]["input"], settings=settings)


def test_an_address_typed_by_an_administrator_is_guarded_before_each_request(monkeypatch):
    setting = {"name": "server", "label": "Server", "type": "url"}
    description = described(function(), base_url="{server}/api", settings=[setting])
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _resolve("10.0.0.7"))
    vendor = engine_fake.Vendor()
    with pytest.raises(engine.AddressRefused) as raised:
        engine.call(description, "get_records", {}, credential={"api_key": KEY},
                    settings={"server": "https://pbx.example.test"}, session=engine_fake.session(vendor))
    assert "pbx" not in str(raised.value) and vendor.requests == []
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _resolve("93.184.216.34"))
    _, vendor = play(description, "get_records", {}, credential={"api_key": KEY},
                     settings={"server": "https://pbx.example.test"})
    assert sent(vendor.requests[0])["url"] == "https://pbx.example.test/api/records"
    with pytest.raises(engine.AccountSettingError, match="must start with https"):
        play(description, "get_records", {}, settings={"server": "http://pbx.example.test"})


# --- le transport ------------------------------------------------------------------------------------------------------

def test_a_redirect_is_never_followed():
    description = described(function(), errors=[])
    with pytest.raises(engine.UpstreamHTTPError) as raised:
        play(description, "get_records", {}, answer(302, headers={"location": "https://elsewhere.example.test/"}))
    assert raised.value.status_code == 302


def test_the_delay_is_the_function_s_then_the_connector_s_then_thirty_seconds():
    _, vendor = play(described(function()), "get_records", {})
    assert vendor.options[0]["timeout"] == 30
    _, vendor = play(described(function(), timeout_s=12), "get_records", {})
    assert vendor.options[0]["timeout"] == 12
    _, vendor = play(described(function(timeout_s=90), timeout_s=12), "get_records", {})
    assert vendor.options[0]["timeout"] == 90


def test_a_delay_passed_is_unreachable_and_says_so():
    vendor = engine_fake.Vendor(requests.ReadTimeout("read timed out"))
    with pytest.raises(engine.Unreachable, match="did not answer within 30 s") as raised:
        engine.call(described(function()), "get_records", {}, credential={"api_key": KEY},
                    session=engine_fake.session(vendor))
    assert raised.value.timed_out


@pytest.mark.parametrize("spec, message", [
    (answer(200, text="<html>maintenance</html>"), "not JSON"),
    (answer(200, text="x" * 64), "too large"),
], ids=["not_json", "too_large"])
def test_a_success_that_cannot_be_read_is_named(monkeypatch, spec, message):
    monkeypatch.setattr(transport, "MAX_ANSWER_BYTES", 32)
    with pytest.raises(engine.UnreadableAnswer, match=message):
        play(described(function()), "get_records", {}, spec)


def test_an_empty_success_is_no_content():
    result, _ = play(described(function()), "get_records", {}, answer(204))
    assert result == {}


def test_a_429_is_replayed_after_its_wait_twice_at_most(_no_wait):
    description = described(function(), errors=[])
    result, vendor = play(description, "get_records", {}, answer(429, headers={"retry-after": "2"}), answer(200, {"id": 1}))
    assert result == {"id": 1} and len(vendor.requests) == 2 and _no_wait == [2.0]
    with pytest.raises(engine.UpstreamHTTPError) as raised:
        play(description, "get_records", {}, *[answer(429)] * 3)
    assert raised.value.status_code == 429
    with pytest.raises(engine.UpstreamHTTPError):
        play(description, "get_records", {}, answer(429, headers={"retry-after": "60"}))


def test_the_pace_of_the_vendor_holds_per_credential(monkeypatch, _no_wait):
    now = [100.0]
    monkeypatch.setattr(transport, "_clock", lambda: now[0])

    def wait(seconds):
        _no_wait.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(transport, "_sleep", wait)
    monkeypatch.setattr(transport, "PACE", transport._Pace())
    description = described(function(), rate_limit={"requests": 2, "seconds": 1})
    for _ in range(3):
        play(description, "get_records", {}, credential={"api_key": KEY})
    assert _no_wait == [1.0]
    play(description, "get_records", {}, credential={"api_key": "another-key-not-real"})
    assert _no_wait == [1.0]


def test_the_engine_s_own_session_reads_nothing_of_the_environment():
    assert run._session().trust_env is False


# --- la pagination -----------------------------------------------------------------------------------------------------

PAGED = function("list_records", call={"method": "GET", "path": "/records", "query": {"cursor": "cursor", "limit": "limit"}},
                 pagination={"kind": "cursor", "request_param": "cursor", "next": "meta.next", "more": "meta.more",
                             "max_pages": 3},
                 output={"items": "data", "strip": ["secret"]})


def _page(ids, following, more=True):
    return answer(200, {"data": [{"id": i, "secret": "s"} for i in ids], "meta": {"next": following, "more": more}})


def test_one_page_without_all_pages_and_the_cursor_of_the_next():
    result, vendor = play(described(PAGED), "list_records", {"limit": 2}, _page([1, 2], "c2"))
    assert result == {"items": [{"id": 1}, {"id": 2}], "next_cursor": "c2", "pages": 1}
    assert one(vendor)["query"] == [["limit", "2"]]


def test_all_pages_follows_the_cursor_until_more_is_false():
    result, vendor = play(described(PAGED), "list_records", {"limit": 2, "all_pages": True},
                          _page([1, 2], "c2"), _page([3], "c3", more=False))
    assert result == {"items": [{"id": 1}, {"id": 2}, {"id": 3}], "next_cursor": None, "pages": 2}
    assert [sent(r)["query"] for r in vendor.requests] == [[["limit", "2"]], [["cursor", "c2"], ["limit", "2"]]]


def test_all_pages_is_bounded_by_max_pages():
    result, vendor = play(described(PAGED), "list_records", {"all_pages": True, "max_pages": 2},
                          _page([1], 2), _page([2], 3))
    assert result["pages"] == 2 and result["next_cursor"] == 3 and len(vendor.requests) == 2
    assert sent(vendor.requests[1])["query"] == [["cursor", "2"]]


def test_an_answer_without_its_list_is_never_read_as_empty():
    from oto.connectors.serve import ServeError
    with pytest.raises(ServeError, match="without its list data"):
        play(described(PAGED), "list_records", {}, answer(200, {"error": "x"}))


# --- les contrôles et les refus ----------------------------------------------------------------------------------------

BALANCED = function("post_entry", call={"method": "POST", "path": "/entries", "body": {"lines": "lines"}},
                    refusals=[{"code": "entry_unbalanced", "when": "debits differ from credits", "message": "Unbalanced."}],
                    checks=[{"kind": "equal_sums", "refusal": "entry_unbalanced", "items": "lines",
                             "fields": ["debit", "credit"]}])


def test_equal_sums_is_exact_and_refuses_before_anything_is_sent():
    lines = [{"debit": "700.10", "credit": "0"}, {"debit": "0.20", "credit": 700.3}]
    _, vendor = play(described(BALANCED), "post_entry", {"lines": lines})
    assert one(vendor)["body"] == {"lines": lines}
    vendor = engine_fake.Vendor()
    with pytest.raises(engine.Refused, match=r"Unbalanced\. Nothing was sent\. \(entry_unbalanced\)") as raised:
        engine.call(described(BALANCED), "post_entry", {"lines": [{"debit": "1.0", "credit": "1.01"}]},
                    credential={"api_key": KEY}, session=engine_fake.session(vendor))
    assert raised.value.status_code is None and vendor.requests == []
    with pytest.raises(engine.Refused):
        play(described(BALANCED), "post_entry", {"lines": [{"debit": True, "credit": True}]})


def test_expect_refuses_an_empty_answer():
    description = descriptions.load("pennylane")
    with pytest.raises(engine.Refused) as raised:
        play(description, "get_quote_pdf_link", {"quote_id": 1}, answer(200, {"id": 1, "public_file_url": None}))
    assert raised.value.code == "pdf_missing"


def test_a_refusal_of_the_function_comes_before_the_table_and_says_its_status():
    fn = function(refusals=[{"code": "record_gone", "when": 404, "message": "The record is gone."}])
    with pytest.raises(engine.Refused, match=r"The record is gone\. \(record_gone\)") as raised:
        play(described(fn), "get_records", {}, answer(404, {"message": "nope"}))
    assert (raised.value.status_code, raised.value.retryable, raised.value.credential_rejected) == (404, False, False)


# --- de vraies descriptions du dépôt -----------------------------------------------------------------------------------

def _bearer(description: dict) -> dict:
    """La description, son compte remplacé par un jeton porteur : la requête hors de l'authentification."""
    changed = copy.deepcopy(description)
    changed["connector"]["auth"] = {"kind": "bearer", "token": "token"}
    changed["connector"]["credential"] = [{"name": "token", "label": "Token", "secret": True}]
    return changed


def test_search_console_requests_out_of_its_authentication():
    description = _bearer(descriptions.load("google_search_console"))
    root = "https://searchconsole.googleapis.com"
    calls = {
        "list_sites": ({}, "GET", "/webmasters/v3/sites", [], None),
        "query_search_analytics": (
            {"site_url": "sc-domain:example.com", "start_date": "2026-10-01", "end_date": "2026-10-31",
             "dimensions": ["query", "page"], "row_limit": 100, "start_row": 100},
            "POST", "/webmasters/v3/sites/sc-domain%3Aexample.com/searchAnalytics/query", [],
            {"startDate": "2026-10-01", "endDate": "2026-10-31", "dimensions": ["query", "page"], "rowLimit": 100,
             "startRow": 100}),
        "inspect_url": ({"site_url": "https://www.example.com/", "inspection_url": "https://www.example.com/pricing"},
                        "POST", "/v1/urlInspection/index:inspect", [],
                        {"siteUrl": "https://www.example.com/", "inspectionUrl": "https://www.example.com/pricing"}),
        "list_sitemaps": ({"site_url": "https://www.example.com/", "sitemap_index": "https://www.example.com/s.xml"},
                          "GET", "/webmasters/v3/sites/https%3A%2F%2Fwww.example.com%2F/sitemaps",
                          [["sitemapIndex", "https://www.example.com/s.xml"]], None),
        "get_sitemap": ({"site_url": "sc-domain:example.com", "feedpath": "https://www.example.com/sitemap.xml"},
                        "GET", "/webmasters/v3/sites/sc-domain%3Aexample.com/sitemaps/https%3A%2F%2Fwww.example.com%2Fsitemap.xml",
                        [], None),
    }
    for name, (args, method, path, query, body) in calls.items():
        vendor = engine_fake.Vendor(answer(200, {}))
        engine.execute(description, name, args, credential={"token": "tok-not-real"}, session=engine_fake.session(vendor))
        parts = sent(vendor.requests[0], root)
        assert (parts["method"], parts["path"], parts["query"], parts["body"]) == (method, path, query, body), name
        assert parts["headers"]["authorization"] == "Bearer tok-not-real"


def test_search_console_refusals_follow_its_table():
    description = _bearer(descriptions.load("google_search_console"))
    args = {"site_url": "sc-domain:example.com", "inspection_url": "https://www.example.com/x"}
    with pytest.raises(engine.Refused) as raised:
        play(description, "inspect_url", args,
             answer(403, {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}], "message": "Quota"}}))
    assert (raised.value.code, raised.value.marks_account) == ("quota_exhausted", "quota_exhausted")
    with pytest.raises(engine.Refused) as raised:
        play(description, "inspect_url", args,
             answer(403, {"error": {"message": "User does not have sufficient permission for site"}}))
    assert raised.value.code == "property_not_shared"


def test_notion_sends_its_version_header_and_pages_by_its_body():
    description = descriptions.load("notion")
    page = {"results": [{"id": "p1"}], "next_cursor": "n2", "has_more": True}
    last = {"results": [{"id": "p2"}], "next_cursor": None, "has_more": False}
    result, vendor = play(description, "search_workspace", {"query": "roadmap", "all_pages": True},
                          answer(200, page), answer(200, last), credential={"token": "secret-token-not-real"})
    first, second = (sent(r, "https://api.notion.com/v1") for r in vendor.requests)
    assert first["headers"]["notion-version"] == "2025-09-03"
    assert first["body"] == {"query": "roadmap"} and second["body"] == {"query": "roadmap", "start_cursor": "n2"}
    assert result["pages"] == 2 and result["next_cursor"] is None


def test_serper_signs_with_its_key_header():
    description = descriptions.load("serper")
    fn = description["functions"][0]
    _, vendor = play(description, fn["name"], fn["examples"][0]["input"], answer(200, {"organic": []}),
                     credential={"api_key": KEY})
    parts = sent(vendor.requests[0], description["connector"]["base_url"])
    assert parts["headers"]["x-api-key"] == KEY and "authorization" not in parts["headers"]
