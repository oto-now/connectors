"""La description `attio` 1.3.0 : celle du client existant (`oto.tools.attio`), ses écritures comprises.

Le client range ses méthodes par ressource (`client.notes.list`, `client.attributes.options`…) : chaque fonction est
une méthode, nommée de son verbe et de sa ressource (`FUNCTIONS`), avec les arguments que la méthode nomme ; les clés
envoyées à Attio (chemin, query, corps) sont les siennes. Les fiches de tous les objets partagent une ressource
(`AttioResource`, bâtie pour un objet) : l'objet est l'argument `object`. Le corps des écritures, que le client emballe
dans `{"data": {...}}`, est un corps emboîté : la constante du corps, puis chaque argument donné posé à son chemin
(`_body`, la référence du format) ; il est jugé égal à celui du client pour chaque argument donné ou absent.
Écarts nommés, gardés ici :
- deux fonctions décrivent la même méthode (`BY_VERB`, écart à « une fonction par méthode, sous son nom » décidé par
  le mainteneur) : `records.update` et `entries.update`, dont l'argument `overwrite_multiselect` choisit le verbe (PATCH
  ajoute aux valeurs d'une multisélection, PUT les remplace), sont chacune une fonction par verbe, l'argument fixé par
  la fonction et jamais pris ; le client est appelé avec la valeur qui correspond à la fonction ;
- les méthodes laissées au client (`LEFT_OUT`) : les tâches
  (l'assigné et la fiche liée dans une liste, l'échéance complétée, l'assigné par défaut lu par une seconde requête),
  et `threads.list`, dont le client envoie des noms de paramètres qu'Attio ne lit pas ;
- l'argument `query` de `records.search`, que le client récrit en filtre sur le nom, et les ancres d'un commentaire
  autres que son fil, envoyées sous des noms que la référence d'Attio refuse (`ARGUMENTS_LEFT_OUT`) ;
- les arguments que le client prend en `**kwargs` (`KEYWORDS`) : les valeurs d'une fiche, les champs d'une liste ;
- les valeurs que le client pose quand l'appel ne les donne pas, exigées ici (`REQUIRED_HERE`) ;
- les valeurs que le client pose quand l'appel ne les donne pas (`CLIENT_FILLS`), défauts non déclarés ;
- ce que le schéma refuse et que le client laisse passer (`NARROWER`), et ce que le client refuse lui-même
  (`CLIENT_REFUSES`).
Rien ne revient à l'agent d'une adresse ouverte sans connexion : l'adresse vidéo d'un enregistrement d'appel et celle
de l'avatar d'un membre sont retirées par la description et par le client.
Les gardes ci-dessous lisent le client lui-même : une méthode, un argument ou une clé qu'il ajoute sans la description
fait échouer le test ; une écriture servie du côté lecture aussi.
"""
from __future__ import annotations

import copy
import inspect
import json
import re
from unittest import mock

import jsonschema
import pytest
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from fabrique.descriptions import body_entries
from oto.connectors.declared import DECLARED
from oto.tools.attio import client as attio_client
from oto.tools.attio.client import AttioClient, AttioResource

ATTIO = CONNECTORS / "attio" / "connector.yaml"

