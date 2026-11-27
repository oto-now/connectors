"""La description `folk` 1.0.0 : celle du client existant (`oto.tools.folk`), sans rien y changer.

Une fonction par méthode du client, sous son nom, avec ses noms d'arguments et les clés que le client envoie à folk
(chemin, query, corps). Trois écarts nommés, gardés ici :
- la page : le client suit lui-même `data.pagination.nextLink` (une adresse complète) jusqu'à la dernière page ;
  chaque fonction de liste lit UNE page, la requête du client pour cette page, et prend `cursor`, que le client n'a pas ;
- la forme de folk : cinq méthodes prennent l'identifiant nu d'une personne ou d'une entreprise (`entity_id`) et
  l'envoient en objet `entity: {id}` ; `create_deal` emballe de même `people_ids` et `company_ids` ; leurs fonctions
  prennent la forme de folk elle-même, pour la même requête ;
- les champs libres (`**fields`, `**kwargs`, `**filters`) : la fonction les prend en arguments nommés en snake_case,
  chacun envoyé sous le nom de folk, que le client reçoit tel quel en mot-clé.
Restent hors du fichier les trois aides locales qui bâtissent un enregistrement. Les gardes ci-dessous lisent le client
lui-même : un endpoint, un argument ou une clé qu'il ajoute sans la description fait échouer le test ; une écriture
servie du côté lecture aussi.
"""
from __future__ import annotations

import copy
import inspect
import re
from unittest import mock
from urllib.parse import unquote, urlencode

import jsonschema
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.folk import client as folk_client
from oto.tools.folk.client import FolkClient

FOLK = CONNECTORS / "folk" / "connector.yaml"

