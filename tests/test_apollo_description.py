"""La description `apollo` 1.0.0 : celle du client existant (`oto.tools.apollo`), sans rien y changer.

Une fonction par méthode du client qui envoie une seule requête à `api.apollo.io/api/v1`, sous son nom, avec ses noms
d'arguments et les clés que le client envoie à Apollo (chemin, query, corps). Restent hors du fichier, au client écrit
à la main : la relève de la révélation de téléphone (`poll_webhook_result`, un 404 lu comme « pas encore »),
l'inscription dans une séquence (l'identifiant de la séquence dans le chemin et dans la query), l'export de
conversations (une adresse de téléchargement signée), et les arguments que le client remet en forme (emballés dans une
liste, fusionnés, emboîtés) ou qui posteraient des numéros à une adresse choisie par l'appelant (`webhook_url`). Les
gardes ci-dessous lisent le client lui-même : un endpoint, un argument ou une clé qu'il ajoute sans la description fait
échouer le test ; une écriture servie du côté lecture aussi.
"""
from __future__ import annotations

import copy
import inspect
import re
from unittest import mock

import jsonschema
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.apollo import client as apollo_client
from oto.tools.apollo.client import ApolloClient

APOLLO = CONNECTORS / "apollo" / "connector.yaml"

# Les méthodes publiques du client qui ne sont pas une requête unitaire que le format sait dire, et pourquoi.
LEFT_OUT = {
    "poll_webhook_result": "a 404 result_pending read as not ready, and a done/result envelope the client builds",
    "add_contacts_to_sequence": "the sequence id goes both in the path and in the query (emailer_campaign_id)",
    "export_conversations": "the export ends as a signed download address, which never goes to the agent",
    "get_conversations_export": "its answer is a signed download address, which never goes to the agent",
}
# Les arguments d'une méthode que sa fonction ne prend pas, et pourquoi.
ARGUMENTS_LEFT_OUT = {
    "search_organizations": {
        "domain": "wrapped by the client in a one-element list (q_organization_domains_list)",
        "country": "merged by the client into the locations list",
        "revenue_min": "nested by the client into revenue_range",
        "revenue_max": "nested by the client into revenue_range",
    },
    "match_person": {
        "reveal_phone_number": "the phone reveal posts the numbers later to webhook_url, a receiver of the host's",
        "webhook_url": "an address chosen by the caller, where Apollo posts the numbers",
    },
    "bulk_match_people": {
        "reveal_phone_number": "the phone reveal posts the numbers later to webhook_url, a receiver of the host's",
        "webhook_url": "an address chosen by the caller, where Apollo posts the numbers",
    },
    "create_sequence": {
        "active": "sent as the constant false: activating is activate_sequence, which asks for a confirmation",
        "extra": "free fields passed as is, which a strict input does not say",
    },
    "create_custom_field": {"max_length": "nested by the client into meta"},
}
# Les méthodes dont le corps est fait des arguments libres (`**fields`) : les champs que leur fonction prend, et ceux
# qu'elle laisse.
KEYWORD_FIELDS = {
    "update_sequence": {"name", "emailer_schedule_id", "label_names", "max_emails_per_day", "cc_emails", "bcc_emails",
                        "emailer_steps"},
    "update_contact": set(ApolloClient.UPDATABLE_CONTACT_FIELDS),
}
KEYWORD_FIELDS_LEFT_OUT = {
    "update_sequence": {"active": "activating and deactivating have their own functions",
                        "sharing_permission": "values the client does not document"},
}
# Ce que le client envoie quand l'appel ne le donne pas, et que la description ne déclare pas en défaut : l'hôte
# n'envoie rien pour un argument absent, et Apollo applique alors le sien.
CLIENT_FILLS = {
    "search_organizations": {"per_page": 10, "page": 1},
    "search_people": {"per_page": 25, "page": 1},
    "search_sequences": {"per_page": 25, "page": 1},
    "get_contact_sequence_activity": {"per_page": 50},
    "search_emails": {"per_page": 25, "page": 1},
    "search_conversations": {"per_page": 25, "page": 1},
    "search_contacts": {"per_page": 25, "page": 1},
}
# Ce que fait chaque fonction : une écriture mal classée est un défaut grave.
CLASSES = {
    "read": {
        "search_organizations", "search_people", "get_job_postings", "list_email_accounts", "list_email_schedules",
        "search_sequences", "get_contact_sequence_activity", "check_email_send_status", "search_emails",
        "get_email_content", "get_email_stats", "search_conversations", "get_conversation",
        "list_typed_custom_fields", "search_contacts", "get_contact",
    },
    "write": {
        "enrich_organization", "bulk_enrich_organizations", "match_person", "bulk_match_people", "create_sequence",
        "deactivate_sequence", "update_sequence_contact_status", "create_email_draft", "create_custom_field",
        "update_contact",
    },
    "sensitive": {"update_sequence", "activate_sequence", "archive_sequence", "send_email_now"},
}
# Les lectures servies en POST (une recherche, une lecture dont l'argument part en corps) ; les enrichissements, qui
# dépensent des crédits sur une personne ou une entreprise, sont des actions quelle que soit leur méthode.
POST_READS = {
    "search_organizations", "search_people", "search_sequences", "get_contact_sequence_activity",
    "check_email_send_status", "get_email_content", "search_conversations", "search_contacts",
}
SPENDS_CREDITS_ON_ENRICHMENT = {"enrich_organization", "bulk_enrich_organizations", "match_person", "bulk_match_people"}
CREDITS = {"per": "request", "unit": "credits"}
COSTS = {
    "search_organizations": CREDITS,
    "enrich_organization": CREDITS,
    "bulk_enrich_organizations": {"per": "item", "items": "domains", "unit": "credits"},
    "match_person": CREDITS,
    "bulk_match_people": {"per": "item", "items": "details", "unit": "credits", "settle": {"path": "credits_consumed"}},
    "get_conversation": CREDITS,
}
REQUEST = {"per": "request"}
# Une valeur par argument, pour un appel qui les donne tous.
VALUES = {
    "name": "Marie Martin", "employee_ranges": ["1,10", "11,50"], "locations": ["France"], "keywords": ["saas"],
    "technologies": ["salesforce"], "org_ids": ["5f0c"], "per_page": 20, "page": 2, "domain": "example.test",
    "domains": ["example.test", "example.net"], "org_id": "5f0c", "titles": ["sales"], "seniorities": ["director"],
    "person_locations": ["France"], "organization_locations": ["Paris, France"], "person_id": "6a1b",
    "linkedin_url": "https://www.example.test/in/marie", "email": "marie@example.test", "first_name": "Marie",
    "last_name": "Martin", "org_name": "Example", "reveal_personal_emails": True,
    "details": [{"id": "6a1b"}, {"email": "paul@example.test"}], "emailer_schedule_id": "6b0c",
    "label_names": ["Q4"], "folder_id": "f1", "max_emails_per_day": 40, "emailer_steps": [{"type": "auto_email"}],
    "sequence_id": "6c0c", "cc_emails": ["cc@example.test"], "bcc_emails": ["bcc@example.test"],
    "emailer_campaign_ids": ["6c0c"], "contact_ids": ["6d0c", "6d0d"], "mode": "stop", "contact_id": "6d0c",
    "subject": "Hello", "body_html": "<p>Hello</p>",
    "recipients": [{"email": "marie@example.test", "recipient_type_cd": "to"}],
    "in_response_to_emailer_message_id": "6e0b", "emailer_template_id": "t1", "attachment_ids": ["a1"],
    "enable_tracking": False, "outreach_task_id": "k1", "message_id": "6e0c", "surface": "emails",
    "stats": ["opened"], "reply_classes": ["willing_to_meet"], "sequence_ids": ["6c0c"],
    "exclude_sequence_ids": ["6c0d"], "date_range_mode": "completed_at", "date_min": "2026-10-01",
    "date_max": "2026-10-31", "ids": ["6e0c", "6e0d"], "body_format": "html", "conversation_type": "phone_call",
    "account_id": "ac1", "tag_ids": ["tg1"], "tracker_ids": ["tr1"], "organization_ids": ["5f0c"],
    "date_range": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-31T00:00:00Z"}, "conversation_id": "6f0c",
    "label": "Hook", "modality": "account", "field_type": "textarea", "q_keywords": "martin",
    "contact_stage_ids": ["st1"], "contact_label_ids": ["lb1"], "sort_by_field": "contact_created_at",
    "sort_ascending": True, "organization_name": "Example", "title": "CEO", "website_url": "https://example.test",
    "contact_stage_id": "st1", "present_raw_address": "1 rue de l'Exemple, Paris", "direct_phone": "+33100000000",
    "corporate_phone": "+33100000001", "mobile_phone": "+33600000000", "home_phone": "+33100000002",
    "other_phone": "+33100000003", "typed_custom_fields": {"6a0c": "2026-10-21"},
}
# Un argument du même nom qui n'a pas le même type d'une fonction à l'autre.
VALUES_OF = {"search_emails": {"keywords": "martin"}}
WRITING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(APOLLO.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(ApolloClient, inspect.isfunction) if not n.startswith("_")}