# La ressource (attribut du client, `records` pour `AttioResource`) et la méthode de chaque fonction.
FUNCTIONS = {
    "list_records": ("records", "list"), "get_record": ("records", "get"),
    "search_records": ("records", "search"), "delete_record": ("records", "delete"),
    "list_notes": ("notes", "list"), "get_note": ("notes", "get"), "delete_note": ("notes", "delete"),
    "list_tasks": ("tasks", "list"), "get_task": ("tasks", "get"), "delete_task": ("tasks", "delete"),
    "list_lists": ("lists", "list"), "get_list": ("lists", "get"), "list_list_views": ("lists", "views"),
    "query_entries": ("entries", "query"), "get_entry": ("entries", "get"), "delete_entry": ("entries", "delete"),
    "list_workspace_members": ("workspace_members", "list"), "get_workspace_member": ("workspace_members", "get"),
    "get_comment": ("comments", "get"), "delete_comment": ("comments", "delete"),
    "get_thread": ("threads", "get"),
    "list_meetings": ("meetings", "list"), "get_meeting": ("meetings", "get"),
    "list_call_recordings": ("call_recordings", "list"), "get_call_recording": ("call_recordings", "get"),
    "get_call_recording_transcript": ("call_recordings", "transcript"),
    "list_objects": ("objects", "list"), "get_object": ("objects", "get"), "list_object_views": ("objects", "views"),
    "list_attributes": ("attributes", "list"), "get_attribute": ("attributes", "get"),
    "list_attribute_options": ("attributes", "options"), "list_attribute_statuses": ("attributes", "statuses"),
    "create_attribute": ("attributes", "create"),
    "create_record": ("records", "create"), "merge_records": ("records", "merge"),
    "create_note": ("notes", "create"),
    "create_list": ("lists", "create"), "update_list": ("lists", "update"),
    "create_entry": ("entries", "create"),
    "create_comment": ("comments", "create"),
    "create_attribute_option": ("attributes", "create_option"),
    "create_attribute_status": ("attributes", "create_status"),
    "update_record": ("records", "update"), "replace_record_values": ("records", "update"),
    "update_entry": ("entries", "update"), "replace_entry_values": ("entries", "update"),
}
# L'écart décidé par le mainteneur : deux fonctions par méthode, une par verbe, l'argument qui choisit le verbe fixé par chacune
# (PATCH ajoute aux valeurs d'une multisélection, PUT les remplace) et passé au client tel que la fonction le fixe.
BY_VERB = {
    "update_record": ("PATCH", {"overwrite_multiselect": False}),
    "replace_record_values": ("PUT", {"overwrite_multiselect": True}),
    "update_entry": ("PATCH", {"overwrite_multiselect": False}),
    "replace_entry_values": ("PUT", {"overwrite_multiselect": True}),
}
_IN_A_LIST = "the assignee and the linked record go inside a list, which the nested body does not say"
# Les méthodes laissées au client, et pourquoi.
LEFT_OUT = {
    "create_task": (("tasks", "create"), _IN_A_LIST + "; a date deadline completed to midnight; a second request "
                                          "(the first workspace member, the default assignee)"),
    "update_task": (("tasks", "update"), _IN_A_LIST + "; a date deadline completed to midnight"),
    "list_threads": (("threads", "list"), "the client sends parent_object, parent_record_id and list_id where Attio "
                                          "reads object, record_id and list: swallowed, then an unfiltered thread list "
                                          "is refused"),
}
RESOURCES = {"records": AttioResource, **{
    attr: type(getattr(AttioClient(api_key="k"), attr)) for attr in (
        "notes", "tasks", "lists", "entries", "workspace_members", "comments", "threads", "meetings",
        "call_recordings", "objects", "attributes")}}
