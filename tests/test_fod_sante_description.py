"""La description `fod` 1.7.0 : les routes santé du service (médicaments, essais cliniques,
publications, Transparence Santé) entrent en lecture seule.

Chaque fonction `health_` de 1.7.0 est une lecture en GET sous `/api/sante/` ; 1.11.0
en ajoute d'autres (FINESS, ESSMS), dont une en POST. Les deux fonctions
Transparence Santé disent la licence CC BY-NC-SA 4.0, sa restriction, l'attribution que
la source exige, et qu'on n'additionne pas des montants de types différents (exposition
validée par Alexis le 2026-11-21).
"""
from __future__ import annotations

import copy

import yaml

from fabrique.descriptions import CONNECTORS, check

FOD = CONNECTORS / "fod" / "connector.yaml"
HEALTH = {
    "health_search_medicines": "/api/sante/medicaments",
    "health_get_medicine": "/api/sante/medicaments/{cis}",
    "health_search_clinical_trials": "/api/sante/essais/clinicaltrials",
    "health_get_clinical_trial": "/api/sante/essais/clinicaltrials/{nct}",
    "health_search_eu_clinical_trials": "/api/sante/essais/ctis",
    "health_get_eu_clinical_trial": "/api/sante/essais/ctis/{numero}",
    "health_search_pubmed": "/api/sante/publications/pubmed",
    "health_get_pubmed_article": "/api/sante/publications/pubmed/{pmid}",
    "health_search_europepmc": "/api/sante/publications/europepmc",
    "health_get_europepmc_article": "/api/sante/publications/europepmc/article",
    "health_list_interest_declarations": "/api/sante/transparence/declarations",
    "health_sum_interests_by_company": "/api/sante/transparence/par-entreprise",
}
TRANSPARENCE = ("health_list_interest_declarations", "health_sum_interests_by_company")
ATTRIBUTION = ("Source : base Transparence - Santé (Direction Générale de la Santé), "
               "transparence.sante.gouv.fr, licence CC BY-NC-SA 4.0.")


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(FOD.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def test_the_description_is_version_1_7_0_or_later_and_passes_the_fabrique():
    description = _description()
    major, minor, _ = (int(n) for n in description["connector"]["version"].split("."))
    assert (major, minor) >= (1, 7)
    check(description, FOD)


def test_each_health_function_is_a_get_read_on_its_route():
    functions = _functions()
    assert set(HEALTH) <= {n for n in functions if n.startswith("health_")}
    for name, path in HEALTH.items():
        function = functions[name]
        assert function["class"] == "read", name
        assert function.get("act", False) is False, name
        assert function["call"]["method"] == "GET", name
        assert function["call"]["path"] == path, name


def test_transparence_says_its_licence_attribution_and_not_to_add_types():
    functions = _functions()
    for name in TRANSPARENCE:
        text = " ".join(functions[name]["description"].split())
        assert "CC BY-NC-SA 4.0" in text, name
        assert "no strictly commercial use" in text, name
        assert ATTRIBUTION in text, name
        assert "Do not add" in text and "different types" in text, name


def test_a_beneficiary_is_personal_data():
    for name in TRANSPARENCE:
        properties = _functions()[name]["input"]["properties"]
        assert properties["beneficiaire"].get("personal") is True, name
        assert properties["rpps"].get("personal") is True, name


def test_country_codes_stay_text_under_yaml_1_1():
    """PyYAML lit `NO` (Norvège) comme un booléen s'il n'est pas entre guillemets."""
    functions = _functions()
    codes = [functions["health_search_eu_clinical_trials"]["input"]["properties"]["pays"]["items"]["enum"],
             functions["health_get_eu_clinical_trial"]["input"]["properties"]["pays"]["enum"]]
    for enum in codes:
        assert "NO" in enum
        assert all(isinstance(code, str) for code in enum)
