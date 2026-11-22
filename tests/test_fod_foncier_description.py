"""La description `fod` 1.9.0 : les routes foncier et OpenStreetMap du service que la
description ne disait pas entrent en lecture seule, lues sur le service à a7cb702.

Bâti d'une parcelle (BDTOPO), productible solaire (PVGIS), isochrone (IGN), consommation
électrique du réseau de distribution (Enedis, par ligne et par site) et de transport (ODRÉ,
et ses millésimes), ventes d'une commune (DVF), étiquettes DPE d'une commune et du parc
tertiaire, bilans GES (BEGES), bâtiments et propriétaires (BDNB), émetteurs (IREP) et points
d'intérêt OpenStreetMap. Toutes restent dans `property_` : elles décrivent un site. Le
service répond 500 à une zone, un périmètre ou un budget de trajet manquant : les schémas
d'entrée les exigent.
"""
from __future__ import annotations

import copy

import jsonschema
import yaml

from fabrique.descriptions import CONNECTORS, check

FOD = CONNECTORS / "fod" / "connector.yaml"
ROUTES = {
    "property_get_parcel_buildings": ("POST", "/api/foncier/bdtopo/bati"),
    "property_get_solar_yield": ("GET", "/api/foncier/pvgis/productible"),
    "property_get_isochrone": ("GET", "/api/foncier/ign/isochrone"),
    "property_list_electricity_consumption": ("POST", "/api/foncier/enedis/conso"),
    "property_list_electricity_sites": ("POST", "/api/foncier/enedis/sites"),
    "property_list_transmission_consumption": ("POST", "/api/foncier/odre/conso"),
    "property_list_transmission_years": ("GET", "/api/foncier/odre/annees"),
    "property_list_commune_sales": ("POST", "/api/foncier/dvf/comparables"),
    "property_get_energy_rating_stats": ("POST", "/api/foncier/dpe/stats"),
    "property_list_non_residential_energy_ratings": ("POST", "/api/foncier/dpe/tertiaire"),
    "property_list_ghg_reports": ("POST", "/api/foncier/beges"),
    "property_list_building_owners": ("POST", "/api/foncier/bdnb"),
    "property_list_pollutant_emitters": ("POST", "/api/foncier/irep"),
    "property_list_points_of_interest": ("GET", "/api/osm/pois"),
}
LOOKUPS = ("property_get_solar_yield",)


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(FOD.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _accepts(name: str, arguments: dict) -> bool:
    return jsonschema.Draft202012Validator(_functions()[name]["input"]).is_valid(arguments)


def test_the_description_is_version_1_9_0_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.9.0"
    check(description, FOD)


def test_each_function_is_a_read_on_its_route():
    functions = _functions()
    for name, (method, path) in ROUTES.items():
        function = functions[name]
        assert function["class"] == "read", name
        assert function.get("act", False) is False, name
        assert (function["call"]["method"], function["call"]["path"]) == (method, path), name


def test_every_foncier_and_osm_route_of_the_service_is_described():
    paths = {f["call"]["path"] for f in _functions().values() if "call" in f}
    assert {path for _, path in ROUTES.values()} <= paths


def test_a_lookup_that_answers_null_has_a_named_refusal():
    functions = _functions()
    for name in LOOKUPS:
        function = functions[name]
        codes = {r["code"] for r in function["refusals"]}
        assert function["expect"] and {e["refusal"] for e in function["expect"]} <= codes, name


def test_an_isochrone_takes_exactly_one_travel_budget():
    point = {"lat": 45.758381, "lon": 4.83276}
    assert _accepts("property_get_isochrone", {**point, "minutes": 10})
    assert _accepts("property_get_isochrone", {**point, "metres": 800})
    assert not _accepts("property_get_isochrone", point)
    assert not _accepts("property_get_isochrone", {**point, "minutes": 10, "metres": 800})


def test_osm_takes_exactly_one_area():
    assert _accepts("property_list_points_of_interest", {"selector": "amenity=parking", "commune": "13055"})
    assert not _accepts("property_list_points_of_interest", {"selector": "amenity=parking"})
    assert not _accepts("property_list_points_of_interest",
                        {"selector": "amenity=parking", "commune": "13055", "departement": "13"})
    assert not _accepts("property_list_points_of_interest", {"selector": "a\"]", "departement": "13"})
    assert not _accepts("property_list_points_of_interest", {"selector": "shop", "bbox": "44.8,-0.5,44.9"})


def test_enedis_bdnb_and_non_residential_dpe_need_a_perimeter():
    assert not _accepts("property_list_electricity_consumption", {"annee": "2024"})
    assert not _accepts("property_list_electricity_sites", {"annee": "2024"})
    assert _accepts("property_list_electricity_sites", {"annee": "2024", "code_epci": "243300316"})
    assert not _accepts("property_list_building_owners", {"emprise_min": 5000})
    assert not _accepts("property_list_non_residential_energy_ratings", {"etiquette": ["G"]})


def test_enedis_limit_is_minus_one_or_positive():
    base = {"annee": "2024", "dept": "33"}
    assert _accepts("property_list_electricity_consumption", {**base, "limit": -1})
    assert not _accepts("property_list_electricity_consumption", {**base, "limit": 0})


def test_the_erp_sector_is_an_exact_label():
    sectors = _functions()["property_list_non_residential_energy_ratings"]["input"]["properties"]["secteur"]["enum"]
    assert len(sectors) == len(set(sectors)) == 32
    assert "U : Établissements de soins" in sectors
    assert not _accepts("property_list_non_residential_energy_ratings", {"departement": "33", "secteur": "hospital"})


def test_the_bdnb_raw_row_is_stripped():
    assert _functions()["property_list_building_owners"]["output"]["strip"] == ["signaux.raw"]