def _client_endpoints() -> dict[str, tuple[str, str]]:
    """Chaque méthode publique qui appelle `_request` une seule fois, sa méthode HTTP et son chemin, tel qu'écrit."""
    endpoints = {}
    for name in _public_methods():
        found = re.findall(r'self\._request\(\s*"([A-Z]+)",\s*f?"([^"]+)"', inspect.getsource(getattr(ApolloClient, name)))
        if len(found) == 1:
            endpoints[name] = (found[0][0], "/" + found[0][1])
    return endpoints


class _Answer:
    status_code = 200
    ok = True
    headers: dict = {}
    text = "{}"

    def json(self):
        return {}


def _sent_by_the_client(name: str, arguments: dict) -> tuple:
    """La méthode, l'adresse, la query, le corps JSON et les en-têtes que la méthode du client envoie, sans réseau."""
    sent = []

    def request(method, url, **kwargs):
        sent.append((method, url, kwargs.get("params") or {}, kwargs.get("json"), kwargs.get("headers")))
        return _Answer()

    with mock.patch.object(apollo_client.requests, "request", request):
        getattr(ApolloClient(api_key="k"), name)(**arguments)
    assert len(sent) == 1, name
    return sent[0]


def _in_query(value):
    """Une valeur en query comme l'écrit l'hôte : booléen en minuscules, nombre en texte."""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _described(name: str, function: dict, arguments: dict) -> tuple:
    """La requête que dit la description : listes en query avec crochets (`query_arrays: brackets`)."""
    call = function["call"]
    properties = function["input"]["properties"]
    given = {**{a: p["default"] for a, p in properties.items() if "default" in p},
             **CLIENT_FILLS.get(name, {}), **arguments}
    path = call["path"].format(**{a: given[a] for a in re.findall(r"{(\w+)}", call["path"])})
    query = {}
    for api, argument in call.get("query", {}).items():
        if argument in given:
            value = given[argument]
            if isinstance(value, list):
                query[f"{api}[]"] = [_in_query(v) for v in value]
            else:
                query[api] = _in_query(value)
    has_body = "body" in call or "body" in call.get("constants", {})
    body = None
    if has_body:
        body = {**call.get("constants", {}).get("body", {}),
                **{api: given[a] for api, a in call.get("body", {}).items() if a in given}}
    return call["method"], ApolloClient.BASE_URL + path, query, body


