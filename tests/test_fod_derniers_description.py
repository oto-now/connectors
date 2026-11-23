"""La description `fod` 1.11.0 : les dernières routes du service que la description ne
disait pas entrent en lecture seule, lues sur le service à a7cb702.

Annuaire FINESS et évaluations qualité des ESSMS (HAS) dans `health_` ; l'écosystème French
Tech Aix-Marseille dans une famille propre, `ecosystem_` ; le stock SIRENE (établissements et
unités légales) dans `company_`. Le stock arrête un scan à 90 s (504, refus nommé) : les
fonctions qui scannent prennent 150 s, la recherche exige un filtre, les lots de SIREN sont
bornés à 1 000 comme ceux de SIRET.
"""
from __future__ import annotations

import copy

import jsonschema
import yaml

from fabrique.descriptions import CONNECTORS, check

FOD = CONNECTORS / "fod" / "connector.yaml"
ROUTES = {
    "health_search_establishments": ("GET", "/api/sante/finess/search"),
    "health_get_establishment": ("GET", "/api/sante/finess/{code}"),
    "health_search_quality_evaluations": ("POST", "/api/sante/essms/search"),
    "health_list_quality_evaluation_values": ("GET", "/api/sante/essms/dimensions"),
    "ecosystem_search_directory": ("POST", "/api/frenchtech/annuaire/search"),
    "ecosystem_get_directory_entry": ("GET", "/api/frenchtech/annuaire/{slug}"),
    "ecosystem_list_members": ("GET", "/api/frenchtech/membres"),
    "ecosystem_list_events": ("GET", "/api/frenchtech/evenements"),
    "ecosystem_list_calls": ("GET", "/api/frenchtech/appels"),
    "ecosystem_list_funding": ("GET", "/api/frenchtech/financements"),
    "ecosystem_list_central_services": ("GET", "/api/frenchtech/ftc_scenarios"),
    "company_get_stock_info": ("GET", "/api/sirene/info"),
    "company_search_establishments": ("POST", "/api/sirene/search"),
    "company_list_headquarters_addresses": ("POST", "/api/sirene/enrich"),
    "company_list_establishments_by_siret": ("POST", "/api/sirene/sirets"),
    "company_list_legal_units": ("POST", "/api/sirene/unites-legales"),
    "company_list_headquarters": ("POST", "/api/sirene/sieges"),
    "company_get_headquarters": ("GET", "/api/sirene/siege/{siren}"),
    "company_list_establishments": ("GET", "/api/sirene/etablissements/{siren}"),
    "company_get_establishment": ("GET", "/api/sirene/siret/{siret}"),
}
LOOKUPS = ("health_get_establishment", "ecosystem_get_directory_entry", "company_get_headquarters",
           "company_get_establishment")