# Les méthodes publiques du client qui ne sont pas une requête, et pourquoi.
LEFT_OUT = {
    "person_body": "local: builds a person record, sends nothing",
    "company_body": "local: builds a company record, sends nothing",
    "deal_body": "local: builds a deal record, sends nothing",
}
# Les arguments d'une méthode que sa fonction ne prend pas, et pourquoi.
ARGUMENTS_LEFT_OUT = {
    "list_notes": {"entity_id": "folk ignores that filter: the client filters after reading every page"},
    "list_reminders": {"entity_id": "folk ignores that filter: the client filters after reading every page"},
    "list_past_interactions": {"max_items": "local: the client stops walking pages once it has that many"},
    "list_upcoming_interactions": {"max_items": "local: the client stops walking pages once it has that many"},
    "list_tasks": {"filters": "a dict whose keys name the field and the operator of each query parameter",
                   "combinator": "goes with filters"},
    "create_person": {"company_name": "wrapped by the client into companies [{name}]; companies is taken as is",
                      "company_id": "wrapped by the client into companies [{id}]; companies is taken as is",
                      "group_ids": "wrapped by the client into groups [{id}]; groups is taken as is"},
}
# La forme de folk à la place de l'argument du client : argument de la fonction -> (argument du client, conversion).
_ID = lambda value: value["id"]  # noqa: E731
_IDS = lambda value: [v["id"] for v in value]  # noqa: E731
FOLK_FORM = {
    "create_note": {"entity": ("entity_id", _ID)},
    "create_interaction": {"entity": ("entity_id", _ID)},
    "update_interaction": {"entity": ("entity_id", _ID)},
    "create_task": {"entity": ("entity_id", _ID)},
    "create_reminder": {"entity": ("entity_id", _ID)},
    "create_deal": {"people": ("people_ids", _IDS), "companies": ("company_ids", _IDS)},
}
# L'argument de la page, que le client n'a pas : il suit lui-même `nextLink`.
PAGE = "cursor"
# Les listes : le client y pose `limit` 100 à chaque page, sauf les deux listes d'interactions, qui n'en prennent pas.
LISTS = {
    "list_users", "list_groups", "list_group_members", "get_group_custom_fields", "list_people", "list_companies",
    "list_deals", "list_notes", "list_tasks", "list_reminders", "list_webhooks",
}
LISTS_WITHOUT_LIMIT = {"list_past_interactions", "list_upcoming_interactions"}
# Ce que le client pose et que folk exige : exigé par le schéma, l'agent le donne.
REQUIRED_WHERE_THE_CLIENT_FILLS = {
    "create_note": {"visibility"},
    "create_reminder": {"visibility"},
    "create_interaction": {"date_time"},
    "mark_task_done": {"completed_at"},
    "get_group_custom_fields": {"entity_type"},
    **{name: {"object_type"} for name in (
        "list_deals", "get_deal", "create_deal", "update_deal", "delete_deal", "create_deals_batch",
        "update_deals_batch")},
}
# Ce que le client écarte quand on le lui donne vide (`if value:`) : le schéma le refuse.
EMPTY_DROPPED = {
    "create_person": {"last_name": "", "emails": [], "phones": [], "job_title": "", "urls": [], "description": ""},
    "create_company": {"emails": [], "industry": ""},
    "create_deal": {"custom_fields": {}},
    "create_task": {"due_time": "", "description": "", "recurrence_frequency": "", "assigned_users": []},
    "create_interaction": {"content": ""},
}
# Les booléens en query, que le client écrit true ou false, comme l'hôte.
QUERY_BOOLEANS = {"list_tasks": "only_assigned_to_me"}
# Ce que fait chaque fonction : une écriture mal classée est un défaut grave.
CLASSES = {
    "read": {
        "get_current_user", "get_user", "list_users", "list_groups", "list_group_members", "get_group_custom_fields",
        "get_group_custom_field", "list_people", "get_person", "list_companies", "get_company", "list_deals",
        "get_deal", "list_notes", "list_past_interactions", "list_upcoming_interactions", "get_interaction",
        "list_tasks", "get_task", "list_reminders", "get_reminder", "list_webhooks", "get_webhook",
    },
    "write": {
        "create_group", "update_group", "add_group_member", "update_group_member", "create_group_custom_field",
        "create_person", "update_person", "create_company", "update_company", "create_deal", "update_deal",
        "create_note", "update_note", "create_interaction", "update_interaction", "create_task", "update_task",
        "mark_task_done", "mark_task_todo", "create_reminder", "update_reminder",
    },
    "sensitive": {
        # Supprime ou retire.
        "remove_group_member", "delete_person", "delete_company", "delete_deal", "delete_note", "delete_interaction",
        "delete_task", "delete_reminder", "delete_webhook",
        # Jusqu'à cent enregistrements d'un coup ; une mise à jour remplace les groupes.
        "create_people_batch", "update_people_batch", "create_companies_batch", "update_companies_batch",
        "create_deals_batch", "update_deals_batch",
        # Une option retirée se vide sur chaque enregistrement qui la porte.
        "update_group_custom_field",
        # folk poste les événements de l'espace à une adresse de l'appelant.
        "create_webhook", "update_webhook",
    },
}
BATCHES = {"create_people_batch", "update_people_batch", "create_companies_batch", "update_companies_batch",
           "create_deals_batch", "update_deals_batch"}
WRITING_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
REQUEST = {"per": "request"}

