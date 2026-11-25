"""La description `salesforce` 1.0.0 : celle du client existant (`oto.tools.salesforce`), sans rien y changer.

Une fonction par méthode du client qui envoie une seule requête dont le format sait dire le corps, sous son nom, avec ses
noms d'arguments et les paramètres que le client envoie à Salesforce, sous `{instance_url}/services/data/v60.0`.
Écarts nommés, gardés ici :
- le consentement : le client reçoit un consentement déjà donné (application, jeton de rafraîchissement, adresse de
  connexion) ; la description dit celui d'oto 1 (`oauth2_user`, PKCE), plus le droit `id`, qu'exige la lecture de
  l'identité à l'adresse fixe du service d'identité de Salesforce (`login.salesforce.com`), quand un bac à sable
  répond sur `test.salesforce.com` ;
- `query_more` prend le dernier segment de `nextRecordsUrl` (`QUERY_LOCATOR`), le client le chemin entier ;
- `delete_record` : le client fabrique `{id, success}` sur le 204 de Salesforce, que l'hôte ne fabrique pas ;
- `describe` : la sortie garde les clés de premier niveau que garde oto 1 (`output.projection`), le client rend tout.
Restent au client écrit à la main, nommés dans `LEFT_OUT` : les écritures dont le corps est un enregistrement libre et
typé, la liste qui compose sa requête SOQL, les notes (deux requêtes). Les gardes ci-dessous lisent le client lui-même :
un endpoint, un argument ou un paramètre qu'il ajoute sans la description fait échouer le test.
"""
from __future__ import annotations

import copy
import inspect
import time
from unittest import mock

import jsonschema
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.salesforce import client as sf_client
from oto.tools.salesforce.client import SalesforceClient

SALESFORCE = CONNECTORS / "salesforce" / "connector.yaml"
LOGIN = "https://acme-dev.my.salesforce.com"
INSTANCE = "https://acme-dev.my.salesforce.com"

# Les méthodes publiques du client qui ne sont pas une fonction de la description, et pourquoi.
_TYPED_BODY = "free keys of a simple value: format change decided on 2026-10-22, salesforce 1.1.0"
LEFT_OUT = {
    "create_record": _TYPED_BODY,
    "update_record": _TYPED_BODY,
    "upsert_record": _TYPED_BODY,
    "create_records": _TYPED_BODY,
    "update_records": _TYPED_BODY,
    "list_records": "the client composes the SOQL text from fields, where and limit, with default fields: query serves it",
    "list_notes": "two requests (ContentDocumentLink, then ContentNote) and the content decoded from base64",
    "create_note": "two requests (ContentNote, then ContentDocumentLink) and the content encoded in base64",
}
TYPED_BODY = {"create_record", "update_record", "upsert_record", "create_records", "update_records"}
# Les arguments d'une méthode que sa fonction prend sous un autre nom et une autre forme : le dernier segment de
# `nextRecordsUrl` (la fonction), le chemin entier (le client).
QUERY_LOCATOR = {"query_more": ("query_locator", "next_records_url")}
# Ce que fait chaque fonction : une écriture mal classée est un défaut grave.
CLASSES = {
    "read": {"describe", "get_record", "query", "query_more", "search"},
    "write": set(),
    # Un enregistrement à la corbeille, avec ce qui en dépend.
    "sensitive": {"delete_record"},
}
# Ce qui peut désigner une personne : une requête SOQL ou SOSL (un nom, un courriel en condition ou en recherche).
PERSONAL = {"query": {"soql"}, "search": {"sosl"}}
# Les droits du consentement d'oto 1 (`oto_mcp/auth/salesforce.py`, `SCOPES = "api refresh_token"`).
OTO1_SCOPES = ["api", "refresh_token"]
# Les clés de premier niveau que garde la projection d'oto 1 (`_DESCRIBE_OBJECT_KEYS`, plus `fields`).
OTO1_DESCRIBE_KEYS = ["name", "label", "labelPlural", "custom", "createable", "updateable", "deletable", "queryable",
                      "searchable", "keyPrefix", "fields"]

