"""La description `emailbison` 1.0.2 : la prospection par email à froid d'EmailBison, sans client Python.

Gardes : le compte est un jeton porteur et l'adresse de l'instance (un réglage `url`, gardé par le moteur à chaque
requête) ; aucune route d'administration, de jeton ni de suppression n'est décrite, sauf la suppression d'un webhook ;
ce qui engage (leads ou liste dans une campagne, reprise, réponse, webhook) est sensible, confirmé et dit son effet ;
l'adresse signée d'une pièce jointe, le corps brut et les en-têtes d'une réponse ne sortent jamais vers l'agent, ce que
prouve la sortie servie sur des réponses simulées ; les listes à curseur envoient `pagination_type=cursor` et suivent
`meta.next_cursor`.
"""
from __future__ import annotations

import copy
import socket

import pytest
import yaml

from engine_fake import answer, play, sent
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors import descriptions, engine, serve
from oto.tools.common import url_guard

EMAILBISON = CONNECTORS / "emailbison" / "connector.yaml"
INSTANCE = "https://send.example.test"
SETTINGS = {"instance_url": INSTANCE}
SENSITIVE = {"add_leads_to_campaign", "add_lead_list_to_campaign", "resume_campaign", "reply_to_email",
             "create_webhook", "delete_webhook"}
NEVER_SERVED = ("password", "profile-picture", "headless-ui-token", "api-tokens", "oauth-access-token",
                "oauth-sessions", "workspaces/v1.1/users", "invite-members", "switch-workspace", "test-email",
                "replies/new", "forward", "bulk/csv")