# Les fiches de tous les objets : l'objet de la ressource est un argument de la fonction.
ARGUMENTS_ADDED = {name: {"object"} for name, (resource, _) in FUNCTIONS.items() if resource == "records"}
_ANCHOR = "sent as such, where Attio's reference reads record {object, record_id} or entry {list, entry_id}"
# Les arguments d'une méthode que sa fonction ne prend pas, et pourquoi.
ARGUMENTS_LEFT_OUT = {
    "search_records": {"query": "the client rewrites it into {'name': {'$contains': query}}"},
    "create_comment": {a: _ANCHOR for a in ("parent_object", "parent_record_id", "list_id", "entry_id")},
}
# Les arguments que le client prend en `**kwargs` : un argument dont il reçoit les clés (`values`, une fiche), ou des
# arguments nommés qu'il reçoit tels quels (les champs d'une liste).
SPREAD = {"create_record": "values", "update_record": "values", "replace_record_values": "values"}
KEYWORDS = {"update_list": {"name", "api_slug", "workspace_access", "workspace_member_access"}}
# Exigés par la description, facultatifs au client : sans `query`, une recherche sans filtre est list_records ; les
# valeurs que le client pose quand l'appel ne les donne pas (un slug tiré du nom, un accès complet, des listes vides),
# qu'Attio exige ; le fil, seule ancre décrite d'un commentaire.
REQUIRED_HERE = {
    "search_records": {"filters"},
    "create_list": {"api_slug", "workspace_access", "workspace_member_access"},
    "create_entry": {"entry_values"},
    "create_comment": {"thread_id"},
}
# Ce que le client pose quand l'appel ne le donne pas : défauts du client, non déclarés (l'hôte n'envoie rien).
CLIENT_FILLS = {
    "list_records": {"limit": 50, "offset": 0},
    "search_records": {"limit": 50},
    "query_entries": {"limit": 50, "offset": 0},
}
POST_READS = {"list_records", "search_records", "query_entries"}
CLASSES = {
    "read": {n for n in FUNCTIONS if n.split("_")[0] in ("list", "get", "search", "query")},
    "write": {"create_attribute", "create_record", "create_note", "create_list", "update_list", "create_entry",
              "create_comment", "create_attribute_option", "create_attribute_status", "update_record", "update_entry"},
    "sensitive": {"delete_record", "delete_note", "delete_task", "delete_entry", "delete_comment", "merge_records",
                  "replace_record_values", "replace_entry_values"},
}
# Sensibles sans être une suppression : la fusion remplace deux fiches par une troisième, sans retour ; le remplacement
# des valeurs d'une fiche ou d'une entrée perd celles d'une multisélection qu'il remplace (`[]` la vide).
SENSITIVE_WRITES = {"merge_records", "replace_record_values", "replace_entry_values"}
ADDRESSES = {
    "list_workspace_members": ("data.avatar_url", "list"), "get_workspace_member": ("data.avatar_url", "one"),
    "list_call_recordings": ("data.video_url", "list"), "get_call_recording": ("data.video_url", "one"),
}
PERSONAL = {"search_records": ["filters"], "query_entries": ["filter"], "create_record": ["values"],
            "create_note": ["content", "title"], "create_entry": ["entry_values"], "create_comment": ["content"],
            "update_record": ["values"], "replace_record_values": ["values"],
            "update_entry": ["entry_values"], "replace_entry_values": ["entry_values"]}
UUID = "0f1e2d3c-4b5a-4968-8776-655443322110"
VALUES = {
    "object": "companies", "record_id": UUID, "limit": 20, "offset": 40,
    "sort": [{"attribute": "created_at", "direction": "desc"}], "sorts": [{"attribute": "name", "direction": "asc"}],
    "filters": {"email_addresses": "ada@acme.example"}, "filter": {"stage": "Won"},
    "parent_object": "people", "parent_record_id": UUID, "note_id": UUID, "task_id": UUID, "completed": False,
    "list_id_or_slug": "sales_pipeline", "entry_id": UUID, "workspace_member_id": UUID, "comment_id": UUID,
    "thread_id": UUID, "meeting_id": UUID, "call_recording_id": UUID, "cursor": "Y3Vyc29y", "ends_from":
    "2026-10-19T00:00:00Z", "starts_before": "2026-10-26T00:00:00Z", "object_id_or_slug": "people",
    "target": "objects", "identifier": "companies", "attribute": "stage",
    "definition": {"title": "Source", "description": None, "api_slug": "source", "type": "text",
                   "is_required": False, "is_unique": False, "is_multiselect": False, "config": {}},
    "values": {"name": "Acme", "domains": ["acme.example"]},
    "primary_record_id": UUID, "secondary_record_id": "1f2e3d4c-5b6a-4778-8887-766554433221",
    "title": "Discovery call", "content": "Budget agreed.", "name": "Sales pipeline", "api_slug": "sales_pipeline",
    "workspace_access": "read-and-write",
    "workspace_member_access": [{"workspace_member_id": UUID, "level": "read-only"}],
    "entry_values": {"stage": "Lead"}, "author_id": UUID,
}
VALUES_OF = {"list_tasks": {"sort": "created_at:desc", "limit": 100}, "list_meetings": {"sort": "start_desc"},
             "create_attribute_option": {"title": "Reseller"}}
