"""Les descriptions servies (`oto/connectors/descriptions/`), les fonctions pures qui les lisent (`oto/connectors/serve.py`)
et l'effet d'une fonction sensible (`effect`).

Un hôte Python (le lecteur générique d'oto 1) sert les fonctions décrites sans fichier YAML : la fabrique génère un
module par connecteur, sans `output.sample`, l'entrée telle qu'elle est servie, `call` gardé pour le moteur
(`oto.connectors.engine`) ; `serve` en lit les refus, la sortie, la pagination et la sonde à la sémantique du format et du
paquet d'oto 2.
"""
from __future__ import annotations

import copy
import pathlib
import re

import pytest
import yaml

from fabrique.descriptions import CONNECTORS, DescriptionError, check, check_rules, load_all
from fabrique.typescript import served_input
from oto.connectors import descriptions, serve

SELLSY = CONNECTORS / "sellsy" / "connector.yaml"


def _load(path: pathlib.Path) -> dict:
    return copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))


# --- les descriptions servies ----------------------------------------------------------------------------------------


def test_every_description_is_served_and_only_them():
    assert descriptions.NAMES == tuple(d.name for d in load_all())


def test_a_served_description_keeps_the_call_drops_the_sample_and_serves_its_input():
    for described in load_all():
        served = descriptions.load(described.name)
        assert served["connector"] == described.data["connector"]
        assert [f["name"] for f in served["functions"]] == [f["name"] for f in described.data["functions"]]
        for function, source in zip(served["functions"], described.data["functions"]):
            assert function["call"] == source["call"], function["name"]
            assert "sample" not in function.get("output", {}), function["name"]
            assert function["input"] == served_input(source), function["name"]
            kept = {k: v for k, v in source.items() if k not in ("input", "output")}
            assert {k: v for k, v in function.items() if k not in ("input", "output")} == kept, function["name"]


def test_a_paginated_function_is_served_with_its_pagination_arguments():
    served = serve.function(descriptions.load("sellsy"), "list_estimates")
    assert {"all_pages", "max_pages"} <= set(served["input"]["properties"])
    assert served["input"]["properties"]["max_pages"]["maximum"] == served["pagination"]["max_pages"]


def test_an_unknown_connector_or_function_is_refused_by_name():
    with pytest.raises(KeyError, match="no described connector 'nope'"):
        descriptions.load("nope")
    with pytest.raises(KeyError, match="sellsy: no function 'nope'"):
        serve.function(descriptions.load("sellsy"), "nope")


# --- l'effet d'une fonction sensible ---------------------------------------------------------------------------------


def _sensitive(d: dict, **extra) -> dict:
    function = next(f for f in d["functions"] if f["name"] == "get_estimate")
    function.update({"class": "sensitive", "confirm": {"summary": "Read estimate {id}."}}, **extra)
    return function


@pytest.mark.parametrize("effect", ["send", "destroy"])
def test_a_sensitive_function_may_say_its_effect(effect):
    description = _load(SELLSY)
    _sensitive(description, effect=effect)
    check(description, SELLSY)


def test_the_effect_stays_optional():
    description = _load(SELLSY)
    _sensitive(description)
    check(description, SELLSY)


@pytest.mark.parametrize("mutate", [
    lambda d: d["functions"][0].update(effect="send"),
    lambda d: _sensitive(d, effect="archive"),
], ids=["on_a_read_function", "unknown_effect"])
def test_the_schema_refuses_an_effect_out_of_place(mutate):
    description = _load(SELLSY)
    mutate(description)
    with pytest.raises(DescriptionError, match="does not match the description schema"):
        check(description, SELLSY)


@pytest.mark.parametrize("klass", ["read", "write"])
def test_rule_9_refuses_an_effect_on_a_function_that_is_not_sensitive(klass):
    description = _load(SELLSY)
    description["functions"][0].update({"class": klass, "effect": "send"})
    with pytest.raises(DescriptionError, match=re.escape("function list_estimates: effect is said of a sensitive function only")):
        check_rules(description, "sellsy")


# --- le refus nommé --------------------------------------------------------------------------------------------------

DESCRIPTION = {
    "connector": {
        "name": "x", "label": "X",
        "errors": [
            {"status": 401, "code": "key_refused", "message": "Key refused.", "retryable": False,
             "marks_account": "credential_rejected"},
            {"status": 422, "body": {"path": "error", "contains": ["Insufficient credits"]}, "code": "credits_exhausted",
             "message": "No credits.", "retryable": False, "marks_account": "quota_exhausted"},
            {"status": 422, "code": "invalid_request", "message": "Invalid.", "retryable": False},
            {"status": [500, 503], "code": "upstream_unavailable", "message": "Down.", "retryable": True},
        ],
        "probe": {"function": "get_account", "non_empty": ["id"]},
    },
    "functions": [],
}
FUNCTION = {"name": "get_thing", "refusals": [
    {"code": "thing_not_found", "when": 404, "message": "No such thing."},
    {"code": "thing_locked", "when": "the thing is locked", "message": "Locked."},
]}


