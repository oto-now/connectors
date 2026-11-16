"""Contrat du client SearchApi — sans réseau (`requests.Session.get` remplacé).

La clé part en en-tête `Authorization`, jamais en query ; `engine` reste ouvert ; les
champs typés deviennent les paramètres de l'API, `params` fusionné en dernier ; un refus
local part avant tout appel ; un refus du service devient `UpstreamHTTPError`.
"""
from __future__ import annotations

import pytest

from oto.tools.common.credentials import MissingCredential
from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.searchapi import SearchApiClient
from oto.tools.searchapi import client as S


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code, self._body = status_code, body if body is not None else {"ok": True}
        self.text, self.headers = str(self._body), {}

    def json(self):
        return self._body


@pytest.fixture
def api(monkeypatch):
    state = {"calls": [], "resp": _Resp()}

    def get(self, url, **kwargs):
        state["calls"].append({"url": url, **kwargs})
        return state["resp"]
    monkeypatch.setattr(S.requests.Session, "get", get)
    return state


def test_key_is_required():
    with pytest.raises(MissingCredential):
        SearchApiClient("")


def test_typed_fields_become_api_params_and_the_key_stays_in_the_header(api):
    assert SearchApiClient("k").search("google", query="pizza", country="fr", language="fr",
                                       location="Paris, France", num=10, page=2) == {"ok": True}
    call = api["calls"][0]
    assert call["url"] == "https://www.searchapi.io/api/v1/search"
    assert call["params"] == {"q": "pizza", "gl": "fr", "hl": "fr", "location": "Paris, France",
                              "num": 10, "page": 2, "engine": "google"}
    assert call["headers"] == {"Authorization": "Bearer k"}
    assert "k" not in call["params"].values()


def test_absent_fields_are_not_sent_and_params_merge_last(api):
    SearchApiClient("k").search("google_lens", params={"url": "https://x/y.png"})
    assert api["calls"][0]["params"] == {"url": "https://x/y.png", "engine": "google_lens"}
    SearchApiClient("k").search("google", query="a", params={"q": "b", "engine": "bing"})
    assert api["calls"][1]["params"] == {"q": "b", "engine": "google"}


def test_any_engine_id_is_accepted(api):
    SearchApiClient("k").search("some_future_engine", query="x")
    assert api["calls"][0]["params"]["engine"] == "some_future_engine"


@pytest.mark.parametrize("kwargs,match", [({"engine": " ", "query": "x"}, "engine"),
                                          ({"engine": "google"}, "query")])
def test_local_refusals_leave_before_any_call(api, kwargs, match):
    with pytest.raises(ValueError, match=match):
        SearchApiClient("k").search(**kwargs)
    assert api["calls"] == []


def test_an_upstream_refusal_is_typed(api):
    api["resp"] = _Resp(401, {"error": "Invalid API key"})
    with pytest.raises(UpstreamHTTPError) as e:
        SearchApiClient("k").search("google", query="x")
    assert e.value.status_code == 401 and e.value.service == "searchapi"


def test_account_reads_the_free_usage_endpoint(api):
    SearchApiClient("k").account()
    call = api["calls"][0]
    assert call["url"] == "https://www.searchapi.io/api/v1/me"
    assert call["headers"] == {"Authorization": "Bearer k"} and not call["params"]