# Le moins qu'accepte une fonction au-delà de ses arguments exigés : une liste change au moins une valeur.
FEWEST = {"update_list": {"name": "Pipeline 2027"}}
# Plus étroit que le client, qui laisse passer : motifs d'identifiant, listes fermées, bornes, paires.
NARROWER = {
    "get_record": [{"object": "companies/x", "record_id": UUID}, {"object": "companies", "record_id": "r1"}],
    "list_records": [{"object": "companies", "sort": [{"attribute": "name", "direction": "up"}]},
                     {"object": "companies", "limit": 0}],
    "search_records": [{"object": "people"}, {"object": "people", "filters": {}}],
    "list_notes": [{"parent_object": "people"}, {"parent_record_id": UUID}],
    "get_attribute": [{"target": "activities", "identifier": "calls", "attribute": "title"}],
    "create_attribute": [{"target": "objects", "identifier": "companies", "definition": {"title": "Source"}},
                         {"target": "objects", "identifier": "companies",
                          "definition": dict(VALUES["definition"], type="formula")}],
    "query_entries": [{"list_id_or_slug": "sales_pipeline", "offset": -1}],
    "create_record": [{"object": "companies", "values": {}}],
    "merge_records": [{"object": "companies", "primary_record_id": "r1", "secondary_record_id": UUID}],
    "create_note": [{"parent_object": "people", "parent_record_id": UUID, "title": "", "content": "x"}],
    "create_list": [{"name": "Pipeline", "parent_object": "companies", "api_slug": "Pipeline",
                     "workspace_access": "full-access", "workspace_member_access": []},
                    {"name": "Pipeline", "parent_object": "companies", "api_slug": "pipeline",
                     "workspace_access": "admin", "workspace_member_access": []}],
    # Le client enverrait {"data": {}} : rien à changer.
    "update_list": [{"list_id_or_slug": "sales_pipeline"}],
    # Le client enverrait une table de valeurs vide : rien à changer.
    **{name: [{"object": "companies", "record_id": UUID, "values": {}}]
       for name in ("update_record", "replace_record_values")},
    **{name: [{"list_id_or_slug": "sales_pipeline", "entry_id": UUID, "entry_values": {}}]
       for name in ("update_entry", "replace_entry_values")},
    "create_attribute_status": [{"target": "objects", "identifier": "deals", "attribute": "stage", "title": ""}],
}
# Ce que le client refuse lui-même (ValueError avant tout envoi), et que le schéma refuse aussi.
CLIENT_REFUSES = {
    "list_notes": [{"limit": 51}, {"limit": 0}],
    "list_tasks": [{"limit": 1001}, {"sort": "due_at:asc"}],
    "list_meetings": [{"limit": 201}, {"sort": "start"}],
    # Un fil vide n'est pas une ancre : le client n'en a alors aucune.
    "create_comment": [{"content": "x", "author_id": UUID, "thread_id": ""}],
}


class _Answer:
    status_code = 200
    ok = True
    headers: dict = {}
    text = "{}"

    def __init__(self, body=None):
        self._body = {} if body is None else body
        self.content = json.dumps(self._body).encode()

    def json(self):
        return copy.deepcopy(self._body)


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(ATTIO.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[tuple[str, str]]:
    return {(attr, n) for attr, cls in RESOURCES.items()
            for n, _ in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")}


def _parameters(name: str) -> dict[str, inspect.Parameter]:
    resource, method = FUNCTIONS[name]
    return {p: v for p, v in inspect.signature(getattr(RESOURCES[resource], method)).parameters.items()
            if p != "self" and v.kind is not inspect.Parameter.VAR_KEYWORD}


def _call_the_client(name: str, arguments: dict, answer=None) -> tuple[dict, object]:
    """Ce que le client envoie pour un appel de la fonction, sans réseau, et ce qu'il rend."""
    sent = []

    def request(method, url, **kwargs):
        sent.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {}),
                     "json": kwargs.get("json"), "headers": dict(kwargs.get("headers") or {}),
                     "timeout": kwargs.get("timeout")})
        return _Answer(answer)

    resource, method = FUNCTIONS[name]
    given = copy.deepcopy(arguments)
    client = AttioClient(api_key="attio-k")
    target = AttioResource(client, given.pop("object")) if resource == "records" else getattr(client, resource)
    if name in SPREAD:
        given.update(given.pop(SPREAD[name]))
    given.update(BY_VERB.get(name, (None, {}))[1])
    with mock.patch.object(attio_client.requests, "request", request):
        returned = getattr(target, method)(**given)
    assert len(sent) == 1, name
    return sent[0], returned


