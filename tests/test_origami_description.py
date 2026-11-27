"""La description `origami` 1.0.0 : celle du client existant (`oto.tools.origami`), sans rien y changer.

Une fonction par méthode du client, sous son nom, avec ses noms d'arguments et les clés que le client envoie à Origami
(chemin, query, corps). Écarts nommés, gardés ici :
- `cells` de `list_rows` : le client envoie `flat` quand l'appel ne le donne pas (`CLIENT_FILLS`), ce n'est pas un défaut
  déclaré ; `enrich` d'`upsert_rows` : le client envoie toujours la valeur, `false` sans demande, là où l'API enrichit
  par défaut : exigé par le schéma (`REQUIRED_WHERE_THE_CLIENT_FILLS`) ;
- un vocabulaire ou une borne que la référence ferme et que le client laisse passer (`NARROWER`) ;
- `project_id` du constructeur (l'en-tête `x-origami-project`), qu'aucun réglage ne sait porter en en-tête.
Reste hors du fichier `upload_documents`, les octets d'un fichier en base64 dans un corps JSON (`LEFT_OUT`). Les gardes
ci-dessous lisent le client lui-même : un endpoint, un argument ou une clé qu'il ajoute sans la description fait échouer
le test ; une écriture servie du côté lecture aussi.
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
from oto.connectors.declared import DECLARED
from oto.tools.origami import client as origami_client
from oto.tools.origami.client import OrigamiClient

ORIGAMI = CONNECTORS / "origami" / "connector.yaml"

# Les méthodes publiques du client que le format ne sait pas dire, et pourquoi.
LEFT_OUT = {
    "upload_documents": "the bytes of a file go base64 inside a JSON body; the format takes a file only as a transit "
                        "file sent in multipart, never its bytes as an argument",
}
# Les arguments du constructeur que la description ne porte pas, et pourquoi.
CONSTRUCTOR_LEFT_OUT = {
    "project_id": "the x-origami-project header that scopes a request to a child organization: a setting of the "
                  "account is cited only in an address, never in a header; oto 1 never passes it",
}
# Les arguments d'une méthode que sa fonction ne prend pas, et pourquoi : aucun.
ARGUMENTS_LEFT_OUT: dict[str, dict[str, str]] = {}
# Ce que le client envoie quand l'appel ne le donne pas, sans qu'Origami l'exige : pas un défaut déclaré, l'hôte
# n'envoyant rien pour un argument absent.
CLIENT_FILLS = {"list_rows": {"cells": "flat"}}
# Ce que le client pose toujours et dont l'absence change ce que fait Origami (il enrichit par défaut) : exigé.
REQUIRED_WHERE_THE_CLIENT_FILLS = {"upsert_rows": {"enrich": False}}
# Les drapeaux qui ne partent que vrais, comme le client les envoie (`const: true`).
ONLY_TRUE = {
    "launch_campaign": {"dry_run": "dryRun"},
    "pause_campaign": {"dry_run": "dryRun"},
    "resume_campaign": {"dry_run": "dryRun"},
    "delete_campaign": {"confirm": "confirm", "dry_run": "dryRun"},
}
# Ce que le client écarte quand on le lui donne vide (`if value:`) : le schéma le refuse.
EMPTY_DROPPED = {
    "list_rows": {"filters": ([], "filters"), "sort": ({}, "sort")},
    "create_campaign": {"settings": ({}, "settings")},
    "upsert_rows": {"batch_id": ("", "batchId")},
}
# Ce que le client refuse avant d'envoyer (ValueError) : le schéma le refuse aussi.
CLIENT_REFUSES = {
    "create_workspace": [{"name": "   "}],
    "create_campaign": [{"instructions": "  "}],
    "upsert_rows": [{"rows": []}, {"rows": [{"email": "x@example.test"}] * 101}, {"match_columns": []}],
    "list_sequences": [{"workspace_id": ""}],
}
# Plus étroit que le client, qui laisse passer : la référence ferme le vocabulaire ou borne la valeur.
NARROWER = {
    "get_table": {"include": "economics"},
    "get_run": {"include": "steps"},
    "create_workspace": {"name": "x" * 81},
    "create_campaign": {"instructions": "x" * 10001},
}
# Les arguments encodés en JSON dans la query, comme le client les sérialise.
ENCODED = {"list_rows": {"filters", "sort"}}
# Les lectures paginées par curseur (`nextCursor`), et leur nombre de pages au plus.
PAGINATED = {"list_workspaces": 10, "list_tables": 10, "list_rows": 20, "campaign_people": 10, "list_sequences": 10}
# Les listes d'une seule page (nextCursor toujours nul).
SINGLE_PAGE_LISTS = {"list_columns", "list_campaigns"}
# Ce que fait chaque fonction : une écriture mal classée est un défaut grave.
CLASSES = {
    "read": {
        "list_workspaces", "list_tables", "get_table", "list_columns", "list_rows", "list_campaigns", "get_campaign",
        "campaign_stats", "campaign_people", "get_run", "list_sequences", "get_sequence",
    },
    "write": {"create_workspace", "pause_campaign"},
    "sensitive": {
        # Jusqu'à cent lignes d'un coup, et l'enrichissement dépense des crédits.
        "upsert_rows",
        # Une course de l'agent d'Origami : des crédits, et les personnes de la table inscrites, peut-être à une
        # campagne qui existe déjà.
        "create_campaign",
        # Envoie des courriels et des messages LinkedIn à de vraies personnes.
        "launch_campaign", "resume_campaign",
        # Supprime.
        "delete_campaign",
    },
}
# La donnée personnelle : où elle est marquée.
PERSONAL = {
    "upsert_rows": ("rows", "items", "additionalProperties"),
    "list_rows": ("filters", "items", "properties", "value"),
    "campaign_people": ("search",),
    "list_sequences": ("recipient",),
}
WRITING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
REQUEST = {"per": "request"}

WS = "00000000-0000-4000-8000-000000000001"
TABLE = "00000000-0000-4000-8000-000000000002"
CAMPAIGN = "00000000-0000-4000-8000-000000000003"
# Une valeur par argument, pour un appel qui les donne tous.
VALUES = {
    "cursor": "eyJpZCI6NTB9", "limit": 25, "search": "outreach", "name": "Pilot Q4", "workspace_id": WS,
    "table_id": TABLE, "include": "stats", "cells": "flat",
    "filters": [{"column": "email", "operator": "is_not_empty"},
                {"column": "company", "operator": "equals", "value": "Example"}],
    "sort": {"column": "company", "direction": "desc"},
    "rows": [{"email": "marie@example.test", "first-name": "Marie", "score": 3}],
    "match_columns": ["email"], "enrich": True, "reenrich_updated": False,
    "batch_id": "00000000-0000-4000-8000-0000000000aa", "campaign_id": CAMPAIGN, "status": "sent,replied",
    "instructions": "Email the heads of sales about a pilot.",
    "settings": {"blockPriorContacts": True, "blockActiveDuplicates": False},
    "agent_id": "00000000-0000-4000-8000-000000000004", "run_id": "00000000-0000-4000-8000-000000000005",
    "dry_run": True, "confirm": True, "channel": "email", "recipient": "marie@example.test",
    "sequence_id": "00000000-0000-4000-8000-000000000006",
}
VALUES_OF = {
    "get_run": {"include": "stats,transcript"},
    "list_sequences": {"status": "active"},
    "campaign_people": {"search": "martin"},
}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(ORIGAMI.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(OrigamiClient, inspect.isfunction) if not n.startswith("_")}


def _parameters(name: str) -> dict[str, inspect.Parameter]:
    return {p: v for p, v in inspect.signature(getattr(OrigamiClient, name)).parameters.items() if p != "self"}


def _client_endpoints() -> dict[str, tuple[str, str]]:
    """Chaque méthode publique qui appelle `_request` une seule fois, sa méthode HTTP et son chemin, tel qu'écrit."""
    endpoints = {}
    for name in _public_methods():
        source = inspect.getsource(getattr(OrigamiClient, name))
        found = re.findall(r'self\._request\(\s*"([A-Z]+)",\s*f?"([^"]+)"', source)
        if len(found) == 1:
            endpoints[name] = found[0]
    return endpoints


