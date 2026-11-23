"""MailClient — verrouille le contrat HTTP de la boîte Outlook : recherche KQL contre
liste triée, corps demandé par `Prefer`, brouillons (nouveau, réponse au-dessus de la
citation), envoi séparé, pièces jointes bornées à 3 Mo, erreurs amont typées ; ids
immuables demandés sur TOUS les appels, relecture bornée de la copie envoyée, pièces
jointes (dont images en ligne) sur un brouillon existant et sur une réponse."""
import base64

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.microsoft import MailClient
from oto.tools.microsoft.mail import MAX_INLINE_ATTACHMENT
from microsoft_fake import G, _Resp, calls  # noqa: F401


@pytest.fixture
def client():
    return MailClient("AT-personne")


def test_liste_recente_d_un_dossier_bien_connu(calls, client):
    calls.responses.append(_Resp({"value": [{"id": "m1"}]}))
    assert client.search_messages(folder="inbox", top=5) == [{"id": "m1"}]
    call = calls[0]
    assert call["url"] == f"{G}/me/mailFolders/inbox/messages"
    assert call["params"]["$orderby"] == "receivedDateTime desc"
    assert call["params"]["$top"] == 5 and "$search" not in call["params"]
    assert call["headers"]["Authorization"] == "Bearer AT-personne"


def test_non_lus_filtre_compatible_avec_le_tri(calls, client):
    calls.responses.append(_Resp({"value": []}))
    client.search_messages(unread=True)
    params = calls[0]["params"]
    assert calls[0]["url"] == f"{G}/me/messages"
    assert params["$filter"] == "receivedDateTime ge 1900-01-01T00:00:00Z and isRead eq false"


def test_recherche_kql_sans_tri(calls, client):
    calls.responses.append(_Resp({"value": []}))
    client.search_messages('from:jane "q3 report"')
    params = calls[0]["params"]
    assert params["$search"] == '"from:jane \\"q3 report\\""'
    assert "$orderby" not in params and "$filter" not in params


def test_recherche_et_non_lus_refuses_ensemble(client):
    with pytest.raises(ValueError, match="unread"):
        client.search_messages("invoice", unread=True)


def test_corps_demande_en_texte_ou_html(calls, client):
    client.get_message("AAMk=")
    client.get_message("AAMk=", body="html")
    assert calls[0]["url"] == f"{G}/me/messages/AAMk%3D"
    assert calls[0]["headers"]["Prefer"] == (
        'IdType="ImmutableId", outlook.body-content-type="text"')
    assert calls[1]["headers"]["Prefer"] == (
        'IdType="ImmutableId", outlook.body-content-type="html"')
    with pytest.raises(ValueError, match="body"):
        client.get_message("m", body="rtf")


def test_pieces_jointes(calls, client):
    calls.responses.append(_Resp({"value": [{"id": "a1"}]}))
    assert client.list_attachments("m1") == [{"id": "a1"}]
    client.get_attachment("m1", "a1")
    assert calls[0]["url"] == f"{G}/me/messages/m1/attachments"
    assert "$top" not in calls[0]["params"] and "contentBytes" not in calls[0]["params"]["$select"]
    assert calls[1]["url"] == f"{G}/me/messages/m1/attachments/a1"


def test_brouillon_avec_piece_jointe(calls, client):
    calls.responses.append(_Resp({"id": "d1", "isDraft": True}))
    draft = client.create_draft(
        to=["jane@contoso.com"], cc=["bob@contoso.com"], subject="Devis",
        body_html="<p>Bonjour</p>",
        attachments=[{"name": "devis.pdf", "content_type": "application/pdf",
                      "content_bytes": b"%PDF"}])
    assert draft["id"] == "d1"
    create, attach = calls
    assert (create["method"], create["url"]) == ("POST", f"{G}/me/messages")
    assert create["json"] == {
        "subject": "Devis", "body": {"contentType": "HTML", "content": "<p>Bonjour</p>"},
        "toRecipients": [{"emailAddress": {"address": "jane@contoso.com"}}],
        "ccRecipients": [{"emailAddress": {"address": "bob@contoso.com"}}],
        "bccRecipients": []}
    assert (attach["method"], attach["url"]) == ("POST", f"{G}/me/messages/d1/attachments")
    assert attach["json"] == {"@odata.type": "#microsoft.graph.fileAttachment",
                              "name": "devis.pdf", "contentType": "application/pdf",
                              "contentBytes": base64.b64encode(b"%PDF").decode()}


