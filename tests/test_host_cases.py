"""Les cas communs du profil hôte (`connectors/_cases/host.yaml`), joués côté Python, une ligne par cas.

Chaque document valide donne sa définition : ses espaces de noms dans l'ordre, les métadonnées de l'hôte de chaque
fonction, chaque fonction du format en entier et les clés attendues de chaque connecteur ; chaque connecteur est un
document que le schéma, les exemples et les règles du format acceptent. Chaque variante refusée ne donne aucune
définition, et exactement ses problèmes, chacun par son code et sa place. Le module TypeScript joue les mêmes cas
(`ts/test/host.test.ts`).
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from oto.connectors.custom_api import from_host_openapi
from oto.connectors.custom_api.profile import _example_problems, _rule_problems, _schema_problems
from test_custom_api_cases import changed

CASES = yaml.safe_load(
    (pathlib.Path(__file__).resolve().parent.parent / "connectors" / "_cases" / "host.yaml").read_text(encoding="utf-8")
)
BASES = {case["name"]: case["document"] for case in CASES["documents"]}
RESERVED = CASES["reserved"]


def _document(case: dict):
    return case["text"] if "text" in case else changed(BASES[case["base"]], case["changes"])


@pytest.mark.parametrize("case", CASES["valid"], ids=[case["name"] for case in CASES["valid"]])
def test_valid(case):
    result = from_host_openapi(_document(case), reserved=RESERVED)
    assert result["problems"] == []
    definition = result["definition"]
    documents = definition["connectors"]
    assert [d["connector"]["name"] for d in documents] == case["namespaces"]
    for document in documents:
        assert {k: document["connector"][k] for k in case["connector"]} == case["connector"]
        schema_problems, matches = _schema_problems(document, [])
        assert schema_problems == [] and matches
        assert _example_problems(document) == [] and _rule_problems(document) == []
    if "host" in case:
        assert definition["host"] == case["host"]
    if "functions" in case:
        assert definition["functions"] == case["functions"]
    if "served" in case:
        served = {f"{d['connector']['name']}.{f['name']}": f for d in documents for f in d["functions"]}
        assert served == case["served"]


@pytest.mark.parametrize("case", CASES["refused"], ids=[case["name"] for case in CASES["refused"]])
def test_refused(case):
    result = from_host_openapi(_document(case), reserved=RESERVED)
    assert result["definition"] is None
    found = sorted({(p["code"], p["path"]) for p in result["problems"]})
    assert found == sorted(tuple(p) for p in case["problems"]), result["problems"]
    assert sorted({p["rule"] for p in result["problems"] if "rule" in p}) == sorted(case.get("rules", []))


def test_the_base_documents_are_not_changed():
    """Les cas partent de la même base : l'import ne la modifie pas."""
    before = yaml.safe_dump(BASES)
    from_host_openapi(BASES["projects_and_shares"], reserved=RESERVED)
    assert yaml.safe_dump(BASES) == before