class _Answer:
    status_code = 200
    ok = True
    headers: dict = {}
    content = b"{}"
    text = "{}"

    def json(self):
        return {}


def _sent_by_the_client(name: str, arguments: dict, **client) -> dict:
    """Ce que la méthode du client envoie, sans réseau : méthode, adresse, query, corps JSON, en-têtes de session."""
    sent = []

    def request(self, method, url, **kwargs):
        sent.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {}),
                     "json": kwargs.get("json"), "headers": dict(self.headers), "timeout": kwargs.get("timeout")})
        return _Answer()

    with mock.patch.object(origami_client.requests.Session, "request", request):
        getattr(OrigamiClient(api_key="og_live_k", **client), name)(**arguments)
    assert len(sent) == 1, name
    return sent[0]


def _in_query(value):
    """Une valeur en query comme l'écrit l'hôte : booléen en minuscules, nombre en texte."""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _described(name: str, function: dict, arguments: dict) -> tuple:
    """La requête que dit la description, plus ce que le client pose quand l'appel ne le donne pas (`CLIENT_FILLS`) :
    méthode, adresse, query, corps JSON. Un argument encodé en JSON est comparé par sa valeur, l'espacement de la
    sérialisation étant libre."""
    call = function["call"]
    given = {**CLIENT_FILLS.get(name, {}), **arguments}
    path = call["path"].format(**{a: given[a] for a in re.findall(r"{(\w+)}", call["path"])})
    encoded = set(call.get("encode", {}))
    query = {}
    for api, argument in call.get("query", {}).items():
        if argument in given:
            query[api] = given[argument] if argument in encoded else _in_query(given[argument])
    body = None
    if "body" in call:
        body = {api: given[a] for api, a in call["body"].items() if a in given}
    return call["method"], OrigamiClient.BASE_URL + path, query, body


