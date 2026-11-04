"""Contrat du client Instantly (API V2, clé en Bearer) : transport, refus locaux, retraits de sortie.

Mocke `requests.Session.request`. La requête de chaque fonction est gardée égale à celle de la description par
`test_instantly_description.py` ; ici : la clé en en-tête seulement, jamais dans l'adresse ; aucun rejeu ni
redirection suivie ; les booléens de query écrits `true` / `false` ; un POST sans corps n'envoie rien ;
`interest_value=None` part `null` ; les refus locaux avant tout appel ; les en-têtes d'un webhook et l'adresse
d'une pièce jointe retirés de toute réponse ; un refus de l'API levé une fois, en `UpstreamHTTPError`.
"""
from __future__ import annotations

import pytest

from oto.tools.common.credentials import MissingCredential
from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.instantly import client as ic
from oto.tools.instantly import without_attachment_urls, without_webhook_headers

CAMPAIGN = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"
LIST = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5d"


class _Resp:
    def __init__(self, status_code: int = 200, body=None, headers=None):
        self.status_code = status_code
        self._body = {} if body is None else body
        self.content = b"x"
        self.text = str(self._body)
        self.headers = headers or {}

    def json(self):
        return self._body


@pytest.fixture()
def sent(monkeypatch):
    seen = {"calls": [], "responses": []}

    def fake_request(self, method, url, **kwargs):
        seen["calls"].append({"method": method, "url": url, "params": kwargs.get("params"),
                              "json": kwargs.get("json"), "timeout": kwargs.get("timeout"),
                              "allow_redirects": kwargs.get("allow_redirects"),
                              "headers": dict(self.headers)})
        return seen["responses"].pop(0) if seen["responses"] else _Resp()

    monkeypatch.setattr(ic.requests.Session, "request", fake_request)
    return seen


@pytest.fixture()
def cli():
    return ic.InstantlyClient(api_key="SECRET_KEY")


# --- compte et transport ----------------------------------------------------------


def test_the_key_is_required():
    with pytest.raises(MissingCredential):
        ic.InstantlyClient(api_key=None)


def test_the_key_goes_in_the_header_only_never_retried_nor_redirected(cli, sent):
    cli.list_campaigns(search="webinar", limit=10)
    [call] = sent["calls"]
    assert call["headers"]["Authorization"] == "Bearer SECRET_KEY"
    assert "SECRET_KEY" not in call["url"] and "SECRET_KEY" not in str(call["params"])
    assert call["url"] == "https://api.instantly.ai/api/v2/campaigns"
    assert call["timeout"] == ic.HTTP_TIMEOUT and call["allow_redirects"] is False
    assert "Content-Type" not in call["headers"]


def test_query_booleans_are_written_true_or_false_and_none_is_not_sent(cli, sent):
    cli.list_emails(is_unread=True, preview_only=False, email_type="received")
    cli.list_campaigns()
    assert sent["calls"][0]["params"] == {"is_unread": "true", "preview_only": "false",
                                          "email_type": "received"}
    assert sent["calls"][1]["params"] is None


def test_a_list_argument_repeats_its_key(cli, sent):
    cli.get_campaign_analytics(campaign_ids=[CAMPAIGN, LIST])
    assert sent["calls"][0]["params"] == {"ids": [CAMPAIGN, LIST]}


@pytest.mark.parametrize("call, path", [
    (lambda c: c.activate_campaign(CAMPAIGN), f"/campaigns/{CAMPAIGN}/activate"),
    (lambda c: c.pause_campaign(CAMPAIGN), f"/campaigns/{CAMPAIGN}/pause"),
    (lambda c: c.mark_thread_as_read(CAMPAIGN), f"/emails/threads/{CAMPAIGN}/mark-as-read"),
])
def test_a_post_without_body_sends_no_body(cli, sent, call, path):
    call(cli)
    [request] = sent["calls"]
    assert request["method"] == "POST" and request["url"].endswith(path)
    assert request["json"] is None


def test_interest_value_none_is_sent_as_null(cli, sent):
    cli.update_lead_interest_status("jane.doe@example.com", None)
    assert sent["calls"][0]["json"] == {"lead_email": "jane.doe@example.com", "interest_value": None}


def test_an_account_email_is_one_path_segment(cli, sent):
    cli.get_account("jane+ops@example.com")
    assert sent["calls"][0]["url"].endswith("/accounts/jane%2Bops@example.com")


def test_a_reply_nests_its_body(cli, sent):
    cli.reply_to_email(LIST, "jane@example.com", "Re: Hello", text="Thanks")
    assert sent["calls"][0]["json"] == {"reply_to_uuid": LIST, "eaccount": "jane@example.com",
                                        "subject": "Re: Hello", "body": {"text": "Thanks"}}


def test_create_webhook_sends_headers_given_by_the_consumer_and_never_returns_them(cli, sent):
    sent["responses"].append(_Resp(200, {"id": "w-1", "headers": {"Authorization": "Bearer x"}}))
    answer = cli.create_webhook("https://hooks.example.com/i", event_type="reply_received",
                                headers={"Authorization": "Bearer x"})
    assert sent["calls"][0]["json"]["headers"] == {"Authorization": "Bearer x"}
    assert answer == {"id": "w-1"}


# --- refus locaux -----------------------------------------------------------------