def _client_view(sent: tuple) -> tuple:
    method, url, params, body, _headers = sent
    query = {k: ([_in_query(x) for x in v] if isinstance(v, list) else _in_query(v)) for k, v in params.items()}
    return method, url, query, body


def test_the_description_is_version_1_1_2_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.1.2"
    check(description, APOLLO)


def test_each_single_request_method_of_the_client_is_a_function_of_the_same_name_method_and_path():
    functions = _functions()
    endpoints = _client_endpoints()
    assert {n: (f["call"]["method"], f["call"]["path"]) for n, f in functions.items()} == {
        n: endpoints[n] for n in functions}
    assert set(endpoints) - set(functions) <= set(LEFT_OUT)


def test_what_the_description_leaves_out_is_named_with_its_reason():
    assert _public_methods() - set(_functions()) == set(LEFT_OUT)


def test_the_connector_says_what_the_client_does():
    connector = _description()["connector"]
    assert connector["base_url"] == ApolloClient.BASE_URL
    assert connector["auth"] == {"kind": "api_key", "in": "header", "name": "X-Api-Key", "key": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "API key", "secret": True}]
    *_, headers = _sent_by_the_client("list_email_accounts", {})
    assert headers["X-Api-Key"] == "k", "the key goes in the X-Api-Key header"
    assert connector["timeout_s"] == apollo_client._HTTP_TIMEOUT[1], "the client waits 60 s for an answer"
    assert connector["query_arrays"] == "brackets"
    assert connector["rate_limit"] == {"requests": 1, "seconds": 1}, "the client spaces two requests by one second"
    assert "platform" not in connector["modes"], "sequences, emails and contacts act on the account behind the key"
    assert "probe" not in connector, "no account read in the client; the reads without argument need a master key"


