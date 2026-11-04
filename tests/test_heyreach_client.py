"""Contrat du client HeyReach (API publique, clé en `X-API-KEY`) ; sa concordance avec la description :
`test_heyreach_description.py`.

Mocke `requests.Session.request`. Ce qui est propre au transport : la clé en en-tête seulement, jamais dans l'adresse ;
aucune redirection suivie ; un 200 vide rendu `{}` ; un succès illisible qui n'est pas pris pour un refus ; un refus
de l'API traduit en `UpstreamHTTPError` ; les retraits de sortie (photo d'un membre, en-têtes d'un webhook) appliqués à
chaque réponse qui les porte, listes traversées ; les corps des gestes, tels que HeyReach les attend.
"""
from __future__ import annotations

import pytest

from oto.tools.common.credentials import MissingCredential
from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.heyreach import client as hr
from oto.tools.heyreach.client import HeyReachClient, HeyReachUnreadable
from oto.tools.http.client import RedirectRefused

URL = "https://www.linkedin.com/in/jane-doe/"


class _Resp:
    def __init__(self, status_code: int = 200, body=None, raw: bytes | None = None, headers=None):
        import json
        self.status_code = status_code
        self._body = body
        self.content = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
        self.text = self.content.decode("utf-8", "replace")
        self.headers = headers or {}

    def json(self):
        import json
        return json.loads(self.content)


@pytest.fixture()
def sent(monkeypatch):
    seen = {"calls": [], "responses": []}

    def fake_request(self, method, url, **kwargs):
        seen["calls"].append({"method": method, "url": url, **kwargs, "headers": dict(self.headers)})
        return seen["responses"].pop(0) if seen["responses"] else _Resp()

    monkeypatch.setattr(hr.requests.Session, "request", fake_request)
    return seen


@pytest.fixture()
def cli():
    return HeyReachClient(api_key=" HR_KEY ")


# --- compte et transport ---------------------------------------------------------


def test_the_key_is_required():
    with pytest.raises(MissingCredential):
        HeyReachClient(api_key=None)


def test_the_key_goes_in_the_header_only(cli, sent):
    cli.list_campaigns(10, keyword="Q4")
    [call] = sent["calls"]
    assert call["headers"]["X-API-KEY"] == "HR_KEY"
    assert "HR_KEY" not in call["url"] and "HR_KEY" not in str(call.get("params"))
    assert "HR_KEY" not in str(call.get("json"))
    assert call["allow_redirects"] is False and call["timeout"] == hr._HTTP_TIMEOUT


def test_the_key_check_is_a_get_and_an_empty_200_is_an_empty_answer(cli, sent):
    assert cli.check_api_key() == {}
    [call] = sent["calls"]
    assert (call["method"], call["url"]) == ("GET", "https://api.heyreach.io/api/public/auth/CheckApiKey")
    assert call["params"] is None and call["json"] is None


def test_a_redirect_is_refused(cli, sent):
    sent["responses"].append(_Resp(302, headers={"Location": "https://elsewhere.example.test/"}))
    with pytest.raises(RedirectRefused):
        cli.get_campaign(1)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_a_refusal_raises_upstream_http_error(cli, sent, status):
    sent["responses"].append(_Resp(status, {"errorMessage": "nope"}))
    with pytest.raises(UpstreamHTTPError) as e:
        cli.get_list(1)
    assert e.value.status_code == status


def test_a_success_that_is_not_json_is_unreadable_not_a_refusal(cli, sent):
    sent["responses"].append(_Resp(200, raw=b"<html>maintenance</html>"))
    with pytest.raises(HeyReachUnreadable):
        cli.get_campaign(1)


# --- ce que la réponse perd ------------------------------------------------------


def test_a_members_picture_never_reaches_the_caller(cli, sent):
    picture = "https://media.example.test/p.jpg"
    sent["responses"] += [
        _Resp(body={"totalCount": 2, "items": [
            {"id": 1, "linkedInUserProfile": {"profileUrl": URL, "imageUrl": picture}},
            {"id": 2, "linkedInUserProfile": None}]}),
        _Resp(body={"totalCount": 1, "items": [{"profileUrl": URL, "imageUrl": picture}]}),
        _Resp(body={"profileUrl": URL, "imageUrl": picture, "firstName": "Jane"}),
        _Resp(body={"items": [{"id": "c", "correspondentProfile": {"imageUrl": picture}}],
                    "nextCursor": None, "hasNextPage": False}),
        _Resp(body={"id": "c", "correspondentProfile": {"imageUrl": picture, "profileUrl": URL}}),
    ]
    answers = [cli.list_campaign_leads(1, 10), cli.list_list_leads(1, 10), cli.get_lead(URL),
               cli.list_conversations(), cli.get_conversation(1, "c")]
    assert picture not in str(answers)
    assert answers[0]["items"][0]["linkedInUserProfile"] == {"profileUrl": URL}
    assert answers[2] == {"profileUrl": URL, "firstName": "Jane"}
    assert answers[4]["correspondentProfile"] == {"profileUrl": URL}


