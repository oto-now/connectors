"""La description `heyreach` 1.0.1 et son client (`oto.tools.heyreach`) : la même requête, argument absent compris.

Une fonction par méthode du client, sous son nom, avec les arguments que la méthode nomme. Pour chaque fonction, deux
appels : tous ses arguments donnés, puis ses seuls arguments requis ; la requête que le client envoie (verbe, adresse,
query, corps JSON) est celle que la description dit, constantes comprises (`includeCustomHeaders` jamais vrai). Un
segment de chemin s'encode en entier (un identifiant de conversation est du base64). Les retraits de sortie
(`output.strip`) sont ceux du client (`STRIP`) ; les classes sont figées ici ; le client refuse localement, avant tout
appel, ce que le schéma refuse (`NARROWER`).
"""
from __future__ import annotations

import copy
import inspect
import json
import re
from unittest.mock import MagicMock
from urllib.parse import quote

import jsonschema
import pytest
import yaml

from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.tools.heyreach import client as hr
from oto.tools.heyreach.client import HeyReachClient

HEYREACH = CONNECTORS / "heyreach" / "connector.yaml"

URL = "https://www.linkedin.com/in/jane-doe/"
LEAD = {"profileUrl": URL, "firstName": "Jane", "lastName": "Doe", "companyName": "Example",
        "customUserFields": [{"name": "opener", "value": "Saw your talk"}]}