def test_piece_jointe_de_plus_de_3_mo_refusee_avant_toute_ecriture(calls, client):
    with pytest.raises(ValueError, match="at most"):
        client.create_draft(to=["a@b.c"], subject="s", body_html="", attachments=[
            {"name": "gros.zip", "content_bytes": b"0" * (MAX_INLINE_ATTACHMENT + 1)}])
    assert calls == []


def test_image_en_ligne_par_content_id(calls, client):
    calls.responses.append(_Resp({"id": "d1", "webLink": "https://outlook.office.com/d1"}))
    draft = client.create_draft(
        to=["jane@contoso.com"], subject="Lettre", body_html='<img src="cid:logo">',
        attachments=[{"name": "logo.png", "content_type": "image/png",
                      "content_bytes": b"\x89PNG", "content_id": "logo"},
                     {"name": "devis.pdf", "content_type": "application/pdf",
                      "content_bytes": b"%PDF"}])
    assert draft["webLink"] == "https://outlook.office.com/d1"
    _, image, pdf = calls
    assert image["json"] == {"@odata.type": "#microsoft.graph.fileAttachment",
                             "name": "logo.png", "contentType": "image/png",
                             "contentBytes": base64.b64encode(b"\x89PNG").decode(),
                             "isInline": True, "contentId": "logo"}
    assert "isInline" not in pdf["json"] and "contentId" not in pdf["json"]


@pytest.mark.parametrize("content_id", ["", "  ", "<logo>", "logo 1", 7])
def test_content_id_invalide_refuse_avant_toute_ecriture(calls, client, content_id):
    with pytest.raises(ValueError, match="content_id"):
        client.create_draft(to=["a@b.c"], subject="s", body_html="", attachments=[
            {"name": "logo.png", "content_bytes": b"x", "content_id": content_id}])
    assert calls == []


def test_content_id_en_double_refuse_avant_toute_ecriture(calls, client):
    with pytest.raises(ValueError, match="two attachments"):
        client.create_draft(to=["a@b.c"], subject="s", body_html="", attachments=[
            {"name": "a.png", "content_bytes": b"x", "content_id": "logo"},
            {"name": "b.png", "content_bytes": b"y", "content_id": "logo"}])
    assert calls == []


def test_destinataires_en_chaine_refuses(client):
    with pytest.raises(TypeError, match="list"):
        client.create_draft(to="jane@contoso.com", subject="s", body_html="")


def test_brouillon_de_reponse_au_dessus_de_la_citation(calls, client):
    calls.responses.extend([
        _Resp({"id": "r1", "body": {"contentType": "html",
                                    "content": "<html><body dir=ltr><hr>cité</body></html>"}}),
        _Resp({"id": "r1"}),
    ])
    assert client.create_reply_draft("m1", body_html="<p>OK</p>", reply_all=True) == {"id": "r1"}
    create, patch = calls
    assert (create["method"], create["url"]) == ("POST", f"{G}/me/messages/m1/createReplyAll")
    assert (patch["method"], patch["url"]) == ("PATCH", f"{G}/me/messages/r1")
    assert patch["json"] == {"body": {"contentType": "HTML", "content":
                                      "<html><body dir=ltr><p>OK</p><hr>cité</body></html>"}}