def _in_query(value):
    """Une valeur en query comme l'écrit l'hôte : booléen en minuscules, nombre en texte."""
    return str(value).lower() if isinstance(value, bool) else str(value)


def _body(call: dict, given: dict) -> dict:
    """Le corps que bâtit l'hôte (« Le corps emboîté ») : la constante du corps, copiée, puis chaque argument donné posé
    à son chemin, les objets parents créés au besoin ; un argument absent ne crée rien, un `null` part `null`."""
    sent = copy.deepcopy((call.get("constants") or {}).get("body") or {})
    for path, argument in body_entries(call["body"]):
        if argument in given:
            parent = sent
            for key in path[:-1]:
                parent = parent.setdefault(key, {})
            parent[path[-1]] = copy.deepcopy(given[argument])
    return sent


def _described(name: str, function: dict, arguments: dict) -> tuple:
    """La requête que dit la description, plus ce que le client pose quand l'appel ne le donne pas."""
    call = function["call"]
    given = {**CLIENT_FILLS.get(name, {}), **arguments}
    path = call["path"].format(**{a: given[a] for a in re.findall(r"{(\w+)}", call["path"])})
    query = {api: _in_query(given[a]) for api, a in call.get("query", {}).items() if a in given}
    body = _body(call, given) if "body" in call else None
    return call["method"], AttioClient.BASE_URL + path, query, body


def _client_view(sent: dict) -> tuple:
    return sent["method"], sent["url"], {k: _in_query(v) for k, v in sent["params"].items()}, sent["json"]


def _every_argument(name: str, function: dict) -> dict:
    values = {**VALUES, **VALUES_OF.get(name, {})}
    return {a: values[a] for a in function["input"]["properties"]}


def _required(name: str, function: dict) -> dict:
    values = {**VALUES, **VALUES_OF.get(name, {})}
    return {**{a: values[a] for a in function["input"].get("required", [])}, **FEWEST.get(name, {})}


def _each_absence(name: str, function: dict) -> list[dict]:
    """Tous les arguments, puis chaque argument facultatif absent tour à tour, puis le moins accepté."""
    every = _every_argument(name, function)
    required = set(function["input"].get("required", []))
    calls = [every] + [{a: v for a, v in every.items() if a != absent} for absent in every if absent not in required]
    return [c for c in calls if _valid(function, c)] + [_required(name, function)]


def _valid(function: dict, arguments: dict) -> bool:
    return jsonschema.Draft202012Validator(function["input"]).is_valid(arguments)


def _shaped(answer, paths: list[str]):
    """La réponse taillée comme l'hôte la taille (`output.strip`), chaque chemin à travers les listes."""
    answer = copy.deepcopy(answer)

    def strip(node, keys):
        if isinstance(node, list):
            for item in node:
                strip(item, keys)
        elif isinstance(node, dict):
            if len(keys) == 1:
                node.pop(keys[0], None)
            elif keys[0] in node:
                strip(node[keys[0]], keys[1:])

    for path in paths:
        strip(answer, path.split("."))
    return answer


def test_the_description_is_version_1_2_0_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.3.0"
    check(description, ATTIO)


def test_each_client_method_is_one_function_or_left_out_by_name():
    """Une fonction par méthode, sauf l'écart nommé : deux fonctions, une par verbe, décrivent la même méthode."""
    assert set(_functions()) == set(FUNCTIONS)
    described = set(FUNCTIONS.values())
    shared = {method for method in described if list(FUNCTIONS.values()).count(method) > 1}
    assert shared == {FUNCTIONS[name] for name in BY_VERB} == {("records", "update"), ("entries", "update")}
    assert len(described) == len(FUNCTIONS) - len(shared)
    left_out = {method for method, _ in LEFT_OUT.values()}
    assert len(left_out) == len(LEFT_OUT) and not described & left_out
    assert described | left_out == _public_methods()