SIGNED = "https://bison.example.test/reply-attachments/0/download?signature=secret-signature"


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(EMAILBISON.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _served() -> dict:
    return descriptions.load("emailbison")


@pytest.fixture(autouse=True)
def _public_instance(monkeypatch):
    """L'instance résout vers une adresse publique : la garde du moteur la laisse passer, sans réseau."""
    def resolver(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", resolver)


def _message(**extra) -> dict:
    """Un message tel qu'EmailBison le rend, valeurs inventées."""
    return {"id": 45, "subject": "Re: hello", "text_body": "Sounds good", "html_body": "<p>Sounds good</p>",
            "raw_body": "MIME-Version: 1.0", "headers": "Authentication-Results: pass",
            "attachments": [{"id": 8, "file_name": "deck.pdf", "download_url": SIGNED}], **extra}


def _assert_clean(message: dict, *, html: bool):
    assert "raw_body" not in message and "headers" not in message
    assert ("html_body" in message) is html
    assert message["attachments"] == [{"id": 8, "file_name": "deck.pdf"}]
    assert "secret-signature" not in repr(message)
    assert message["text_body"] == "Sounds good"


def test_description_passes_the_fabrique():
    module = connector_module(check(_description(), EMAILBISON))
    assert module.generated and module.skipped == []


def test_the_account_is_a_bearer_token_on_the_instance_address():
    connector = _description()["connector"]
    assert connector["auth"] == {"kind": "bearer", "token": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "API token", "secret": True}]
    assert connector["base_url"] == "{instance_url}/api"
    assert [(s["name"], s["type"]) for s in connector["settings"]] == [("instance_url", "url")]
    assert set(connector["modes"]) == {"byo_user", "byo_org"}
    assert "quota" not in connector


def test_reads_stay_on_the_read_side_and_only_stats_are_read_in_post():
    for name, fn in _functions().items():
        if fn["class"] != "read":
            continue
        assert fn.get("act", False) is False, name
        expected = "POST" if name == "get_campaign_stats" else "GET"
        assert fn["call"]["method"] == expected, name


def test_what_engages_is_sensitive_confirmed_and_says_its_effect():
    functions = _functions()
    assert {n for n, f in functions.items() if f["class"] == "sensitive"} == SENSITIVE
    for name in SENSITIVE:
        assert functions[name]["effect"] == ("destroy" if name == "delete_webhook" else "send"), name
        assert functions[name]["confirm"]["summary"], name


def test_only_a_webhook_is_ever_deleted_and_no_admin_or_token_route_is_described():
    for name, fn in _functions().items():
        path = fn["call"]["path"]
        if fn["call"]["method"] == "DELETE":
            assert name == "delete_webhook"
        assert not any(part in path for part in NEVER_SERVED), (name, path)


def test_the_reply_list_never_serves_an_attachment_address_a_raw_body_or_headers():
    fn = serve.function(_served(), "list_replies")
    served = serve.items_of(fn, [{"data": [_message(), _message(id=46)]}])
    assert [m["id"] for m in served] == [45, 46]
    for message in served:
        _assert_clean(message, html=False)


def test_a_reply_keeps_its_html_but_never_its_attachment_address():
    fn = serve.function(_served(), "get_reply")
    _assert_clean(serve.shaped({"data": _message()}, fn["output"])["data"], html=True)


def test_a_thread_is_clean_in_every_message():
    fn = serve.function(_served(), "get_reply_thread")
    thread = serve.shaped({"data": {"current_reply": _message(), "older_messages": [_message(id=44)],
                                    "newer_messages": [_message(id=46)]}}, fn["output"])["data"]
    _assert_clean(thread["current_reply"], html=False)
    for message in thread["older_messages"] + thread["newer_messages"]:
        _assert_clean(message, html=False)


def test_the_sent_reply_is_clean_too():
    fn = serve.function(_served(), "reply_to_email")
    reply = serve.shaped({"data": {"success": True, "reply": _message()}}, fn["output"])["data"]["reply"]
    _assert_clean(reply, html=True)


def test_a_cursor_list_asks_for_cursors_follows_the_next_one_and_writes_booleans_as_digits():
    first = answer(200, {"data": [_message()], "meta": {"next_cursor": "c2", "per_page": 15}})
    last = answer(200, {"data": [_message(id=46)], "meta": {"next_cursor": None, "per_page": 15}})
    result, vendor = play(_served(), "list_replies", {"read": 0, "tag_ids": [1, 2], "all_pages": True}, first, last,
                          settings=SETTINGS)
    one, two = (sent(r, f"{INSTANCE}/api") for r in vendor.requests)
    assert one["path"] == "/replies" and two["path"] == "/replies"
    assert one["query"] == [["pagination_type", "cursor"], ["read", "0"], ["tag_ids[]", "1"], ["tag_ids[]", "2"]]
    assert ["cursor", "c2"] in two["query"] and ["pagination_type", "cursor"] in two["query"]
    assert [m["id"] for m in result["items"]] == [45, 46]
    assert one["headers"]["authorization"] == "Bearer test-key-not-real"


def test_lead_filters_go_under_filters_brackets():
    args = {"lead_campaign_status": "replied", "verification_statuses": ["verified"], "without_tags": 1}
    _, vendor = play(_served(), "list_campaign_leads", {"campaign_id": 6, **args},
                     answer(200, {"data": [], "meta": {"next_cursor": None}}), settings=SETTINGS)
    request = sent(vendor.requests[0], f"{INSTANCE}/api")
    assert request["path"] == "/campaigns/6/leads"
    assert request["query"] == [["pagination_type", "cursor"], ["filters[lead_campaign_status]", "replied"],
                                ["filters[verification_statuses][]", "verified"], ["filters[without_tags]", "1"]]


def test_the_campaign_stats_are_read_with_their_dates_in_the_body():
    _, vendor = play(_served(), "get_campaign_stats", {"campaign_id": 6, "start_date": "2026-11-01",
                                                       "end_date": "2026-11-30"}, answer(200, {"data": {}}),
                     settings=SETTINGS)
    request = sent(vendor.requests[0], f"{INSTANCE}/api")
    assert (request["method"], request["path"]) == ("POST", "/campaigns/6/stats")
    assert request["body"] == {"start_date": "2026-11-01", "end_date": "2026-11-30"}


def test_a_reply_needs_a_sender_and_recipients_unless_it_replies_to_all():
    schema = _functions()["reply_to_email"]["input"]
    from jsonschema import Draft202012Validator
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"reply_id": 1, "message": "Hi", "reply_all": True})
    assert not validator.is_valid({"reply_id": 1, "message": "Hi"})
    assert not validator.is_valid({"reply_id": 1, "message": "Hi", "reply_all": False, "sender_email_id": 2})
    assert validator.is_valid({"reply_id": 1, "message": "Hi", "sender_email_id": 2,
                               "to_emails": [{"email_address": "jane@example.test"}]})