VALUES = {
    "sobject": "Contact", "record_id": "003000000000001AAA", "fields": "Id,FirstName,Email",
    "soql": "SELECT Id, Email FROM Contact WHERE LastName = 'Martin'",
    "sosl": "FIND {Martin} IN NAME FIELDS RETURNING Contact(Id, Name)",
    "query_locator": "01g000000000001AAA-2000",
}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(SALESFORCE.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(SalesforceClient, inspect.isfunction) if not n.startswith("_")}


def _parameters(name: str) -> dict[str, inspect.Parameter]:
    return {p: v for p, v in inspect.signature(getattr(SalesforceClient, name)).parameters.items() if p != "self"}


class _Answer:
    def __init__(self, body=None, status_code: int = 200):
        self.status_code = status_code
        self.headers = {}
        self._body = body if body is not None else {"totalSize": 0, "done": True, "records": []}
        self.content = b"{}" if body is not False else b""

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


TOKEN_ANSWER = {"access_token": "access-0", "instance_url": INSTANCE, "id": "https://login.salesforce.com/id/00D/005",
                "token_type": "Bearer", "issued_at": "1767225600000", "signature": "c2lnbmF0dXJl"}


def _client_calls(name: str, arguments: dict, answers: list | None = None, token: dict | None = None) -> dict:
    """Ce que la méthode du client envoie, sans réseau : la demande de jeton et chaque requête à l'instance."""
    posts, sent = [], []
    answers = list(answers or [])

    def post(url, **kwargs):
        posts.append({"url": url, "data": dict(kwargs.get("data") or {})})
        return _Answer(token or TOKEN_ANSWER)

    def request(method, url, **kwargs):
        sent.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {}),
                     "json": kwargs.get("json"), "headers": dict(kwargs.get("headers") or {})})
        return answers.pop(0) if answers else _Answer()

    client = SalesforceClient(client_id="cid-0", client_secret="csecret-0", refresh_token="rtok-0", login_url=LOGIN)
    with mock.patch.dict(sf_client._TOKEN_CACHE, clear=True), \
            mock.patch.object(sf_client.requests, "post", post), \
            mock.patch.object(sf_client.requests, "request", request):
        result = getattr(client, name)(**arguments)
    return {"posts": posts, "sent": sent, "client": client, "result": result}


def _client_arguments(name: str, arguments: dict) -> dict:
    """Les arguments de la méthode du client pour ceux d'une fonction : le chemin entier de `nextRecordsUrl` pour le
    dernier segment."""
    if name in QUERY_LOCATOR:
        ours, theirs = QUERY_LOCATOR[name]
        arguments = {k: v for k, v in arguments.items() if k != ours} | {
            theirs: f"/services/data/{SalesforceClient.API_VERSION}/query/{arguments[ours]}"}
    return arguments


def _sent_by_the_client(name: str, arguments: dict) -> dict:
    sent = _client_calls(name, _client_arguments(name, arguments))["sent"]
    assert len(sent) == 1, name
    return sent[0]


def _base_url() -> str:
    return _description()["connector"]["base_url"].replace("{instance_url}", INSTANCE)


def _described(function: dict, arguments: dict) -> tuple:
    """La requête que dit la description : méthode, adresse (instance comprise), query, corps JSON."""
    call = function["call"]
    path = call["path"]
    for argument, value in arguments.items():
        path = path.replace("{" + argument + "}", str(value))
    query = {api: str(arguments[a]) for api, a in call.get("query", {}).items() if a in arguments}
    body = {api: arguments[a] for api, a in call["body"].items() if a in arguments} if "body" in call else None
    return call["method"], _base_url() + path, query, body


def _client_view(sent: dict) -> tuple:
    return sent["method"], sent["url"], {k: str(v) for k, v in sent["params"].items()}, sent["json"]


def _values(function: dict) -> dict:
    return {argument: VALUES[argument] for argument in function["input"]["properties"]}


