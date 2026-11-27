"""La description `hubspot` 1.0.1 : celle du client existant (`oto.tools.hubspot`), qui ne gagne que `filter_groups`.

Une fonction par méthode du client qui envoie une seule requête à `api.hubapi.com`, sous son nom, avec ses noms
d'arguments et les clés que le client envoie à HubSpot (chemin, query, corps). Restent hors du fichier, au client écrit
à la main : les compositions (`batch_read_objects`, `create_note`), les requêtes que le format ne sait pas dire (liste
répétée en query, corps qui est un tableau ou l'argument lui-même) et l'argument `filters` de `search_objects`, que le
client emballe dans `filterGroups` : la description déclare à sa place `filter_groups`, la forme native que le client
envoie telle quelle (1.0.1). Les gardes ci-dessous lisent le client lui-même : un endpoint, un argument ou une clé
qu'il ajoute sans la description fait échouer le test ; une écriture servie du côté lecture aussi.
"""
from __future__ import annotations

import copy
import inspect
import re

import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.hubspot.client import HubSpotClient

HUBSPOT = CONNECTORS / "hubspot" / "connector.yaml"

# Les méthodes publiques du client qui ne sont pas une requête unitaire que le format sait dire, et pourquoi.
LEFT_OUT = {
    "batch_read_objects": "several /batch/read calls, one per slice of 100 ids, and missing_ids, a local diff",
    "create_note": "a composition: association type looked up, timestamp set to now, association built",
    "get_lists": "listIds repeated in the query, where the connector joins lists by commas",
    "add_list_memberships": "the body is a bare array of ids",
    "remove_list_memberships": "the body is a bare array of ids",
    "create_property": "the body is the definition argument itself",
    "update_property": "the body is the definition argument itself",
}
# Les arguments d'une méthode que sa fonction ne prend pas, et pourquoi.
ARGUMENTS_LEFT_OUT = {
    "search_objects": {"filters": "the client wraps them in filterGroups: [{filters}], a nesting body does not say; "
                                  "filter_groups, sent as is, is declared instead"},
}
# Ce que le client envoie quand l'appel ne le donne pas, et que la description ne déclare pas en défaut : HubSpot prend
# 10 par défaut, le client 100 ; l'hôte n'envoie rien pour un argument absent.
CLIENT_FILLS = {"limit": 100}
# Ce que fait chaque fonction : une écriture mal classée est un défaut grave.
CLASSES = {
    "read": {
        "list_objects", "get_object", "search_objects", "list_associations", "list_owners", "list_properties",
        "get_property", "list_property_groups", "get_list", "get_list_by_name", "search_lists",
        "get_list_memberships", "get_record_memberships",
    },
    "write": {
        "create_object", "update_object", "create_list", "update_list_name", "restore_list",
        "add_and_remove_list_memberships", "add_memberships_from_list",
    },
    "sensitive": {
        "delete_object", "delete_property", "update_list_filters", "delete_list", "delete_all_list_memberships",
    },
}
# Une valeur par argument, pour un appel qui les donne tous.
VALUES = {
    "object_type": "contacts", "object_id": "1001", "properties": ["firstname", "email"], "limit": 20,
    "after": "cursor-0", "associations": ["deals", "companies"], "query": "dupont",
    "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}], "to_object_type": "deals",
    "archived": True, "property_name": "lead_source_detail", "name": "Leads", "object_type_id": "0-1",
    "processing_type": "DYNAMIC", "filter_branch": {"filterBranchType": "OR", "filterBranches": []},
    "custom_properties": {"team": "sales"}, "list_id": "42", "include_filters": True, "list_name": "Leads 2026",
    "processing_types": ["MANUAL"], "count": 20, "offset": 40, "additional_properties": ["hs_list_size"],
    "enroll_objects_in_workflows": True, "record_ids_to_add": ["1"], "record_ids_to_remove": ["2"],
    "source_list_id": "41", "record_id": "1001",
    "filter_groups": [
        {"filters": [{"propertyName": "lifecyclestage", "operator": "EQ", "value": "customer"}]},
        {"filters": [{"propertyName": "amount", "operator": "BETWEEN", "value": 10, "highValue": 20},
                     {"propertyName": "hs_lead_status", "operator": "IN", "values": ["NEW"]},
                     {"propertyName": "email", "operator": "HAS_PROPERTY"}]},
    ],
}
COST = {"per": "request"}
WRITING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(HUBSPOT.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(HubSpotClient, inspect.isfunction) if not n.startswith("_")}


