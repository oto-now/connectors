"""La description `linear` 1.0.0 : celle du client existant (`oto.tools.linear`), en corps emboîté.

Linear est une API GraphQL : un seul `POST /graphql`, un corps `{query, variables}`. Chaque fonction décrite envoie
le texte constant de sa méthode (`constants.body.query`, à l'espacement près, sans valeur en GraphQL) et place ses
arguments sous `variables`, l'objet d'entrée sous `variables.input` ; un argument absent ne part pas. Les gardes
ci-dessous lisent le client lui-même : pour chaque fonction, le corps que l'hôte construit (constante, puis chaque
argument donné à son chemin) est exactement celui du client pour les mêmes arguments, arguments absents compris, chacun
seul et tous ensemble. Restent au client, nommées (`tests/test_linear_surface_frozen.py`) : les méthodes dont le texte
dépend des arguments donnés, la sélection libre `fields` et le secret de signature d'un webhook.
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
from oto.tools.linear import client as linear_client
from oto.tools.linear.client import LinearClient

LINEAR = CONNECTORS / "linear" / "connector.yaml"

# Les arguments que la méthode nomme et que la fonction ne prend pas, et pourquoi.
FIELDS = "fields"  # écrit par le client dans le texte de l'opération : une sélection libre
LEFT_TO_THE_CLIENT_ARGUMENTS = {"create_webhook": {FIELDS, "secret"}}  # un secret écrit par l'agent passerait par lui
CLASSES = {
    "read": {"get_issue", "get_comment", "list_comments", "get_project", "get_team", "list_teams",
             "list_workflow_states", "get_cycle", "get_label", "get_viewer", "get_user", "list_users"},
    "write": {"create_issue", "update_issue", "create_comment", "update_comment", "create_project", "update_project",
              "create_label", "create_webhook", "update_webhook"},
    # Archiver (réversible dans Linear) et supprimer.
    "sensitive": {"archive_issue", "delete_issue", "delete_comment", "delete_project", "delete_label",
                  "delete_webhook"},
}
PAGINATED = {"list_comments": "comments", "list_teams": "teams", "list_workflow_states": "workflowStates",
             "list_users": "users"}
# Où l'agent lit la réponse : le client rend `data.<champ>` seul.
ANSWER = {
    "get_issue": "issue", "create_issue": "issueCreate", "update_issue": "issueUpdate", "archive_issue": "issueArchive",
    "delete_issue": "issueDelete", "get_comment": "comment", "list_comments": "comments",
    "create_comment": "commentCreate", "update_comment": "commentUpdate", "delete_comment": "commentDelete",
    "get_project": "project", "create_project": "projectCreate", "update_project": "projectUpdate",
    "delete_project": "projectDelete", "get_team": "team", "list_teams": "teams",
    "list_workflow_states": "workflowStates", "get_cycle": "cycle", "get_label": "issueLabel",
    "create_label": "issueLabelCreate", "delete_label": "issueLabelDelete", "get_viewer": "viewer", "get_user": "user",
    "list_users": "users", "create_webhook": "webhookCreate", "update_webhook": "webhookUpdate",
    "delete_webhook": "webhookDelete",
}
PERSONAL = {"assignee_id", "lead_id", "user_id"}
# Plus étroit que le client, qui laisse passer : chaque cas est refusé par le schéma, envoyé par le client.
NARROWER = {
    "list_comments": [{"issue_id": "i-0", "first": 251}, {"issue_id": "i-0", "first": 0}],
    "create_issue": [{"title": "t", "team_id": "t-0", "priority": 5},
                     {"title": "t", "team_id": "t-0", "due_date": "15/11/2026"}],
    "update_issue": [{"issue_id": "i-0"}],
    "update_project": [{"project_id": "p-0"}, {"project_id": "p-0", "target_date": "2026-12"}],
    "update_webhook": [{"webhook_id": "w-0"}, {"webhook_id": "w-0", "url": "http://hooks.example.test/l"}],
    "create_webhook": [{"url": "https://hooks.example.test/l"},
                       {"url": "https://hooks.example.test/l", "resource_types": []},
                       {"url": "https://hooks.example.test/l", "resource_types": ["Issue"], "all_public_teams": False}],
    "create_label": [{"name": "n", "color": "red"}],
}
VALUES = {
    "issue_id": "ENG-123", "title": "Login fails", "team_id": "t-0", "description": "On Safari.", "assignee_id": "u-0",
    "state_id": "s-0", "priority": 0, "label_ids": ["l-0", "l-1"], "project_id": "p-0", "cycle_id": "cy-0",
    "parent_id": "i-9", "due_date": "2026-11-15", "estimate": 3, "comment_id": "c-0", "body": "Reproduced.",
    "first": 100, "after": "cursor-0", "name": "Checkout", "team_ids": ["t-0", "t-1"], "status_id": "ps-0",
    "lead_id": "u-1", "target_date": "2026-12-18", "label_id": "l-0", "color": "#EB5757", "user_id": "u-2",
    "url": "https://hooks.example.test/linear", "resource_types": ["Issue", "Comment"], "enabled": False,
    "all_public_teams": True, "webhook_id": "w-0",
}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(LINEAR.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _parameters(method: str) -> dict[str, inspect.Parameter]:
    return {p: v for p, v in inspect.signature(getattr(LinearClient, method)).parameters.items() if p != "self"}


def _squeezed(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _Answer:
    def __init__(self, body, status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}
        self.text = str(body)

    def json(self):
        return copy.deepcopy(self._body)


def _sent_by_the_client(name: str, arguments: dict, answer=None, status_code=200) -> tuple[dict, object]:
    """Ce que le client envoie pour un appel de la fonction, sans réseau, et ce qu'il rend."""
    sent = []

    def post(self, url, **kwargs):
        sent.append({"url": url, "json": copy.deepcopy(kwargs.get("json")), "timeout": kwargs.get("timeout"),
                     "headers": dict(self.headers)})
        return _Answer({"data": {ANSWER[name]: {"id": "x-0"}}} if answer is None else answer, status_code)

    with mock.patch.object(linear_client.requests.Session, "post", post):
        returned = getattr(LinearClient(api_key="k"), name)(**arguments)
    assert len(sent) == 1, name
    return sent[0], returned