def test_the_description_is_version_1_0_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.1"
    check(description, SALESFORCE)


def test_each_method_of_the_client_is_a_function_or_named_with_its_reason():
    assert _public_methods() - set(_functions()) == set(LEFT_OUT)
    assert not set(LEFT_OUT) & set(_functions())
    assert set(_functions()) <= _public_methods()


def test_the_api_version_and_the_instance_are_the_clients():
    """`base_url` cite l'instance que rend la réponse de jeton (`from_token`), suivie de la version du client."""
    connector = _description()["connector"]
    assert connector["api_version"] == SalesforceClient.API_VERSION == "v60.0"
    assert connector["base_url"] == "{instance_url}/services/data/" + SalesforceClient.API_VERSION
    assert connector["auth"]["from_token"] == ["instance_url"]
    other = "https://acme-dev--uat.sandbox.my.salesforce.com"
    calls = _client_calls("query", {"soql": "SELECT Id FROM Account"}, token={**TOKEN_ANSWER, "instance_url": other})
    assert calls["sent"][0]["url"] == f"{other}/services/data/v60.0/query/", "the instance comes from the token answer"


def test_the_consent_is_the_one_the_client_renews():
    """Le jeton se renouvelle à `{login_url}/services/oauth2/token`, grant `refresh_token`, l'application dans le corps du
    formulaire ; sans `expires_in`, une heure ; un jeton tourné remplace l'ancien ; l'adresse de connexion est un réglage
    `url`, le défaut du client étant `login.salesforce.com`."""
    connector = _description()["connector"]
    auth = connector["auth"]
    assert auth["kind"] == "oauth2_user" and "credential" not in connector
    assert connector["settings"] == [{"name": "login_url", "label": "Login URL (your My Domain)", "type": "url"}]
    assert auth["token_url"] == "{login_url}/services/oauth2/token"
    assert auth["authorize_url"] == "{login_url}/services/oauth2/authorize"
    assert auth["client_auth"] == "body" and auth["refresh"] == "refresh_token" and auth["pkce"] == "S256"
    calls = _client_calls("query", {"soql": "SELECT Id FROM Account"})
    assert calls["posts"] == [{"url": f"{LOGIN}/services/oauth2/token", "data": {
        "grant_type": "refresh_token", "client_id": "cid-0", "client_secret": "csecret-0", "refresh_token": "rtok-0"}}]
    assert calls["sent"][0]["headers"] == {"Authorization": "Bearer access-0"}
    assert SalesforceClient(client_id="a", client_secret="b", refresh_token="c").login_url == "https://login.salesforce.com"
    # Une heure, faute d'`expires_in` dans la réponse de Salesforce.
    before = time.time()
    lifetime = calls["client"]._token_expires_at - before
    assert auth["expires_in_default"] == 3600 and 3590 < lifetime <= 3600
    # La rotation : le jeton rendu remplace celui qu'on a lu.
    rotated = _client_calls("query", {"soql": "SELECT Id FROM Account"}, token={**TOKEN_ANSWER, "refresh_token": "rtok-1"})
    assert auth["rotates"] is True and rotated["client"].refresh_token == "rtok-1"
    # Les droits d'oto 1, plus celui de l'identité.
    assert auth["scopes"] == [*OTO1_SCOPES, "id"]
    assert auth["identity"] == {"url": "https://login.salesforce.com/services/oauth2/userinfo",
                                "path": "preferred_username", "scopes": ["id"]}
    assert sf_client._HTTP_TIMEOUT == (10, 60) and connector["timeout_s"] == 60
    assert "rate_limit" not in connector and "probe" not in connector
    assert connector["modes"] == ["byo_user", "byo_org"]