@pytest.mark.parametrize("call, name", [
    (lambda c: c.get_campaign("../workspaces"), "campaign_id"),
    (lambda c: c.get_lead("1"), "lead_id"),
    (lambda c: c.get_account("not-an-email"), "email"),
    (lambda c: c.get_account("a/b@example.com"), "email"),
    (lambda c: c.get_background_job("../x"), "job_id"),
    (lambda c: c.list_campaigns(limit=0), "limit"),
    (lambda c: c.list_campaigns(limit=101), "limit"),
    (lambda c: c.list_campaigns(status=9), "status"),
    (lambda c: c.list_leads(filter="interested"), "filter"),
    (lambda c: c.get_campaign_analytics(campaign_id=CAMPAIGN, campaign_ids=[CAMPAIGN]), "campaign_id"),
    (lambda c: c.add_leads([{"email": "a@example.com"}]), "campaign_id"),
    (lambda c: c.add_leads([{"email": "a@example.com"}], campaign_id=CAMPAIGN, list_id=LIST), "campaign_id"),
    (lambda c: c.add_leads([{"first_name": "Jane"}], campaign_id=CAMPAIGN), "email"),
    (lambda c: c.add_leads([], list_id=LIST), "leads"),
    (lambda c: c.add_leads([{"email": "a@example.com"}] * 1001, list_id=LIST), "leads"),
    (lambda c: c.add_leads([{"email": "a@example.com", "notes": "x"}], list_id=LIST), "leads"),
    (lambda c: c.add_leads([{"email": "a@example.com", "custom_variables": {"x": [1]}}], list_id=LIST),
     "custom_variables"),
    (lambda c: c.create_lead("not-an-email"), "email"),
    (lambda c: c.create_lead("a@example.com", campaign=CAMPAIGN, list_id=LIST), "campaign"),
    (lambda c: c.update_lead(CAMPAIGN), "field"),
    (lambda c: c.update_lead_interest_status("a@example.com", "2"), "interest_value"),
    (lambda c: c.move_leads(to_list_id=LIST), "campaign"),
    (lambda c: c.move_leads(campaign=CAMPAIGN), "to_campaign_id"),
    (lambda c: c.move_leads(campaign=CAMPAIGN, to_list_id=LIST, to_campaign_id=CAMPAIGN), "to_campaign_id"),
    (lambda c: c.move_leads(campaign=CAMPAIGN, to_list_id=LIST, limit=0), "limit"),
    (lambda c: c.create_lead_list(None), "name"),
    (lambda c: c.add_block_list_entry(""), "bl_value"),
    (lambda c: c.reply_to_email(LIST, "jane@example.com", "Re: Hello"), "text"),
    (lambda c: c.reply_to_email(LIST, "jane", "Re: Hello", text="x"), "eaccount"),
    (lambda c: c.create_webhook("http://hooks.example.com/i", event_type="reply_received"), "target_hook_url"),
    (lambda c: c.create_webhook("https://hooks.example.com/i"), "event_type"),
    (lambda c: c.create_webhook("https://hooks.example.com/i", event_type="reply"), "event_type"),
    (lambda c: c.delete_webhook("w-1"), "webhook_id"),
])
def test_a_bad_argument_is_refused_before_any_call(cli, sent, call, name):
    with pytest.raises(ValueError, match=name):
        call(cli)
    assert sent["calls"] == []


# --- réponses ---------------------------------------------------------------------


def test_webhook_headers_are_removed_from_every_webhook_answer(cli, sent):
    sent["responses"] += [
        _Resp(200, {"items": [{"id": "w-1", "headers": {"X-Token": "t"}}, {"id": "w-2"}],
                    "next_starting_after": "w-2"}),
        _Resp(200, {"id": "w-1", "headers": {"X-Token": "t"}}),
    ]
    assert cli.list_webhooks() == {"items": [{"id": "w-1"}, {"id": "w-2"}], "next_starting_after": "w-2"}
    assert cli.delete_webhook(CAMPAIGN) == {"id": "w-1"}


def test_attachment_addresses_are_removed_from_every_email_answer(cli, sent):
    files = {"files": [{"filename": "a.pdf", "size": 10, "url": "https://files.example.test/a.pdf"}]}
    sent["responses"] += [
        _Resp(200, {"items": [{"id": "e-1", "attachment_json": dict(files, files=[dict(files["files"][0])])},
                              {"id": "e-2", "attachment_json": None}]}),
        _Resp(200, {"id": "e-1", "attachment_json": {"files": [dict(files["files"][0])]},
                    "body": {"text": "t", "html": "<p>t</p>"}}),
    ]
    listed = cli.list_emails()
    assert listed["items"][0]["attachment_json"] == {"files": [{"filename": "a.pdf", "size": 10}]}
    one = cli.get_email(CAMPAIGN)
    assert one["attachment_json"] == {"files": [{"filename": "a.pdf", "size": 10}]}
    assert one["body"] == {"text": "t", "html": "<p>t</p>"}


def test_the_removals_are_tolerant_of_any_shape():
    for odd in (None, [], "x", {"items": None}, {"items": [None, 1]}, {"attachment_json": "x"},
                {"attachment_json": {"files": "x"}}, {"attachment_json": {"files": [None]}}):
        assert without_attachment_urls(odd) == odd
        assert without_webhook_headers(odd) == odd


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 429, 500, 503])
def test_a_refusal_raises_upstream_http_error_once(cli, sent, status):
    sent["responses"].append(_Resp(status, {"statusCode": status, "error": "x", "message": "y"}))
    with pytest.raises(UpstreamHTTPError) as caught:
        cli.add_leads([{"email": "a@example.com"}], campaign_id=CAMPAIGN)
    assert caught.value.status_code == status and caught.value.service == "instantly"
    assert len(sent["calls"]) == 1


def test_a_redirect_is_refused_never_followed(cli, sent):
    sent["responses"].append(_Resp(302, {}, headers={"Location": "https://elsewhere.example.test/"}))
    with pytest.raises(UpstreamHTTPError):
        cli.get_workspace()
    assert len(sent["calls"]) == 1