STOCK = tuple(name for name, (_, path) in ROUTES.items() if path.startswith("/api/sirene/"))
SCANS = ("company_search_establishments", "company_list_headquarters_addresses", "company_list_headquarters",
         "company_list_legal_units", "company_list_establishments_by_siret", "company_list_establishments")


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(FOD.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _accepts(name: str, arguments: dict) -> bool:
    return jsonschema.Draft202012Validator(_functions()[name]["input"]).is_valid(arguments)


def test_the_description_is_version_1_11_0_or_later_and_passes_the_fabrique():
    description = _description()
    major, minor, _ = (int(n) for n in description["connector"]["version"].split("."))
    assert (major, minor) >= (1, 11)
    check(description, FOD)


def test_each_function_is_a_read_on_its_route():
    functions = _functions()
    for name, (method, path) in ROUTES.items():
        function = functions[name]
        assert function["class"] == "read", name
        assert function.get("act", False) is False, name
        assert (function["call"]["method"], function["call"]["path"]) == (method, path), name


def test_every_route_the_service_served_is_now_described():
    paths = {(f["call"]["method"], f["call"]["path"]) for f in _functions().values() if "call" in f}
    assert set(ROUTES.values()) <= paths


def test_the_families():
    assert all(name.startswith("health_") for name, (_, p) in ROUTES.items() if p.startswith("/api/sante/"))
    assert all(name.startswith("ecosystem_") for name, (_, p) in ROUTES.items() if p.startswith("/api/frenchtech/"))
    assert all(name.startswith("company_") for name in STOCK)


def test_a_lookup_that_answers_null_has_a_named_refusal():
    functions = _functions()
    for name in LOOKUPS:
        function = functions[name]
        codes = {r["code"] for r in function["refusals"]}
        assert function["expect"] and {e["refusal"] for e in function["expect"]} <= codes, name


def test_a_stopped_scan_is_a_named_refusal_and_scans_wait_longer_than_the_stop():
    functions = _functions()
    for name in STOCK:
        if name == "company_get_stock_info":
            continue
        assert {"code": "stock_scan_too_long", "when": 504} in [
            {"code": r["code"], "when": r["when"]} for r in functions[name]["refusals"]], name
    for name in SCANS:
        assert functions[name]["timeout_s"] > 90 + 30, name


def test_a_stock_search_needs_a_filter_and_a_bounded_page():
    assert not _accepts("company_search_establishments", {})
    assert not _accepts("company_search_establishments", {"sieges_only": True, "tranche_effectifs": ["22"]})
    assert _accepts("company_search_establishments", {"departement": "33", "sieges_only": True,
                                                      "tranche_effectifs": ["22", "31", "32"]})
    assert _accepts("company_search_establishments", {"naf": "4711F"})
    assert not _accepts("company_search_establishments", {"naf": "47.11F", "limit": 1001})
    assert not _accepts("company_search_establishments", {"departement": "2A"})
    assert _accepts("company_search_establishments", {"departement": "971"})


def test_batches_are_bounded_at_a_thousand():
    sirens = [f"{n:09d}" for n in range(1001)]
    sirets = [f"{n:014d}" for n in range(1001)]
    for name in ("company_list_headquarters_addresses", "company_list_headquarters", "company_list_legal_units"):
        assert _accepts(name, {"sirens": sirens[:1000]}), name
        assert not _accepts(name, {"sirens": sirens}), name
    assert _accepts("company_list_establishments_by_siret", {"sirets": sirets[:1000]})
    assert not _accepts("company_list_establishments_by_siret", {"sirets": sirets})


def test_the_legal_unit_stock_missing_is_a_named_refusal():
    refusals = {r["code"]: r["when"] for r in _functions()["company_list_legal_units"]["refusals"]}
    assert refusals["legal_unit_stock_unavailable"] == 501


def test_the_stock_location_on_the_service_is_stripped():
    assert _functions()["company_get_stock_info"]["output"]["strip"] == ["path"]


def test_finess_department_codes_are_finess_own():
    assert _accepts("health_search_establishments", {"q": "bordeaux", "departement": "9A"})
    assert _accepts("health_search_establishments", {"q": "ajaccio", "departement": "2A"})
    assert not _accepts("health_search_establishments", {"q": "pointe", "departement": "971"})
    assert not _accepts("health_search_establishments", {"q": "x", "limit": 101})
    assert _accepts("health_get_establishment", {"code": "2A0000154"})


def test_essms_closed_values():
    assert _accepts("health_search_quality_evaluations", {"secteur": "Médico-social", "type_structure": "Service"})
    assert not _accepts("health_search_quality_evaluations", {"secteur": "Sanitaire"})
    assert not _accepts("health_search_quality_evaluations", {"limit": 501})


def test_named_persons_of_the_ecosystem_are_said_and_kept_out_of_logs():
    functions = _functions()
    assert functions["ecosystem_list_members"]["input"]["properties"]["query"]["personal"] is True
    for name in ("ecosystem_search_directory", "ecosystem_list_members"):
        assert "personal data" in functions[name]["description"].lower(), name
    codes = {r["code"]: r["when"] for r in functions["ecosystem_search_directory"]["refusals"]}
    assert codes == {"unknown_directory_term": 500}