@pytest.mark.parametrize("status, body, code, marks, retryable", [
    (404, {"error": "x"}, "thing_not_found", None, False),
    (401, "Unauthorized", "key_refused", "credential_rejected", False),
    (422, {"error": "You have INSUFFICIENT credits"}, "credits_exhausted", "quota_exhausted", False),
    (422, {"error": 3}, "invalid_request", None, False),
    (422, "insufficient credits", "invalid_request", None, False),
    (503, None, "upstream_unavailable", None, True),
], ids=["function_first", "text_body", "contains", "contains_not_a_text", "contains_not_json", "status_list"])
def test_refusal_reads_the_function_then_the_table(status, body, code, marks, retryable):
    entry = serve.refusal(DESCRIPTION, FUNCTION, status, body)
    assert (entry["code"], entry["marks_account"], entry["retryable"]) == (code, marks, retryable)
    assert entry["message"]


def test_no_entry_for_an_undeclared_status():
    assert serve.refusal(DESCRIPTION, FUNCTION, 418, {}) is None


# --- la sortie -------------------------------------------------------------------------------------------------------

LISTED = {"name": "list_things", "output": {"items": "data", "strip": ["secret", "owner.photo"], "projection": ["id", "owner"]},
          "pagination": {"kind": "cursor", "request_param": "cursor", "next": "next", "more": "has_more", "max_pages": 3}}


def test_result_of_a_list_concatenates_the_shaped_pages():
    pages = [{"data": [{"id": 1, "secret": "s", "owner": {"photo": "p", "name": "a"}, "extra": 0}], "next": "c1"},
             {"data": [{"id": 2}], "next": None}]
    before = copy.deepcopy(pages)
    assert serve.result(LISTED, pages, None) == {
        "items": [{"id": 1, "owner": {"name": "a"}}, {"id": 2}], "next_cursor": None, "pages": 2}
    assert pages == before


def test_a_list_missing_from_an_answer_is_refused_never_read_empty():
    with pytest.raises(serve.ServeError, match="list_things: answer without its list data"):
        serve.result(LISTED, [{"items": []}])


@pytest.mark.parametrize("answer, expected", [
    ({"id": 1, "secret": "s"}, {"id": 1}), (None, {}), ([1, 2], {"value": [1, 2]}),
], ids=["object", "no_content", "other_value"])
def test_result_of_a_single_answer(answer, expected):
    assert serve.result({"name": "get_thing", "output": {"strip": ["secret"]}}, [answer]) == expected


# --- la pagination ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("args, pages", [
    ({}, 1), ({"all_pages": False}, 1), ({"all_pages": True}, 3), ({"all_pages": True, "max_pages": 2}, 2),
    ({"all_pages": True, "max_pages": 9}, 3),
])
def test_page_count(args, pages):
    assert serve.page_count(LISTED, args) == pages
    assert serve.page_count({"name": "get_thing"}, args) == 1


@pytest.mark.parametrize("answer, cursor", [
    ({"next": "c1", "has_more": True}, "c1"), ({"next": "c1", "has_more": False}, None), ({"next": ""}, None),
    ({"next": 7}, 7), ({"next": True}, None), ({}, None),
], ids=["text", "more_false", "empty", "number", "boolean", "absent"])
def test_next_cursor(answer, cursor):
    assert serve.next_cursor(LISTED, answer) == cursor


def test_call_arguments_drop_the_pagination_arguments_and_carry_the_cursor():
    args = {"limit": 10, "all_pages": True, "max_pages": 2}
    assert serve.call_arguments(LISTED, args) == {"limit": 10}
    assert serve.call_arguments(LISTED, args, "c1") == {"limit": 10, "cursor": "c1"}
    with pytest.raises(serve.ServeError, match="does not paginate"):
        serve.call_arguments({"name": "get_thing"}, {}, "c1")


# --- la sonde --------------------------------------------------------------------------------------------------------


def _with_balance(**balance) -> dict:
    d = copy.deepcopy(DESCRIPTION)
    d["connector"]["probe"]["balance"] = {"path": "credits.left", "min": 1, "unit": "credits", **balance}
    return d


def test_probe_health_reads_its_non_empty_paths():
    assert serve.probe_health(DESCRIPTION, {"id": "a"}) == {"healthy": True}
    assert serve.probe_health(DESCRIPTION, {"id": ""}) == {"healthy": False, "reason": "X answered without id."}


@pytest.mark.parametrize("answer, healthy, value, reason", [
    ({"id": "a", "credits": {"left": 5}}, True, 5, None),
    ({"id": "a", "credits": {"left": 0.5}}, False, 0.5, "X balance is 0.5 credits, under 1 credits."),
    ({"id": "a", "credits": {}}, False, None, "X answered without its balance credits.left."),
    ({"id": "a", "credits": {"left": "12"}}, False, None, "X answered a balance credits.left that is not a number."),
    ({"id": "a", "credits": {"left": True}}, False, None, "X answered a balance credits.left that is not a number."),
], ids=["enough", "under", "absent", "text", "boolean"])
def test_probe_health_judges_the_balance(answer, healthy, value, reason):
    health = serve.probe_health(_with_balance(), answer)
    assert health["healthy"] is healthy and health.get("reason") == reason
    assert health["balance"] == {"value": value, "unit": "credits", "min": 1}
