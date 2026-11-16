"""Les vues d'une page d'activités de lemlist (`fields`, `compact`) : appliquées par le client à chaque activité,
jamais envoyées à lemlist ; sans vue, la liste de lemlist telle quelle, le même objet.

Une activité porte son lead entier (`lead.variables` : toutes les copies personnalisées de la séquence, en HTML) et
ses métadonnées : 100 activités pèsent des centaines de milliers de caractères. Les activités ci-dessous sont
inventées, à la forme de la route `GET /activities` (version v2).
"""
from __future__ import annotations

import json

import pytest

from oto.tools.lemlist import activity_views as av
from oto.tools.lemlist import client as lm


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.content = b"x"
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _sent(i: int) -> dict:
    copy_ = "Hello Ada,<br><br>" + "x" * 2000
    return {
        "_id": f"act_{i}", "type": "emailsSent", "emailId": f"eml_{i}", "messageId": f"<m{i}@example.test>",
        "emailTemplateId": "etp_1", "to": [{"address": f"ada{i}@example.test", "name": "Ada Example"}],
        "createdAt": f"2026-01-0{i % 9 + 1}T10:00:00.000Z", "isFirst": True, "stopped": False, "paused": False,
        "subject": "An idea", "messagePreview": "Hello Ada,", "bot": False, "teamId": "tea_1",
        "leadId": f"lea_{i}", "campaignId": "cam_1", "sequenceId": "seq_1", "stepId": "stp_1",
        "createdBy": "usr_1", "sendUserId": "usr_1", "totalSequenceStep": 1, "journeyDepth": 1,
        "name": "Example campaign", "sendUserName": "Sam Sender", "sendUserEmail": "sam@example.test",
        "sendUserMailboxId": "usm_1", "leadFirstName": "Ada", "leadLastName": "Example",
        "leadEmail": f"ada{i}@example.test", "leadCompanyName": "Example Inc", "contactId": f"ctc_{i}",
        "companyId": f"cpn_{i}", "metaData": {"teamId": "tea_1", "campaignId": "cam_1", "s3ready": True},
        "lead": {"_id": f"lea_{i}", "state": "emailsSent", "variables": {
            "email": f"ada{i}@example.test", "companyDescription": "d" * 3000,
            "technologies": [f"t{n}" for n in range(200)],
            **{f"h_e{n}": copy_ for n in range(1, 6)}}, "enrichment": {}},
        "campaignName": "Example campaign", "sequenceTested": "A", "sequenceStep": 1, "emailTemplateName": "Blank",
    }


def _reply() -> dict:
    return {
        "_id": "act_r", "type": "emailsReplied", "createdAt": "2026-01-10T08:00:00.000Z",
        "fromEmail": "ada@example.test", "contactId": "ctc_1", "subject": "Re: An idea",
        "messagePreview": "Thanks,", "text": "Thanks,\n\n  happy to   talk next week.\n" + "q" * 1000,
        "html": "<div>Thanks,<br>happy to talk next week.</div>" + "<p>q</p>" * 1000,
        "metaData": {"teamId": "tea_1"},
    }


# --- Sans vue : rien ne change --------------------------------------------------------------------------------------

def test_sans_vue_la_liste_est_rendue_telle_quelle():
    page = [_sent(1), _reply()]
    assert av.shape_activities(page, None) is page
    assert av.view_of() is None
    assert av.view_of(None, False) is None


def test_le_client_sans_vue_rend_la_reponse_de_lemlist(monkeypatch):
    body = [_sent(1)]
    monkeypatch.setattr(lm.requests, "request", lambda *a, **k: _Resp(body))
    out = lm.LemlistClient(api_key="k").get_activities(campaign_id="cam_1")
    assert out == body and out[0]["lead"]["variables"]["h_e1"].startswith("Hello")


# --- compact ---------------------------------------------------------------------------------------------------------

def test_compact_garde_qui_quoi_quand_et_un_extrait():
    sent = av.shape_activities([_sent(3)], av.view_of(compact=True))[0]
    assert sent == {
        "_id": "act_3", "type": "emailsSent", "createdAt": "2026-01-04T10:00:00.000Z", "campaignId": "cam_1",
        "campaignName": "Example campaign", "sequenceStep": 1, "leadId": "lea_3", "contactId": "ctc_3",
        "leadEmail": "ada3@example.test", "leadFirstName": "Ada", "leadLastName": "Example",
        "leadCompanyName": "Example Inc", "sendUserEmail": "sam@example.test", "subject": "An idea",
        "excerpt": "Hello Ada,",
    }


