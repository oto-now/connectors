"""La description `sirene` 1.0.0 : celle du client de l'API Sirene de l'INSEE (`oto.tools.sirene.SireneClient`).

Deux fonctions du client, sous leur nom : `get_siret` et `get_by_siren`, en GET, la clé dans
`X-INSEE-Api-Key-Integration` et `Accept: application/json`, comme le client. Restent au client, nommées dans la
description : `get_headquarters`, `get_establishments`, `search` et `search_siret`, qui bâtissent la requête `q`
depuis leurs arguments (ce que le format ne dit pas). Une fonction de plus, `get_service_status` (`/informations`),
n'est pas une méthode du client : c'est la sonde, une lecture gratuite sans argument.
Tout est lecture, gratuite ; le rythme de l'INSEE (30 requêtes par minute et par clé) est déclaré.
"""
from __future__ import annotations

import copy
import inspect
from unittest.mock import MagicMock, patch

import jsonschema
import yaml

from fabrique.descriptions import CONNECTORS, check
from oto.tools.sirene import SireneClient

SIRENE = CONNECTORS / "sirene" / "connector.yaml"

LEFT_TO_THE_CLIENT = {"get_headquarters", "get_establishments", "search", "search_siret"}
PROBE_ONLY = {"get_service_status"}
VALUES = {"siret": "55203253400646", "siren": "552032534"}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(SIRENE.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(SireneClient, inspect.isfunction) if not n.startswith("_")}


def _sent_by_the_client(name: str, arguments: dict) -> dict:
    answer = MagicMock(ok=True)
    answer.json.return_value = {}
    with patch("france_opendata.sirene.requests.get", return_value=answer) as get:
        getattr(SireneClient(api_key="k"), name)(**arguments)
    assert get.call_count == 1, name
    (url,), kwargs = get.call_args
    return {"method": "GET", "url": url, "params": kwargs.get("params"), "headers": kwargs.get("headers"),
            "timeout": kwargs.get("timeout")}


def _described(function: dict, arguments: dict) -> dict:
    connector = _description()["connector"]
    call = function["call"]
    query = {api: arguments[a] for api, a in call.get("query", {}).items() if a in arguments}
    return {"method": call["method"], "url": connector["base_url"] + call["path"].format(**arguments),
            "params": query or None, "headers": {**connector["headers"], connector["auth"]["name"]: "k"}}


def test_the_description_is_version_1_0_0_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.0"
    check(description, SIRENE)


def test_one_function_per_described_client_method_and_the_others_are_named():
    functions = set(_functions())
    assert functions - PROBE_ONLY == _public_methods() - LEFT_TO_THE_CLIENT
    comment = SIRENE.read_text(encoding="utf-8").split("\nconnector:", 1)[0]
    for name in LEFT_TO_THE_CLIENT | PROBE_ONLY:
        assert name in comment, name


def test_the_connector_says_what_the_client_does():
    connector = _description()["connector"]
    assert connector["base_url"] == SireneClient.BASE_URL
    assert connector["auth"] == {"kind": "api_key", "in": "header", "name": "X-INSEE-Api-Key-Integration",
                                 "key": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "INSEE API key", "secret": True}]
    assert connector["modes"] == ["platform", "byo_user", "byo_org"]
    assert connector["rate_limit"] == {"requests": 30, "seconds": 60}
    sent = _sent_by_the_client("get_siret", {"siret": VALUES["siret"]})
    # Le délai de lecture du client (30 s) est le défaut du format.
    assert sent["timeout"][1] == 30 and "timeout_s" not in connector


def test_each_function_takes_the_arguments_its_client_method_names():
    for name, function in _functions().items():
        if name in PROBE_ONLY:
            continue
        parameters = {p: v for p, v in inspect.signature(getattr(SireneClient, name)).parameters.items()
                      if p != "self"}
        assert set(function["input"]["properties"]) == set(parameters), name
        required = {p for p, v in parameters.items() if v.default is inspect.Parameter.empty}
        assert set(function["input"].get("required", [])) == required, name


def test_each_argument_and_each_example_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        if name in PROBE_ONLY:
            continue
        cases = [{a: VALUES[a] for a in function["input"]["properties"]}]
        cases += [example["input"] for example in function["examples"]]
        for arguments in cases:
            jsonschema.validate(arguments, function["input"])
            sent = _sent_by_the_client(name, arguments)
            sent.pop("timeout")
            assert sent == _described(function, arguments), (name, arguments)


def test_numbers_are_digits_of_their_length():
    functions = _functions()
    for name, argument, good in (("get_siret", "siret", VALUES["siret"]), ("get_by_siren", "siren", VALUES["siren"])):
        schema = functions[name]["input"]
        assert jsonschema.Draft202012Validator(schema).is_valid({argument: good})
        for bad in (good[:-1], good + "0", good[:3] + " " + good[3:], "A" + good[1:]):
            assert not jsonschema.Draft202012Validator(schema).is_valid({argument: bad}), (name, bad)


def test_every_function_is_a_free_read_and_the_probe_takes_no_argument():
    description = _description()
    for function in description["functions"]:
        assert function["class"] == "read", function["name"]
        assert "cost" not in function and "Free" in function["description"], function["name"]
    probe = description["connector"]["probe"]["function"]
    assert probe in PROBE_ONLY
    assert not _functions()[probe]["input"].get("required")


def test_partial_diffusion_is_said_where_a_unit_comes_back():
    for name in ("get_siret", "get_by_siren"):
        text = " ".join(_functions()[name]["description"].split())
        assert "partial diffusion" in text and "prospecting" in text, name


def test_the_refused_key_marks_the_account():
    errors = {e["code"]: e for e in _description()["connector"]["errors"]}
    assert errors["key_refused"]["status"] == 401
    assert errors["key_refused"]["marks_account"] == "credential_rejected"
    assert errors["rate_limited"]["status"] == 429 and errors["rate_limited"]["retryable"] is True
    assert "marks_account" not in errors["rate_limited"]