def _client_endpoints() -> dict[str, tuple[str, str]]:
    """Chaque méthode publique qui appelle `_request` une seule fois, sa méthode HTTP et son chemin, tel qu'écrit."""
    endpoints = {}
    for name in _public_methods():
        found = re.findall(r'self\._request\(\s*"([A-Z]+)",\s*f?"([^"]+)"', inspect.getsource(getattr(HubSpotClient, name)))
        if len(found) == 1:
            endpoints[name] = found[0]
    return endpoints


class _Answer:
    status_code = 200
    headers: dict = {}
    content = b"{}"

    def json(self):
        return {}


def _sent_by_the_client(name: str, arguments: dict) -> tuple[str, str, dict, dict]:
    """La méthode, l'adresse, la query et le corps JSON que la méthode du client envoie, sans réseau."""
    sent = []

    def request(method, url, **kwargs):
        sent.append((method, url, kwargs.get("params") or {}, kwargs.get("json") or {}))
        return _Answer()

    client = HubSpotClient(api_key="k")
    client.session.request = request
    getattr(client, name)(**arguments)
    assert len(sent) == 1, name
    return sent[0]


def _in_query(value):
    """Une valeur en query comme l'écrit le client : liste jointe par des virgules (`query_arrays: comma`), booléen en
    minuscules."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return ",".join(value)
    return value


def _described(function: dict, arguments: dict) -> tuple[str, str, dict, dict]:
    call = function["call"]
    properties = function["input"]["properties"]
    given = {**{a: p["default"] for a, p in properties.items() if "default" in p},
             **{a: v for a, v in CLIENT_FILLS.items() if a in properties}, **arguments}
    path = call["path"].format(**{a: given[a] for a in re.findall(r"{(\w+)}", call["path"])})
    query = {api: _in_query(given[a]) for api, a in call.get("query", {}).items() if a in given}
    body = {api: given[a] for api, a in call.get("body", {}).items() if a in given}
    return call["method"], HubSpotClient.BASE_URL + path, query, body


def _client_view(sent: tuple[str, str, dict, dict]) -> tuple[str, str, dict, dict]:
    method, url, params, body = sent
    return method, url, {k: (_in_query(v) if isinstance(v, (bool, list)) else v) for k, v in params.items()}, body


def test_the_description_is_version_1_0_3_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.3"
    check(description, HUBSPOT)


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
    assert connector["base_url"] == HubSpotClient.BASE_URL
    assert connector["auth"] == {"kind": "bearer", "token": "access_token"}
    assert connector["credential"] == [{"name": "access_token", "label": "Private app access token", "secret": True}]
    assert HubSpotClient(api_key="k").session.headers["Authorization"] == "Bearer k"
    assert connector.get("timeout_s", 30) == 30, "the client waits 30 s per request"
    assert connector["query_arrays"] == "comma"
    assert "platform" not in connector["modes"], "no platform key: each portal brings its own token"
    assert "probe" not in connector, "no account read in the client, and list_owners needs a scope of its own"


def test_each_function_takes_the_arguments_of_its_client_method():
    for name, function in _functions().items():
        parameters = set(inspect.signature(getattr(HubSpotClient, name)).parameters) - {"self"}
        properties = set(function["input"]["properties"])
        assert properties <= parameters, name
        assert parameters - properties == set(ARGUMENTS_LEFT_OUT.get(name, {})), name


def test_each_argument_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        arguments = {argument: VALUES[argument] for argument in function["input"]["properties"]}
        assert _client_view(_sent_by_the_client(name, arguments)) == _described(function, arguments), name


def test_each_example_sends_what_the_client_sends_for_it():
    """Le client pose `limit` 100 et les booléens à false quand l'appel ne les donne pas : false est le défaut de
    HubSpot, déclaré au schéma ; 100 ne l'est pas (CLIENT_FILLS)."""
    for name, function in _functions().items():
        for example in function["examples"]:
            sent = _client_view(_sent_by_the_client(name, example["input"]))
            assert sent == _described(function, example["input"]), (name, example["title"])