def test_a_401_renews_the_token_and_retries_once_as_the_host_does():
    """Le client renouvelle sur un 401 et rejoue une fois : l'hôte fait de même pour un `oauth2_user` ; le 401 qui
    reste n'est pas marqué (l'hôte relève le consentement)."""
    calls = _client_calls("query", {"soql": "SELECT Id FROM Account"},
                          answers=[_Answer([{"errorCode": "INVALID_SESSION_ID"}], status_code=401), _Answer()])
    assert len(calls["posts"]) == 2 and len(calls["sent"]) == 2
    errors = _description()["connector"]["errors"]
    assert not [e for e in errors if "marks_account" in e]
    assert [e for e in errors if e["status"] == 401] == [{
        "status": 401, "code": "authorization_expired", "retryable": False,
        "message": "Salesforce no longer accepts this authorization (expired or revoked): the person must connect again."}]


def test_each_function_takes_the_arguments_of_its_client_method():
    for name, function in _functions().items():
        properties = set(function["input"]["properties"])
        named = set(_parameters(name))
        if name in QUERY_LOCATOR:
            ours, theirs = QUERY_LOCATOR[name]
            properties, named = properties - {ours}, named - {theirs}
        assert properties == named, name
        required = set(function["input"].get("required", []))
        assert required == {p for p in named if _parameters(name)[p].default is inspect.Parameter.empty} | (
            {QUERY_LOCATOR[name][0]} if name in QUERY_LOCATOR else set()), name


def test_each_argument_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        arguments = _values(function)
        jsonschema.validate(arguments, function["input"])
        assert _client_view(_sent_by_the_client(name, arguments)) == _described(function, arguments), name
        required = {a: VALUES[a] for a in function["input"].get("required", [])}
        assert _client_view(_sent_by_the_client(name, required)) == _described(function, required), name


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent = _sent_by_the_client(name, example["input"])
            assert _client_view(sent) == _described(function, example["input"]), (name, example["title"])


def test_query_more_reads_the_locator_of_next_records_url():
    """La page suivante : le dernier segment de `nextRecordsUrl` donne l'adresse exacte où le client la lit."""
    next_records_url = "/services/data/v60.0/query/01g000000000002AAA-4000"
    first = _client_calls("query", {"soql": "SELECT Id FROM Contact"},
                          answers=[_Answer({"totalSize": 4500, "done": False, "records": [],
                                            "nextRecordsUrl": next_records_url})])
    url = first["result"]["nextRecordsUrl"]
    function = _functions()["query_more"]
    locator = url.rsplit("/query/", 1)[1]
    jsonschema.validate({"query_locator": locator}, function["input"])
    assert _described(function, {"query_locator": locator})[1] == _client_calls(
        "query_more", {"next_records_url": url})["sent"][0]["url"]
    assert all("pagination" not in f for f in _functions().values())


def test_the_typed_body_and_the_composed_query_and_the_notes_stay_with_the_client():
    """Les motifs de `LEFT_OUT`, lus sur le client : un corps qui porte des nombres, des booléens et `null` tels quels ;
    une requête SOQL composée de trois arguments ; deux requêtes par note."""
    record = {"LastName": "Martin", "NumberOfEmployees": 12, "HasOptedOutOfEmail": True, "Birthdate": None}
    sent = {name: _client_calls(name, arguments)["sent"] for name, arguments in {
        "create_record": {"sobject": "Contact", "data": record},
        "update_record": {"sobject": "Contact", "record_id": VALUES["record_id"], "data": record},
        "upsert_record": {"sobject": "Contact", "external_id_field": "Ext__c", "external_id": "x-1", "data": record},
        "create_records": {"sobject": "Contact", "items": [record]},
        "update_records": {"sobject": "Contact", "items": [{"Id": VALUES["record_id"], **record}]},
    }.items()}
    assert set(sent) == TYPED_BODY <= set(LEFT_OUT)
    for name, requests in sent.items():
        body = requests[0]["json"]
        written = body["records"][0] if "records" in body else body
        assert {k: written[k] for k in record} == record, name
    composed = _client_calls("list_records", {"sobject": "Lead", "where": "Status = 'Open'", "limit": 5})["sent"]
    assert composed[0]["params"] == {"q": f"SELECT {SalesforceClient.DEFAULT_FIELDS['Lead']} FROM Lead WHERE "
                                          "Status = 'Open' LIMIT 5"}
    links = _Answer({"records": [{"ContentDocumentId": "069000000000001AAA"}]})
    assert len(_client_calls("list_notes", {"parent_id": VALUES["record_id"]}, answers=[links])["sent"]) == 2
    note = _Answer({"id": "069000000000001AAA", "success": True})
    assert len(_client_calls("create_note", {"parent_id": VALUES["record_id"], "title": "Call", "body": "Notes"},
                             answers=[note])["sent"]) == 2