def test_each_function_takes_the_arguments_of_its_client_method():
    for name, function in _functions().items():
        signature = inspect.signature(getattr(ApolloClient, name)).parameters
        named = {p for p, v in signature.items() if p != "self" and v.kind is not inspect.Parameter.VAR_KEYWORD}
        free = {p for p, v in signature.items() if v.kind is inspect.Parameter.VAR_KEYWORD}
        properties = set(function["input"]["properties"])
        left_out = set(ARGUMENTS_LEFT_OUT.get(name, {}))
        assert named - properties == left_out - free, name
        assert properties - named == KEYWORD_FIELDS.get(name, set()), name
        if name in KEYWORD_FIELDS:
            assert free, name
            assert not KEYWORD_FIELDS[name] & set(KEYWORD_FIELDS_LEFT_OUT.get(name, {})), name


def test_the_values_the_client_fills_are_its_own_defaults_and_never_declared_defaults():
    for name, fills in CLIENT_FILLS.items():
        signature = inspect.signature(getattr(ApolloClient, name)).parameters
        assert {a: signature[a].default for a in fills} == fills, name
        properties = _functions()[name]["input"]["properties"]
        assert all("default" not in properties[a] for a in fills), name


def test_each_argument_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        values = {**VALUES, **VALUES_OF.get(name, {})}
        arguments = {argument: values[argument] for argument in function["input"]["properties"]}
        jsonschema.validate(arguments, function["input"])
        assert _client_view(_sent_by_the_client(name, arguments)) == _described(name, function, arguments), name


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            sent = _client_view(_sent_by_the_client(name, example["input"]))
            assert sent == _described(name, function, example["input"]), (name, example["title"])


def test_the_input_refuses_what_the_client_refuses_before_sending():
    """Le client refuse un identifiant trop faible (un prénom et une entreprise), qu'Apollo facturerait pour une coquille
    vide ; la description le refuse par son schéma, avant l'appel."""
    functions = _functions()
    weak = {"first_name": "Marie", "domain": "example.test"}
    for name, arguments in (("match_person", weak), ("bulk_match_people", {"details": [weak]})):
        assert not jsonschema.Draft202012Validator(functions[name]["input"]).is_valid(arguments), name
    full = {"name": "Marie Martin", "domain": "example.test"}
    assert jsonschema.Draft202012Validator(functions["match_person"]["input"]).is_valid(full)
    assert not jsonschema.Draft202012Validator(functions["match_person"]["input"]).is_valid({"name": "Marie"})
    assert not jsonschema.Draft202012Validator(functions["create_email_draft"]["input"]).is_valid({"subject": "Hi"})
    assert not jsonschema.Draft202012Validator(functions["update_contact"]["input"]).is_valid({"contact_id": "6d0c"})
    assert not jsonschema.Draft202012Validator(functions["search_contacts"]["input"]).is_valid({"sort_ascending": True})