PER = "per_00000000-0000-4000-8000-000000000001"
GRP = "grp_00000000-0000-4000-8000-000000000001"
COM = "com_00000000-0000-4000-8000-000000000001"
# Une valeur par argument, pour un appel qui les donne tous.
VALUES = {
    "user_id": "usr_00000000-0000-4000-8000-000000000001", "cursor": "eyJvZmZzZXQiOjEwMH0=", "name": "Example",
    "visibility": "private", "group_id": GRP, "role": "reader", "entity_type": "person",
    "custom_field_name": "Deal Status", "type": "call", "options": [{"label": "Lead", "color": "#5738ff"}],
    "config": {"format": "currency", "currency": "EUR", "decimals": 0},
    "add_options": [{"label": "Lost", "color": "#f54e50"}], "remove_options": ["opt_example1"],
    "update_options": [{"id": "opt_example2", "label": "Won", "color": "#20cea9"}],
    "full_name": "Marie Martin", "first_name": "Marie", "last_name": "Martin", "job_title": "Head of Sales",
    "description": "Met at the trade show.", "emails": ["marie@example.test"], "phones": ["+33 1 99 00 00 00"],
    "addresses": ["1 rue de l'Exemple, Paris"], "urls": ["https://www.example.test/in/marie"],
    "groups": [{"id": GRP}], "companies": [{"id": COM}], "person_id": PER, "birthday": "1990-05-17",
    "gender": "Female", "custom_field_values": {GRP: {"Stage": "Lead"}}, "company_id": COM, "industry": "Software",
    "funding_raised": 2000000, "last_funding_date": "2026-01-15", "foundation_year": "2015",
    "employee_range": "11-50", "object_type": "deals", "deal_id": "obj_00000000-0000-4000-8000-000000000001",
    "people": [{"id": PER}], "custom_fields": {"Status": "Qualified"}, "note_id": "nte_example1",
    "entity": {"id": PER}, "content": "Interested in a pilot.", "title": "Intro call",
    "date_time": "2026-10-22T09:30:00.000Z", "entity_id": PER, "interaction_id": "itr_example1",
    "activity_type": "meeting", "only_assigned_to_me": True, "task_id": "tsk_example1", "due_at": "2026-10-29",
    "due_time": "09:30", "recurrence_frequency": "weekly", "assigned_users": [{"email": "me@example.test"}],
    "is_public": False, "completed_at": "2026-10-22T10:00:00.000Z", "reminder_id": "rmd_example1",
    "recurrence_rule": "DTSTART;TZID=Europe/Paris:20261029T093000\nRRULE:FREQ=DAILY;COUNT=1",
    "webhook_id": "wbk_example1", "target_url": "https://hooks.example.test/folk",
    "subscribed_events": [{"eventType": "person.created", "filter": {"groupId": GRP}}], "status": "inactive",
}
_TEXT_FILTERS = {"full_name": "Martin", "first_name": "Marie", "last_name": "Martin", "job_title": "Sales",
                 "description": "fair", "emails": "@example.test", "phones": "+33", "addresses": "Paris",
                 "urls": "example.test", "name": "Example", "industry": "Software", "employee_range": "11-50",
                 "groups": GRP, "companies": COM}
_PERSON_RECORD = {"firstName": "Marie", "lastName": "Martin", "emails": ["marie@example.test"], "groups": [{"id": GRP}]}
_COMPANY_RECORD = {"name": "Example Corp", "industry": "Software"}
_DEAL_RECORD = {"name": "Example Corp renewal", "people": [{"id": PER}], "customFieldValues": {"Status": "Lead"}}
VALUES_OF = {
    "list_people": _TEXT_FILTERS, "list_companies": _TEXT_FILTERS, "list_deals": _TEXT_FILTERS,
    "create_group_custom_field": {"type": "singleSelect", "name": "Stage"},
    "create_people_batch": {"records": [_PERSON_RECORD]},
    "update_people_batch": {"records": [{"id": PER, "jobTitle": "VP Sales"}]},
    "create_companies_batch": {"records": [_COMPANY_RECORD]},
    "update_companies_batch": {"records": [{"id": COM, "industry": "Retail"}]},
    "create_deals_batch": {"records": [_DEAL_RECORD]},
    "update_deals_batch": {"records": [{"id": "obj_example1", "customFieldValues": {"Status": "Won"}}]},
    "update_deal": {"custom_field_values": {"Status": "Won"}},
}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(FOLK.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(FolkClient, inspect.isfunction) if not n.startswith("_")}


def _parameters(name: str) -> dict[str, inspect.Parameter]:
    return {p: v for p, v in inspect.signature(getattr(FolkClient, name)).parameters.items() if p != "self"}


def _named(name: str) -> set[str]:
    return {p for p, v in _parameters(name).items() if v.kind is not inspect.Parameter.VAR_KEYWORD}


def _api_name(function: dict, argument: str) -> str:
    """Le nom côté folk d'un argument : sa clé de corps, ou le champ d'un filtre `filter[<champ>][…]`."""
    call = function["call"]
    for api, a in call.get("body", {}).items():
        if a == argument:
            return api
    for api, a in call.get("query", {}).items():
        if a == argument:
            found = re.match(r"filter\[(\w+)\]", api)
            return found.group(1) if found else api
    raise AssertionError(f"{function['name']}: {argument} has no API name")


def _keyword_arguments(name: str, function: dict) -> set[str]:
    """Les arguments de la fonction que le client reçoit en mot-clé libre (`**fields`, `**kwargs`, `**filters`)."""
    return set(function["input"]["properties"]) - _named(name) - set(FOLK_FORM.get(name, {})) - {PAGE}


