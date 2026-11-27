"""La description `granola` 1.0.0 : celle du client existant (`oto.tools.granola`).

Une fonction par méthode du client, sous son nom ; les arguments sont ceux que la méthode nomme (ses paramètres, et
les clés libres `**params`/`**body` que sa docstring documente) ; les clés envoyées à Granola (chemin, query, corps)
sont les siennes. Écart nommé, gardé ici : les identifiants (`not_`, `fol_`, `whe_`) suivent les motifs de l'OpenAPI,
là où le client laisse passer tout texte (`NARROWER`).
Rien ne revient à l'agent ni d'un secret ni d'une adresse ouverte sans connexion : le secret de signature d'un webhook
et l'adresse d'une photo jointe à une note (`attachments[].url`) sont retirés par la description et par le client.
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
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.granola import client as granola_client
from oto.tools.granola.client import GranolaClient

GRANOLA = CONNECTORS / "granola" / "connector.yaml"

# Les clés libres (`**params`, `**body`) que la docstring de chaque méthode documente.
FREE_KEYS = {
    "list_notes": {"created_before", "created_after", "updated_after", "folder_id", "cursor", "page_size"},
    "get_note": {"include"},
    "get_transcript": {"cursor", "page_size"},
    "list_folders": {"cursor", "page_size"},
    "create_webhook_endpoint": {"events", "folder_ids"},
    "update_webhook_endpoint": {"url", "scopes", "events", "folder_ids", "enabled"},
}
CLASSES = {
    "read": {"list_notes", "get_note", "get_transcript", "list_folders", "list_webhook_endpoints"},
    "write": {"create_webhook_endpoint", "update_webhook_endpoint"},
    # Les livraisons s'arrêtent pour de bon.
    "sensitive": {"delete_webhook_endpoint"},
}
PAGINATED = {"list_notes": ("notes", 10), "get_transcript": ("transcript", 20), "list_folders": ("folders", 10)}
WEBHOOK_ANSWERS = {"list_webhook_endpoints", "create_webhook_endpoint", "update_webhook_endpoint"}
PHOTO_ADDRESS = {"get_note": ["attachments.url"]}
# Plus étroit que le client, qui laisse passer : les motifs d'identifiant de l'OpenAPI, les bornes et les listes.
NARROWER = {
    "list_notes": [{"folder_id": "4y6LduVdwSKC27"}, {"page_size": 31}],
    "get_note": [{"note_id": "1d3tmYTlCICgjy"}, {"note_id": "not_1d3tmYTlCICgjy", "include": "attachments"}],
    "get_transcript": [{"note_id": "not_short"}, {"note_id": "not_1d3tmYTlCICgjy", "page_size": 101}],
    "list_folders": [{"page_size": 0}],
    "create_webhook_endpoint": [{"url": "http://hooks.example.test/g", "scopes": ["personal"]},
                                {"url": "https://hooks.example.test/g", "scopes": []},
                                {"url": "https://hooks.example.test/g", "scopes": ["team"]}],
    "update_webhook_endpoint": [{"webhook_endpoint_id": "whe_2mKr8fQxLp7Ta3"},
                                {"webhook_endpoint_id": "2mKr8fQxLp7Ta3", "enabled": True}],
    "delete_webhook_endpoint": [{"webhook_endpoint_id": "whe_"}],
}
VALUES = {
    "created_before": "2026-10-31", "created_after": "2026-10-01T08:00:00Z", "updated_after": "2026-10-15",
    "folder_id": "fol_4y6LduVdwSKC27", "cursor": "eyJjcmVkZW50aWFsfQ", "page_size": 20,
    "note_id": "NOT_1d3tmYTlCICgjy", "include": "transcript", "url": "https://hooks.example.test/granola",
    "scopes": ["personal", "public"], "events": ["note.generated", "note.edited"],
    "folder_ids": ["fol_4y6LduVdwSKC27"], "enabled": False, "webhook_endpoint_id": "whe_2mKr8fQxLp7Ta3",
}
SECRET = "sample-signing-secret"
ENDPOINT = {"id": "whe_2mKr8fQxLp7Ta3", "object": "webhook_endpoint", "url": "https://hooks.example.test/granola",
            "url_redacted": False, "events": ["note.generated"], "folder_ids": [], "scopes": ["personal"],
            "enabled": True}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(GRANOLA.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(GranolaClient, inspect.isfunction) if not n.startswith("_")}


def _named_parameters(method: str) -> set[str]:
    return {p for p, v in inspect.signature(getattr(GranolaClient, method)).parameters.items()
            if p != "self" and v.kind is not inspect.Parameter.VAR_KEYWORD}


class _Answer:
    status_code = 200
    ok = True
    headers: dict = {}
    content = b"{}"
    text = "{}"

    def __init__(self, body=None):
        self._body = {} if body is None else body

    def json(self):
        return copy.deepcopy(self._body)


def _sent_by_the_client(name: str, arguments: dict, answer=None) -> tuple[dict, object]:
    """Ce que le client envoie pour un appel de la fonction, sans réseau, et ce qu'il rend."""
    sent = []

    def request(self, method, url, **kwargs):
        sent.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {}),
                     "json": kwargs.get("json"), "headers": dict(self.headers), "timeout": kwargs.get("timeout")})
        return _Answer(answer)

    with mock.patch.object(granola_client.requests.Session, "request", request):
        returned = getattr(GranolaClient(api_key="grn_k"), name)(**arguments)
    assert len(sent) == 1, name
    return sent[0], returned