def test_the_connector_says_what_the_client_does():
    """Jeton en Bearer ; délai de lecture de 60 s, celui du client ; le rythme des écritures d'Attio (25 par seconde),
    que toute fonction admet ; sonde par la liste des objets ; ni clé de la plateforme ni en-tête constant."""
    connector = _description()["connector"]
    assert connector["base_url"] == AttioClient.BASE_URL
    assert connector["auth"] == {"kind": "bearer", "token": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "Access token", "secret": True}]
    sent, _ = _call_the_client("list_objects", {})
    assert sent["headers"]["Authorization"] == "Bearer attio-k"
    assert sent["timeout"] == (10, connector["timeout_s"]) == attio_client._HTTP_TIMEOUT
    assert connector["rate_limit"] == {"requests": 25, "seconds": 1}
    assert connector["modes"] == ["byo_user", "byo_org"]
    assert connector["probe"] == {"function": "list_objects", "non_empty": ["data"]}
    assert "headers" not in connector and "query_arrays" not in connector


def test_each_function_takes_the_arguments_its_client_method_names():
    for name, function in _functions().items():
        parameters = _parameters(name)
        left_out = set(ARGUMENTS_LEFT_OUT.get(name, {})) | set(BY_VERB.get(name, (None, {}))[1])
        spread = {SPREAD[name]} if name in SPREAD else set()
        properties = set(function["input"]["properties"])
        assert properties == ((set(parameters) - left_out) | ARGUMENTS_ADDED.get(name, set()) | spread
                              | KEYWORDS.get(name, set())), name
        without_default = {p for p, v in parameters.items() if v.default is inspect.Parameter.empty} - left_out
        assert set(function["input"].get("required", [])) == (
            without_default | ARGUMENTS_ADDED.get(name, set()) | REQUIRED_HERE.get(name, set()) | spread), name
    keyword_methods = {n for n in FUNCTIONS if any(
        v.kind is inspect.Parameter.VAR_KEYWORD for v in inspect.signature(
            getattr(RESOURCES[FUNCTIONS[n][0]], FUNCTIONS[n][1])).parameters.values())}
    assert keyword_methods == set(SPREAD) | set(KEYWORDS)
    assert set(ARGUMENTS_LEFT_OUT) <= set(FUNCTIONS) and set(REQUIRED_HERE) <= set(FUNCTIONS)


def test_each_argument_goes_where_the_client_sends_it():
    """Pour chaque fonction, tous ses arguments, chaque facultatif absent tour à tour et le moins accepté : la requête
    de l'hôte (verbe, chemin, query, corps emboîté) est celle du client."""
    for name, function in _functions().items():
        for arguments in _each_absence(name, function):
            jsonschema.validate(arguments, function["input"])
            sent, _ = _call_the_client(name, arguments)
            assert _client_view(sent) == _described(name, function, arguments), (name, arguments)


def test_each_write_body_is_the_clients_under_data():
    """Les écritures : le corps de l'hôte, constante et table emboîtée, est exactement celui du client, `data` compris,
    y compris un `null` donné."""
    functions = _functions()
    writes = {n for n, f in functions.items() if f["class"] != "read" and "body" in f["call"]}
    assert writes == CLASSES["write"] | SENSITIVE_WRITES
    for name in writes:
        assert set(functions[name]["call"]["body"]) == {"data"}, name
    expected = {
        "create_note": {"data": {"parent_object": "people", "parent_record_id": UUID, "title": "Discovery call",
                                 "format": "markdown", "content": "Budget agreed."}},
        "create_comment": {"data": {"format": "plaintext", "content": "Budget agreed.", "thread_id": UUID,
                                    "author": {"type": "workspace-member", "id": UUID}}},
        "create_record": {"data": {"values": {"name": "Acme", "domains": ["acme.example"]}}},
        "update_record": {"data": {"values": {"name": "Acme", "domains": ["acme.example"]}}},
        "replace_record_values": {"data": {"values": {"name": "Acme", "domains": ["acme.example"]}}},
        "update_entry": {"data": {"entry_values": {"stage": "Lead"}}},
        "replace_entry_values": {"data": {"entry_values": {"stage": "Lead"}}},
    }
    for name, body in expected.items():
        arguments = _every_argument(name, functions[name])
        sent, _ = _call_the_client(name, arguments)
        assert sent["json"] == body == _described(name, functions[name], arguments)[3], name
    for name, arguments in {
        "update_list": {"list_id_or_slug": "sales_pipeline", "workspace_access": None},
        "create_list": dict(_every_argument("create_list", functions["create_list"]), workspace_access=None),
    }.items():
        jsonschema.validate(arguments, functions[name]["input"])
        sent, _ = _call_the_client(name, arguments)
        assert sent["json"]["data"]["workspace_access"] is None, name
        assert _client_view(sent) == _described(name, functions[name], arguments), name


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent, _ = _call_the_client(name, example["input"])
            assert _client_view(sent) == _described(name, function, example["input"]), (name, example["title"])