def test_reponse_a_un_message_texte_echappe_la_citation(calls, client):
    calls.responses.extend([_Resp({"id": "r1", "body": {"contentType": "text",
                                                        "content": "a < b"}}), _Resp({})])
    client.create_reply_draft("m1", body_html="<p>OK</p>")
    assert calls[0]["url"] == f"{G}/me/messages/m1/createReply"
    assert calls[1]["json"]["body"]["content"] == (
        '<p>OK</p><div style="white-space:pre-wrap">a &lt; b</div>')


def test_envoi_deplacement_suppression(calls, client):
    calls.responses.extend([_Resp(status_code=202), _Resp({"id": "m1"}),
                            _Resp(status_code=204)])
    assert client.send_draft("d1") == "d1"
    assert client.move("m1", "archive") == {"id": "m1"}  # immutable: same id
    assert client.delete("m1") == "m1"
    assert [(c["method"], c["url"]) for c in calls] == [
        ("POST", f"{G}/me/messages/d1/send"),
        ("POST", f"{G}/me/messages/m1/move"),
        ("DELETE", f"{G}/me/messages/m1")]
    assert calls[1]["json"] == {"destinationId": "archive"}


def test_dossiers(calls, client):
    calls.responses.append(_Resp({"value": [{"id": "f1", "displayName": "Inbox"}]}))
    assert client.list_folders()[0]["id"] == "f1"
    assert calls[0]["url"] == f"{G}/me/mailFolders"


def test_refus_amont_type(calls, client):
    calls.responses.append(_Resp({"error": {"code": "ErrorAccessDenied"}}, status_code=403))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.send_draft("d1")
    assert exc.value.status_code == 403 and exc.value.service == "microsoft"


IMMUABLE = 'IdType="ImmutableId"'


def test_ids_immuables_sur_tous_les_appels(calls, client):
    calls.responses.extend([
        _Resp({"value": [{"id": "m1"}], "@odata.nextLink": f"{G}/me/messages?$skip=1"}),
        _Resp({"value": [{"id": "m2"}]}),
        _Resp({"value": []}), _Resp({"value": []}), _Resp({"id": "a1"}),
        _Resp({"id": "m1"}), _Resp(status_code=204),
        _Resp({"id": "d1"}), _Resp({"id": "a1"}), _Resp(status_code=202)])
    client.search_messages(top=2)
    client.list_folders()
    client.list_attachments("m1")
    client.get_attachment("m1", "a1")
    client.move("m1", "archive")
    client.delete("m1")
    client.create_draft(to=["a@b.c"], subject="s", body_html="", attachments=[
        {"name": "a.pdf", "content_bytes": b"%PDF"}])
    client.send_draft("d1")
    assert len(calls) == 10, "la page suivante aussi"
    assert [c["headers"].get("Prefer") for c in calls] == [IMMUABLE] * 10


def test_copie_envoyee_relue_apres_quelques_404(calls, client, monkeypatch):
    from oto.tools.microsoft import mail

    attentes = []
    monkeypatch.setattr(mail.time, "sleep", attentes.append)
    vu = {"id": "d1", "webLink": "https://outlook.office.com/sent/d1",
          "sentDateTime": "2026-10-13T10:00:00Z"}
    calls.responses.extend([_Resp({"error": {"code": "ErrorItemNotFound"}}, status_code=404),
                            _Resp({"error": {"code": "ErrorItemNotFound"}}, status_code=404),
                            _Resp(vu)])
    assert client.sent_message("d1") == vu
    assert attentes == list(mail._SENT_POLL_DELAYS[:3])
    for c in calls:
        assert (c["method"], c["url"]) == ("GET", f"{G}/me/messages/d1")
        assert c["params"] == {"$select": "id,webLink,sentDateTime"}
        assert c["headers"]["Prefer"] == IMMUABLE


