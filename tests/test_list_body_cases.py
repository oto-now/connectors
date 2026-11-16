"""Les cas communs du corps en liste (`connectors/_cases/list_body.yaml`), joués côté Python, une ligne par cas.

Ils se jouent contre le moteur de la lib (`oto.connectors.engine`), qui compose et envoie la requête à un faux tiers
(`engine_fake`) ; le paquet de l'hôte joue les mêmes cas, sortis par la fabrique dans `ts/src/list-body-cases.ts`, contre
son moteur. Les entrées rejetées le sont par le schéma d'entrée, avant tout envoi ; les fonctions refusées le sont par la
fabrique (règle 23), avec leur code.
"""
from __future__ import annotations

import copy
import re

import pytest
from jsonschema import Draft202012Validator

from fabrique import list_body_cases
from fabrique.descriptions import LIST_BODY_REFUSALS, DescriptionError, body_list_refusal, check_rules
from oto.connectors.engine import request as engine_request
from oto.connectors.engine.errors import EngineError
from test_body_cases import sent

LOADED = list_body_cases.load()
FUNCTIONS = {function["name"]: function for function in LOADED.description.data["functions"]}
WHERE = "connectors/_cases/list_body.yaml"


@pytest.mark.parametrize("case", LOADED.cases, ids=[case["name"] for case in LOADED.cases])
def test_list_body_case(case):
    function = case["function"]
    given = copy.deepcopy(case["input"])
    assert Draft202012Validator(function["input"]).is_valid(given)
    assert sent(LOADED.description.data, function, given) == case["request"]
    assert given == case["input"]


@pytest.mark.parametrize("rejected", LOADED.rejected, ids=[rejected["name"] for rejected in LOADED.rejected])
def test_rejected_input_is_refused_by_the_schema(rejected):
    assert not Draft202012Validator(FUNCTIONS[rejected["function"]]["input"]).is_valid(rejected["input"])


@pytest.mark.parametrize("refused", LOADED.refused, ids=[refused["name"] for refused in LOADED.refused])
def test_refused_function_is_refused_by_rule_23(refused):
    function = refused["function"]
    code, _reason = body_list_refusal(function)
    assert code == refused["refusal"]
    expected = f"function {function['name']}: call.body_list: {code}: "
    with pytest.raises(DescriptionError, match=re.escape(expected)):
        check_rules(list_body_cases.alone(LOADED, function), WHERE)


def test_every_refusal_has_a_refused_function():
    assert {refused["refusal"] for refused in LOADED.refused} == set(LIST_BODY_REFUSALS)


def test_without_the_flag_the_same_function_sends_the_object():
    function = copy.deepcopy(FUNCTIONS["search_keyword"])
    del function["call"]["body_list"]
    description = {**LOADED.description.data, "functions": [function]}
    assert sent(description, function, {"engine": "web", "keyword": "alpha"})["body"] == {"keyword": "alpha"}


def test_the_engine_refuses_a_list_body_without_a_body():
    """Une description que la fabrique refuse (règle 23) n'est pas envoyée non plus par le moteur."""
    function = copy.deepcopy(next(r["function"] for r in LOADED.refused if r["refusal"] == "body_list_without_body"))
    with pytest.raises(EngineError, match="body_list without a body"):
        engine_request.build(LOADED.description.data["connector"], function, {})


def test_typescript_carries_the_flag():
    source = list_body_cases.typescript_source(LOADED)
    assert "    bodyList: true,\n" in source
    assert '      body: [{"keyword": "alpha"}],\n' in source
    assert "export const listBodyRefusedFunctions: readonly ListBodyRefusedFunction[]" in source