def _placed(table: dict, arguments: dict, into: dict) -> None:
    """Chaque argument donné à son chemin, les parents créés au besoin ; un argument absent ne crée rien."""
    for key, value in table.items():
        if isinstance(value, dict):
            child = into.get(key, {})
            _placed(value, arguments, child)
            if child:
                into[key] = child
        elif value in arguments:
            into[key] = arguments[value]


def _described(function: dict, arguments: dict) -> tuple[str, dict]:
    """La requête que l'hôte construit : la constante du corps, puis la table emboîtée."""
    call = function["call"]
    body = copy.deepcopy(call.get("constants", {}).get("body", {}))
    _placed(call.get("body", {}), arguments, body)
    return _description()["connector"]["base_url"] + call["path"], body


def _client_view(sent: dict) -> tuple[str, dict]:
    body = dict(sent["json"])
    body["query"] = _squeezed(body["query"])
    return sent["url"], body


def _required(function: dict) -> dict:
    return {a: VALUES[a] for a in function["input"].get("required", [])}


def _valid(function: dict, arguments: dict) -> bool:
    return jsonschema.Draft202012Validator(function["input"]).is_valid(arguments)


def _argument_sets(function: dict):
    """Tous les arguments ; le moins possible ; chaque argument facultatif seul à côté des requis."""
    every = {a: VALUES[a] for a in function["input"]["properties"]}
    yield every
    required = _required(function)
    optional = [a for a in function["input"]["properties"] if a not in required]
    if _valid(function, required):
        yield required
    for argument in optional:
        yield required | {argument: VALUES[argument]}


def test_the_description_is_version_1_0_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.1"
    check(description, LINEAR)