def _client_arguments(name: str, function: dict, arguments: dict) -> dict:
    """Les arguments de la méthode du client pour un appel de la fonction (sans le curseur, que le client suit)."""
    keywords = _keyword_arguments(name, function)
    given = {}
    for argument, value in arguments.items():
        if argument == PAGE:
            continue
        if argument in FOLK_FORM.get(name, {}):
            client_argument, convert = FOLK_FORM[name][argument]
            given[client_argument] = convert(value)
        elif argument in keywords:
            given[_api_name(function, argument)] = value
        else:
            given[argument] = value
    return given


class _Stub:
    """Un filtre de champs neutre : la politique locale du client n'est pas celle de la description."""

    def apply(self, data):
        return data


class _Answer:
    status_code = 200
    ok = True
    headers: dict = {}

    def __init__(self, body: dict | None = None):
        self._body = body if body is not None else {"data": {"items": [], "pagination": {}}}
        self.content = b"{}"
        self.text = "{}"

    def json(self):
        return self._body


def _requests_of_the_client(name: str, arguments: dict, answers: list | None = None) -> list[dict]:
    """Ce que la méthode du client envoie, sans réseau : méthode, adresse, query, corps JSON, en-têtes."""
    sent = []
    answers = list(answers or [])

    def request(method, url, **kwargs):
        sent.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {}),
                     "json": kwargs.get("json"), "headers": kwargs.get("headers") or {}})
        return answers.pop(0) if answers else _Answer()

    with mock.patch.object(folk_client.requests, "request", request), \
            mock.patch.object(folk_client.time, "sleep", lambda _s: None):
        getattr(FolkClient(api_key="k", field_filter=_Stub(), min_interval_s=0), name)(**arguments)
    return sent


def _sent_by_the_client(name: str, function: dict, arguments: dict) -> dict:
    """La requête du client pour cette page : la première, ou, avec un curseur, la suivante, quand folk a donné
    `nextLink` (le client en tire le paramètre `cursor`)."""
    given = _client_arguments(name, function, arguments)
    if PAGE not in arguments:
        sent = _requests_of_the_client(name, given)
        assert len(sent) == 1, name
        return sent[0]
    next_link = "https://api.folk.app/v1/next?" + urlencode({"limit": 100, "cursor": arguments[PAGE]})
    sent = _requests_of_the_client(name, given, [_Answer({"data": {"items": [], "pagination": {"nextLink": next_link}}})])
    assert len(sent) == 2, name
    return sent[1]


def _in_query(value) -> str:
    """Une valeur en query comme l'écrit l'hôte : booléen en minuscules, nombre en texte."""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _described(function: dict, arguments: dict) -> tuple:
    """La requête que dit la description : méthode, adresse (chemin non encodé), query, corps JSON."""
    call = function["call"]
    path = call["path"].format(**{a: arguments[a] for a in re.findall(r"{(\w+)}", call["path"])})
    query = {k: str(v) for k, v in call.get("constants", {}).get("query", {}).items()}
    for api, argument in call.get("query", {}).items():
        if argument in arguments:
            query[api] = _in_query(arguments[argument])
    body = None
    if "body" in call:
        body = {api: arguments[a] for api, a in call["body"].items() if a in arguments}
    return call["method"], FolkClient.BASE_URL + path, query, body


def _client_view(sent: dict) -> tuple:
    return sent["method"], unquote(sent["url"]), {k: _in_query(v) for k, v in sent["params"].items()}, sent["json"]


def _values(name: str, function: dict) -> dict:
    values = {**VALUES, **VALUES_OF.get(name, {})}
    return {argument: values[argument] for argument in function["input"]["properties"]}


def test_the_description_is_version_1_0_2_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.2"
    check(description, FOLK)


def test_each_method_of_the_client_is_a_function_or_named_with_its_reason():
    assert _public_methods() - set(_functions()) == set(LEFT_OUT)
    assert not set(LEFT_OUT) & set(_functions())
    assert set(_functions()) <= _public_methods()