def test_copie_envoyee_pas_encore_visible_rend_none_en_dix_secondes(calls, client,
                                                                    monkeypatch):
    from oto.tools.microsoft import mail

    attentes = []
    monkeypatch.setattr(mail.time, "sleep", attentes.append)
    calls.responses.extend([_Resp({"error": {}}, status_code=404)
                            for _ in mail._SENT_POLL_DELAYS])
    assert client.sent_message("d1") is None
    assert len(calls) == len(mail._SENT_POLL_DELAYS)
    assert 8 <= sum(attentes) <= 10


def test_copie_envoyee_autre_erreur_levee(calls, client, monkeypatch):
    from oto.tools.microsoft import mail

    monkeypatch.setattr(mail.time, "sleep", lambda d: None)
    calls.responses.append(_Resp({"error": {"code": "ErrorAccessDenied"}}, status_code=403))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.sent_message("d1")
    assert exc.value.status_code == 403 and len(calls) == 1


def test_pieces_ajoutees_a_un_brouillon_existant(calls, client):
    calls.responses.extend([_Resp({"id": "a1"}), _Resp({"id": "a2"})])
    assert client.add_attachments("d1", [
        {"name": "logo.png", "content_type": "image/png", "content_bytes": b"P",
         "content_id": "logo"},
        {"name": "devis.pdf", "content_bytes": b"%PDF"}]) == [{"id": "a1"}, {"id": "a2"}]
    logo, devis = calls
    assert (logo["method"], logo["url"]) == ("POST", f"{G}/me/messages/d1/attachments")
    assert logo["json"]["isInline"] is True and logo["json"]["contentId"] == "logo"
    assert devis["json"]["contentType"] == "application/octet-stream"
    assert "isInline" not in devis["json"]
    calls.clear()
    with pytest.raises(ValueError, match="two attachments"):
        client.add_attachments("d1", [
            {"name": "a.png", "content_bytes": b"x", "content_id": "logo"},
            {"name": "b.png", "content_bytes": b"y", "content_id": "logo"}])
    with pytest.raises(ValueError, match="at most"):
        client.add_attachments("d1", [
            {"name": "ok.pdf", "content_bytes": b"x"},
            {"name": "gros.zip", "content_bytes": b"0" * (MAX_INLINE_ATTACHMENT + 1)}])
    assert calls == []


def test_reponse_avec_image_en_ligne(calls, client):
    calls.responses.extend([
        _Resp({"id": "r1", "body": {"contentType": "html", "content": "<body><hr></body>"}}),
        _Resp({"id": "r1", "webLink": "https://outlook.office.com/r1"}),
        _Resp({"id": "a1"})])
    out = client.create_reply_draft("m1", body_html='<img src="cid:logo">', attachments=[
        {"name": "logo.png", "content_type": "image/png", "content_bytes": b"P",
         "content_id": "logo"}])
    assert out["webLink"] == "https://outlook.office.com/r1"
    create, patch, logo = calls
    assert [c["headers"].get("Prefer") for c in calls] == [IMMUABLE] * 3
    assert patch["json"]["body"]["content"] == '<body><img src="cid:logo"><hr></body>'
    assert (logo["method"], logo["url"]) == ("POST", f"{G}/me/messages/r1/attachments")
    assert logo["json"]["isInline"] is True and logo["json"]["contentId"] == "logo"


def test_reponse_avec_content_id_invalide_rien_n_est_ecrit(calls, client):
    with pytest.raises(ValueError, match="content_id"):
        client.create_reply_draft("m1", body_html="", attachments=[
            {"name": "logo.png", "content_bytes": b"P", "content_id": "<logo>"}])
    assert calls == []


# --- Boîtes partagées ou déléguées : lecture seule, sous /users/{mailbox} ---------

BOITE = "accueil@contoso.com"


