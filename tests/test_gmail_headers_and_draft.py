"""Composer, puis se RELIRE : les trois écarts vécus sur les brouillons Gmail.

Trois signaux, une même racine — ce qu'on écrit et ce qu'on relit ne se
parlaient pas :

- #340 le `cc` semblait ignoré. Il ne l'était pas : il partait sous `cc:`, que
  personne ne cherchait sous ce nom-là.
- #342 relire un brouillon rendait `to`/`cc`/`subject` vides — donc impossible
  de vérifier ses destinataires avant envoi, et c'est ce qui a masqué #340.
- #341/#343 le markdown restait littéral dans un brouillon, alors que `send` et
  `reply` le rendaient depuis toujours.

Aucun réseau : le service Google est mocké, on inspecte le MIME construit.
"""
from __future__ import annotations

import base64
from email import message_from_string
from unittest.mock import MagicMock

import pytest

from oto.tools.google.gmail.lib.gmail_client import GmailClient


@pytest.fixture
def client():
    c = GmailClient.__new__(GmailClient)      # pas d'auth : on teste la construction
    c.service = MagicMock()
    return c


def _raw_of(client) -> str:
    """Le message RFC 2822 réellement envoyé à l'API drafts.create."""
    body = client.service.users().drafts().create.call_args.kwargs["body"]
    return base64.urlsafe_b64decode(body["message"]["raw"]).decode()


def _parts_of(client) -> dict:
    """`{mimetype: contenu décodé}` — une partie accentuée part en base64, donc
    la chercher en clair dans le raw ne prouverait rien."""
    msg = message_from_string(_raw_of(client))
    if not msg.is_multipart():
        return {msg.get_content_type(): msg.get_payload(decode=True).decode()}
    return {p.get_content_type(): p.get_payload(decode=True).decode()
            for p in msg.walk() if not p.is_multipart()}


@pytest.fixture
def drafted(client):
    client.service.users().drafts().create().execute.return_value = {
        "id": "d1", "message": {"id": "m1", "threadId": "t1"}}
    return client


# ── En-têtes canoniques : l'API rend les noms TELS QU'ÉCRITS ─────────────────

def test_headers_are_written_in_canonical_form(client):
    m = client._build_message("a@x.fr", "Sujet", "corps", html=None,
                              cc="b@y.fr", bcc="c@z.fr")
    assert [h for h in ("To", "Subject", "Cc", "Bcc") if m[h]] == ["To", "Subject", "Cc", "Bcc"]
    # …et pas en minuscules, forme sous laquelle Gmail les restitue telles quelles :
    # un lecteur cherchant `Cc` (le nôtre compris) ne les voyait jamais.
    assert "\nto:" not in m.as_string() and "\ncc:" not in m.as_string()


def test_cc_and_bcc_reach_the_draft(drafted):
    drafted.create_draft(to="a@x.fr", subject="S", body="b",
                         cc="b@y.fr,c@y.fr", bcc="d@z.fr")
    raw = _raw_of(drafted)
    assert "Cc: b@y.fr,c@y.fr" in raw
    assert "Bcc: d@z.fr" in raw


# ── Relire : lookup insensible à la casse ────────────────────────────────────

def _message(headers: dict, parts=None):
    return {
        "id": "m1", "threadId": "t1", "labelIds": [],
        "payload": {"headers": [{"name": k, "value": v} for k, v in headers.items()],
                    "body": {}, "parts": parts or []},
    }


@pytest.mark.parametrize("case", [
    {"to": "a@x.fr", "cc": "b@y.fr", "subject": "S"},          # les nôtres, historiques
    {"To": "a@x.fr", "Cc": "b@y.fr", "Subject": "S"},          # forme canonique
    {"TO": "a@x.fr", "CC": "b@y.fr", "SUBJECT": "S"},          # un émetteur exotique
])
def test_get_message_reads_headers_whatever_their_case(client, case):
    client.service.users().messages().get().execute.return_value = _message(case)
    out = client.get_message("m1")
    assert (out["to"], out["cc"], out["subject"]) == ("a@x.fr", "b@y.fr", "S")


def test_get_message_signals_the_html_part(client):
    """`body` est le text/plain — donc le markdown SOURCE quand le mail est rendu.
    Sans ce drapeau, relire un mail correct fait conclure « rendu cassé »."""
    html = base64.urlsafe_b64encode(b"<p><strong>gras</strong></p>").decode()
    plain = base64.urlsafe_b64encode(b"**gras**").decode()
    client.service.users().messages().get().execute.return_value = _message(
        {"To": "a@x.fr"},
        parts=[{"mimeType": "text/plain", "body": {"data": plain}},
               {"mimeType": "text/html", "body": {"data": html}}])
    out = client.get_message("m1")
    assert out["body"] == "**gras**" and out["has_html"] is True


