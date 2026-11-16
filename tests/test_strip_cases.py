"""Les cas communs de `output.strip` (`connectors/_cases/strip.yaml`), joués côté Python, une ligne par cas.

Ils se jouent contre le moteur de la lib (`oto.connectors.engine`), qui appelle la fonction du cas devant un faux tiers
(`engine_fake`) rendant la réponse du cas, et taille sa sortie par `oto.connectors.serve` (`shaped`, `items_of`), à la
sémantique du format (`docs/conception/sortie.md`) ; le paquet de l'hôte joue les mêmes cas, sortis par la fabrique dans
`ts/src/strip-cases.ts`, contre son moteur. `shaped` reste la lecture de `serve` seule, que les tests d'une description
appliquent à son exemple de réponse. Les sorties refusées le sont par la fabrique (règles 18 et 19), avec leur
code et le chemin qu'elles nomment ; celles de la règle 19 (l'exemple de réponse, `output.sample`) ne valent que pour la
fabrique et ne sortent pas en TypeScript.
"""
from __future__ import annotations

import copy
import json
import re

import pytest
from jsonschema import Draft202012Validator

import engine_fake
from fabrique import strip_cases
from fabrique.descriptions import DescriptionError, check_rules, schema
from oto.connectors import serve

LOADED = strip_cases.load()


def shaped(answer, output: dict):
    """Ce que sert `oto.connectors.serve` : `strip`, puis `projection`, sur la réponse, ou sur chaque élément de la
    liste `items`."""
    if "items" in output:
        return serve.items_of({"name": "carrier", "output": output}, [answer])
    return serve.shaped(answer, output)


def served(case: dict):
    """Ce que le moteur rend de la réponse du cas, appelé avec l'exemple de la fonction : la liste `items` taillée, ou la réponse taillée (`serve.result` :
    un objet tel quel, `{}` pour rien, `{"value": …}` pour une autre valeur)."""
    function = case["function"]
    result, _ = engine_fake.play(LOADED.description.data, function["name"], function["examples"][0]["input"],
                                 engine_fake.answer(200, text=json.dumps(case["answer"])))
    if "items" in function["output"]:
        assert set(result) == {"items", "next_cursor", "pages"}
        return result["items"]
    return result


def expected(case: dict):
    shaped_answer = case["shaped"]
    if "items" in case["function"]["output"] or isinstance(shaped_answer, dict):
        return shaped_answer
    return {} if shaped_answer is None else {"value": shaped_answer}


@pytest.mark.parametrize("case", LOADED.cases, ids=[case["name"] for case in LOADED.cases])
def test_strip_case(case):
    assert served(case) == expected(case)


@pytest.mark.parametrize("case", LOADED.cases, ids=[case["name"] for case in LOADED.cases])
def test_strip_case_by_serve_leaves_the_answer_intact(case):
    answer = copy.deepcopy(case["answer"])
    assert shaped(answer, case["function"]["output"]) == case["shaped"]
    assert answer == case["answer"]


@pytest.mark.parametrize("refused", LOADED.refused, ids=[refused["name"] for refused in LOADED.refused])
def test_refused_output_is_refused_by_the_fabrique(refused):
    description = strip_cases.carrying(LOADED, refused["output"])
    at = "output.sample" if refused["path"] is None else f"output.strip {refused['path']!r}"
    expected = f"function {strip_cases.CARRIER}: {at}: {refused['refusal']}"
    with pytest.raises(DescriptionError, match=re.escape(expected)):
        check_rules(description, "connectors/_cases/strip.yaml")


@pytest.mark.parametrize("refused", [r for r in LOADED.refused if r["refusal"] in ("strip_path_malformed", "strip_path_twice")],
                         ids=lambda r: r["name"])
def test_malformed_or_twice_is_refused_by_the_schema_too(refused):
    description = strip_cases.carrying(LOADED, refused["output"])
    assert next(iter(Draft202012Validator(schema()).iter_errors(description)), None) is not None


def test_cases_cover_every_refusal():
    assert sorted({r["refusal"] for r in LOADED.refused}) == sorted(strip_cases.REFUSALS + strip_cases.SAMPLE_REFUSALS)


def test_every_dotted_case_carries_a_sample_that_its_paths_designate():
    for case in LOADED.cases:
        output = case["function"]["output"]
        assert ("sample" in output) == any("." in path for path in output["strip"]), case["name"]


@pytest.mark.parametrize("value", [
    "https://files.example.test/r.mp3", "https://api.example.com/x", "https://a.b.example/x", "http://h.invalid",
    "https://localhost/x", "someone@example.test", "plain text", "s-0",
], ids=["test", "example_com", "example_tld", "invalid", "localhost", "mail_test", "text", "identifier"])
def test_sample_admits_reserved_addresses(value):
    description = strip_cases.carrying(LOADED, {"strip": ["call.recording"], "sample": {"call": {"recording": value}}})
    check_rules(description, "connectors/_cases/strip.yaml")


@pytest.mark.parametrize("value", [
    "https://files.not-reserved.zz/r.mp3", "https://example.test.zz/x", "ftp://files.not-reserved.zz",
    "someone@not-reserved.zz", "see https://files.example.test and https://other.zz/x",
], ids=["host", "reserved_as_a_label", "other_scheme", "mail", "second_address"])
def test_sample_refuses_a_foreign_address_without_echoing_it(value):
    description = strip_cases.carrying(LOADED, {"strip": ["call.recording"], "sample": {"call": {"recording": value}}})
    with pytest.raises(DescriptionError, match=re.escape("output.sample: strip_sample_foreign_address")) as raised:
        check_rules(description, "connectors/_cases/strip.yaml")
    assert "zz" not in str(raised.value) and "call.recording" in str(raised.value)


def test_sample_address_in_a_key_is_judged_too():
    description = strip_cases.carrying(
        LOADED, {"strip": ["call.recording"], "sample": {"call": {"recording": "r", "https://files.not-reserved.zz": 1}}})
    with pytest.raises(DescriptionError, match="strip_sample_foreign_address"):
        check_rules(description, "connectors/_cases/strip.yaml")


def test_refused_output_that_the_fabrique_accepts_is_refused_at_load(tmp_path):
    text = strip_cases.CASES.read_text(encoding="utf-8").replace("strip: [call..recording]", "strip: [secret]", 1)
    path = tmp_path / "strip.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DescriptionError, match="refused output empty_segment is accepted by the fabrique"):
        strip_cases.load(path)


def test_refused_output_with_another_refusal_is_refused_at_load(tmp_path):
    text = strip_cases.CASES.read_text(encoding="utf-8").replace(
        "path: call..recording\n    refusal: strip_path_malformed", "path: call..recording\n    refusal: strip_path_twice", 1)
    path = tmp_path / "strip.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DescriptionError, match="refused output empty_segment: the fabrique answers strip_path_malformed"):
        strip_cases.load(path)