def test_reads_and_writes_are_classified_and_every_write_is_served_on_the_act_side():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        method = function["call"]["method"]
        acts = (method in WRITING_METHODS and name not in POST_READS) or name in SPENDS_CREDITS_ON_ENRICHMENT
        assert acts == (function["class"] != "read"), name
        assert ("confirm" in function) == (function["class"] == "sensitive"), name
        assert function.get("act", function["class"] != "read") == (name != "create_email_draft" and acts), name


def test_each_call_declares_its_cost_and_nothing_is_paginated():
    for name, function in _functions().items():
        assert function["cost"] == COSTS.get(name, REQUEST), name
        assert "pagination" not in function, name


def test_the_service_signals_a_refused_key_and_an_empty_account():
    """Un 401 dit la clé refusée, un 402 le compte sans crédit, comme un 422 dont le message le dit, là où le client le
    cherche (`_upstream_message` : `error_message`, `error`, puis `message`), par un texte que son motif
    (`_CREDITS_EXHAUSTED`) reconnaît partout où il paraît, jamais plus large : une marque à tort bloquerait un compte
    qui a encore des crédits. « ran out of … credits », que le motif reconnaît aussi, ne se dit pas par un texte fixe :
    il reste au 422 générique, sans marque, comme tout autre 422, après eux. Un 403 (clé sans le droit master, offre
    sans l'endpoint) ne marque rien. Un 5xx n'est pas rejouable : un envoi a pu partir."""
    errors = _description()["connector"]["errors"]
    marked = [(e["code"], e["status"], e.get("body"), e["marks_account"]) for e in errors if "marks_account" in e]
    texts = ["insufficient credits"]
    assert marked == [
        ("key_refused", 401, None, "credential_rejected"),
        ("credits_exhausted", 402, None, "quota_exhausted"),
        *[("credits_exhausted", 422, {"path": path, "contains": texts}, "quota_exhausted")
          for path in ("error_message", "error", "message")],
    ]
    plain = [i for i, e in enumerate(errors) if e["status"] == 422 and "body" not in e]
    assert plain == [max(i for i, e in enumerate(errors) if e["status"] == 422)]
    source = inspect.getsource(ApolloClient._upstream_message)
    assert source.index('"error_message"') < source.index('"error"') < source.index('"message"')
    for text in texts:
        # Le texte seul est reconnu par le motif : tout message qui le contient l'est aussi, rien de plus large.
        assert apollo_client._CREDITS_EXHAUSTED.fullmatch(text), text
    assert apollo_client._CREDITS_EXHAUSTED.search("You have insufficient credits")
    assert any(t in "You have insufficient credits".lower() for t in texts)
    # La forme que le motif reconnaît sans texte fixe reste au 422 générique.
    missing = "You ran out of mobile number credits"
    assert apollo_client._CREDITS_EXHAUSTED.search(missing) and not any(t in missing.lower() for t in texts)
    assert {e["code"] for e in errors if e["retryable"]} == {"rate_limited"}


def test_no_signed_recording_address_reaches_the_agent():
    assert _functions()["get_conversation"]["output"]["strip"] == ["video_recording", "audio_recording"]


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), APOLLO)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "apollo.{name}",' in module.source, name
    for name in LEFT_OUT:
        assert f'"apollo.{name}"' not in module.source, name
    table = python.declared([checked])["apollo"]
    assert set(table) == set(_functions())
    assert DECLARED.get("apollo") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
