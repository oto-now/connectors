"""Compactage des réponses entreprise et ICPE (`oto.tools.fod.compact`), sans réseau.

Le module re-exporte `france_opendata.compact` : ces tests fixent le contrat que les consommateurs de la lib lisent
(oto 1 dans ses outils fr_search, fr_get et foncier_icpe). Les clients amont sont des doublures de même surface.
"""
from __future__ import annotations

import pytest

from oto.tools.fod import compact
from oto.tools.fod.compact import (
    FILTRE_CA_AVERTISSEMENT,
    ICPE_MAX_RUBRIQUES,
    ParametreInvalide,
    compact_icpe,
    compact_rubriques,
    profil_entreprise,
    profils_entreprises,
    rechercher,
    split_legal_form,
)


# --- ICPE : rubriques ---------------------------------------------------------

def _rubrique(numero: str, nature: str = "peu importe") -> dict:
    return {"numeroRubrique": numero, "nature": nature, "regimeAutoriseAlinea": "Déclaration",
            "quantiteTotale": "1.000", "unite": "m3"}


def test_les_rubriques_sortent_avec_leur_quantite_et_leur_unite():
    rubriques, _, _ = compact_rubriques([_rubrique("2663", "Stockage de pneumatiques")])
    assert rubriques == [{"numero": "2663", "nature": "Stockage de pneumatiques", "regime": "Déclaration",
                          "quantite": "1.000", "unite": "m3"}]


def test_une_rubrique_energetique_est_lue_pas_seulement_rendue():
    _, energie, _ = compact_rubriques([_rubrique("2910", "Combustion")])
    assert energie[0]["numero"] == "2910"
    assert energie[0]["lecture"] == "combustion (boilers, engines)"


def test_une_rubrique_sans_signal_energetique_n_est_pas_interpretee():
    _, energie, _ = compact_rubriques([_rubrique("2663")])
    assert energie == []


def test_la_troncature_ne_mange_pas_la_rubrique_qui_porte_le_signal():
    brutes = [_rubrique(str(4000 + i)) for i in range(ICPE_MAX_RUBRIQUES + 5)]
    brutes.append(_rubrique("2910", "Combustion"))
    rubriques, energie, tronquees = compact_rubriques(brutes)
    assert tronquees is True
    assert len(rubriques) == ICPE_MAX_RUBRIQUES
    assert rubriques[0]["numero"] == "2910"
    assert energie[0]["numero"] == "2910"


def test_une_fiche_sans_rubrique_rend_une_liste_vide_pas_une_erreur():
    assert compact_rubriques([]) == ([], [], False)


def test_fiche_icpe_trois_dernieres_inspections_et_troncature_dite():
    fiche = {"raisonSociale": "ACME", "siret": "81103850400019", "bruit": "x" * 100,
             "inspections": [{"dateInspection": f"2026-0{i}-01", "fichierInspection": {"urlFichier": f"u{i}"}}
                             for i in range(1, 6)],
             "rubriques": [_rubrique(str(4000 + i)) for i in range(ICPE_MAX_RUBRIQUES + 1)]}
    out = compact_icpe(fiche)
    assert "bruit" not in out and len(out) == 22 + 3 + 1
    assert out["inspections"] == [{"date": f"2026-0{i}-01", "url": f"u{i}"} for i in (3, 4, 5)]
    assert out["rubriques_tronquees"] is True


# --- Recherche ----------------------------------------------------------------

def _entreprise(siren: str, **extra) -> dict:
    return {"siren": siren, "nom_complet": f"SOCIETE {siren}", "etat_administratif": "A",
            "activite_principale": "68.20B", "complements": {"liste_idcc": ["1486"], "est_ess": False},
            "siege": {"siret": siren + "00011", "latitude": 50.6}, "dirigeants": [], **extra}


class _Entreprises:
    def __init__(self, pages: dict):
        self.pages, self.calls = pages, []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return {"results": list(self.pages[(kwargs["query"], tuple(kwargs["nature_juridique"] or ()))]),
                "total_results": 1}


@pytest.mark.parametrize("query, attendu", [
    ("SCI ASC", ("SCI", "ASC")),
    ("sas. Dupont", ("SAS", "Dupont")),
    ("SCI", None),
    ("ASCENSEURS DU NORD", None),
    (None, None),
])
def test_split_legal_form(query, attendu):
    assert split_legal_form(query) == attendu


def test_recherche_compacte_et_reprend_par_forme_juridique():
    sci = compact.LEGAL_FORM_CODES["SCI"]
    client = _Entreprises({("SCI ASC", ()): [_entreprise("111111111")],
                           ("ASC", sci): [_entreprise("111111111"), _entreprise("222222222")]})
    res = rechercher(client, query="SCI ASC")
    assert [r["siren"] for r in res["results"]] == ["111111111", "222222222"]
    assert "matched_by" not in res["results"][0], "la recherche littérale reste en tête, non marquée"
    assert res["results"][1]["matched_by"] == "legal_form"
    assert res["legal_form_retry"] == {"form": "SCI", "query": "ASC", "nature_juridique": list(sci),
                                       "total_results": 1, "added": 1}
    premier = res["results"][0]
    assert premier["liste_idcc"] == ["1486"] and "complements" not in premier
    assert premier["siege"] == {"siret": "11111111100011"}, "le siège garde ses seules clés listées"
    assert "filtre_ca_avertissement" not in res