def _client_view(name: str, sent: dict) -> tuple:
    encoded = {api for api, a in _functions()[name]["call"].get("query", {}).items() if a in ENCODED.get(name, set())}
    query = {k: (json.loads(v) if k in encoded else _in_query(v)) for k, v in sent["params"].items()}
    return sent["method"], sent["url"], query, sent["json"]


def _values(name: str, function: dict) -> dict:
    values = {**VALUES, **VALUES_OF.get(name, {})}
    return {argument: values[argument] for argument in function["input"]["properties"]}


def _required(name: str) -> dict:
    function = _functions()[name]
    return {a: _values(name, function)[a] for a in function["input"].get("required", [])}


def _valid(function: dict, arguments: dict) -> bool:
    return jsonschema.Draft202012Validator(function["input"]).is_valid(arguments)


def test_the_description_is_version_1_0_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.1"
    check(description, ORIGAMI)


def test_each_method_of_the_client_is_a_function_of_the_same_name_method_and_path_or_named_with_its_reason():
    functions = _functions()
    endpoints = _client_endpoints()
    assert _public_methods() - set(functions) == set(LEFT_OUT)
    assert not set(LEFT_OUT) & set(functions)
    assert set(endpoints) == _public_methods(), "every method of the client sends one request"
    assert {n: (f["call"]["method"], f["call"]["path"]) for n, f in functions.items()} == {
        n: endpoints[n] for n in functions}


def test_the_connector_says_what_the_client_does():
    """Clé en jeton porteur, dans l'en-tête de la session ; délai de 120 s ; rythme publié par Origami, que le client ne
    tient pas ; sonde par la liste des workspaces, comme oto 1."""
    connector = _description()["connector"]
    assert connector["base_url"] == OrigamiClient.BASE_URL
    assert connector["auth"] == {"kind": "bearer", "token": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "API key", "secret": True}]
    sent = _sent_by_the_client("list_workspaces", {})
    assert sent["headers"]["Authorization"] == "Bearer og_live_k"
    assert "x-origami-project" not in sent["headers"]
    assert connector["timeout_s"] == origami_client._HTTP_TIMEOUT[1] == sent["timeout"][1]
    assert connector["rate_limit"] == {"requests": 100, "seconds": 60}, "Origami's published rate, per organization"
    assert "platform" not in connector["modes"], "credits and sends are those of the Origami account behind the key"
    assert connector["probe"] == {"function": "list_workspaces"}
    assert "query_arrays" not in connector, "no list goes in a query"
    assert "headers" not in connector


