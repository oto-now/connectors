"""Contrat du client grec (registre GEMI + VIES) — sans réseau (`requests.Session.get` remplacé)."""
from __future__ import annotations

import pytest

from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.gr import GrClient
from oto.tools.gr import client as G


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code, self._body = status_code, body
        self.text, self.headers = str(body), {}

    def json(self):
        return self._body


_MATCH = {"co_name": "ACME A.E.", "title": "ACME", "arGemi": 123456789000, "afm": "094014201",
          "companyStatusId": 3, "companyStatus": "Active", "type": "AE"}


@pytest.fixture
def api(monkeypatch):
    state = {"calls": [], "routes": []}

    def get(self, url, **kwargs):
        state["calls"].append(url)
        for prefix, resp in state["routes"]:
            if url.startswith(prefix):
                return resp.pop(0) if isinstance(resp, list) else resp
        raise AssertionError(f"unexpected call {url}")
    monkeypatch.setattr(G.requests.Session, "get", get)
    monkeypatch.setattr(G.time, "sleep", lambda *_: None)
    return state


_AUTO = "https://publicity.businessportal.gr/api/autocomplete/"
_VIES = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/EL/vat/"


def _auto(matches):
    return _Resp(200, {"payload": {"autocomplete": matches}})


def test_a_single_match_is_normalised_and_enriched_by_vies(api):
    api["routes"] = [(_AUTO, _auto([_MATCH])),
                     (_VIES, _Resp(200, {"isValid": True, "address": "ODOS 1\nATHINA"}))]
    out = GrClient().lookup("EL094014201")
    assert api["calls"][0] == _AUTO + "094014201", "the EL prefix is stripped for the search"
    assert out["count"] == 1 and out["query"] == "EL094014201"
    row = out["results"][0]
    assert (row["name"], row["gemi"], row["vat"], row["active"]) == ("ACME A.E.", "123456789000", "EL094014201", True)
    assert (row["address"], row["vat_valid"]) == ("ODOS 1 ATHINA", True)


def test_a_name_is_url_encoded_and_several_matches_skip_vies(api):
    api["routes"] = [(_AUTO, _auto([_MATCH, {**_MATCH, "afm": "1"}]))]
    out = GrClient().lookup("acme a/e")
    assert api["calls"] == [_AUTO + "acme%20a%2Fe"]
    assert out["count"] == 2 and "vat_valid" not in out["results"][0]


def test_no_match(api):
    api["routes"] = [(_AUTO, _auto([]))]
    assert GrClient().lookup("nothing")["results"] == []


def test_a_vies_failure_is_named_not_read_as_invalid(api):
    api["routes"] = [(_AUTO, _auto([_MATCH])), (_VIES, _Resp(503, "down"))]
    row = GrClient().lookup("acme")["results"][0]
    assert "vat_valid" not in row and "VIES unavailable" in row["vat_check_error"]


def test_throttling_is_retried_then_raised(api):
    api["routes"] = [(_AUTO, [_Resp(429, "slow"), _auto([])])]
    assert GrClient().lookup("acme")["count"] == 0
    api["calls"].clear()
    api["routes"] = [(_AUTO, _Resp(429, "slow"))]
    with pytest.raises(UpstreamHTTPError) as e:
        GrClient().lookup("acme")
    assert e.value.status_code == 429 and len(api["calls"]) == 3


def test_empty_query_is_refused_before_any_call(api):
    with pytest.raises(ValueError):
        GrClient().lookup("  ")
    assert api["calls"] == []


def test_a_vies_outage_answered_200_is_named_not_read_as_invalid(api):
    down = {"isValid": False, "userError": "MS_UNAVAILABLE", "address": "---"}
    api["routes"] = [(_AUTO, _auto([_MATCH])), (_VIES, _Resp(200, down))]
    row = GrClient().lookup("ACME")["results"][0]
    assert "vat_valid" not in row and "address" not in row
    assert row["vat_check_error"] == "VIES unavailable (MS_UNAVAILABLE)"


def test_an_invalid_number_is_a_verdict(api):
    api["routes"] = [(_AUTO, _auto([_MATCH])),
                     (_VIES, _Resp(200, {"isValid": False, "userError": "INVALID", "address": "---"}))]
    row = GrClient().lookup("ACME")["results"][0]
    assert row["vat_valid"] is False and row["address"] is None and "vat_check_error" not in row