def _described(function: dict, arguments: dict) -> tuple:
    call = function["call"]
    path = call["path"].format(**{a: arguments[a] for a in re.findall(r"{(\w+)}", call["path"])})
    query = {api: arguments[a] for api, a in call.get("query", {}).items() if a in arguments}
    body = {api: arguments[a] for api, a in call["body"].items() if a in arguments} if "body" in call else None
    return call["method"], GranolaClient.BASE_URL + path, query, body


def _client_view(sent: dict) -> tuple:
    return sent["method"], sent["url"], sent["params"], sent["json"]


def _every_argument(function: dict) -> dict:
    return {a: VALUES[a] for a in function["input"]["properties"]}


def _required(function: dict) -> dict:
    return {a: VALUES[a] for a in function["input"].get("required", [])}


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


def test_the_description_is_version_1_0_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.1"
    check(description, GRANOLA)


def test_one_function_per_client_method_under_its_name():
    assert set(_functions()) == _public_methods()


def test_the_connector_says_what_the_client_does():
    """Clé en Bearer de la session ; délai de lecture de 60 s, celui du client ; le rythme soutenu de Granola (5 par
    seconde) ; sonde par la liste des webhooks, comme oto 1 ; ni clé de la plateforme ni en-tête constant."""
    connector = _description()["connector"]
    assert connector["base_url"] == GranolaClient.BASE_URL
    assert connector["auth"] == {"kind": "bearer", "token": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "API key", "secret": True}]
    sent, _ = _sent_by_the_client("list_webhook_endpoints", {})
    assert sent["headers"]["Authorization"] == "Bearer grn_k"
    assert sent["timeout"] == (10, connector["timeout_s"]) == granola_client._HTTP_TIMEOUT
    assert connector["rate_limit"] == {"requests": 5, "seconds": 1}
    assert connector["modes"] == ["byo_user", "byo_org"]
    assert connector["probe"] == {"function": "list_webhook_endpoints"}
    assert "headers" not in connector and "query_arrays" not in connector


def test_each_function_takes_the_arguments_its_client_method_names():
    for name, function in _functions().items():
        method = getattr(GranolaClient, name)
        properties = set(function["input"]["properties"])
        assert properties == _named_parameters(name) | FREE_KEYS.get(name, set()), name
        assert set(function["input"].get("required", [])) == _named_parameters(name), name
        for key in FREE_KEYS.get(name, set()):
            assert key in inspect.getdoc(method), (name, key)


def test_each_argument_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        arguments = _every_argument(function)
        jsonschema.validate(arguments, function["input"])
        sent, _ = _sent_by_the_client(name, arguments)
        assert _client_view(sent) == _described(function, arguments), name
        fewest = _required(function) | ({"enabled": True} if name == "update_webhook_endpoint" else {})
        sent, _ = _sent_by_the_client(name, fewest)
        assert _client_view(sent) == _described(function, fewest), name


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent, _ = _sent_by_the_client(name, example["input"])
            assert _client_view(sent) == _described(function, example["input"]), (name, example["title"])


def test_an_empty_folder_filter_reaches_granola():
    """`folder_ids: []` retire le filtre : le client l'envoie (seul `None` est retiré), la description l'admet."""
    function = _functions()["update_webhook_endpoint"]
    arguments = {"webhook_endpoint_id": VALUES["webhook_endpoint_id"], "folder_ids": []}
    assert _valid(function, arguments)
    sent, _ = _sent_by_the_client("update_webhook_endpoint", arguments)
    assert sent["json"] == {"folder_ids": []}


def test_where_the_schema_is_narrower_than_the_client_it_is_named():
    functions = _functions()
    for name, cases in NARROWER.items():
        for case in cases:
            assert not _valid(functions[name], case), (name, case)
            _sent_by_the_client(name, case)


def test_reads_and_writes_are_classified_and_the_delete_is_confirmed():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        assert (function["call"]["method"] != "GET") == (function["class"] != "read"), name
        assert ("confirm" in function) == (function["class"] == "sensitive"), name
        assert "act" not in function, name
    assert "{webhook_endpoint_id}" in functions["delete_webhook_endpoint"]["confirm"]["summary"]


