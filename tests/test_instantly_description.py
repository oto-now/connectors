"""La description `instantly` 1.0.1 et son client (`oto.tools.instantly`) : la même requête, argument par argument.

Une fonction par méthode du client, sous son nom, avec ses arguments ; seul `headers` de `create_webhook` reste au
client (un secret, que l'hôte pose côté serveur et qu'aucun agent ne passe). Pour chaque exemple de chaque fonction,
et pour chaque jeu de tous ses arguments (les exclusifs pris l'un après l'autre), le client envoie exactement la
requête que dit la description : verbe, adresse, query (booléens écrits `true` / `false`), corps ; un argument absent
ne part pas. Gardes de plus : les classes et le côté servi (`act`) ; le droit (scope) que nomme chaque fonction, dans
sa description et dans son refus 403 ; les arguments qui nomment une personne sont `personal` ; les retraits de
sortie (en-têtes d'un webhook, adresse d'une pièce jointe).
"""
from __future__ import annotations

import copy
import inspect
import itertools
import re

import jsonschema
import pytest
import yaml

from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.tools.instantly import client as ic
from oto.tools.instantly.client import InstantlyClient

INSTANTLY = CONNECTORS / "instantly" / "connector.yaml"

U = ["0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a%02x" % i for i in range(20)]
VALUES = {
    "campaign_id": U[0], "campaign": U[1], "list_id": U[2], "lead_id": U[3], "email_id": U[4],
    "thread_id": U[5], "webhook_id": U[6], "reply_to_uuid": U[7], "to_campaign_id": U[8],
    "to_list_id": U[9], "blocklist_id": U[10], "campaign_ids": [U[11], U[12]], "ids": [U[13], U[14]],
    "search": "jane.doe@example.com", "status": 1, "campaign_status": 2, "provider_code": 2,
    "tag_ids": U[15], "limit": 50, "starting_after": "cursor-1", "sort_column": "name",
    "sort_order": "desc", "start_date": "2026-10-01", "end_date": "2026-10-31T23:59:59Z",
    "min_timestamp_created": "2026-10-01", "max_timestamp_created": "2026-11-01",
    "exclude_total_leads_count": True, "expand_crm_events": True, "include_opportunities_count": True,
    "in_campaign": True, "in_list": False, "distinct_contacts": True, "has_enrichment_task": False,
    "is_unread": True, "latest_of_thread": True, "preview_only": False, "skip_if_in_workspace": True,
    "skip_if_in_campaign": False, "skip_if_in_list": True, "copy_leads": True, "check_duplicates": True,
    "check_duplicates_in_campaigns": False, "skip_invalid_emails": True,
    "contacts": ["jane.doe@example.com", "john.roe@example.com"], "filter": "FILTER_LEAD_INTERESTED",
    "interest_status": "positive", "job_id": "job-1", "data_fields": "moved_leads,moved_lead_emails",
    "lead": "jane.doe@example.com", "eaccount": "jane@example.com", "email": "jane@example.com",
    "email_type": "received", "i_status": 1, "mode": "emode_focused", "company_domain": "example.com",
    "event_type": "reply_received",
    "leads": [{"email": "jane.doe@example.com", "first_name": "Jane", "custom_variables": {"tier": "A"}}],
    "first_name": "Jane", "last_name": "Doe", "company_name": "Example", "job_title": "Head of Sales",
    "phone": "+10000000000", "website": "https://example.com", "personalization": "Saw your talk.",
    "pl_value_lead": "High", "custom_variables": {"tier": "A", "score": 3, "vip": True},
    "lead_email": "jane.doe@example.com", "interest_value": 2, "name": "Spring fair",
    "bl_value": "example.com", "subject": "Re: Hello", "text": "Thanks", "html": "<p>Thanks</p>",
    "cc_address_email_list": "a@example.com", "bcc_address_email_list": "b@example.com",
    "target_hook_url": "https://hooks.example.com/instantly", "custom_interest_value": 3,
}
#: Arguments of which a call takes one only: each jeu de tous les arguments prend l'un, puis l'autre.
EXCLUSIVE = {
    "get_campaign_analytics": [("campaign_id", "campaign_ids")],
    "get_campaign_analytics_overview": [("campaign_id", "campaign_ids")],
    "add_leads": [("campaign_id", "list_id")],
    "create_lead": [("campaign", "list_id")],
    "update_lead_interest_status": [("campaign_id", "list_id")],
    "move_leads": [("campaign", "list_id"), ("to_campaign_id", "to_list_id")],
}
CLASSES = {
    "write": {"update_lead", "create_lead_list", "add_block_list_entry", "pause_campaign",
              "mark_thread_as_read"},
    "sensitive": {"add_leads", "create_lead", "update_lead_interest_status", "move_leads",
                  "activate_campaign", "reply_to_email", "create_webhook", "delete_webhook"},
}
#: What each sensitive function does once confirmed: something leaves (`send`) or is lost (`destroy`).
EFFECTS = {
    "send": {"add_leads", "create_lead", "update_lead_interest_status", "activate_campaign",
             "reply_to_email", "create_webhook"},
    "destroy": {"move_leads", "delete_webhook"},
}
#: The scope each function names, as the reference spells it; `None`: the reference states none.
SCOPES = {
    "get_workspace": "workspaces:read", "list_campaigns": "campaigns:read", "get_campaign": "campaigns:read",
    "search_campaigns_by_lead": "campaigns:read", "get_campaign_analytics": None,
    "get_campaign_analytics_overview": None, "get_campaign_daily_analytics": None,
    "get_campaign_steps_analytics": None, "list_leads": "leads:read", "get_lead": "leads:read",
    "list_lead_lists": "lead_lists:read", "get_lead_list": "lead_lists:read",
    "list_lead_labels": "lead-labels:read", "get_background_job": "background-jobs:read",
    "list_emails": "emails:read", "get_email": "emails:read", "count_unread_emails": "emails:read",
    "list_accounts": "accounts:read", "get_account": "accounts:read", "list_webhooks": None,
    "list_webhook_event_types": None, "add_leads": "leads:create", "create_lead": "leads:create",
    "update_lead": "leads:update", "update_lead_interest_status": None, "move_leads": "leads:update",
    "create_lead_list": "lead_lists:create", "add_block_list_entry": "block_list_entries:create",
    "activate_campaign": "campaigns:update", "pause_campaign": "campaigns:update",
    "reply_to_email": "emails:create", "mark_thread_as_read": "emails:update",
    "create_webhook": "webhooks:create", "delete_webhook": "webhooks:delete",
}
LEFT_TO_THE_CLIENT = {"create_webhook": {"headers"}}
#: Arguments that name a person (an email, a name, a phone, a free line about them).
PERSONAL = {"search_campaigns_by_lead": {"search"}, "list_leads": {"search", "contacts"},
            "list_emails": {"lead", "search", "eaccount"}, "list_accounts": {"search"},
            "get_account": {"email"}, "update_lead_interest_status": {"lead_email"},
            "move_leads": {"contacts", "search"}, "add_block_list_entry": {"bl_value"},
            "reply_to_email": {"eaccount", "cc_address_email_list", "bcc_address_email_list"},
            **{n: {"email", "first_name", "last_name", "phone", "personalization"}
               for n in ("create_lead",)},
            "update_lead": {"first_name", "last_name", "phone", "personalization"}}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(INSTANTLY.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


class _Resp:
    status_code = 200
    content = b"{}"
    headers: dict = {}
    text = "{}"

    def json(self):
        return {}


def _sent(name: str, arguments: dict) -> dict:
    client = InstantlyClient(api_key="k")
    seen = []

    def request(method, url, **kwargs):
        seen.append({"method": method, "url": url, "params": kwargs.get("params") or {},
                     "json": kwargs.get("json")})
        return _Resp()

    client.session.request = request
    getattr(client, name)(**copy.deepcopy(arguments))
    assert len(seen) == 1, name
    return seen[0]


def _wire(value):
    return ("true" if value else "false") if isinstance(value, bool) else value


def _placed(table: dict, arguments: dict) -> dict:
    """The body a table sends: each given argument at its key, a nested table only when it holds one."""
    out = {}
    for key, value in table.items():
        if isinstance(value, dict):
            inner = _placed(value, arguments)
            if inner:
                out[key] = inner
        elif value in arguments:
            out[key] = arguments[value]
    return out


def _described(function: dict, arguments: dict) -> dict:
    call = function["call"]
    path = re.sub(r"{(\w+)}", lambda m: str(arguments[m.group(1)]), call["path"])
    body = _placed(call["body"], arguments) if "body" in call else None
    return {"method": call["method"], "url": ic.BASE_URL + path,
            "params": {api: _wire(arguments[a]) for api, a in call.get("query", {}).items() if a in arguments},
            "json": body}


def _full_sets(name: str, function: dict) -> list[dict]:
    everything = {a: VALUES[a] for a in function["input"]["properties"]}
    pairs = EXCLUSIVE.get(name, [])
    sets = []
    for choice in itertools.product(*pairs):
        dropped = {a for pair, kept in zip(pairs, choice) for a in pair if a != kept}
        sets.append({k: v for k, v in everything.items() if k not in dropped})
    return sets


def _cases():
    for name, function in _functions().items():
        for example in function["examples"]:
            yield pytest.param(name, example["input"], id=f"{name}: {example['title']}")
        for i, arguments in enumerate(_full_sets(name, function)):
            yield pytest.param(name, arguments, id=f"{name}: every argument #{i}")


def test_the_description_is_version_1_0_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.1"
    module = connector_module(check(description, INSTANTLY))
    assert module.generated and module.skipped == []


def test_one_method_per_function_with_its_arguments():
    public = {n for n, _ in inspect.getmembers(InstantlyClient, inspect.isfunction) if not n.startswith("_")}
    assert public == set(_functions())
    for name, function in _functions().items():
        arguments = set(inspect.signature(getattr(InstantlyClient, name)).parameters) - {"self"}
        assert arguments - LEFT_TO_THE_CLIENT.get(name, set()) == set(function["input"]["properties"]), name


@pytest.mark.parametrize("name, arguments", list(_cases()))
def test_the_client_sends_the_request_the_description_says(name, arguments):
    function = _functions()[name]
    jsonschema.Draft202012Validator(function["input"]).validate(arguments)
    assert _sent(name, arguments) == _described(function, arguments)


def test_a_call_without_body_sends_none():
    """Activating, pausing and marking read have no body: none is sent, not an empty JSON."""
    for name in ("activate_campaign", "pause_campaign", "mark_thread_as_read"):
        assert "body" not in _functions()[name]["call"], name


def test_classes_and_the_served_side():
    for name, function in _functions().items():
        expected = next((cls for cls, names in CLASSES.items() if name in names), "read")
        assert function["class"] == expected, name
        assert "act" not in function, name
        assert ("confirm" in function) == (expected == "sensitive"), name
        effect = next((e for e, names in EFFECTS.items() if name in names), None)
        assert function.get("effect") == effect, name
        if expected == "read":
            assert function["call"]["method"] == "GET" or name == "list_leads", name


def test_every_function_names_its_scope_in_its_description_and_its_403():
    for name, function in _functions().items():
        text = " ".join(function["description"].split())
        scope = SCOPES[name]
        if scope is None:
            assert "Scope: unstated by Instantly" in text, name
            continue
        assert scope in text, name
        [refusal] = [r for r in function.get("refusals", []) if r["when"] == 403]
        assert refusal["code"] == "scope_missing" and scope in refusal["message"], name
        if function["class"] != "read":
            assert "read-only key refuses this action" in refusal["message"], name


def test_an_argument_that_names_a_person_is_personal():
    functions = _functions()
    for name, arguments in PERSONAL.items():
        properties = functions[name]["input"]["properties"]
        for argument in arguments:
            schema = properties[argument]
            assert (schema.get("personal") or schema.get("items", {}).get("personal")) is True, (name, argument)
    lead = functions["add_leads"]["input"]["properties"]["leads"]["items"]["properties"]
    assert all(lead[k].get("personal") is True for k in ("email", "first_name", "last_name", "phone"))
    assert lead["custom_variables"]["additionalProperties"]["personal"] is True


def test_no_secret_nor_open_address_reaches_the_agent():
    functions = _functions()
    for name in ("list_webhooks", "create_webhook", "delete_webhook"):
        assert "headers" in functions[name]["output"]["strip"], name
        assert "headers" not in functions[name]["input"]["properties"], name
    for name in ("list_emails", "get_email", "reply_to_email"):
        assert "attachment_json.files.url" in functions[name]["output"]["strip"], name


def test_the_account_is_one_bearer_key_never_the_platform():
    connector = _description()["connector"]
    assert connector["auth"] == {"kind": "bearer", "token": "api_key"}
    assert connector["base_url"] == ic.BASE_URL == "https://api.instantly.ai/api/v2"
    assert connector["modes"] == ["byo_user", "byo_org"]
    assert connector["probe"]["function"] == "get_workspace"
    [rejected] = [e for e in connector["errors"] if e.get("marks_account")]
    assert rejected["status"] == 401 and rejected["marks_account"] == "credential_rejected"