def test_the_connector_says_what_the_client_does():
    """La clé telle quelle dans `Authorization`, sans `Bearer` ; délai de lecture de 60 s, celui du client ; les deux
    modes d'oto 1, sans clé de la plateforme ; sonde par le titulaire de la clé, comme oto 1."""
    connector = _description()["connector"]
    for function in _functions().values():
        assert connector["base_url"] + function["call"]["path"] == LinearClient.ENDPOINT
        assert function["call"]["method"] == "POST"
    assert connector["auth"] == {"kind": "api_key", "in": "header", "name": "Authorization", "key": "api_key"}
    assert "prefix" not in connector["auth"]
    assert connector["credential"] == [{"name": "api_key", "label": "API key", "secret": True}]
    sent, _ = _sent_by_the_client("get_viewer", {})
    assert sent["headers"]["Authorization"] == "k"
    assert sent["timeout"] == (10, connector["timeout_s"]) == linear_client._HTTP_TIMEOUT
    assert connector["modes"] == ["byo_user", "byo_org"]
    assert connector["probe"] == {"function": "get_viewer", "non_empty": ["data.viewer.id"]}
    assert "headers" not in connector and "query_arrays" not in connector


def test_each_function_takes_the_arguments_its_method_names_save_those_named():
    for name, function in _functions().items():
        left = LEFT_TO_THE_CLIENT_ARGUMENTS.get(name, {FIELDS})
        assert set(function["input"]["properties"]) == set(_parameters(name)) - left, name
        required = {p for p, v in _parameters(name).items() if v.default is inspect.Parameter.empty}
        assert set(function["input"].get("required", [])) >= required, name


def test_each_body_is_the_clients_arguments_absent_included():
    """Le corps de l'hôte (constante + table emboîtée) est celui du client : tous les arguments, le moins possible,
    chaque argument facultatif seul. Le texte de la requête est celui du client, à l'espacement près."""
    for name, function in _functions().items():
        for arguments in _argument_sets(function):
            assert _valid(function, arguments), (name, arguments)
            sent, _ = _sent_by_the_client(name, arguments)
            assert _client_view(sent) == _described(function, arguments), (name, sorted(arguments))


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent, _ = _sent_by_the_client(name, example["input"])
            assert _client_view(sent) == _described(function, example["input"]), (name, example["title"])


def test_the_query_text_is_the_clients_default_selection():
    """La constante est le texte du client sans `fields` : sa sélection par défaut, jamais une sélection libre."""
    for name, function in _functions().items():
        sent, _ = _sent_by_the_client(name, {a: VALUES[a] for a in function["input"]["properties"]})
        assert function["call"]["constants"]["body"]["query"] == _squeezed(sent["json"]["query"]), name
        with_fields, _ = _sent_by_the_client(name, {a: VALUES[a] for a in function["input"].get("required", [])}
                                             | ({"enabled": True} if name == "update_webhook" else {})
                                             | ({"title": "t"} if name == "update_issue" else {})
                                             | ({"name": "n"} if name == "update_project" else {})
                                             | ({FIELDS: "id"} if FIELDS in _parameters(name) else {}))
        if FIELDS in _parameters(name):
            assert _squeezed(with_fields["json"]["query"]) != function["call"]["constants"]["body"]["query"], name


def test_where_the_schema_is_narrower_than_the_client_it_is_named():
    functions = _functions()
    for name, cases in NARROWER.items():
        for case in cases:
            assert not _valid(functions[name], case), (name, case)
            _sent_by_the_client(name, case)


def test_reads_and_writes_are_classified_and_the_deletes_are_confirmed():
    functions = _functions()
    assert set(functions) == set().union(*CLASSES.values())
    for kind, names in CLASSES.items():
        for name in names:
            function = functions[name]
            assert function["class"] == kind and "act" not in function, name
            assert ("confirm" in function) == (kind == "sensitive"), name
            if kind == "sensitive":
                for argument in re.findall(r"{(\w+)}", function["confirm"]["summary"]):
                    assert argument in function["input"].get("required", []), name


