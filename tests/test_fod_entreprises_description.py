"""La description `fod` 1.8.0 : les routes entreprises du service que la description ne
disait pas entrent en lecture seule, lues sur le service à a7cb702.

BODACC en lot, index Egapro, accords d'entreprise (ACCO et leur texte), avis de marchés
(BOAMP), marchés attribués (DECP) et aides publiques. Les deux routes INSEE restent
dehors : la clé INSEE vient de l'appelant, en en-tête, et le format ne dit pas de
credential propre à une fonction. Une recherche qui pagine par `offset` garde ses clés
de premier niveau par `output.projection`.
"""
from __future__ import annotations

import copy

import yaml

from fabrique.descriptions import CONNECTORS, check

FOD = CONNECTORS / "fod" / "connector.yaml"
ROUTES = {
    "company_screen_bodacc_notices": ("POST", "/api/fr/bodacc/batch"),
    "company_get_equality_index": ("GET", "/api/fr/egapro/{siren}/declaration"),
    "company_get_latest_equality_index": ("GET", "/api/fr/egapro/{siren}/latest"),
    "company_search_agreements": ("POST", "/api/fr/accords/search"),
    "company_get_agreement": ("GET", "/api/fr/accords/{id_or_numero}"),
    "company_list_agreement_themes": ("GET", "/api/fr/accords/themes"),
    "company_get_agreement_text": ("GET", "/api/accords/text/{acco_id}"),
    "procurement_search_notices": ("POST", "/api/fr/tenders/search"),
    "procurement_get_notice": ("GET", "/api/fr/tenders/{idweb}"),
    "procurement_search_awarded_contracts": ("GET", "/api/fr/tenders/awarded"),
    "funding_search_schemes": ("POST", "/api/fr/aides/search"),
    "funding_get_scheme": ("GET", "/api/fr/aides/{id_aid}"),
}
OFFSET_SEARCHES = ("company_search_agreements", "procurement_search_notices", "funding_search_schemes")
LOOKUPS = ("company_get_equality_index", "company_get_latest_equality_index", "company_get_agreement",
           "company_get_agreement_text", "procurement_get_notice", "funding_get_scheme")


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(FOD.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def test_the_description_is_version_1_8_0_or_later_and_passes_the_fabrique():
    description = _description()
    major, minor, _ = (int(n) for n in description["connector"]["version"].split("."))
    assert (major, minor) >= (1, 8)
    check(description, FOD)


def test_each_function_is_a_read_on_its_route():
    functions = _functions()
    for name, (method, path) in ROUTES.items():
        function = functions[name]
        assert function["class"] == "read", name
        assert function.get("act", False) is False, name
        assert (function["call"]["method"], function["call"]["path"]) == (method, path), name


def test_the_insee_routes_are_not_described():
    paths = {f["call"]["path"] for f in _functions().values() if "call" in f}
    assert not [p for p in paths if p.startswith("/api/fr/insee/")]


def test_an_offset_search_keeps_its_envelope_by_projection():
    functions = _functions()
    for name in OFFSET_SEARCHES:
        function = functions[name]
        assert "offset" in function["input"]["properties"], name
        assert "pagination" not in function, name
        assert function["output"]["projection"], name


def test_a_lookup_that_answers_null_has_a_named_refusal():
    functions = _functions()
    for name in LOOKUPS:
        function = functions[name]
        codes = {r["code"] for r in function["refusals"]}
        assert {e["refusal"] for e in function["expect"]} <= codes, name


def test_the_awarded_contracts_need_a_criterion():
    required = [set(branch["required"]) for branch in _functions()["procurement_search_awarded_contracts"]["input"]["anyOf"]]
    assert required == [{"mot_cle"}, {"titulaire_siret"}, {"acheteur_siret"}, {"lieu"}]
