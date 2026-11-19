"""Crunchbase, exécuté dans le navigateur de l'hôte : requêtes, refus de session, sonde de login.

Un faux navigateur d'hôte rend des réponses posées : aucun appel à Crunchbase.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from oto.tools.common.browser_session import AUTH_REJECTED, LOGGED_IN, NO_SESSION, ProbeUnavailable
from oto.tools.common.credentials import MissingCredential
from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.common.host_session import SessionRejected
from oto.tools.crunchbase import CrunchbaseClient
from oto.tools.crunchbase.client import API_URL, APP_URL, permalink, read_probe


class Navigateur:
    def __init__(self, reponse):
        self.appels: list[tuple] = []
        self.reponse = reponse

    def __call__(self, jar, page_url, page_function, arg):
        self.appels.append((jar, page_url, page_function, arg))
        return self.reponse


def _client(reponse=None, jar="profil-1", on_jar=None):
    nav = Navigateur({"status": 200, "data": {"ok": True}} if reponse is None else reponse)
    return CrunchbaseClient(nav, jar=jar, on_jar=on_jar), nav


def _url(nav):
    jar, page, _, arg = nav.appels[-1]
    assert jar == "profil-1" and page == APP_URL and arg["method"] == "GET"
    u = urlsplit(arg["url"])
    return u.path, parse_qs(u.query)


def test_la_fiche_d_une_organisation_lit_ses_cartes_depuis_une_url_ou_un_permalink():
    client, nav = _client()
    assert client.get_company("https://www.crunchbase.com/organization/anthropic?tab=x") == {"ok": True}
    assert _url(nav) == ("/v4/data/entities/organizations/anthropic",
                         {"card_ids": ["founders,raised_funding_rounds"]})
    client.get_funding_rounds("anthropic")
    assert _url(nav) == ("/v4/data/entities/organizations/anthropic",
                         {"card_ids": ["raised_funding_rounds"]})


def test_la_fiche_d_une_personne():
    client, nav = _client()
    client.get_person("crunchbase.com/person/jane-doe")
    assert _url(nav) == ("/v4/data/entities/people/jane-doe", {})


def test_la_recherche_vise_la_bonne_collection_et_borne_la_limite():
    client, nav = _client()
    client.search_companies("acme", limit=5)
    assert _url(nav) == ("/v4/data/autocompletes", {"query": ["acme"],
                         "collection_ids": ["organization.companies"], "limit": ["5"]})
    client.search_people("jane")
    assert _url(nav)[1]["collection_ids"] == ["person.people"]
    for limite in (0, 26):
        with pytest.raises(ValueError, match="limit"):
            client.search_companies("acme", limit=limite)
    with pytest.raises(ValueError, match="query"):
        client.search_people("  ")


def test_un_permalink_vide_est_refuse_avant_tout_appel():
    client, nav = _client()
    with pytest.raises(ValueError, match="permalink"):
        client.get_company("https://www.crunchbase.com/organization/")
    assert nav.appels == []
    assert permalink("acme", "organization") == "acme"


@pytest.mark.parametrize("statut", [401, 403])
def test_une_session_refusee_leve_session_rejected(statut):
    client, _ = _client({"status": statut, "data": None})
    with pytest.raises(SessionRejected) as e:
        client.get_person("jane-doe")
    assert e.value.code == "credential_rejected"


def test_une_autre_erreur_est_une_erreur_amont_nommee():
    client, _ = _client({"status": 404, "data": {"error": "not found"}})
    with pytest.raises(UpstreamHTTPError) as e:
        client.get_company("inconnue")
    assert e.value.status_code == 404 and e.value.service == "crunchbase"
    assert e.value.url == API_URL + "/entities/organizations/inconnue"


def test_sans_session_rien_ne_part():
    client, nav = _client(jar=None)
    with pytest.raises(MissingCredential) as e:
        client.get_company("acme")
    assert e.value.name == "CRUNCHBASE_SESSION" and nav.appels == []


def test_la_sonde_tranche_sur_le_statut():
    assert read_probe(200).reason == LOGGED_IN
    assert read_probe(401).reason == AUTH_REJECTED and read_probe(403).reason == AUTH_REJECTED
    assert read_probe(0).reason == NO_SESSION
    with pytest.raises(ProbeUnavailable):
        read_probe(404)


def test_un_login_confirme_rend_la_session_a_l_hote():
    rendues: list[str] = []
    client, nav = _client(reponse=200, jar=None, on_jar=rendues.append)
    assert client.finish_login("profil-neuf").connected
    assert rendues == ["profil-neuf"]
    jar, page, _, arg = nav.appels[0]
    assert jar == "profil-neuf" and page == APP_URL and arg.startswith(API_URL)


def test_un_login_inacheve_ne_rend_rien():
    rendues: list[str] = []
    client, _ = _client(reponse=0, jar=None, on_jar=rendues.append)
    verdict = client.finish_login("profil-neuf")
    assert not verdict.connected and verdict.reason == NO_SESSION and rendues == []
