"""Client n8n : la clé `X-N8N-API-KEY` ne suit jamais une redirection.

Aucun réseau : le transport est doublé."""
from __future__ import annotations

import pytest

from oto.tools.n8n import client as mod

class _Redirige:
    """Une 3xx vers un autre hôte, dont le `Location` garde la clé en query."""
    status_code = 302
    headers = {"Location": "https://ailleurs.test/v1/x?api_key=SECRET#f"}
    content = b""
    text = ""
    ok = False

    def json(self):
        return {}

    def raise_for_status(self):
        return None


def test_une_redirection_est_refusee_jamais_suivie(monkeypatch):
    """La clé part dans un en-tête maison que `requests` ne retire pas sur un changement
    d'hôte : une 3xx est refusée (`RedirectRefused`), jamais suivie ni retentée, et
    sa cible est réduite à schéma, hôte et chemin — jamais sa query."""
    from oto.tools.http.client import RedirectRefused
    vus = []

    def fake(method, url, **kw):
        vus.append(kw)
        return _Redirige()

    c = mod.N8nClient(api_key="k", base_url="https://n8n.test")
    c.session.request = fake
    with pytest.raises(RedirectRefused) as e:
        c.get_workflow("w1")
    assert vus[0]["allow_redirects"] is False
    assert e.value.location == "https://ailleurs.test/v1/x"
    assert "SECRET" not in str(e.value)
    assert len(vus) == 1


class _Ok:
    """Une 200 qui rend `payload`."""
    status_code = 200
    headers: dict = {}
    ok = True

    def __init__(self, payload):
        self._payload = payload
        self.content = b"x"
        self.text = "x"

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


#: Un workflow tel que `get_workflow` le rend : des clés en lecture seule à côté du corps.
_LU = {
    "id": "w1", "name": "Relance", "active": True, "activeVersionId": "v9",
    "createdAt": "2026-11-01T00:00:00.000Z", "updatedAt": "2026-11-02T00:00:00.000Z",
    "isArchived": False, "versionId": "v9", "triggerCount": 1, "tags": [{"id": "t1"}],
    "shared": [{"role": "workflow:owner", "createdAt": "2026-11-01T00:00:00.000Z"}],
    "nodes": [{"name": "Start", "type": "n8n-nodes-base.manualTrigger"}],
    "connections": {}, "settings": {"executionOrder": "v1"},
}


def _client_qui_note(reponses):
    """Un client dont chaque appel est noté, et qui rend tour à tour `reponses`."""
    vus = []

    def fake(method, url, **kw):
        vus.append((method, url, kw))
        return _Ok(reponses[len(vus) - 1])

    c = mod.N8nClient(api_key="k", base_url="https://n8n.test")
    c.session.request = fake
    return c, vus


def test_workflow_body_ne_garde_que_les_cles_ecrivables():
    corps, ecartees = mod.workflow_body(_LU)
    assert set(corps) == {"name", "nodes", "connections", "settings"}
    assert ecartees == sorted(["id", "active", "activeVersionId", "createdAt", "updatedAt",
                               "isArchived", "versionId", "triggerCount", "tags", "shared"])
    assert "projectId" not in mod.workflow_body({"projectId": "p"})[0]
    assert mod.workflow_body({"projectId": "p"}, create=True)[0] == {"projectId": "p"}


def test_un_null_n_est_envoye_que_la_ou_l_api_l_accepte():
    """Un workflow lu porte `description: null`, que l'API refuse en écriture : il est
    écarté (la description reste inchangée), tandis que `parentFolderId: null` (la
    racine du projet) et `pinData: null` passent."""
    corps, ecartees = mod.workflow_body(dict(_LU, description=None, parentFolderId=None,
                                             pinData=None))
    assert "description" not in corps and "description" in ecartees
    assert corps["parentFolderId"] is None and corps["pinData"] is None


def test_creer_un_workflow_poste_le_corps_ecrivable_et_des_reglages_par_defaut():
    c, vus = _client_qui_note([{"id": "w2"}])
    a_creer = {k: v for k, v in _LU.items() if k != "settings"}
    assert c.create_workflow(a_creer) == {"id": "w2"}
    method, url, kw = vus[0]
    assert (method, url) == ("POST", "https://n8n.test/api/v1/workflows")
    assert kw["json"] == {"name": "Relance", "nodes": _LU["nodes"], "connections": {},
                          "settings": {}}


def test_un_workflow_lu_puis_renvoye_se_remplace_sans_relecture():
    """Le geste `get_workflow` → changer → `update_workflow` : les clés en lecture seule
    sont retirées, et rien n'est relu puisque le corps est complet."""
    c, vus = _client_qui_note([{"id": "w1"}])
    c.update_workflow("w1", dict(_LU, name="Relance v2"))
    assert len(vus) == 1
    method, url, kw = vus[0]
    assert (method, url) == ("PUT", "https://n8n.test/api/v1/workflows/w1")
    assert kw["json"]["name"] == "Relance v2"
    assert set(kw["json"]) == {"name", "nodes", "connections", "settings"}
    assert kw["params"] is None


def test_une_modification_partielle_garde_le_reste_du_workflow():
    """Le PUT remplace tout : une clé requise omise reprend sa valeur actuelle (une
    lecture avant l'écriture), jamais un vide."""
    c, vus = _client_qui_note([_LU, {"id": "w1"}])
    c.update_workflow("w1", {"name": "Renommé"})
    assert [(m, u) for m, u, _ in vus] == [
        ("GET", "https://n8n.test/api/v1/workflows/w1"),
        ("PUT", "https://n8n.test/api/v1/workflows/w1")]
    assert vus[1][2]["json"] == {"name": "Renommé", "nodes": _LU["nodes"],
                                 "connections": {}, "settings": {"executionOrder": "v1"}}


def test_publish_if_active_ne_part_que_s_il_est_donne():
    c, vus = _client_qui_note([{"id": "w1"}])
    c.update_workflow("w1", _LU, publish_if_active=False)
    assert vus[0][2]["params"] == {"publishIfActive": "false"}


def test_supprimer_et_poser_les_tags():
    c, vus = _client_qui_note([{"id": "w1"}, [{"id": "t1"}, {"id": "t2"}]])
    c.delete_workflow("w1")
    c.set_workflow_tags("w1", ["t1", "t2"])
    assert (vus[0][0], vus[0][1]) == ("DELETE", "https://n8n.test/api/v1/workflows/w1")
    assert (vus[1][0], vus[1][1]) == ("PUT", "https://n8n.test/api/v1/workflows/w1/tags")
    assert vus[1][2]["json"] == [{"id": "t1"}, {"id": "t2"}]