def test_the_lists_page_by_cursor_under_variables():
    functions = _functions()
    for name, function in functions.items():
        if name not in PAGINATED:
            assert "pagination" not in function and "output" not in function, name
            continue
        field = PAGINATED[name]
        assert function["call"]["body"]["variables"]["after"] == "after"
        assert function["call"]["body"]["variables"]["first"] == "first"
        assert function["pagination"] == {
            "kind": "cursor", "request_param": "after", "next": f"data.{field}.pageInfo.endCursor",
            "more": f"data.{field}.pageInfo.hasNextPage", "max_pages": 20}
        assert function["output"] == {"items": f"data.{field}.nodes"}
        assert "pageInfo { hasNextPage endCursor }" in function["call"]["constants"]["body"]["query"]


def test_each_answer_is_read_under_data_and_a_missing_one_is_refused():
    """La réponse est `{data: {<champ>}}` ; le client rend le champ seul. Une erreur GraphQL sous un 200 que la table
    ne nomme pas laisse le champ nul : `expect` la refuse."""
    for name, function in _functions().items():
        field = ANSWER[name]
        assert function["expect"] == [{"kind": "non_empty", "refusal": "no_result", "path": f"data.{field}"}], name
        assert [r["code"] for r in function["refusals"]] == ["no_result"], name
        assert f"data.{field}" in function["description"], name
        _, returned = _sent_by_the_client(name, _required(function) | (
            {"enabled": True} if name == "update_webhook" else {}) | (
            {"title": "t"} if name == "update_issue" else {}) | ({"name": "n"} if name == "update_project" else {}))
        assert returned == {"id": "x-0"}, name


def test_an_argument_that_names_a_person_is_personal():
    for name, function in _functions().items():
        for argument, schema in function["input"]["properties"].items():
            assert schema.get("personal", False) == (argument in PERSONAL), (name, argument)


def test_no_secret_and_no_open_address_is_selected():
    """Aucune sélection ne lit le secret d'un webhook, une adresse de fichier ou d'avatar ; la sélection libre reste au
    client."""
    for name, function in _functions().items():
        words = set(re.findall(r"\w+", function["call"]["constants"]["body"]["query"]))
        assert not words & {"secret", "uploadUrl", "avatarUrl", "attachments", "assetUrl", "imageUrl"}, name
        assert FIELDS not in function["input"]["properties"], name


def test_graphql_errors_are_read_as_the_client_reads_them():
    """`AUTHENTICATION_ERROR` marque la clé refusée, sous un 200, un 400 ou un 401 ; `RATELIMITED` (400) ne se rejoue
    pas, le client disant d'attendre la remise à zéro horaire ; les 5xx se rejouent, comme le dit oto 1."""
    errors = _description()["connector"]["errors"]
    marks = [(e["status"], e.get("body"), e["marks_account"]) for e in errors if "marks_account" in e]
    assert marks == [([200, 400, 401], {"path": "errors.0.extensions.code", "equals": ["AUTHENTICATION_ERROR"]},
                      "credential_rejected"), (401, None, "credential_rejected")]
    limited = [e for e in errors if e["code"] == "rate_limited"]
    assert limited[0]["body"] == {"path": "errors.0.extensions.code", "equals": ["RATELIMITED"]}
    assert 400 in limited[0]["status"]
    assert {e["retryable"] for e in limited} == {False}
    assert {e["code"] for e in errors if e["retryable"]} == {"upstream_unavailable"}
    for status, code, raised in ((200, "AUTHENTICATION_ERROR", linear_client.LinearGraphQLError),
                                 (400, "RATELIMITED", linear_client.LinearRateLimited)):
        answer = {"errors": [{"message": "x", "extensions": {"code": code}}], "data": None}
        try:
            _sent_by_the_client("get_viewer", {}, answer, status)
        except raised as error:
            assert error.code == code
        else:
            raise AssertionError(code)


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), LINEAR)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "linear.{name}",' in module.source
    assert not module.skipped
    assert python.declared([checked]).get("linear", {}) == {}
