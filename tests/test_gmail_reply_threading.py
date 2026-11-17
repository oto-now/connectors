"""Une réponse doit se ranger sous le DERNIER message du fil, chez tous les clients.

Trois écarts vécus sur `reply` et `create_draft_reply` :

- `to` ignoré : le destinataire se déduisait du message cité, et pour viser la bonne
  personne l'agent répondait à un message plus ancien — rangé sous lui.
- `References` ne portait que le Message-ID du parent : Outlook, chez le destinataire,
  sortait la réponse de la conversation (RFC 5322 §3.6.4 : References du parent + son id).
- le Cc du message d'origine se perdait : il suit désormais sur demande (`reply_all`) ;
  par défaut, la réponse va à l'auteur seul.

Aucun réseau : le service Google est mocké, on inspecte le MIME construit.
"""
from __future__ import annotations

import base64
from email import message_from_string
from unittest.mock import MagicMock

import pytest

from oto.tools.google.gmail.lib.gmail_client import GmailClient, GmailClientError

ME = "me@example.com"


def _msg(mid: str, labels=(), **headers) -> dict:
    names = {"from": "From", "to": "To", "cc": "Cc", "reply_to": "Reply-To",
             "subject": "Subject", "message_id": "Message-ID", "references": "References",
             "in_reply_to": "In-Reply-To"}
    return {"id": mid, "threadId": "t1", "labelIds": list(labels),
            "payload": {"headers": [{"name": names[k], "value": v} for k, v in headers.items()]}}


FIRST = _msg(
    "m1", **{"from": "Ann <ann@example.com>", "to": ME, "cc": "bob@example.com",
             "subject": "Projet", "message_id": "<1@example.com>"})
SECOND = _msg(
    "m2", **{"from": ME, "to": "ann@example.com", "cc": "bob@example.com",
             "subject": "Re: Projet", "message_id": "<2@example.com>",
             "references": "<1@example.com>", "in_reply_to": "<1@example.com>"})
THIRD = _msg(
    "m3", **{"from": "Bob <bob@example.com>", "to": f"ann@example.com, {ME}",
             "cc": "Carl <carl@example.com>, ANN@example.com, me@example.com",
             "subject": "Re: Projet", "message_id": "<3@example.com>",
             "references": "<1@example.com> <2@example.com>"})
DRAFT = _msg("m4", labels=["DRAFT"], **{"from": ME, "to": "bob@example.com",
                                        "subject": "Re: Projet", "message_id": "<4@example.com>"})


@pytest.fixture
def client():
    c = GmailClient.__new__(GmailClient)      # pas d'auth : on teste la construction
    c.service = MagicMock()
    users = c.service.users()
    users.getProfile().execute.return_value = {"emailAddress": ME}
    users.messages().get().execute.return_value = {"id": "?", "threadId": "t1"}
    users.threads().get().execute.return_value = {"id": "t1",
                                                  "messages": [FIRST, SECOND, THIRD, DRAFT]}
    users.messages().send().execute.return_value = {"id": "sent", "threadId": "t1"}
    users.drafts().create().execute.return_value = {
        "id": "d1", "message": {"id": "dm1", "threadId": "t1"}}
    return c


def _sent(client) -> tuple:
    body = client.service.users().messages().send.call_args.kwargs["body"]
    return message_from_string(base64.urlsafe_b64decode(body["raw"]).decode()), body


def _drafted(client) -> tuple:
    body = client.service.users().drafts().create.call_args.kwargs["body"]["message"]
    return message_from_string(base64.urlsafe_b64decode(body["raw"]).decode()), body


# ── (a) `to` et `cc` explicites remplacent les destinataires déduits ─────────

def test_explicit_to_and_cc_replace_the_deduced_ones(client):
    out = client.reply("m3", "corps", to="dan@example.com", cc="eve@example.com")
    mime, _ = _sent(client)
    assert mime["To"] == "dan@example.com" and mime["Cc"] == "eve@example.com"
    assert (out["to"], out["cc"]) == ("dan@example.com", "eve@example.com")


def test_explicit_to_alone_copies_nobody(client):
    """Viser quelqu'un, c'est ne pas recopier d'office ceux du message d'origine."""
    client.reply("m3", "corps", to="dan@example.com")
    mime, _ = _sent(client)
    assert mime["To"] == "dan@example.com" and mime["Cc"] is None


def test_explicit_cc_alone_keeps_the_deduced_to(client):
    client.create_draft_reply("m3", "corps", cc="eve@example.com")
    mime, _ = _drafted(client)
    assert mime["To"] == "bob@example.com" and mime["Cc"] == "eve@example.com"


# ── (b) In-Reply-To = id du parent ; References = References du parent + son id ──

