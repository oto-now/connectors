"""La description `fod` 3.0.0 : `company_search` rend une identité courte par résultat, plus la fiche entière.

Une recherche d'un nom répandu, sans autre filtre, rendait chaque résultat avec son siège (près de cinquante clés),
ses dirigeants, ses compléments et ses établissements correspondants : une page dépassait de loin ce qu'un hôte
transmet à l'assistant, qui la recevait coupée. Le siège, les dirigeants, les compléments et les établissements
correspondants sortent par `output.strip` ; `company_get` rend la fiche entière.

Le défaut de `per_page` reste celui du service (25) : l'hôte n'envoie que ce que donne l'appel, un défaut déclaré plus
petit ne serait qu'une annotation que rien n'applique. La réponse ci-dessous est inventée, à la forme de celle du
service, au pire des cas relevés : une page pleine, des groupes à nombreux dirigeants et établissements.
"""
from __future__ import annotations

import json

import yaml

from fabrique.descriptions import CONNECTORS, check
from oto.connectors import serve

FOD = CONNECTORS / "fod" / "connector.yaml"
LEFT_OUT = ("siege", "dirigeants", "complements", "matching_etablissements")
HOST_LIMIT = 45_000


def _description() -> dict:
    return yaml.safe_load(FOD.read_text(encoding="utf-8"))


def _search() -> dict:
    return next(f for f in _description()["functions"] if f["name"] == "company_search")


def _etablissement(i: int) -> dict:
    return {f"champ_{k:02d}": f"valeur inventée {i}-{k}" for k in range(48)} | {"siret": f"{i:014d}"}


def _resultat(i: int) -> dict:
    return {
        "siren": f"{i:09d}", "nom_complet": f"SOCIETE EXEMPLE {i}", "nom_raison_sociale": f"SOCIETE EXEMPLE {i}",
        "sigle": None, "activite_principale": "70.10Z", "nature_juridique": "5599", "categorie_entreprise": "GE",
        "tranche_effectif_salarie": "53", "etat_administratif": "A", "date_creation": "2000-01-01",
        "date_fermeture": None, "nombre_etablissements": 40, "nombre_etablissements_ouverts": 12,
        "finances": {"2025": {"ca": 1_000_000, "resultat_net": 10_000}},
        "siege": _etablissement(i),
        "dirigeants": [{"nom": f"NOM {k}", "prenoms": "PRENOM", "annee_de_naissance": "1970",
                        "qualite": "Administrateur", "type_dirigeant": "personne physique"} for k in range(40)],
        "complements": {f"est_label_{k:02d}": False for k in range(30)} | {"liste_idcc": ["0000"]},
        "matching_etablissements": [_etablissement(i * 100 + k) for k in range(10)],
    }


def _page() -> dict:
    return {"results": [_resultat(i) for i in range(25)], "total_results": 4000, "page": 1, "per_page": 25,
            "total_pages": 160}


def test_the_description_is_version_3_and_passes_the_fabrique():
    description = _description()
    assert int(description["connector"]["version"].split(".")[0]) >= 3
    check(description, FOD)


def test_per_page_keeps_the_service_default():
    per_page = _search()["input"]["properties"]["per_page"]
    assert (per_page["default"], per_page["maximum"]) == (25, 25)


def test_a_result_loses_the_heavy_keys_and_keeps_its_identity():
    received = serve.result(_search(), [_page()])
    assert (received["total_results"], received["page"], received["per_page"], received["total_pages"]) == (
        4000, 1, 25, 160)
    for resultat in received["results"]:
        assert not set(LEFT_OUT) & set(resultat)
        assert {"siren", "nom_complet", "etat_administratif", "finances"} <= set(resultat)


def test_a_full_page_of_heavy_results_stays_well_below_what_a_host_passes_on():
    page = _page()
    assert len(json.dumps(page, ensure_ascii=False)) > 10 * HOST_LIMIT
    assert len(json.dumps(serve.result(_search(), [page]), ensure_ascii=False)) < HOST_LIMIT // 2


def test_the_description_says_what_is_left_out_and_where_to_read_it():
    description = _search()["description"]
    assert "company_get" in description
    for key in ("siege", "dirigeants", "complements"):
        assert key in description