def test_lectures_sur_une_boite_partagee(calls, client):
    calls.responses.extend([_Resp({"value": []}), _Resp({"value": []}), _Resp({"id": "m1"}),
                            _Resp({"value": []}), _Resp({"id": "a1"})])
    client.list_folders(mailbox=BOITE)
    client.search_messages("facture", folder="inbox", mailbox=BOITE)
    client.get_message("AAMk=", mailbox=BOITE)
    client.list_attachments("m1", mailbox=BOITE)
    client.get_attachment("m1", "a1", mailbox=BOITE)
    base = f"{G}/users/accueil@contoso.com"
    assert [c["url"] for c in calls] == [
        f"{base}/mailFolders", f"{base}/mailFolders/inbox/messages",
        f"{base}/messages/AAMk%3D", f"{base}/messages/m1/attachments",
        f"{base}/messages/m1/attachments/a1"]
    assert all(c["headers"]["Authorization"] == "Bearer AT-personne" for c in calls)
    assert all(c["headers"]["Prefer"].startswith('IdType="ImmutableId"') for c in calls)


def test_boite_par_id_et_toutes_ses_lectures(calls, client):
    calls.responses.append(_Resp({"value": []}))
    client.search_messages(mailbox="0b3c1f2e-aaaa-bbbb-cccc-1234567890ab", unread=True)
    assert calls[0]["url"] == f"{G}/users/0b3c1f2e-aaaa-bbbb-cccc-1234567890ab/messages"


def test_sans_boite_la_sienne(calls, client):
    calls.responses.append(_Resp({"value": []}))
    client.list_folders()
    assert calls[0]["url"] == f"{G}/me/mailFolders"


def test_boite_vide_refusee(calls, client):
    from oto.tools.common.credentials import MissingCredential
    with pytest.raises(MissingCredential):
        client.list_folders(mailbox="")
    assert not calls


def test_boite_segment_encode(calls, client):
    calls.responses.append(_Resp({"value": []}))
    client.list_folders(mailbox="a/b?c")
    assert calls[0]["url"] == f"{G}/users/a%2Fb%3Fc/mailFolders"


@pytest.mark.parametrize("status,code", [(403, "ErrorAccessDenied"), (404, "ErrorInvalidUser")])
def test_refus_sur_une_boite_nomme_la_boite_et_le_code(calls, client, status, code):
    from oto.tools.microsoft import MailboxAccessError
    calls.responses.append(_Resp({"error": {"code": code, "message": "Access is denied."}},
                                 status_code=status))
    with pytest.raises(MailboxAccessError) as e:
        client.get_message("m1", mailbox=BOITE)
    err = e.value
    assert isinstance(err, UpstreamHTTPError) and err.status_code == status
    assert err.mailbox == BOITE and err.graph_code == code
    assert f"No access to mailbox {BOITE}" in str(err) and code in str(err)
    assert len(calls) == 1  # aucun repli sur /me


def test_refus_sur_sa_propre_boite_inchange(calls, client):
    from oto.tools.microsoft import MailboxAccessError
    calls.responses.append(_Resp({"error": {"code": "ErrorItemNotFound"}}, status_code=404))
    with pytest.raises(UpstreamHTTPError) as e:
        client.get_message("m1")
    assert not isinstance(e.value, MailboxAccessError)


def test_autre_erreur_sur_une_boite_non_requalifiee(calls, client):
    from oto.tools.microsoft import MailboxAccessError
    calls.responses.append(_Resp({"error": {"code": "TooManyRequests"}}, status_code=429))
    with pytest.raises(UpstreamHTTPError) as e:
        client.list_folders(mailbox=BOITE)
    assert e.value.status_code == 429 and not isinstance(e.value, MailboxAccessError)


def test_ecritures_sans_parametre_de_boite():
    import inspect
    for name in ("create_draft", "create_reply_draft", "add_attachments", "send_draft",
                 "sent_message", "move", "delete"):
        assert "mailbox" not in inspect.signature(getattr(MailClient, name)).parameters