def test_the_lists_page_by_cursor_and_each_call_costs_one_request():
    functions = _functions()
    for name, function in functions.items():
        assert function["cost"] == {"per": "request"}, name
        if name not in PAGINATED:
            assert "pagination" not in function, name
            continue
        items, max_pages = PAGINATED[name]
        assert function["pagination"] == {"kind": "cursor", "request_param": "cursor", "next": "cursor",
                                          "more": "hasMore", "max_pages": max_pages}, name
        assert function["output"] == {"items": items}, name


def test_no_argument_names_a_person():
    """Ni filtre par personne ni courriel : rien n'est donnée personnelle."""
    for name, function in _functions().items():
        assert '"personal": true' not in json.dumps(function["input"]), name


def test_no_webhook_answer_gives_the_signing_secret_description_and_client():
    """Granola ne rend le secret de signature qu'à la création ; il ne revient jamais à l'agent, ni par la description
    (`output.strip`), ni par le client."""
    functions = _functions()
    for name in WEBHOOK_ANSWERS:
        function = functions[name]
        assert "signing secret" in function["description"].lower() and "never given" in function["description"], name
        strip = function["output"]["strip"]
        if name == "list_webhook_endpoints":
            assert strip == ["webhook_endpoints.signing_secret"]
            sample = function["output"]["sample"]
            assert SECRET in str(sample) and SECRET not in str(_shaped(sample, strip))
            answer = {"webhook_endpoints": [dict(ENDPOINT, signing_secret=SECRET)]}
        else:
            assert strip == ["signing_secret"], name
            answer = dict(ENDPOINT, signing_secret=SECRET)
        assert SECRET not in str(_shaped(answer, strip)), name
        _, returned = _sent_by_the_client(name, _required(function) | (
            {"enabled": True} if name == "update_webhook_endpoint" else {}), answer)
        assert SECRET not in str(returned), name
        assert returned == _shaped(answer, strip), name
    assert "output" not in functions["delete_webhook_endpoint"]


def test_the_address_of_an_attached_photo_is_stripped_description_and_client():
    """Quiconque détient l'adresse d'une photo jointe la voit : la description la retire de `get_note`, et le client
    de même ; le reste de la note et de chaque pièce jointe reste."""
    function = _functions()["get_note"]
    assert function["output"]["strip"] == PHOTO_ADDRESS["get_note"]
    assert "never given" in function["description"]
    answer = {"id": "not_1d3tmYTlCICgjy", "attachments": [
        {"id": "att_4y6LduVdwSKC27", "type": "photo", "url": "https://files.example.test/p.jpg"}]}
    shaped = _shaped(answer, function["output"]["strip"])
    assert "files.example.test" not in str(shaped)
    _, returned = _sent_by_the_client("get_note", {"note_id": VALUES["note_id"]}, answer)
    assert returned == shaped == {"id": "not_1d3tmYTlCICgjy", "attachments": [
        {"id": "att_4y6LduVdwSKC27", "type": "photo"}]}
    stripped = {n for n, f in _functions().items() if "strip" in f.get("output", {})}
    assert stripped == WEBHOOK_ANSWERS | set(PHOTO_ADDRESS)


def test_the_401_marks_the_account_and_429_and_5xx_are_retried_as_oto_1_says():
    """Le client ne nomme aucun refus (`raise_for_upstream`). Le 401 dit une clé invalide : il marque
    `credential_rejected` ; le 403 dit un droit, pas une clé. Le 429 et les 5xx se rejouent, comme le dit oto 1."""
    errors = _description()["connector"]["errors"]
    assert [(e["status"], e["code"], e["marks_account"]) for e in errors if "marks_account" in e] == [
        (401, "key_refused", "credential_rejected")]
    assert not [e for e in errors if "body" in e]
    assert {e["code"] for e in errors if e["retryable"]} == {"rate_limited", "upstream_unavailable"}
    assert next(e for e in errors if e["code"] == "upstream_unavailable")["status"] == [500, 502, 503, 504]
    refusals = {n: f["refusals"] for n, f in _functions().items() if "refusals" in f}
    assert refusals == {"get_note": [{"code": "transcript_too_large", "when": 413, "message": refusals["get_note"][0][
        "message"]}]}
    transport = inspect.getsource(GranolaClient._request)
    assert "raise_for_upstream(resp" in transport and "status_code" not in transport


def test_the_audit_log_is_not_described():
    for function in _functions().values():
        assert "audit" not in function["call"]["path"], function["name"]


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), GRANOLA)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "granola.{name}",' in module.source, name
    assert not module.skipped
    table = python.declared([checked])["granola"]
    assert set(table) == set(_functions())
    assert DECLARED.get("granola") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