def test_a_webhooks_custom_headers_are_never_asked_for_nor_returned(cli, sent):
    secret = [{"name": "authorization", "value": "Bearer s3cr3t"}]
    sent["responses"] += [
        _Resp(body={"totalCount": 1, "items": [{"id": 1, "webhookUrl": "https://h.example.test/x",
                                                 "customHeaders": secret}]}),
        _Resp(body={"id": 1, "customHeaders": secret}),
    ]
    listed, one = cli.list_webhooks(100), cli.get_webhook(1)
    assert "s3cr3t" not in str([listed, one])
    assert listed["items"][0]["webhookUrl"] == "https://h.example.test/x"
    first, second = sent["calls"]
    assert first["json"]["includeCustomHeaders"] is False
    assert second["params"] == {"webhookId": 1, "includeCustomHeaders": "false"}


# --- corps des gestes ------------------------------------------------------------


def test_leads_go_with_heyreachs_own_keys_and_absent_flags_stay_absent(cli, sent):
    lead = {"profileUrl": URL, "firstName": "Jane",
            "customUserFields": [{"name": "opener", "value": "Hi"}]}
    cli.add_leads_to_campaign(1001, [{"linkedInAccountId": 3001, "lead": lead}])
    [call] = sent["calls"]
    assert call["url"].endswith("/campaign/AddLeadsToCampaignV2")
    assert call["json"] == {"campaignId": 1001,
                            "accountLeadPairs": [{"linkedInAccountId": 3001, "lead": lead}]}


def test_pause_and_resume_take_the_campaign_in_the_query_and_send_no_body(cli, sent):
    cli.pause_campaign(1001)
    cli.resume_campaign(1001)
    pause, resume = sent["calls"]
    assert (pause["method"], pause["url"], pause["params"], pause["json"]) == (
        "POST", hr.BASE_URL + "/campaign/Pause", {"campaignId": 1001}, None)
    assert resume["url"] == hr.BASE_URL + "/campaign/Resume"


def test_the_inbox_filters_nest_and_vanish_when_none_is_given(cli, sent):
    cli.list_conversations(25, seen=False, campaign_ids=[1001])
    cli.list_conversations(10, "CURSOR")
    filtered, plain = sent["calls"]
    assert filtered["json"] == {"limit": 25, "filters": {"campaignIds": [1001], "seen": False}}
    assert plain["json"] == {"limit": 10, "cursor": "CURSOR"}


def test_a_conversation_id_is_one_encoded_segment(cli, sent):
    cli.get_conversation(3001, "2-ab/c+d==")
    [call] = sent["calls"]
    assert call["url"] == hr.BASE_URL + "/inbox/GetChatroom/3001/2-ab%2Fc%2Bd%3D%3D"


def test_stats_keep_an_empty_list_which_means_all(cli, sent):
    cli.get_overall_stats("2026-10-01T00:00:00Z", "2026-10-31T23:59:59Z", account_ids=[],
                          campaign_ids=[])
    [call] = sent["calls"]
    assert call["json"] == {"startDate": "2026-10-01T00:00:00Z", "endDate": "2026-10-31T23:59:59Z",
                            "accountIds": [], "campaignIds": []}


def test_a_message_is_sent_once_as_given(cli, sent):
    cli.send_message(3001, "2-abc", "Thanks!", subject="Re")
    [call] = sent["calls"]
    assert call["json"] == {"linkedInAccountId": 3001, "conversationId": "2-abc",
                            "message": "Thanks!", "subject": "Re"}


def test_tags_name_the_lead_by_one_key(cli, sent):
    cli.add_lead_tags(["hot"], lead_linkedin_id="58344979", create_tag_if_not_existing=True)
    [call] = sent["calls"]
    assert call["json"] == {"leadLinkedInId": "58344979", "tags": ["hot"],
                            "createTagIfNotExisting": True}


def test_a_webhook_is_an_https_address_and_takes_an_empty_campaign_list(cli, sent):
    cli.create_webhook("Replies", "https://hooks.example.com/h", "EVERY_MESSAGE_REPLY_RECEIVED",
                       campaign_ids=[])
    [call] = sent["calls"]
    assert call["json"]["campaignIds"] == []
    with pytest.raises(ValueError, match="https"):
        cli.create_webhook("x", "hooks.example.com/h", "MESSAGE_SENT")


def test_delete_webhook_is_a_delete_with_the_id_in_the_query(cli, sent):
    cli.delete_webhook(4001)
    [call] = sent["calls"]
    assert (call["method"], call["params"], call["json"]) == ("DELETE", {"webhookId": 4001}, None)