def test_compact_d_une_reponse_extrait_le_texte_et_jette_le_html():
    reply = av.shape_activities([_reply()], av.view_of(compact=True))[0]
    assert reply["fromEmail"] == "ada@example.test" and reply["contactId"] == "ctc_1"
    assert "html" not in reply and "text" not in reply and "messagePreview" not in reply
    assert reply["excerpt"].startswith("Thanks, happy to talk next week. qqq")
    assert len(reply["excerpt"]) == av.EXCERPT_CHARS + 1 and reply["excerpt"].endswith("…")


def test_compact_tire_l_extrait_du_html_quand_il_n_y_a_que_lui():
    activity = {"_id": "act_h", "type": "linkedinReplied", "html": "<p>Sure,&nbsp;<b>call me</b></p><br>"}
    assert av.shape_activities([activity], av.view_of(compact=True))[0] == {
        "_id": "act_h", "type": "linkedinReplied", "excerpt": "Sure, call me"}


def test_compact_sans_corps_ni_extrait():
    activity = {"_id": "act_p", "type": "paused", "messagePreview": "   ", "leadId": "lea_1"}
    assert av.shape_activities([activity], av.view_of(compact=True))[0] == {
        "_id": "act_p", "type": "paused", "leadId": "lea_1"}


def test_compact_reduit_le_poids_d_une_page():
    page = [_sent(i) for i in range(100)]
    before = len(json.dumps(page))
    after = len(json.dumps(av.shape_activities(page, av.view_of(compact=True))))
    assert after * 20 < before


# --- fields ----------------------------------------------------------------------------------------------------------

def test_fields_garde_les_cles_nommees_et_les_chemins_pointes():
    out = av.shape_activities([_sent(1)], av.view_of(fields=["type", "lead.variables.email", "missing"]))[0]
    assert out == {"type": "emailsSent", "lead": {"variables": {"email": "ada1@example.test"}}}


def test_fields_avec_compact_ajoute_au_prereglage():
    out = av.shape_activities([_sent(1)], av.view_of(fields=["sequenceId", "to.address"], compact=True))[0]
    assert out["sequenceId"] == "seq_1" and out["to"] == [{"address": "ada1@example.test"}]
    assert out["excerpt"] == "Hello Ada," and "lead" not in out


def test_un_element_qui_n_est_pas_un_objet_passe_tel_quel():
    assert av.shape_activities(["x", 3], av.view_of(compact=True)) == ["x", 3]


def test_une_reponse_qui_n_est_pas_une_liste_leve_sous_une_vue():
    with pytest.raises(RuntimeError, match="not a list"):
        av.shape_activities({"error": "x"}, av.view_of(compact=True))


@pytest.mark.parametrize("fields", [[], "type", ["a..b"], [""], [3]])
def test_des_fields_malformes_sont_refuses(fields):
    with pytest.raises(ValueError):
        av.view_of(fields=fields)


def test_compact_non_booleen_refuse():
    with pytest.raises(ValueError):
        av.view_of(compact="yes")


# --- Le client ------------------------------------------------------------------------------------------------------

def test_la_vue_ne_part_jamais_a_lemlist(monkeypatch):
    sent = []

    def _request(method, url, **kw):
        sent.append(kw.get("params"))
        return _Resp([_sent(1)])

    monkeypatch.setattr(lm.requests, "request", _request)
    out = lm.LemlistClient(api_key="k").get_activities(campaign_id="cam_1", compact=True)
    assert "compact" not in sent[0] and "fields" not in sent[0]
    assert out[0]["excerpt"] == "Hello Ada," and "lead" not in out[0]


def test_une_vue_malformee_est_refusee_avant_toute_requete(monkeypatch):
    sent = []
    monkeypatch.setattr(lm.requests, "request", lambda *a, **k: sent.append(1) or _Resp([]))
    with pytest.raises(ValueError):
        lm.LemlistClient(api_key="k").get_activities(fields=["a..b"])
    with pytest.raises(ValueError):
        lm.LemlistClient(api_key="k").sync_activities(fields=[])
    assert not sent


def test_sync_activities_filtre_sur_la_date_puis_applique_la_vue(monkeypatch):
    pages = [[_sent(1), {**_sent(2), "createdAt": "2025-12-01T00:00:00.000Z"}]]

    def _request(method, url, **kw):
        return _Resp(pages.pop(0) if pages else [])

    monkeypatch.setattr(lm.requests, "request", _request)
    out = lm.LemlistClient(api_key="k").sync_activities(since="2026-01-01", fields=["_id", "createdAt"])
    assert out == [{"_id": "act_1", "createdAt": "2026-01-02T10:00:00.000Z"}]