def test_what_the_constructor_takes_beyond_the_key_is_named():
    parameters = set(inspect.signature(OrigamiClient.__init__).parameters) - {"self"}
    assert parameters - {"api_key"} == set(CONSTRUCTOR_LEFT_OUT)
    sent = _sent_by_the_client("list_workspaces", {}, project_id="00000000-0000-4000-8000-0000000000bb")
    assert sent["headers"]["x-origami-project"] == "00000000-0000-4000-8000-0000000000bb"
    description = ORIGAMI.read_text(encoding="utf-8")
    assert "x-origami-project" not in yaml.safe_load(description)["connector"].get("headers", {})
    assert all("project" not in f["input"]["properties"] for f in _functions().values())


def test_each_function_takes_the_arguments_of_its_client_method():
    for name, function in _functions().items():
        properties = set(function["input"]["properties"])
        parameters = set(_parameters(name))
        assert properties <= parameters, name
        assert parameters - properties == set(ARGUMENTS_LEFT_OUT.get(name, {})), name
    assert set(ARGUMENTS_LEFT_OUT) <= set(_functions())


def test_each_argument_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        arguments = _values(name, function)
        jsonschema.validate(arguments, function["input"])
        assert _client_view(name, _sent_by_the_client(name, arguments)) == _described(name, function, arguments), name


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent = _sent_by_the_client(name, {**REQUIRED_WHERE_THE_CLIENT_FILLS.get(name, {}), **example["input"]})
            assert _client_view(name, sent) == _described(name, function, example["input"]), (name, example["title"])


def test_what_the_client_fills_is_never_a_declared_default_and_enrich_is_required():
    functions = _functions()
    for name, fills in CLIENT_FILLS.items():
        assert {a: _parameters(name)[a].default for a in fills} == fills, name
        assert all("default" not in functions[name]["input"]["properties"][a] for a in fills), name
        assert all(a not in functions[name]["input"].get("required", []) for a in fills), name
    for name, fills in REQUIRED_WHERE_THE_CLIENT_FILLS.items():
        assert {a: _parameters(name)[a].default for a in fills} == fills, name
        assert set(fills) <= set(functions[name]["input"]["required"]), name
        assert all("default" not in functions[name]["input"]["properties"][a] for a in fills), name
        # Le client envoie la valeur même quand l'appel ne la donne pas.
        sent = _sent_by_the_client(name, {a: v for a, v in _required(name).items() if a not in fills})
        assert {k: sent["json"][k] for k in fills} == fills, name


def test_the_flags_the_client_sends_only_when_true_are_const_true():
    functions = _functions()
    for name, flags in ONLY_TRUE.items():
        properties = functions[name]["input"]["properties"]
        assert {f: properties[f].get("const") for f in flags} == {f: True for f in flags}, name
        query = functions[name]["call"]["query"]
        assert {f: next(k for k, v in query.items() if v == f) for f in flags} == flags, name
        assert _sent_by_the_client(name, {**_required(name), **{f: False for f in flags}})["params"] == {}, name
        sent = _sent_by_the_client(name, {**_required(name), **{f: True for f in flags}})
        assert sent["params"] == {api: "true" for api in flags.values()}, name


def test_what_the_client_drops_when_empty_the_schema_refuses():
    functions = _functions()
    for name, empties in EMPTY_DROPPED.items():
        for argument, (empty, api) in empties.items():
            arguments = {**_required(name), argument: empty}
            assert not _valid(functions[name], arguments), (name, argument)
            sent = _sent_by_the_client(name, arguments)
            assert api not in sent["params"] and api not in (sent["json"] or {}), (name, argument)


def test_what_the_client_refuses_the_schema_refuses():
    functions = _functions()
    for name, cases in CLIENT_REFUSES.items():
        for case in cases:
            arguments = {**_required(name), **case}
            assert not _valid(functions[name], arguments), (name, case)
            with pytest.raises(ValueError):
                _sent_by_the_client(name, arguments)


def test_where_the_schema_is_narrower_than_the_client_it_is_named():
    """La référence ferme un vocabulaire ou borne une valeur que le client laisse passer : l'écart est nommé ici."""
    functions = _functions()
    for name, case in NARROWER.items():
        arguments = {**_required(name), **case}
        assert not _valid(functions[name], arguments), (name, case)
        _sent_by_the_client(name, arguments)