def test_the_connector_says_what_the_client_does():
    """Clé en jeton porteur ; délai de 60 s ; rythme publié par folk, que l'espacement du client respecte."""
    connector = _description()["connector"]
    assert connector["base_url"] == FolkClient.BASE_URL
    assert connector["auth"] == {"kind": "bearer", "token": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "API key", "secret": True}]
    sent = _requests_of_the_client("get_current_user", {})[0]
    assert sent["headers"]["Authorization"] == "Bearer k"
    assert "timeout=(10, 60)" in inspect.getsource(FolkClient._request) and connector["timeout_s"] == 60
    rate = connector["rate_limit"]
    assert rate == {"requests": 20, "seconds": 2}, "folk's burst policy, per user; it implies the 600 per minute"
    assert folk_client.DEFAULT_MIN_INTERVAL_S >= rate["seconds"] / rate["requests"], "the client paces under it"
    assert "platform" not in connector["modes"], "people and deals are the workspace's behind the key"
    assert connector["probe"] == {"function": "get_current_user"}
    assert "query_arrays" not in connector, "no list goes in a query"


def test_each_function_takes_the_arguments_of_its_client_method():
    """Un argument nommé du client est un argument de la fonction, sauf ceux qu'`ARGUMENTS_LEFT_OUT` écarte et ceux
    que `FOLK_FORM` remplace ; un argument de plus est soit le curseur d'une liste, soit un champ libre que la
    méthode reçoit en mot-clé (elle a `**`)."""
    for name, function in _functions().items():
        properties = set(function["input"]["properties"])
        forms = FOLK_FORM.get(name, {})
        replaced = {client for client, _ in forms.values()}
        assert _named(name) - properties == set(ARGUMENTS_LEFT_OUT.get(name, {})) | replaced, name
        assert not set(ARGUMENTS_LEFT_OUT.get(name, {})) & replaced, name
        assert (PAGE in properties) == (name in LISTS | LISTS_WITHOUT_LIMIT), name
        keywords = _keyword_arguments(name, function)
        if keywords:
            assert any(v.kind is inspect.Parameter.VAR_KEYWORD for v in _parameters(name).values()), name
            assert all(_api_name(function, k) not in _named(name) for k in keywords), name
    assert set(ARGUMENTS_LEFT_OUT) | set(FOLK_FORM) <= set(_functions())


def test_each_argument_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        arguments = _values(name, function)
        jsonschema.validate(arguments, function["input"])
        assert _client_view(_sent_by_the_client(name, function, arguments)) == _described(function, arguments), name


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent = _sent_by_the_client(name, function, example["input"])
            assert _client_view(sent) == _described(function, example["input"]), (name, example["title"])


def test_a_list_reads_one_page_the_clients_request_for_it():
    """Le client pose `limit` 100 à chaque page (sauf les interactions) et suit `nextLink` : la description en dit la
    constante, et la page suivante est celle que le client demande avec le curseur de `nextLink`."""
    functions = _functions()
    for name in LISTS | LISTS_WITHOUT_LIMIT:
        constants = functions[name]["call"].get("constants", {}).get("query", {})
        assert constants == ({} if name in LISTS_WITHOUT_LIMIT else {"limit": 100}), name
        assert "pagination" not in functions[name], name
        assert functions[name]["call"]["query"]["cursor"] == PAGE, name
        assert "nextLink" in functions[name]["description"], name


def test_what_the_client_fills_and_folk_requires_is_required_and_never_a_declared_default():
    functions = _functions()
    for name, arguments in REQUIRED_WHERE_THE_CLIENT_FILLS.items():
        assert all(_parameters(name)[a].default is not inspect.Parameter.empty for a in arguments), name
        assert arguments <= set(functions[name]["input"]["required"]), name
        assert all("default" not in functions[name]["input"]["properties"][a] for a in arguments), name


def test_what_the_client_drops_when_empty_the_schema_refuses():
    functions = _functions()
    for name, empties in EMPTY_DROPPED.items():
        function = functions[name]
        base = {a: VALUES[a] for a in function["input"]["required"]}
        for argument, empty in empties.items():
            arguments = {**base, argument: empty}
            assert not jsonschema.Draft202012Validator(function["input"]).is_valid(arguments), (name, argument)
            body = _requests_of_the_client(name, _client_arguments(name, function, arguments))[0]["json"]
            api = _api_name(function, argument)
            assert api not in body, (name, argument)


def test_the_folk_form_is_what_the_client_sends_for_its_bare_ids():
    """`entity: {id}`, `people: [{id}]`, `companies: [{id}]` : le corps que le client bâtit de l'identifiant nu."""
    functions = _functions()
    for name, forms in FOLK_FORM.items():
        arguments = _values(name, functions[name])
        body = _sent_by_the_client(name, functions[name], arguments)["json"]
        for argument in forms:
            assert body[_api_name(functions[name], argument)] == arguments[argument], (name, argument)