def test_what_the_client_fills_is_never_a_declared_default_nor_required():
    functions = _functions()
    for name, fills in CLIENT_FILLS.items():
        properties = functions[name]["input"]["properties"]
        assert all("default" not in properties[a] for a in fills), name
        assert not set(fills) & set(functions[name]["input"].get("required", [])), name
        assert {a: _parameters(name)[a].default for a in fills} == fills, name


def test_where_the_schema_is_narrower_than_the_client_it_is_named():
    functions = _functions()
    for name, cases in NARROWER.items():
        for case in cases:
            assert not _valid(functions[name], case), (name, case)
            _call_the_client(name, case)


def test_what_the_client_refuses_the_schema_refuses():
    functions = _functions()
    for name, cases in CLIENT_REFUSES.items():
        for case in cases:
            assert not _valid(functions[name], case), (name, case)
            with pytest.raises(ValueError):
                _call_the_client(name, case)


def test_reads_writes_and_deletes_are_classified_and_each_delete_is_confirmed():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    assert set().union(*CLASSES.values()) == set(FUNCTIONS)
    for name, function in functions.items():
        method = function["call"]["method"]
        if function["class"] == "read":
            assert method == ("POST" if name in POST_READS else "GET"), name
        assert (method == "DELETE" or name in SENSITIVE_WRITES) == (function["class"] == "sensitive"), name
        assert ("confirm" in function) == (function["class"] == "sensitive"), name
        assert "act" not in function, name
        if "confirm" in function:
            for argument in function["input"]["required"]:
                assert "{" + argument + "}" in function["confirm"]["summary"], name


def test_only_the_meetings_page_by_cursor_and_each_call_costs_one_request():
    for name, function in _functions().items():
        assert function["cost"] == {"per": "request"}, name
        if name != "list_meetings":
            assert "pagination" not in function, name
    meetings = _functions()["list_meetings"]
    assert meetings["pagination"] == {"kind": "cursor", "request_param": "cursor",
                                      "next": "pagination.next_cursor", "max_pages": 10}
    assert meetings["output"] == {"items": "data"}


def test_the_personal_arguments_are_named():
    """Un filtre, les valeurs d'une fiche ou d'une entrée portent un courriel, un nom ou un numéro ; le titre et le
    texte d'une note, le texte d'un commentaire parlent de personnes : donnée personnelle. Aucun autre argument, ni un
    identifiant."""
    for name, function in _functions().items():
        properties = function["input"]["properties"]
        assert sorted(a for a, s in properties.items() if s.get("personal") is True) == PERSONAL.get(name, []), name
        others = {a: s for a, s in properties.items() if a not in PERSONAL.get(name, [])}
        assert '"personal"' not in json.dumps(others), name


def test_no_address_that_opens_content_reaches_the_agent_description_and_client():
    """L'adresse vidéo d'un enregistrement (signée, une heure) et celle de l'avatar d'un membre ouvrent leur contenu
    sans connexion : la description les retire (`output.strip`), le client de même ; le reste demeure."""
    functions = _functions()
    stripped = {n for n, f in functions.items() if "strip" in f.get("output", {})}
    assert stripped == set(ADDRESSES)
    for name, (path, shape) in ADDRESSES.items():
        function = functions[name]
        assert function["output"]["strip"] == [path], name
        assert "never given" in function["description"], name
        key = path.split(".")[1]
        one = {"id": {"workspace_id": "w-0"}, "status": "completed", key: "https://files.example.test/x"}
        answer = {"data": [one, dict(one)] if shape == "list" else one}
        shaped = _shaped(answer, function["output"]["strip"])
        assert "files.example.test" not in str(shaped) and "w-0" in str(shaped), name
        _, returned = _call_the_client(name, _required(name, function), answer)
        assert returned == shaped, name
        sample = function["output"]["sample"]
        assert key in str(sample) and key not in str(_shaped(sample, function["output"]["strip"])), name