def test_plain_only_message_has_no_html(client):
    plain = base64.urlsafe_b64encode(b"texte").decode()
    client.service.users().messages().get().execute.return_value = _message(
        {"To": "a@x.fr"}, parts=[{"mimeType": "text/plain", "body": {"data": plain}}])
    assert client.get_message("m1")["has_html"] is False


# ── Le brouillon rend le markdown, comme send et reply ───────────────────────

def test_draft_renders_markdown_by_default(drafted):
    drafted.create_draft(to="a@x.fr", subject="S", body="Corps **en gras**\n\n- un\n- deux")
    parts = _parts_of(drafted)
    html = parts["text/html"]
    assert "<strong>en gras</strong>" in html or "<b>en gras</b>" in html
    assert "<li>" in html
    assert "**en gras**" in parts["text/plain"]      # la partie texte garde la source


def test_draft_keeps_plain_text_when_markdown_is_off(drafted):
    drafted.create_draft(to="a@x.fr", subject="S", body="Corps **brut**", markdown=False)
    parts = _parts_of(drafted)
    assert set(parts) == {"text/plain"} and "**brut**" in parts["text/plain"]


def test_explicit_html_is_never_re_rendered(drafted):
    """La CLI rend déjà (signature comprise) et passe son HTML : pas de double passe."""
    drafted.create_draft(to="a@x.fr", subject="S", body="**gras**",
                         html="<p>déjà rendu</p>")
    html = _parts_of(drafted)["text/html"]
    assert html == "<p>déjà rendu</p>" and "<strong>" not in html


# ── Envoyer un brouillon existant : par son id, sans le recomposer ───────────

def test_send_draft_sends_the_draft_by_id(client):
    client.service.users().drafts().send().execute.return_value = {
        "id": "m9", "threadId": "t9", "labelIds": ["SENT"]}
    assert client.send_draft("r-42") == {"id": "m9", "threadId": "t9"}
    client.service.users().drafts().send.assert_called_with(
        userId="me", body={"id": "r-42"})


# ── Modifier un brouillon existant : même brouillon, même fil ────────────────

def _existing(client, headers: dict, thread_id="t1"):
    client.service.users().drafts().get().execute.return_value = {
        "id": "d1", "message": {"id": "m0", "threadId": thread_id, "payload": {
            "headers": [{"name": k, "value": v} for k, v in headers.items()]}}}
    client.service.users().drafts().update().execute.return_value = {
        "id": "d1", "message": {"id": "m2", "threadId": thread_id}}


def _update_call(client):
    return client.service.users().drafts().update.call_args.kwargs


def test_update_draft_replaces_body_and_keeps_the_rest(client):
    _existing(client, {"To": "a@x.fr", "Subject": "Devis", "Cc": "b@y.fr"})
    out = client.update_draft("d1", body="Nouveau **texte**")
    call = _update_call(client)
    assert call["id"] == "d1" and call["body"]["id"] == "d1"
    assert call["body"]["message"]["threadId"] == "t1"
    msg = message_from_string(base64.urlsafe_b64decode(
        call["body"]["message"]["raw"]).decode())
    assert (msg["To"], msg["Subject"], msg["Cc"]) == ("a@x.fr", "Devis", "b@y.fr")
    html = [p for p in msg.walk() if p.get_content_type() == "text/html"][0]
    assert "<strong>texte</strong>" in html.get_payload(decode=True).decode()
    assert out == {"id": "d1", "message_id": "m2", "threadId": "t1"}


def test_update_draft_overrides_recipients_and_keeps_the_thread_headers(client):
    _existing(client, {"To": "a@x.fr", "Subject": "Re: Devis",
                       "In-Reply-To": "<p@mail>", "References": "<o@mail> <p@mail>"})
    client.update_draft("d1", body="b", to="c@z.fr", subject="Re: Devis v2", markdown=False)
    msg = message_from_string(base64.urlsafe_b64decode(
        _update_call(client)["body"]["message"]["raw"]).decode())
    assert (msg["To"], msg["Subject"]) == ("c@z.fr", "Re: Devis v2")
    assert msg["In-Reply-To"] == "<p@mail>"
    assert msg["References"] == "<o@mail> <p@mail>"