def test_delete_record_answers_what_the_client_synthesizes():
    """Salesforce répond 204 sans corps : le client rend `{id, success}`, que la description ne fabrique pas."""
    calls = _client_calls("delete_record", {"sobject": "Lead", "record_id": "00Q000000000001AAA"},
                          answers=[_Answer(False, status_code=204)])
    assert calls["result"] == {"id": "00Q000000000001AAA", "success": True}
    assert "output" not in _functions()["delete_record"]


def test_describe_keeps_the_keys_oto_1_keeps():
    assert _functions()["describe"]["output"] == {"projection": OTO1_DESCRIBE_KEYS}
    assert {n for n, f in _functions().items() if "output" in f} == {"describe"}


def test_reads_and_writes_are_classified_and_every_act_is_served_on_the_act_side():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        assert (function["call"]["method"] == "GET") == (function["class"] == "read"), name
        assert ("confirm" in function) == (function["class"] == "sensitive"), name
        assert function.get("act", function["class"] != "read") == (function["class"] != "read"), name
        assert function["cost"] == {"per": "request"}, name
    assert {n for n in functions if n.startswith("delete")} <= CLASSES["sensitive"]


def test_what_designates_a_person_is_personal():
    for name, function in _functions().items():
        personal = {a for a, s in function["input"]["properties"].items() if s.get("personal") is True}
        assert personal == PERSONAL.get(name, set()), name


def test_the_errors_tell_a_statement_and_the_daily_allocation_by_error_code():
    """Salesforce refuse en liste `[{message, errorCode}]` : sous un 400, une requête mal formée ; sous un 403, l'allocation
    du jour épuisée (que le client ne rejoue pas : un quota sur 24 heures, pas un débit)."""
    errors = _description()["connector"]["errors"]
    conditioned = {e["code"]: e["body"] for e in errors if "body" in e}
    assert conditioned == {
        "invalid_statement": {"path": "0.errorCode", "equals": ["MALFORMED_QUERY", "MALFORMED_SEARCH", "INVALID_FIELD",
                                                               "INVALID_TYPE"]},
        "api_limit_reached": {"path": "0.errorCode", "equals": ["REQUEST_LIMIT_EXCEEDED"]},
    }
    assert "REQUEST_LIMIT_EXCEEDED" in inspect.getsource(SalesforceClient._request)
    by_code = {e["code"]: e for e in errors}
    assert by_code["api_limit_reached"]["retryable"] is False and by_code["upstream_unavailable"]["retryable"] is True
    assert {(e["status"] if isinstance(e["status"], int) else tuple(e["status"])) for e in errors} == {
        400, 401, 403, 404, (500, 502, 503)}


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), SALESFORCE)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "salesforce.{name}",' in module.source, name
    for name in LEFT_OUT:
        assert f'"salesforce.{name}"' not in module.source, name
    assert '"fromToken": ["instance_url"]' in module.source
    table = python.declared([checked])["salesforce"]
    assert set(table) == set(_functions())
    assert DECLARED.get("salesforce") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"


def test_no_function_names_a_secret_and_the_examples_are_invented():
    for name, function in _functions().items():
        assert not {"client_id", "client_secret", "refresh_token", "access_token", "login_url", "instance_url"} & set(
            function["input"]["properties"]), name
        assert "salesforce.com" not in str(function["examples"]), name
