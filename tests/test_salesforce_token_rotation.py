"""Le client Salesforce doit survivre à la rotation des jetons (RTR).

Contexte : la rotation est un **contrôle obligatoire** des External Client Apps
Salesforce (échéance 2026). Sous ce régime, chaque échange invalide le refresh token
utilisé et en renvoie un neuf. Cette classe jetait le neuf — donc la connexion était
révoquée dès le premier appel, et toute réutilisation du jeton mort fait révoquer par
Salesforce le jeton courant ET les access tokens associés.

Vécu le 31/07 sur une connexion client : jeton posé à 12:07:31.570, sonde réussie à
12:07:32.089, jeton mort à 12:07:33.
"""
from __future__ import annotations

import time

import pytest

from oto.tools.salesforce.client import SalesforceClient


class _Resp:
    def __init__(self, payload: dict, status: int = 200):
        self._payload, self.status_code = payload, status

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError("ne devrait pas être atteint dans ces tests")


@pytest.fixture(autouse=True)
def _cache_propre():
    """Le cache est process-wide : sans purge, un test sert le jeton d'un autre."""
    from oto.tools.salesforce import client as m
    m._TOKEN_CACHE.clear()
    yield
    m._TOKEN_CACHE.clear()


def _client(**kw) -> SalesforceClient:
    return SalesforceClient(client_id="ci", client_secret="cs",
                            refresh_token="RT-1", login_url="https://acme.my.salesforce.com", **kw)


def _token(refresh: str | None, access: str = "AT") -> dict:
    body = {"access_token": access, "instance_url": "https://acme.my.salesforce.com"}
    if refresh:
        body["refresh_token"] = refresh
    return body


# --- rotation ------------------------------------------------------------------

def test_le_jeton_renouvele_est_adopte(monkeypatch):
    monkeypatch.setattr("oto.tools.salesforce.client.requests.post",
                        lambda *a, **k: _Resp(_token("RT-2")))
    c = _client()
    c._get_access_token()
    assert c.refresh_token == "RT-2", (
        "le jeton renouvelé est jeté : la connexion meurt dès le premier appel")


def test_le_jeton_renouvele_est_notifie_a_lappelant(monkeypatch):
    """Adopter le jeton en mémoire ne suffit pas : l'instance est jetée à la fin de
    la requête. Sans notification, le coffre garde le jeton révoqué."""
    monkeypatch.setattr("oto.tools.salesforce.client.requests.post",
                        lambda *a, **k: _Resp(_token("RT-2")))
    vus = []
    _client(on_refresh=vus.append)._get_access_token()
    assert [v.get("refresh_token") for v in vus] == ["RT-2"]


def test_sans_rotation_le_jeton_est_conserve(monkeypatch):
    """Tous les fournisseurs ne tournent pas : l'absence de `refresh_token` dans la
    réponse ne doit pas effacer celui qu'on détient."""
    monkeypatch.setattr("oto.tools.salesforce.client.requests.post",
                        lambda *a, **k: _Resp(_token(None)))
    c = _client()
    c._get_access_token()
    assert c.refresh_token == "RT-1"


def test_une_persistance_en_echec_ne_casse_pas_lappel(monkeypatch):
    """Le jeton d'accès obtenu est valide : un échec d'écriture ne doit pas priver
    l'utilisateur de son appel."""
    monkeypatch.setattr("oto.tools.salesforce.client.requests.post",
                        lambda *a, **k: _Resp(_token("RT-2")))

    def _boom(_):
        raise RuntimeError("coffre indisponible")

    token, _ = _client(on_refresh=_boom)._get_access_token()
    assert token == "AT"


# --- cache process-wide --------------------------------------------------------

def test_un_second_client_reutilise_le_jeton_du_premier(monkeypatch):
    """LE point qui rendait la rotation explosive : le serveur construit une instance
    neuve à CHAQUE appel d'outil, donc le cache d'instance ne servait jamais et on
    rafraîchissait — donc on faisait tourner le jeton — à chaque appel."""
    appels = []
    monkeypatch.setattr("oto.tools.salesforce.client.requests.post",
                        lambda *a, **k: (appels.append(1), _Resp(_token(None)))[1])
    assert _client()._get_access_token() == ("AT", "https://acme.my.salesforce.com")
    assert _client()._get_access_token() == ("AT", "https://acme.my.salesforce.com")
    assert len(appels) == 1, "un client neuf a re-rafraîchi alors qu'un jeton valide existait"


def test_un_jeton_renouvele_ne_ressert_pas_une_entree_perimee(monkeypatch):
    """La clé de cache inclut le refresh_token : après rotation, la clé change, donc
    l'entrée liée au jeton révoqué n'est jamais resservie."""
    from oto.tools.salesforce import client as m
    monkeypatch.setattr("oto.tools.salesforce.client.requests.post",
                        lambda *a, **k: _Resp(_token("RT-2")))
    c = _client()
    c._get_access_token()
    assert m._cred_key("https://acme.my.salesforce.com", "ci", "RT-1") in m._TOKEN_CACHE
    assert m._cred_key("https://acme.my.salesforce.com", "ci", "RT-2") not in m._TOKEN_CACHE


def test_invalider_purge_aussi_le_cache_partage(monkeypatch):
    """Ne vider que le cache d'instance laisserait le prochain appel resservir un
    jeton qu'on vient de juger mort — et reboucler sur le même 401."""
    from oto.tools.salesforce import client as m
    monkeypatch.setattr("oto.tools.salesforce.client.requests.post",
                        lambda *a, **k: _Resp(_token(None)))
    c = _client()
    c._get_access_token()
    assert m._TOKEN_CACHE
    c._invalidate_token()
    assert not m._TOKEN_CACHE, "le jeton jugé mort reste servi aux appels suivants"


def test_aucun_secret_en_clair_dans_la_cle_de_cache():
    from oto.tools.salesforce import client as m
    k = m._cred_key("https://acme.my.salesforce.com", "ci-secret", "RT-secret")
    assert "RT-secret" not in k and "ci-secret" not in k and len(k) == 64