VALUES = {
    "campaign_id": 1001, "list_id": 2001, "account_id": 3001, "sender_account_id": 3001,
    "linkedin_account_id": 3001, "webhook_id": 4001, "limit": 50, "offset": 20,
    "keyword": "Q4", "statuses": ["IN_PROGRESS", "PAUSED"], "account_ids": [3001],
    "campaign_ids": [1001], "linkedin_account_ids": [3001], "time_filter": "LastActionTakenTime",
    "time_from": "2026-11-02T00:00:00Z", "time_to": "2026-11-04T23:59:59+01:00",
    "created_from": "2026-11-02T00:00:00Z", "created_to": "2026-11-04T00:00:00Z",
    "start_date": "2026-10-01T00:00:00Z", "end_date": "2026-10-31T23:59:59Z",
    "last_message_from": "2026-11-01T00:00:00Z", "last_message_to": "2026-11-04T00:00:00Z",
    "profile_url": URL, "lead_profile_url": URL, "lead_url": URL, "linkedin_id": "000000000-fake",
    "lead_linkedin_id": "000000000-fake", "lead_member_id": "000000000-fake", "email": "jane@example.com",
    "list_type": "USER_LIST", "type": "COMPANY_LIST", "name": "Webinar attendees",
    "cursor": "WzE3NTQwMDAwMDAwMDAsMTIzNF0=", "search": "pricing", "tags": ["hot", "q4"],
    "latest_auto_tag_names": ["Interested"], "seen": False,
    "conversation_id": "2-ODM0Y/mIz+NzgtOGEy==", "message": "Thanks, Tuesday works.",
    "subject": "Re: intro", "create_tag_if_not_existing": True,
    "resume_paused_campaign": True, "resume_finished_campaign": False,
    "account_lead_pairs": [{"linkedInAccountId": 3001, "lead": LEAD}, {"lead": {"profileUrl": URL}}],
    "leads": [LEAD, {"profileUrl": URL}],
    "webhook_name": "Replies", "webhook_url": "https://hooks.example.com/heyreach",
    "event_type": "EVERY_MESSAGE_REPLY_RECEIVED",
}
#: Arguments that cannot go together: the full call drops the second.
EXCLUSIVE = {"list_conversations": "lead_profile_url", "check_connection": "lead_linkedin_id"}
CLASSES = {
    "read": {"check_api_key", "list_campaigns", "get_campaign", "list_campaign_leads",
             "list_lead_campaigns", "list_lists", "get_list", "list_list_leads", "list_lead_lists",
             "get_lead", "get_lead_tags", "get_overall_stats", "get_stats_by_campaign",
             "list_conversations", "get_conversation", "list_linkedin_accounts",
             "get_linkedin_account", "check_connection", "list_webhooks", "get_webhook"},
    "write": {"pause_campaign", "create_empty_list", "set_seen_status", "add_lead_tags"},
    "sensitive": {"add_leads_to_campaign", "resume_campaign", "send_message", "replace_lead_tags",
                  "create_webhook", "delete_webhook", "add_leads_to_list", "stop_lead_in_campaign"},
}
#: What each sensitive function does once confirmed: something leaves (`send`) or is lost (`destroy`).
EFFECTS = {
    "send": {"add_leads_to_campaign", "resume_campaign", "send_message", "create_webhook",
             "add_leads_to_list"},
    "destroy": {"replace_lead_tags", "delete_webhook", "stop_lead_in_campaign"},
}
#: What the schema refuses, refused by the client before any call.
NARROWER = [
    ("list_campaigns", {"limit": 0}), ("list_campaigns", {"limit": 101}),
    ("list_campaigns", {"limit": 10, "statuses": ["RUNNING"]}),
    ("list_list_leads", {"list_id": 1, "limit": 1001}),
    ("list_campaign_leads", {"campaign_id": 1, "limit": 10, "time_from": "2026-11-02T00:00:00"}),
    ("list_lead_campaigns", {"limit": 10}),
    ("list_conversations", {"lead_profile_url": URL, "lead_linkedin_id": "1"}),
    ("check_connection", {"sender_account_id": 1}),
    ("check_connection", {"sender_account_id": 1, "lead_profile_url": URL, "lead_linkedin_id": "1"}),
    ("add_leads_to_list", {"list_id": 1, "leads": [{"firstName": "Jane"}]}),
    ("add_leads_to_list", {"list_id": 1, "leads": [{"profileUrl": URL}] * 101}),
    ("add_leads_to_list", {"list_id": 1, "leads": [{"profileUrl": URL, "first_name": "Jane"}]}),
    ("add_leads_to_list", {"list_id": 1, "leads": [
        {"profileUrl": URL, "customUserFields": [{"name": "bad-name", "value": "x"}]}]}),
    ("add_leads_to_campaign", {"campaign_id": 1, "account_lead_pairs": [{"profileUrl": URL}]}),
    ("add_lead_tags", {"tags": ["hot"]}), ("add_lead_tags", {"lead_profile_url": URL, "tags": []}),
    ("stop_lead_in_campaign", {"campaign_id": 1}),
    ("create_webhook", {"webhook_name": "w", "webhook_url": "http://hooks.example.com/h",
                        "event_type": "MESSAGE_SENT"}),
    ("create_webhook", {"webhook_name": "w", "webhook_url": "https://hooks.example.com/h",
                        "event_type": "REPLY"}),
    ("get_campaign", {"campaign_id": True}), ("get_conversation", {"linkedin_account_id": 1,
                                                                    "conversation_id": ".."}),
]


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(HEYREACH.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _answer(body=None):
    r = MagicMock()
    r.status_code = 200
    r.headers = {}
    r.content = b"" if body is None else json.dumps(body).encode()
    r.text = r.content.decode()
    r.json.return_value = copy.deepcopy(body)
    return r


def _sent(name: str, arguments: dict) -> dict:
    client = HeyReachClient(api_key="k")
    client.session.request = MagicMock(return_value=_answer())
    getattr(client, name)(**arguments)
    assert client.session.request.call_count == 1, name
    (method, url), kwargs = client.session.request.call_args
    assert kwargs["allow_redirects"] is False and kwargs["timeout"] == hr._HTTP_TIMEOUT
    return {"method": method, "url": url, "query": kwargs.get("params") or {},
            "body": kwargs.get("json")}


def _place(table: dict, arguments: dict) -> dict:
    """A body table filled from the arguments given; a nested table absent when empty."""
    out = {}
    for api, source in table.items():
        if isinstance(source, dict):
            nested = _place(source, arguments)
            if nested:
                out[api] = nested
        elif source in arguments:
            out[api] = arguments[source]
    return out


def _described(function: dict, arguments: dict) -> dict:
    call = function["call"]
    path = re.sub(r"{(\w+)}", lambda m: quote(str(arguments[m.group(1)]), safe=""), call["path"])
    constants = call.get("constants", {})
    query = {api: arguments[a] for api, a in call.get("query", {}).items() if a in arguments}
    query.update(constants.get("query", {}))
    body = None
    if "body" in call:
        body = {**constants.get("body", {}), **_place(call["body"], arguments)}
    return {"method": call["method"], "url": hr.BASE_URL + path, "query": query, "body": body}


def _argument_sets(name: str, function: dict) -> list[dict]:
    schema = function["input"]
    everything = {a: VALUES[a] for a in schema["properties"] if a != EXCLUSIVE.get(name)}
    required = {a: VALUES[a] for a in schema.get("required", [])}
    branches = schema.get("anyOf") or schema.get("oneOf")
    if branches:
        required.update({a: VALUES[a] for a in branches[0]["required"]})
    sets = [everything] if everything == required else [everything, required]
    for given in sets:
        jsonschema.Draft202012Validator(schema).validate(given)
    return sets


def _cases():
    for name, function in _functions().items():
        for i, given in enumerate(_argument_sets(name, function)):
            yield pytest.param(name, given, id=f"{name}-{'all' if i == 0 else 'required'}")


def test_description_passes_the_fabrique():
    module = connector_module(check(_description(), HEYREACH))
    assert module.generated and module.skipped == []


def test_one_method_per_function_and_nothing_else():
    public = {n for n, _ in inspect.getmembers(HeyReachClient, inspect.isfunction)
              if not n.startswith("_")}
    assert public == set(_functions())


def test_every_method_argument_is_an_argument_of_its_function():
    for name, function in _functions().items():
        arguments = set(inspect.signature(getattr(HeyReachClient, name)).parameters) - {"self"}
        assert arguments == set(function["input"]["properties"]), name


@pytest.mark.parametrize("name, given", list(_cases()))
def test_the_client_sends_the_request_the_description_says(name, given):
    assert _sent(name, given) == _described(_functions()[name], given)


def test_examples_send_what_the_description_says():
    for name, function in _functions().items():
        for example in function["examples"]:
            assert _sent(name, example["input"]) == _described(function, example["input"]), (
                name, example["title"])


def test_classes_and_sides_are_frozen():
    by_class = {k: {n for n, f in _functions().items() if f["class"] == k} for k in CLASSES}
    assert by_class == CLASSES
    for name, function in _functions().items():
        assert "act" not in function, name
        assert ("confirm" in function) is (function["class"] == "sensitive"), name
        effect = next((e for e, names in EFFECTS.items() if name in names), None)
        assert function.get("effect") == effect, name


def test_custom_headers_are_never_asked_for():
    for name, function in _functions().items():
        constants = function["call"].get("constants", {})
        for place in ("query", "body"):
            if "includeCustomHeaders" in constants.get(place, {}):
                assert constants[place]["includeCustomHeaders"] in (False, "false"), name


def test_the_client_strips_what_the_description_strips():
    described = {name: (f["output"].get("items"), tuple(f["output"]["strip"]))
                 for name, f in _functions().items() if "strip" in f.get("output", {})}
    assert described == hr.STRIP


def test_a_lead_or_a_person_is_personal():
    for name, function in _functions().items():
        for argument in ("profile_url", "linkedin_id", "email", "lead_profile_url",
                         "lead_linkedin_id", "lead_member_id", "lead_url", "search"):
            if argument in function["input"]["properties"]:
                assert function["input"]["properties"][argument].get("personal") is True, (
                    name, argument)
    lead = _functions()["add_leads_to_list"]["input"]["properties"]["leads"]["items"]["properties"]
    for key in ("profileUrl", "firstName", "lastName", "emailAddress"):
        assert lead[key]["personal"] is True, key


@pytest.mark.parametrize("name, given", NARROWER, ids=[f"{n}-{i}" for i, (n, _) in enumerate(NARROWER)])
def test_what_the_schema_refuses_the_client_refuses_before_any_call(name, given):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_functions()[name]["input"]).validate(given)
    client = HeyReachClient(api_key="k")
    client.session.request = MagicMock()
    with pytest.raises((ValueError, TypeError)):
        getattr(client, name)(**given)
    client.session.request.assert_not_called()
