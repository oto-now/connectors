"""Les adresses où le client Salesforce envoie un secret sont restreintes aux domaines Salesforce.

`login_url` reçoit l'identifiant et le secret de l'application et le jeton de renouvellement ; `instance_url`, que
rend la réponse de jeton, reçoit le jeton d'accès à chaque appel. Une seule fonction les contrôle
(`_check_salesforce_url`) : https, ni userinfo ni port, un hôte qui se termine par `.<domaine>` d'une liste fermée.
Hors liste, `SalesforceHostRefused`, levé avant tout envoi : à la construction pour `login_url`, au renouvellement
(avant la mise en cache), à la relecture du cache et à l'appel pour `instance_url`.

Pas de réseau : `requests.post` et `requests.request` sont remplacés.
"""
import json
import time

import pytest

from oto.tools.salesforce import client as sf_client
from oto.tools.salesforce.client import SalesforceClient, SalesforceHostRefused, _check_salesforce_url

LOGIN = "https://login.salesforce.com"

ADMITTED = [
    "https://login.salesforce.com",
    "https://test.salesforce.com",
    "https://acme.my.salesforce.com",
    "https://ACME.My.Salesforce.com",
    "https://acme--uat.sandbox.my.salesforce.com",
    "https://acme-dev-ed.develop.my.salesforce.com",
    "https://acme.my.salesforce.mil",
    "https://login.sfcrmproducts.cn",
    "https://acme.my.sfcrmproducts.cn",
]

REFUSED = [
    "https://evil.example",                               # autre domaine
    "https://salesforce.com.evil.example",                # le domaine en tête, pas en fin
    "https://evilsalesforce.com",                         # suffixe sans point
    "https://salesforce.com",                             # le domaine nu
    "http://acme.my.salesforce.com",                      # http
    "https://user:pw@acme.my.salesforce.com",             # userinfo
    "https://evil.example@acme.my.salesforce.com",        # userinfo
    "https://acme.my.salesforce.com:8443",                # port
    "https://acme.my.salesforce.com:443",                 # port, même le port par défaut
    "https://acme.my.salesforce.com.",                    # point final
    "https://acme.my.salesforce.com?next=x",              # requête
    "https://acme.my.force.com",                          # contenu, pas l'API
    "https://acme.my.site.com",                           # site public
    "https://acme.my.salesforce-sites.com",               # site public
    "ftp://acme.my.salesforce.com",
    "acme.my.salesforce.com",
    "",
]


class _Resp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self.headers = {}
        self._payload = payload
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


@pytest.fixture(autouse=True)
def _empty_cache(monkeypatch):
    monkeypatch.setattr(sf_client, "_TOKEN_CACHE", {})


def _client(login_url=LOGIN):
    return SalesforceClient(client_id="cid", client_secret="csecret", refresh_token="rtok", login_url=login_url)


@pytest.mark.parametrize("url", ADMITTED)
def test_une_adresse_salesforce_est_admise(url):
    assert _check_salesforce_url(url, "instance_url") == url


@pytest.mark.parametrize("url", REFUSED)
def test_une_adresse_hors_salesforce_est_refusee(url):
    with pytest.raises(SalesforceHostRefused) as e:
        _check_salesforce_url(url, "instance_url")
    assert "instance_url" in str(e.value)


def test_le_refus_ne_reprend_pas_le_userinfo():
    with pytest.raises(SalesforceHostRefused) as e:
        _check_salesforce_url("https://user:secret@acme.my.salesforce.com", "login_url")
    assert "secret" not in str(e.value)


@pytest.mark.parametrize("url", ["https://evil.example", "http://login.salesforce.com",
                                 "https://u@login.salesforce.com", "https://login.salesforce.com:8443"])
def test_login_url_hors_liste_refusee_avant_tout_envoi(url, monkeypatch):
    sent = []
    monkeypatch.setattr(sf_client.requests, "post", lambda *a, **k: sent.append(a) or _Resp({}))
    with pytest.raises(SalesforceHostRefused) as e:
        _client(url)
    assert "login_url" in str(e.value)
    assert sent == []


def test_login_url_par_defaut_et_my_domain_admises():
    assert SalesforceClient(client_id="a", client_secret="b", refresh_token="c").login_url == LOGIN
    assert _client("https://acme.my.salesforce.com/").login_url == "https://acme.my.salesforce.com"


@pytest.mark.parametrize("instance_url", ["https://salesforce.com.evil.example", "https://evilsalesforce.com",
                                          "http://acme.my.salesforce.com", "https://acme.my.salesforce.com:444"])
def test_instance_hors_liste_refusee_au_renouvellement_avant_la_mise_en_cache(instance_url, monkeypatch):
    monkeypatch.setattr(sf_client.requests, "post", lambda *a, **k: _Resp(
        {"access_token": "tok", "instance_url": instance_url}))
    refreshed = []
    c = SalesforceClient(client_id="cid", client_secret="csecret", refresh_token="rtok", login_url=LOGIN,
                         on_refresh=refreshed.append)
    with pytest.raises(SalesforceHostRefused):
        c._get_access_token()
    assert sf_client._TOKEN_CACHE == {}
    assert c._access_token is None and c._instance_url is None
    assert refreshed == []


def test_instance_hors_liste_refusee_a_l_appel(monkeypatch):
    sent = []
    monkeypatch.setattr(sf_client.requests, "request", lambda *a, **k: sent.append(a) or _Resp({}))
    c = _client()
    c._access_token, c._instance_url, c._token_expires_at = "tok", "https://evil.example", time.time() + 600
    with pytest.raises(SalesforceHostRefused):
        c.query("SELECT Id FROM Account")
    with pytest.raises(SalesforceHostRefused):
        c._url_for("https://evil.example", "query/")
    assert sent == []


def test_entree_du_cache_hors_liste_refusee_sans_repli(monkeypatch):
    posts, sent = [], []
    monkeypatch.setattr(sf_client.requests, "post", lambda *a, **k: posts.append(a) or _Resp(
        {"access_token": "tok", "instance_url": "https://acme.my.salesforce.com"}))
    monkeypatch.setattr(sf_client.requests, "request", lambda *a, **k: sent.append(a) or _Resp({}))
    c = _client()
    key = sf_client._cred_key(c.login_url, c.client_id, c.refresh_token)
    sf_client._TOKEN_CACHE[key] = ("tok", "https://evil.example", time.time() + 600)
    with pytest.raises(SalesforceHostRefused):
        c.query("SELECT Id FROM Account")
    assert posts == [] and sent == [], "ni renouvellement de repli, ni appel"


def test_instance_admise_sert_l_appel(monkeypatch):
    sent = []
    monkeypatch.setattr(sf_client.requests, "post", lambda *a, **k: _Resp(
        {"access_token": "tok", "instance_url": "https://acme--uat.sandbox.my.salesforce.com"}))
    monkeypatch.setattr(sf_client.requests, "request",
                        lambda method, url, **k: sent.append(url) or _Resp({"done": True, "records": []}))
    _client().query("SELECT Id FROM Account")
    assert sent == ["https://acme--uat.sandbox.my.salesforce.com/services/data/v60.0/query/"]