def test_pas_de_reprise_hors_page_1_ni_avec_une_nature_juridique_explicite():
    client = _Entreprises({("SCI ASC", ()): [], ("SCI ASC", ("6540",)): []})
    assert "legal_form_retry" not in rechercher(client, query="SCI ASC", page=2)
    assert "legal_form_retry" not in rechercher(client, query="SCI ASC", nature_juridique=["6540"])
    assert len(client.calls) == 2


def test_un_filtre_de_ca_porte_son_avertissement():
    client = _Entreprises({(None, ()): []})
    assert rechercher(client, ca_max=400000)["filtre_ca_avertissement"] == FILTRE_CA_AVERTISSEMENT


def test_etablissements_bornes_et_troncature_dite():
    etabs = [{"siret": f"{i:014d}", "latitude": 1.0} for i in range(30)]
    client = _Entreprises({("X", ()): [_entreprise("111111111", matching_etablissements=etabs)]})
    r = rechercher(client, query="X")["results"][0]
    assert len(r["etablissements"]) == compact.ETABLISSEMENTS_MAX
    assert r["_etablissements_truncated"] == 30
    assert r["etablissements"][0] == {"siret": "00000000000000"}


# --- Profil -------------------------------------------------------------------

class _Inpi:
    def __init__(self, exercises=None, fail: bool = False):
        self.exercises, self.fail = exercises, fail

    def list_exercises(self, siren):
        if self.fail:
            raise TimeoutError("INPI muet")
        return self.exercises

    def get_bilan(self, siren, date):
        return {"date_cloture_exercice": date, "chiffre_d_affaires": 1200, "alerte": None, "hors_liste": 1}


class _Bodacc:
    def __init__(self, results=()):
        self.results = list(results)

    def search_by_siren(self, siren, famille, limit):
        return {"results": self.results, "total_count": len(self.results)}


class _Identites:
    def __init__(self, fiches: dict):
        self.fiches = fiches

    def get_by_siren(self, siren):
        fiche = self.fiches[siren]
        if isinstance(fiche, Exception):
            raise fiche
        return fiche


_CLOTURE = {"id": "B1", "dateparution": "2026-03-02", "familleavis": "collective",
            "jugement": {"nature": "Jugement de clôture pour insuffisance d'actif", "date": "2026-02-20"}}


def test_liquidation_cloturee_contredit_un_registre_resté_actif():
    out = profil_entreprise("111111111", entreprises=_Identites({"111111111": _entreprise("111111111")}),
                            inpi=_Inpi([]), bodacc=_Bodacc([_CLOTURE]))
    assert out["procedure_collective_cloturee"] == {
        "nature": "Jugement de clôture pour insuffisance d'actif", "date_jugement": "2026-02-20",
        "date_parution": "2026-03-02", "id": "B1"}
    assert "etat_administratif_avertissement" in out
    assert out["bilans_outline"] == [] and "finances_note" in out


def test_cloture_d_un_plan_de_sauvegarde_ne_clot_pas_la_societe():
    sauvegarde = {**_CLOTURE, "jugement": {"nature": "Jugement de clôture du plan de sauvegarde"}}
    assert compact.cloture_liquidation([sauvegarde]) is None


def test_panne_des_bilans_dite_dans_partial_errors_sans_faire_tomber_la_fiche():
    out = profil_entreprise("111111111", entreprises=_Identites({"111111111": _entreprise("111111111")}),
                            inpi=_Inpi(fail=True), bodacc=_Bodacc())
    assert out["partial_errors"] == {"latest_bilan": "TimeoutError: INPI muet"}
    assert out["bilans_outline"] is None and out["latest_bilan"] is None
    assert out["identity"]["siren"] == "111111111"


def test_dernier_bilan_reduit_et_survol_des_exercices():
    exercices = [{"date_cloture_exercice": "2025-12-31", "type_bilan": "C", "confidentiality": "Public",
                  "chiffre_d_affaires": 1200, "alerte": None, "hors_liste": 1}]
    out = profil_entreprise("111111111", entreprises=_Identites({"111111111": _entreprise("111111111")}),
                            inpi=_Inpi(exercices), bodacc=_Bodacc())
    assert set(out["latest_bilan"]) == set(compact.LATEST_BILAN_KEYS)
    assert out["bilans_outline"] == [{"date_cloture_exercice": "2025-12-31", "type_bilan": "C",
                                      "confidentiality": "Public", "chiffre_d_affaires": 1200, "alerte": None}]


def test_identite_en_panne_ou_absente():
    entreprises = _Identites({"111111111": RuntimeError("down"), "222222222": None})
    panne = profil_entreprise("111111111", entreprises=entreprises, inpi=_Inpi([]), bodacc=_Bodacc())
    assert panne["error"] == "identity_unavailable"
    assert panne["partial_errors"]["identity"] == "RuntimeError: down"
    absente = profil_entreprise("222222222", entreprises=entreprises, inpi=_Inpi([]), bodacc=_Bodacc())
    assert absente == {"error": "not_found", "siren": "222222222"}


def test_lot_dans_l_ordre_recu():
    entreprises = _Identites({s: _entreprise(s) for s in ("111111111", "222222222")})
    out = profils_entreprises([" 222222222 ", "111111111", ""], entreprises=entreprises, inpi=_Inpi([]),
                              bodacc=_Bodacc())
    assert out["count"] == 2
    assert [p["siren"] for p in out["profiles"]] == ["222222222", "111111111"]


@pytest.mark.parametrize("sirens", [[], ["  "], [str(i) for i in range(compact.PROFILS_LOT_MAX + 1)]])
def test_borne_du_lot(sirens):
    with pytest.raises(ParametreInvalide):
        profils_entreprises(sirens, entreprises=_Identites({}), inpi=_Inpi([]), bodacc=_Bodacc())