def test_a_private_or_plain_http_instance_is_refused_before_anything_is_sent(monkeypatch):
    with pytest.raises(engine.AccountSettingError, match="must start with https"):
        play(_served(), "get_account", {}, settings={"instance_url": "http://send.example.test"})

    def private(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.7", port))]
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", private)
    with pytest.raises(engine.AddressRefused):
        play(_served(), "get_account", {}, settings=SETTINGS)


WEBHOOK_EVENTS = {  # GET /api/webhook-events/event-types, in the reference
    "email_sent", "email_send_failed", "manual_email_sent", "manual_email_send_failed", "lead_first_contacted",
    "lead_replied", "lead_interested", "lead_unsubscribed", "untracked_reply_received", "email_opened",
    "email_bounced", "email_account_added", "email_account_removed", "email_account_disconnected",
    "email_account_reconnected", "blacklisted_email_added", "blacklisted_email_removed", "blacklisted_domain_added",
    "blacklisted_domain_removed"}


def test_a_webhook_subscribes_to_the_event_types_of_the_reference_only():
    events = _functions()["create_webhook"]["input"]["properties"]["events"]["items"]["enum"]
    assert set(events) == WEBHOOK_EVENTS and len(events) == len(WEBHOOK_EVENTS)


def test_every_paged_list_keeps_a_projection_or_strips():
    for name, fn in _functions().items():
        if "pagination" in fn or "page" in fn["input"]["properties"]:
            assert {"projection", "strip"} & set(fn.get("output") or {}), name


def test_lead_lists_keep_their_ids_and_counts_but_not_custom_variables_or_notes():
    lead = {"id": 1, "first_name": "Jane", "email": "jane@example.test", "status": "verified", "notes": "private",
            "custom_variables": [{"name": "phone", "value": "+33000000000"}], "overall_stats": {"replies": 1},
            "lead_campaign_data": [{"campaign_id": 6}], "uuid": "u-1"}
    for name in ("list_leads", "list_campaign_leads"):
        (served,) = serve.items_of(serve.function(_served(), name), [{"data": [lead]}])
        assert served == {"id": 1, "first_name": "Jane", "email": "jane@example.test", "status": "verified",
                          "overall_stats": {"replies": 1}, "lead_campaign_data": [{"campaign_id": 6}]}, name


def test_sending_account_lists_leave_signature_and_servers_to_get_sender_email():
    account = {"id": 20, "email": "sales@example.test", "status": "connected", "email_signature": "<p>Jane</p>",
               "imap_server": "imap.example.test", "imap_port": 993, "smtp_server": "smtp.example.test",
               "smtp_port": 465, "emails_sent_count": 12}
    for name in ("list_sender_emails", "list_campaign_sender_emails"):
        (served,) = serve.items_of(serve.function(_served(), name), [{"data": [account]}])
        assert served == {"id": 20, "email": "sales@example.test", "status": "connected", "emails_sent_count": 12}


def test_lead_lists_drop_their_import_errors_and_keep_the_page():
    fn = serve.function(_served(), "list_lead_lists")
    served = serve.result(fn, [{"data": [{"id": 12, "name": "Webinar", "error_messages": ["row 3: x@example.test"]}],
                                "meta": {"current_page": 1, "last_page": 2}}])
    assert served == {"data": [{"id": 12, "name": "Webinar"}], "meta": {"current_page": 1, "last_page": 2}}


def test_a_function_named_as_a_destruction_is_one():
    """oto 1 reads `delete_`, `remove_`, `archive_`… as a destruction: such a name is a sensitive `destroy`."""
    for name, fn in _functions().items():
        if name.split("_")[0] in ("delete", "remove", "merge", "purge", "archive", "revoke", "trash"):
            assert (fn["class"], fn.get("effect")) == ("sensitive", "destroy"), name