def test_the_query_booleans_go_true_or_false_as_the_host_writes_them():
    functions = _functions()
    for name, argument in QUERY_BOOLEANS.items():
        assert functions[name]["input"]["properties"][argument]["type"] == "boolean", name
        api = next(k for k, v in functions[name]["call"]["query"].items() if v == argument)
        for value, written in ((True, "true"), (False, "false")):
            sent = _sent_by_the_client(name, functions[name], {argument: value})
            assert sent["params"][api] == written == _in_query(value), (name, value)


def test_the_assignees_are_all_by_id_or_all_by_email_as_the_client_requires():
    functions = _functions()
    mixed = [{"id": "usr_example1"}, {"email": "me@example.test"}]
    for name, argument in (("create_task", "assigned_users"), ("update_task", "assigned_users"),
                           ("update_reminder", "assigned_users")):
        required = {a: VALUES[a] for a in functions[name]["input"]["required"]}
        assert not jsonschema.Draft202012Validator(functions[name]["input"]).is_valid({**required, argument: mixed})
    try:
        _requests_of_the_client("create_task", {"entity_id": PER, "title": "t", "due_at": "2026-10-29",
                                                "assigned_users": mixed})
    except ValueError:
        pass
    else:
        raise AssertionError("the client refuses mixed assignees")


def test_a_batch_takes_at_most_the_records_the_client_takes():
    functions = _functions()
    for name in BATCHES:
        records = functions[name]["input"]["properties"]["records"]
        assert records["maxItems"] == FolkClient.BATCH_MAX_RECORDS and records["minItems"] == 1, name
        assert functions[name]["call"]["body"] == {"records": "records"}, name
        sent = _sent_by_the_client(name, functions[name], _values(name, functions[name]))
        # Le client ajoute une clé d'idempotence à chaque lot, pour son propre rejeu sur 429 ; l'hôte ne rejoue rien.
        assert set(sent["headers"]) == {"Authorization", "Content-Type", "Idempotency-Key"}, name


def test_the_webhook_events_are_the_clients_and_the_signing_secret_never_goes_out():
    functions = _functions()
    for name, argument in (("create_webhook", "subscribed_events"), ("update_webhook", "subscribed_events")):
        events = functions[name]["input"]["properties"][argument]["items"]["properties"]["eventType"]["enum"]
        assert set(events) == folk_client.WEBHOOK_EVENT_TYPES, name
    assert functions["create_webhook"]["output"]["strip"] == ["data.signingSecret"]


def test_reads_and_writes_are_classified_and_every_write_is_served_on_the_act_side():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        method = function["call"]["method"]
        assert (method in WRITING_METHODS) == (function["class"] != "read"), name
        assert ("confirm" in function) == (function["class"] == "sensitive"), name
        if method == "DELETE" or name.endswith("_batch"):
            assert function["class"] == "sensitive", name
        assert function.get("act", function["class"] != "read") == (function["class"] != "read"), name


def test_each_call_costs_one_request_and_nothing_is_paginated():
    for name, function in _functions().items():
        assert function["cost"] == REQUEST, name
        assert "pagination" not in function, name


def test_only_the_401_marks_the_account_as_a_refused_key():
    """folk documente le 401 comme une clé invalide ou absente ; l'hôte la fournit toujours : seule cette ligne marque le
    compte. Seul le 429 se rejoue, comme le client, qui ne rejoue que lui."""
    errors = _description()["connector"]["errors"]
    marking = [e for e in errors if "marks_account" in e]
    assert marking == [{"status": 401, "code": "key_refused", "retryable": False,
                        "marks_account": "credential_rejected", "message": marking[0]["message"]}]
    assert not [e for e in errors if "body" in e]
    assert {e["code"] for e in errors if e["retryable"]} == {"rate_limited"}
    source = inspect.getsource(FolkClient._request)
    assert "resp.status_code == 429" in source and source.count("status_code ==") == 1


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), FOLK)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "folk.{name}",' in module.source, name
    for name in LEFT_OUT:
        assert f'"folk.{name}"' not in module.source, name
    table = python.declared([checked])["folk"]
    assert set(table) == set(_functions())
    assert DECLARED.get("folk") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
