"""Le contrat de session gardée par l'hôte : reçue en `jar`, rendue par `on_jar`, rien de stocké dans la lib.

Un faux navigateur d'hôte (`run`) suffit : aucun navigateur, aucun appel réseau.
"""
from __future__ import annotations

import pytest

from oto.tools.common.browser_session import (
    AUTH_REJECTED,
    FETCH_JS,
    LOGGED_IN,
    BrowserSession,
    ProbeUnavailable,
    Verdict,
)
from oto.tools.common.credentials import MissingCredential
from oto.tools.common.host_session import HostJar, SessionRejected
from oto.tools.http import SessionRejected as SessionRejectedHttp


class Navigateur:
    """Faux navigateur de l'hôte : note chaque évaluation et rend la réponse posée."""

    def __init__(self, reponse=None):
        self.appels: list[tuple] = []
        self.reponse = reponse

    def __call__(self, jar, page_url, page_function, arg):
        self.appels.append((jar, page_url, page_function, arg))
        return self.reponse


def test_le_mode_session_du_noeud_http_et_le_navigateur_refusent_par_la_meme_exception():
    assert SessionRejectedHttp is SessionRejected
    assert SessionRejected.code == "credential_rejected"


def test_host_jar_ne_rend_la_session_que_quand_elle_change():
    rendues: list[str] = []
    hote = HostJar("s1", rendues.append)
    hote.save("s1")
    hote.save("s2")
    hote.save("s2")
    assert rendues == ["s2"] and hote.text == "s2"


def test_une_session_vide_compte_comme_absente():
    assert HostJar("", None).text is None


def test_sans_navigateur_d_hote_rien_ne_se_construit():
    with pytest.raises(ValueError, match="browser runner"):
        BrowserSession(None, name="X_SESSION")


def test_sans_session_l_appel_ne_part_pas():
    nav = Navigateur()
    with pytest.raises(MissingCredential) as e:
        BrowserSession(nav, name="X_SESSION").evaluate("https://app.example.org/", "async () => 1")
    assert e.value.name == "X_SESSION" and nav.appels == []


def test_fetch_passe_la_session_gardee_au_navigateur_et_rend_statut_et_donnees():
    nav = Navigateur({"status": 200, "data": {"ok": True}})
    session = BrowserSession(nav, name="X_SESSION", jar="profil-1")
    assert session.fetch("https://app.example.org/", "https://app.example.org/api/x", "POST",
                         {"a": 1}) == {"status": 200, "data": {"ok": True}}
    assert nav.appels == [("profil-1", "https://app.example.org/", FETCH_JS,
                           {"url": "https://app.example.org/api/x", "method": "POST", "body": {"a": 1}})]


def test_un_navigateur_qui_ne_rend_pas_le_resultat_du_fetch_leve():
    session = BrowserSession(Navigateur({"oops": 1}), name="X_SESSION", jar="profil-1")
    with pytest.raises(ValueError, match="fetch result"):
        session.fetch("https://app.example.org/", "https://app.example.org/api/x")


def test_un_login_confirme_rend_la_session_a_l_hote_et_sert_aux_appels_suivants():
    rendues: list[str] = []
    nav = Navigateur({"status": 200, "data": None})
    session = BrowserSession(nav, name="X_SESSION", on_jar=rendues.append)
    sondes: list = []

    def sonde(evaluate):
        sondes.append(evaluate("https://app.example.org/", "async () => 1", None))
        return Verdict(True, LOGGED_IN)

    assert session.finish_login("profil-neuf", sonde) == Verdict(True, LOGGED_IN)
    assert rendues == ["profil-neuf"] and nav.appels[0][0] == "profil-neuf"
    session.fetch("https://app.example.org/", "https://app.example.org/api/x")
    assert nav.appels[-1][0] == "profil-neuf"


def test_un_login_refuse_ne_rend_rien_et_garde_l_ancienne_session():
    rendues: list[str] = []
    session = BrowserSession(Navigateur(), name="X_SESSION", jar="ancien", on_jar=rendues.append)
    verdict = session.finish_login("profil-neuf", lambda ev: Verdict(False, AUTH_REJECTED, "refus"))
    assert not verdict.connected and rendues == []


def test_une_sonde_sans_verdict_remonte_et_ne_rend_rien():
    rendues: list[str] = []
    session = BrowserSession(Navigateur(), name="X_SESSION", on_jar=rendues.append)

    def sonde(evaluate):
        raise ProbeUnavailable("404")

    with pytest.raises(ProbeUnavailable):
        session.finish_login("profil-neuf", sonde)
    assert rendues == []