def test_reads_and_writes_are_classified_and_every_write_is_served_on_the_act_side():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        method = function["call"]["method"]
        assert (method in WRITING_METHODS) == (function["class"] != "read"), name
        assert ("confirm" in function) == (function["class"] == "sensitive"), name
        if method == "DELETE":
            assert function["class"] == "sensitive", name
        assert function.get("act", function["class"] != "read") == (function["class"] != "read"), name
        assert "act" not in function, name


def test_the_lists_page_by_cursor_and_one_page_by_default():
    """Toute liste rend l'enveloppe `{items, nextCursor}` ; `nextCursor` nul à la dernière page. Les cinq listes qui se
    paginent déclarent la pagination, que le client laisse à l'appelant ; les autres tiennent en une page."""
    functions = _functions()
    for name, function in functions.items():
        if name in PAGINATED:
            pagination = function["pagination"]
            assert {k: pagination[k] for k in ("kind", "request_param", "next", "max_pages")} == {
                "kind": "cursor", "request_param": "cursor", "next": "nextCursor", "max_pages": PAGINATED[name]}, name
            assert "more" not in pagination, name
            assert function["call"]["query"]["cursor"] == "cursor", name
            assert "cursor" in _parameters(name), name
            assert function["output"] == {"items": "items"}, name
        else:
            assert "pagination" not in function, name
            assert "cursor" not in function["input"]["properties"], name
    for name in SINGLE_PAGE_LISTS:
        assert functions[name]["output"] == {"items": "items"}, name
    assert functions["list_rows"]["pagination"]["total"] == "total"
    assert functions["campaign_people"]["pagination"]["total"] == "total"


def test_the_json_query_arguments_are_encoded_as_the_client_serializes_them():
    functions = _functions()
    assert {n: set(f["call"].get("encode", {})) for n, f in functions.items() if "encode" in f["call"]} == ENCODED
    for name, arguments in ENCODED.items():
        assert all(functions[name]["call"]["encode"][a] == "json" for a in arguments), name
        source = inspect.getsource(getattr(OrigamiClient, name))
        assert all(f"_json.dumps({a})" in source for a in arguments), name


def test_each_call_costs_one_request():
    for name, function in _functions().items():
        assert function["cost"] == REQUEST, name


def test_the_personal_values_are_marked():
    functions = _functions()
    for name, path in PERSONAL.items():
        node = functions[name]["input"]["properties"]
        for key in path:
            node = node[key]
        assert node.get("personal") is True, (name, path)
    marked = {n for n, f in functions.items() if '"personal": true' in json.dumps(f["input"])}
    assert marked == set(PERSONAL)


def test_only_the_401_and_exhausted_credits_mark_the_account():
    """Le client ne nomme aucun refus (`raise_for_upstream`, tout statut au-delà de 400 la même erreur) ; la référence
    dit le 401 « clé absente ou invalide », et l'hôte envoie toujours la clé : il marque `credential_rejected`. Le 402
    au code `INSUFFICIENT_CREDITS` (que le client nomme pour `create_campaign`) marque `quota_exhausted` ; les autres 402
    et le 403 ne marquent rien. Seul le 429 se rejoue."""
    errors = _description()["connector"]["errors"]
    marking = [e for e in errors if "marks_account" in e]
    assert [(e["status"], e["code"], e["marks_account"], e.get("body")) for e in marking] == [
        (401, "key_refused", "credential_rejected", None),
        (402, "credits_exhausted", "quota_exhausted", {"path": "code", "equals": ["INSUFFICIENT_CREDITS"]}),
    ]
    assert [e for e in errors if "body" in e] == [marking[1]]
    assert {e["code"] for e in errors if e["retryable"]} == {"rate_limited"}
    assert not [r for f in _functions().values() for r in f.get("refusals", [])]
    transport = inspect.getsource(OrigamiClient._request)
    assert "raise_for_upstream(resp" in transport and "status_code" not in transport
    assert "402 `INSUFFICIENT_CREDITS`" in inspect.getsource(OrigamiClient.create_campaign)


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), ORIGAMI)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "origami.{name}",' in module.source, name
    for name in LEFT_OUT:
        assert f'"origami.{name}"' not in module.source, name
    table = python.declared([checked])["origami"]
    assert set(table) == set(_functions())
    assert DECLARED.get("origami") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
