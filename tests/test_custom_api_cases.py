"""Les cas communs du profil Custom API (`connectors/_cases/custom_api.yaml`), joués côté Python, une ligne par cas.

Chaque définition valide passe le profil sans un problème ; chaque variante refusée donne exactement ses codes (et les
règles du format nommées) ; chaque document OpenAPI donne ses candidats, ses opérations écartées et les problèmes du
connecteur proposé ; chaque application d'oto 1 donne son statut, ses codes bloquants et les parties attendues du
document. Le module TypeScript du paquet de l'hôte joue les mêmes cas.
"""

from __future__ import annotations

import copy
import pathlib

import pytest
import yaml

from oto.connectors.custom_api import OpenAPIRefused, from_openapi, from_oto1, validate

CASES = yaml.safe_load(
    (pathlib.Path(__file__).resolve().parent.parent / "connectors" / "_cases" / "custom_api.yaml").read_text(
        encoding="utf-8"
    )
)
VALID = {case["name"]: case["document"] for case in CASES["valid"]}


def _key(container, part: str):
    return int(part) if isinstance(container, list) else part


def changed(document: dict, changes: list[dict]) -> dict:
    """La définition après ses changements : chaque chemin à points, posé (`value`) ou retiré (`remove`)."""
    out = copy.deepcopy(document)
    for change in changes:
        *parents, last = change["path"].split(".")
        node = out
        for part in parents:
            node = node[_key(node, part)]
        if change.get("remove"):
            del node[_key(node, last)]
        else:
            node[_key(node, last)] = copy.deepcopy(change["value"])
    return out


@pytest.mark.parametrize("name", list(VALID))
def test_valid(name):
    assert [problem.as_dict() for problem in validate(VALID[name])] == []


@pytest.mark.parametrize("case", CASES["refused"], ids=[case["name"] for case in CASES["refused"]])
def test_refused(case):
    found = validate(changed(VALID[case["base"]], case["changes"]))
    assert sorted({problem.code for problem in found}) == sorted(case["codes"]), [p.as_dict() for p in found]
    assert sorted({problem.rule for problem in found if problem.rule}) == sorted(case.get("rules", []))


@pytest.mark.parametrize("case", CASES["openapi"], ids=[case["name"] for case in CASES["openapi"]])
def test_openapi(case):
    if "refused" in case:
        with pytest.raises(OpenAPIRefused) as refused:
            from_openapi(case["document"])
        assert refused.value.code == case["refused"]
        return
    result = from_openapi(case["document"])
    assert [
        {"name": c["function"]["name"], "problems": [p["code"] for p in c["problems"]]} for c in result["candidates"]
    ] == case["candidates"]
    assert [{k: s[k] for k in ("method", "path", "code")} for s in result["skipped"]] == case["skipped"]
    assert sorted(p["code"] for p in result["problems"]) == sorted(case["connector_problems"])


@pytest.mark.parametrize(
    "case",
    [c for c in CASES["openapi"] if "refused" not in c and not c["connector_problems"]],
    ids=lambda case: case["name"],
)
def test_openapi_candidates_without_problem_make_a_valid_definition(case):
    """Un connecteur proposé sans problème et ses candidats sans problème forment une définition que le profil admet."""
    result = from_openapi(case["document"])
    document = {
        "connector": result["connector"],
        "functions": [c["function"] for c in result["candidates"] if not c["problems"]],
    }
    assert [problem.as_dict() for problem in validate(document)] == []


def _contains(whole, part) -> bool:
    if isinstance(part, dict):
        return isinstance(whole, dict) and all(k in whole and _contains(whole[k], v) for k, v in part.items())
    return whole == part


@pytest.mark.parametrize("case", CASES["oto1"], ids=[case["name"] for case in CASES["oto1"]])
def test_oto1(case):
    document, report = from_oto1(case["application"])
    assert report["status"] == case["status"]
    assert sorted({problem["code"] for problem in report["blocking"]}) == sorted(case["blocking"]), report["blocking"]
    assert (document is None) == (case["status"] == "blocked")
    if document is not None:
        assert validate(document) == []
    expected = case.get("expected")
    if expected:
        assert _contains(document["connector"], expected.get("connector", {}))
        if "functions" in expected:
            assert document["functions"] == expected["functions"]