def test_reads_and_writes_are_classified_and_every_write_is_served_on_the_act_side():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        assert "act" not in function, name
        writes = function["call"]["method"] in WRITING_METHODS and not function["call"]["path"].endswith("/search")
        assert writes == (function["class"] != "read"), name
        assert ("confirm" in function) == (function["class"] == "sensitive"), name


def test_each_call_costs_one_request_and_the_cursor_reads_paging_next_after():
    for name, function in _functions().items():
        assert function["cost"] == COST, name
        pagination = function.get("pagination")
        if pagination and name != "search_lists":
            assert pagination == {"kind": "cursor", "request_param": "after", "next": "paging.next.after",
                                  "max_pages": 10}, name
            assert function["output"] == {"items": "results"}, name


def test_the_service_signals_a_refused_token_and_a_spent_daily_quota():
    """Un 401 dit le jeton refusé ; un 429 sous `policyName` DAILY ou MONTHLY, le quota journalier du portail épuisé.
    Un scope manquant (403 `MISSING_SCOPES`) ne marque rien : une case à cocher, pas un jeton refusé. Un 5xx n'est pas
    rejouable : le client ne le rejoue pas, une écriture a pu passer."""
    errors = _description()["connector"]["errors"]
    marked = {e["code"]: (e["status"], e.get("body"), e["marks_account"]) for e in errors if "marks_account" in e}
    assert marked == {
        "token_refused": (401, None, "credential_rejected"),
        "daily_limit_reached": (429, {"path": "policyName", "equals": ["DAILY", "MONTHLY"]}, "quota_exhausted"),
    }
    assert {e["code"] for e in errors if e["retryable"]} == {"rate_limited"}
    scopes = next(e for e in errors if e["code"] == "missing_scopes")
    assert scopes["body"] == {"path": "category", "equals": ["MISSING_SCOPES"]} and "marks_account" not in scopes


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), HUBSPOT)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "hubspot.{name}",' in module.source, name
    for name in LEFT_OUT:
        assert f'"hubspot.{name}"' not in module.source, name
    table = python.declared([checked])["hubspot"]
    assert set(table) == set(_functions())
    assert all(entry["cost"] == COST for entry in table.values())
    assert DECLARED.get("hubspot") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"


def test_search_filters_through_filter_groups_and_every_operator_of_the_reference():
    """Les opérateurs du guide de recherche CRM de HubSpot (relu le 2026-10-21), chacun avec les seules clés qu'il prend."""
    search = _functions()["search_objects"]
    assert search["call"]["body"]["filterGroups"] == "filter_groups"
    branches = search["input"]["properties"]["filter_groups"]["items"]["properties"]["filters"]["items"]["oneOf"]
    keys = {}
    for branch in branches:
        operator = branch["properties"]["operator"]
        for name in operator.get("enum", [operator.get("const")]):
            keys[name] = set(branch["required"]) - {"propertyName", "operator"}
            assert set(branch["properties"]) == set(branch["required"]) and not branch["additionalProperties"], name
    assert keys == {
        **{o: {"value"} for o in ("EQ", "NEQ", "LT", "LTE", "GT", "GTE", "CONTAINS_TOKEN", "NOT_CONTAINS_TOKEN")},
        "BETWEEN": {"value", "highValue"}, "IN": {"values"}, "NOT_IN": {"values"},
        "HAS_PROPERTY": set(), "NOT_HAS_PROPERTY": set(),
    }