def test_the_401_marks_the_account_and_429_and_5xx_are_retried_as_oto_1_says():
    """Le client ne nomme aucun refus (`UpstreamHTTPError` sur tout statut hors 2xx). Le 401 dit un jeton invalide :
    il marque `credential_rejected` ; le 403 dit un droit ou une offre, pas une clé. Le 429 et les 5xx se rejouent."""
    errors = _description()["connector"]["errors"]
    assert [(e["status"], e["code"], e["marks_account"]) for e in errors if "marks_account" in e] == [
        (401, "key_refused", "credential_rejected")]
    assert not [e for e in errors if "body" in e]
    assert {e["code"] for e in errors if e["retryable"]} == {"rate_limited", "upstream_unavailable"}
    assert next(e for e in errors if e["code"] == "upstream_unavailable")["status"] == [500, 502, 503, 504]
    assert not [f for f in _functions().values() if "refusals" in f]
    transport = inspect.getsource(AttioClient._request)
    assert "UpstreamHTTPError.from_response" in transport


def test_what_stays_in_the_client_is_named_in_the_description():
    """Chaque méthode laissée au client, l'argument `query` et les ancres d'un commentaire hors de son fil sont nommés
    dans l'en-tête du fichier, comme ce que la description exige et que le client pose."""
    header = ATTIO.read_text(encoding="utf-8").split("\nconnector:")[0]
    for name in LEFT_OUT:
        assert name in header, name
        assert name not in _functions(), name
    assert "query argument of search_records" in header
    for argument in ARGUMENTS_LEFT_OUT["create_comment"]:
        assert argument in header, argument
    for name, required in REQUIRED_HERE.items():
        if name != "search_records":
            assert name in header and all(a in header for a in required), name


def test_two_functions_describe_one_method_each_with_its_verb():
    """L'écart nommé : `records.update` et `entries.update` sont chacune deux fonctions, PATCH (ajoute) et PUT
    (remplace). Chacune ne prend pas `overwrite_multiselect`, et le client, appelé avec la valeur qui correspond à la
    fonction, envoie son verbe, son chemin et son corps ; l'autre valeur enverrait l'autre verbe. Le remplacement est
    sensible : sa confirmation et sa description disent la perte des valeurs, celle de la fiche la valeur unique qu'il
    libère. L'en-tête du fichier nomme l'écart."""
    functions = _functions()
    header = ATTIO.read_text(encoding="utf-8").split("\nconnector:")[0]
    assert "a gap to \"one function per method, under its name\"" in header
    for name, (verb, fixed) in BY_VERB.items():
        function = functions[name]
        assert function["call"]["method"] == verb, name
        assert set(fixed) == {"overwrite_multiselect"} and not set(fixed) & set(function["input"]["properties"]), name
        assert name in header and f"{verb}, {str(fixed['overwrite_multiselect']).lower()}" in header, name
        arguments = _every_argument(name, function)
        sent, _ = _call_the_client(name, arguments)
        assert sent["method"] == verb and _client_view(sent) == _described(name, function, arguments), name
        other = {"PATCH": "PUT", "PUT": "PATCH"}[verb]
        given = copy.deepcopy(arguments)
        client = AttioClient(api_key="attio-k")
        resource, method = FUNCTIONS[name]
        target = AttioResource(client, given.pop("object")) if resource == "records" else getattr(client, resource)
        if name in SPREAD:
            given.update(given.pop(SPREAD[name]))
        seen = []
        with mock.patch.object(attio_client.requests, "request",
                               lambda m, u, **k: seen.append(m) or _Answer()):
            getattr(target, method)(overwrite_multiselect=not fixed["overwrite_multiselect"], **given)
        assert seen == [other], name
        if verb == "PUT":
            assert function["class"] == "sensitive" and "lost" in function["confirm"]["summary"], name
            assert "which are lost" in function["description"] and "[] empties it" in function["description"], name
        else:
            assert function["class"] == "write" and "[] changes nothing" in function["description"], name
    assert "free a unique value" in functions["replace_record_values"]["description"]


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), ATTIO)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "attio.{name}",' in module.source, name
    assert not module.skipped
    table = python.declared([checked])["attio"]
    assert set(table) == set(_functions())
    assert DECLARED.get("attio") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