def test_references_chain_the_parents_references(client):
    client.reply("m3", "corps")
    mime, body = _sent(client)
    assert mime["In-Reply-To"] == "<3@example.com>"
    assert mime["References"] == "<1@example.com> <2@example.com> <3@example.com>"
    assert body["threadId"] == "t1"


def test_references_from_in_reply_to_when_the_parent_has_none(client):
    parent = _msg("m2", **{"from": "ann@example.com", "to": ME, "subject": "S",
                           "message_id": "<2@example.com>", "in_reply_to": "<1@example.com>"})
    client.service.users().threads().get().execute.return_value = {"messages": [parent]}
    client.create_draft_reply("m2", "corps")
    mime, body = _drafted(client)
    assert mime["References"] == "<1@example.com> <2@example.com>"
    assert body["threadId"] == "t1"


def test_first_message_references_only_itself(client):
    client.reply("m1", "corps")
    mime, _ = _sent(client)
    assert mime["In-Reply-To"] == mime["References"] == "<1@example.com>"


# ── (d) l'auteur seul par défaut ; répondre à tous se demande ─────────────────

def test_by_default_the_reply_goes_to_the_author_alone(client):
    """Personne d'autre ne reçoit une réponse sans que l'appel le dise."""
    out = client.reply("m3", "corps")
    mime, _ = _sent(client)
    assert mime["To"] == "bob@example.com"
    assert mime["Cc"] is None and out["cc"] == ""


def test_reply_all_copies_everyone_but_me(client):
    out = client.reply("m3", "corps", reply_all=True)
    mime, _ = _sent(client)
    assert mime["To"] == "bob@example.com"
    # To et Cc d'origine, sans moi, sans le destinataire, sans doublon (casse comprise).
    assert mime["Cc"] == "ann@example.com, carl@example.com"
    assert out["cc"] == "ann@example.com, carl@example.com"


def test_explicit_to_wins_over_reply_all(client):
    client.reply("m3", "corps", to="dan@example.com", reply_all=True)
    mime, _ = _sent(client)
    assert mime["To"] == "dan@example.com" and mime["Cc"] is None


def test_reply_to_our_own_message_goes_to_its_recipients(client):
    client.reply("m2", "corps")
    mime, _ = _sent(client)
    assert mime["To"] == "ann@example.com" and mime["Cc"] is None


def test_reply_all_to_our_own_message_keeps_its_cc(client):
    client.reply("m2", "corps", reply_all=True)
    mime, _ = _sent(client)
    assert mime["To"] == "ann@example.com" and mime["Cc"] == "bob@example.com"


# ── (c) répondre à un message qui n'est pas le dernier : averti, nommé ───────

def test_replying_to_an_older_message_warns_with_the_latest_id(client):
    out = client.create_draft_reply("m1", "corps")
    assert out["replied_to"] == "m1"
    assert out["warning"]["code"] == "not_latest_in_thread"
    # Le brouillon (m4) n'est pas un message de la conversation : le dernier est m3.
    assert out["warning"]["latest_message_id"] == "m3"


def test_replying_to_the_latest_message_does_not_warn(client):
    assert "warning" not in client.reply("m3", "corps")


# ── (c) mode fil : répond au dernier message du fil ──────────────────────────

def test_thread_mode_answers_the_latest_message(client):
    out = client.reply(None, "corps", thread_id="t1")
    mime, body = _sent(client)
    assert out["replied_to"] == "m3" and "warning" not in out
    assert mime["In-Reply-To"] == "<3@example.com>" and body["threadId"] == "t1"
    assert mime["Subject"] == "Re: Projet"


def test_thread_mode_skips_drafts_and_trash(client):
    trashed = _msg("m5", labels=["TRASH"], **{"from": "x@example.com", "to": ME,
                                              "message_id": "<5@example.com>"})
    client.service.users().threads().get().execute.return_value = {
        "messages": [FIRST, SECOND, THIRD, DRAFT, trashed]}
    assert client.create_draft_reply(None, "corps", thread_id="t1")["replied_to"] == "m3"


@pytest.mark.parametrize("message_id, thread_id", [(None, None), ("m3", "t1")])
def test_exactly_one_target(client, message_id, thread_id):
    with pytest.raises(GmailClientError, match="exactly one"):
        client.reply(message_id, "corps", thread_id=thread_id)
    client.service.users().messages().send().execute.assert_not_called()


def test_thread_with_nothing_to_answer_is_refused(client):
    client.service.users().threads().get().execute.return_value = {"messages": [DRAFT]}
    with pytest.raises(GmailClientError, match="no message to answer"):
        client.create_draft_reply(None, "corps", thread_id="t1")
