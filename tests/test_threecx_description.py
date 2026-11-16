"""La description `threecx` 1.1.0 : celle du client existant (`oto.tools.threecx`), sans rien y changer ; 1.1.0 ne dit
que l'arrivée de la configuration au client, que la description ne décrit pas (`CONFIGURATION`).

Une fonction par méthode du client, sous son nom, avec ses noms d'arguments, le chemin OData et la query qu'il envoie
à la XAPI du standard (`{adresse}/xapi/v1`), l'adresse du standard étant un réglage `url` du compte. Seul le client
d'API (`client_id`/`client_secret`, `grant_type=client_credentials` à `{adresse}/connect/token`) est décrit : la
connexion par compte utilisateur (`/webclient/api/Login/GetAccessToken`, un corps JSON dont la réponse porte le jeton
sous `Token.access_token`) reste au client écrit à la main, comme `recorded_only`, un filtre que le client applique
après la pagination. L'audio d'un enregistrement se rend en fichier (`output.file`, `from: body`) ; le chemin
`RecordingUrl` d'une ligne du journal ne sort pas. Les gardes ci-dessous lisent le client lui-même : un endpoint, un
argument ou une clé qu'il ajoute ou change sans la description fait échouer le test.
"""
from __future__ import annotations

import copy
import inspect
import re
from datetime import datetime

import jsonschema
import pytest
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.threecx import auth as threecx_auth
from oto.tools.threecx import client as threecx_client
from oto.tools.threecx.client import ThreeCXClient

THREECX = CONNECTORS / "threecx" / "connector.yaml"
BASE = "https://pbx.example.test"

# Les arguments d'une méthode que sa fonction ne prend pas, et pourquoi.
ARGUMENTS_LEFT_OUT = {
    "list_calls": {"recorded_only": "a filter the client applies to the rows after paging, on its own side"},
}
# Ce que le client envoie quand l'appel ne le donne pas, et que la description ne déclare pas en défaut : l'hôte
# n'envoie rien pour un argument absent, et le standard ne saute alors aucune ligne.
CLIENT_FILLS = {"list_calls": {"skip": 0}}
# Ce que le client remplit et que la description exige : sans `$top`, rien ne borne la page.
REQUIRED_HERE = {"list_calls": {"top": 100}}
# Une valeur par argument, pour un appel qui les donne tous.
VALUES = {"date_from": "2026-10-01T00:00:00Z", "date_to": "2026-10-02T06:30:00.5Z", "top": 50, "skip": 150,
          "rec_id": 4711}
# Des instants que le schéma admet, et ce que le client en écrit dans le chemin.
INSTANTS = {
    "2026-10-01T00:00:00Z": "2026-10-01T00:00:00.000000Z",
    "2026-10-01T23:59:59.5Z": "2026-10-01T23:59:59.500000Z",
    "2026-02-28T12:00:00.123456Z": "2026-02-28T12:00:00.123456Z",
}


def _utc(value: str) -> datetime:
    """L'instant d'un texte `…Z`, sa fraction complétée à six chiffres."""
    padded = re.sub(r"\.(\d+)", lambda m: "." + m.group(1).ljust(6, "0"), value)
    return datetime.fromisoformat(padded.replace("Z", "+00:00"))


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(THREECX.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


# La configuration du standard : un accès générique, guidé par le `$metadata` du standard lui-même (`Schema`), qui ne se
# décrit pas fonction par fonction. Liste fermée : une méthode publique nouvelle est décrite, ou ajoutée ici.
CONFIGURATION = {"schema", "read", "create", "update", "delete", "invoke"}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(ThreeCXClient, inspect.isfunction) if not n.startswith("_")} - CONFIGURATION


class _Answer:
    def __init__(self, payload=None, content=b"", headers=None):
        self.status_code = 200
        self.ok = True
        self._payload = payload
        self.content = content
        self.headers = headers or {"Content-Type": "application/json"}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Ni DNS, ni jeton réel : l'adresse est publique, le jeton est donné."""
    threecx_auth._TOKEN_CACHE.clear()
    monkeypatch.setattr("socket.getaddrinfo", lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(threecx_auth, "get_access_token", lambda base_url, **kw: "token-of-the-test")
    yield
    threecx_auth._TOKEN_CACHE.clear()


def _client() -> ThreeCXClient:
    return ThreeCXClient(BASE, client_id="client-a", client_secret="not-a-secret")


def _sent_by_the_client(monkeypatch, name: str, arguments: dict, answer: _Answer | None = None) -> dict:
    """L'adresse, la query et l'en-tête Accept que la méthode du client envoie, sans réseau ; et ce qu'elle rend."""
    sent = []

    def request(self, method, url, params=None, headers=None, **kwargs):
        assert method == "GET", method
        sent.append({"url": url, "params": params or {}, "accept": headers["Accept"]})
        return answer or _Answer({"value": []})

    monkeypatch.setattr("requests.Session.request", request)
    returned = getattr(_client(), name)(**arguments)
    assert len(sent) == 1, name
    return {**sent[0], "returned": returned}


def _described(function: dict, arguments: dict, write_instant=lambda value: value) -> tuple[str, dict]:
    """L'adresse et la query que dit la description, sous l'adresse de base résolue sur le réglage du compte.
    `write_instant` : la forme d'un instant dans le chemin, celle de l'agent par défaut."""
    call = function["call"]
    connector = _description()["connector"]
    base = connector["base_url"].replace("{base_url}", BASE)
    given = {**CLIENT_FILLS.get(function["name"], {}), **arguments}
    path = call["path"].format(**{
        a: (write_instant(given[a]) if a.startswith("date_") else given[a])
        for a in re.findall(r"{(\w+)}", call["path"])})
    query = {api: given[a] for api, a in call.get("query", {}).items() if a in given}
    return base + path, query


def test_the_description_is_version_1_1_0_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.1.0"
    check(description, THREECX)


def test_each_method_of_the_client_is_a_function_of_the_same_name():
    assert set(_functions()) == _public_methods()
    assert CONFIGURATION <= {n for n, _ in inspect.getmembers(ThreeCXClient, inspect.isfunction)}
    for function in _functions().values():
        assert function["call"]["method"] == "GET", function["name"]
        assert function["class"] == "read", function["name"]
        assert function.get("act", False) is False, function["name"]
        assert "confirm" not in function, function["name"]


def test_the_connector_says_the_address_and_the_api_client_of_the_client():
    connector = _description()["connector"]
    assert connector["settings"] == [{"name": "base_url", "label": "Phone system address", "type": "url"}]
    assert list(inspect.signature(ThreeCXClient.__init__).parameters)[1] == "base_url"
    assert connector["base_url"] == "{base_url}/xapi/v1"
    assert connector["credential"] == [
        {"name": "client_id", "label": "API client ID", "secret": False},
        {"name": "client_secret", "label": "API client secret", "secret": True},
    ]
    parameters = inspect.signature(ThreeCXClient.__init__).parameters
    assert {"client_id", "client_secret"} <= set(parameters)
    assert connector["auth"] == {
        "kind": "oauth2_client_credentials", "token_url": "{base_url}/connect/token", "token_request": "form",
        "client_auth": "body", "client_id": "client_id", "client_secret": "client_secret", "expires_in_default": 3600,
    }
    assert connector["timeout_s"] == threecx_client._HTTP_TIMEOUT[1], "the client waits 120 s for an answer"
    assert "rate_limit" not in connector and "probe" not in connector
    assert "platform" not in connector["modes"], "a phone system is the account's own"


def test_the_token_request_is_the_one_the_client_sends(monkeypatch):
    """Le formulaire `grant_type`, `client_id`, `client_secret` à `{adresse}/connect/token`, et une heure quand la
    réponse ne dit pas sa durée."""
    posted = []

    def post(url, data=None, json=None, **kwargs):
        posted.append((url, data, json))
        return _Answer({"access_token": "token-of-the-test"})

    monkeypatch.setattr(threecx_auth.requests, "post", post)
    _, ttl = threecx_auth._client_token(BASE, "client-a", "not-a-secret")
    assert posted == [(BASE + "/connect/token",
                       {"grant_type": "client_credentials", "client_id": "client-a", "client_secret": "not-a-secret"},
                       None)]
    assert ttl == _description()["connector"]["auth"]["expires_in_default"]


def test_the_user_account_stays_with_the_client_for_its_stated_reason():
    """La connexion par compte utilisateur n'est pas un échange OAuth2 : un corps JSON, un jeton sous `Token`."""
    source = inspect.getsource(threecx_auth._user_token)
    assert '"/webclient/api/Login/GetAccessToken"' in source.replace("{base_url}", "")
    assert 'json={"Username"' in source and 'payload.get("Token")' in source
    assert {"username", "password"} <= set(inspect.signature(ThreeCXClient.__init__).parameters)
    assert all(f["name"] not in ("username", "password") for f in _description()["connector"]["credential"])


def test_each_function_takes_the_arguments_of_its_client_method():
    for name, function in _functions().items():
        signature = inspect.signature(getattr(ThreeCXClient, name)).parameters
        named = {p for p in signature if p != "self"}
        assert named - set(function["input"]["properties"]) == set(ARGUMENTS_LEFT_OUT.get(name, {})), name
        assert set(function["input"]["properties"]) <= named, name


def test_the_values_the_client_fills_are_its_defaults_never_declared_ones():
    for table in (CLIENT_FILLS, REQUIRED_HERE):
        for name, fills in table.items():
            signature = inspect.signature(getattr(ThreeCXClient, name)).parameters
            assert {a: signature[a].default for a in fills} == fills, name
            properties = _functions()[name]["input"]["properties"]
            assert all("default" not in properties[a] for a in fills), name
    for name, required in REQUIRED_HERE.items():
        assert set(required) <= set(_functions()[name]["input"]["required"]), name


def test_each_instant_the_schema_admits_is_written_by_the_client_as_the_same_instant():
    schema = _functions()["list_calls"]["input"]["properties"]["date_from"]
    for value, written in INSTANTS.items():
        jsonschema.validate(value, schema)
        assert threecx_client._instant(value, "date_from") == written
        assert _utc(written) == _utc(value)


def test_the_call_log_goes_where_the_client_sends_it(monkeypatch):
    function = _functions()["list_calls"]
    for arguments in [{a: VALUES[a] for a in function["input"]["properties"]},
                      *[example["input"] for example in function["examples"]]]:
        jsonschema.validate(arguments, function["input"])
        sent = _sent_by_the_client(monkeypatch, "list_calls", arguments)
        url, query = _described(function, arguments, lambda v: threecx_client._instant(v, "date"))
        assert sent["url"] == url
        assert sent["params"] == query
        assert sent["accept"] == "application/json"
        # Sans la réécriture de l'instant, le chemin est celui que l'agent donne : le même instant en UTC.
        agent_url, _ = _described(function, arguments)
        for value in (arguments["date_from"], arguments["date_to"]):
            assert value in agent_url and threecx_client._instant(value, "date") in sent["url"]


def test_the_recording_is_fetched_where_the_client_fetches_it_and_named_as_it_names_it(monkeypatch):
    function = _functions()["download_recording"]
    file = function["output"]["file"]
    for arguments in [{"rec_id": VALUES["rec_id"]}, *[example["input"] for example in function["examples"]]]:
        jsonschema.validate(arguments, function["input"])
        sent = _sent_by_the_client(monkeypatch, "download_recording", arguments,
                                   _Answer(content=b"RIFF....WAVE", headers={"Content-Type": "audio/x-wav"}))
        url, query = _described(function, arguments)
        assert sent["url"] == url and sent["params"] == query == {}
        assert sent["accept"] == "audio/*"
        # Sans en-tête de nom, le client nomme le fichier comme le gabarit ; le type par défaut est l'un des siens.
        assert sent["returned"]["filename"] == file["name"]["template"].format(**arguments)
        assert sent["returned"]["content_type"] in file["types"]["wav"]
    named = _sent_by_the_client(monkeypatch, "download_recording", {"rec_id": 7}, _Answer(
        content=b"RIFF", headers={"Content-Type": "audio/wav", "Content-Disposition": 'attachment; filename="c.wav"'}))
    assert named["returned"]["filename"] == "c.wav" and file["name"]["header"] == "Content-Disposition"


def test_the_recording_is_a_file_read_from_the_body_and_never_a_link():
    functions = _functions()
    file = functions["download_recording"]["output"]["file"]
    assert file == {"from": "body", "types": {"wav": ["audio/wav", "audio/x-wav", "audio/wave"]},
                    "name": {"header": "Content-Disposition", "template": "recording-{rec_id}.wav"},
                    "max_bytes": 52_428_800}
    assert set(functions["download_recording"]["output"]) == {"file"}
    assert {r["code"]: r["when"] for r in functions["download_recording"]["refusals"]} == {"recording_not_found": 404}
    # La ligne d'un segment enregistré porte le chemin de l'enregistrement, que l'agent ne reçoit pas.
    assert "`RecordingUrl`" in threecx_client.__doc__
    assert functions["list_calls"]["output"] == {"items": "value", "strip": ["RecordingUrl"]}
    assert 'json().get("value")' in inspect.getsource(ThreeCXClient.list_calls)


def test_the_input_refuses_what_the_client_refuses_and_what_it_rewrites():
    functions = _functions()
    calls = jsonschema.Draft202012Validator(functions["list_calls"]["input"])
    day = {"date_from": "2026-10-01T00:00:00Z", "date_to": "2026-10-02T00:00:00Z"}
    assert calls.is_valid({**day, "top": 1}) and calls.is_valid({**day, "top": threecx_client._MAX_TOP, "skip": 0})
    for refused in ({**day, "top": 0}, {**day, "top": threecx_client._MAX_TOP + 1}, {**day, "top": 10, "skip": -1},
                    day, {**day, "top": 10, "recorded_only": True}):
        assert not calls.is_valid(refused), refused
    # Une date seule, un décalage ou plus de six décimales : le client les réécrit, la description ne les prend pas.
    for date in ("2026-10-01", "2026-10-01T00:00:00+02:00", "2026-10-01T00:00:00.1234567Z", "2026-10-01T00:00Z"):
        assert not calls.is_valid({**day, "date_from": date, "top": 10}), date
    recording = jsonschema.Draft202012Validator(functions["download_recording"]["input"])
    assert not recording.is_valid({"rec_id": "4711"}) and not recording.is_valid({"rec_id": 0})


def test_no_refusal_marks_the_account_and_only_the_unavailable_service_is_retried():
    """Le jeton se renouvelle chez l'hôte (`oauth2_client_credentials`) : un 401 ne dit pas un compte à marquer ; un
    403 dit aussi un rôle insuffisant. Les deux fonctions sont des lectures : un 5xx se rejoue."""
    description = _description()
    entries = description["connector"]["errors"] + [r for f in description["functions"] for r in f.get("refusals", [])]
    assert not any("marks_account" in e or "body" in e for e in entries)
    assert {e["code"] for e in description["connector"]["errors"] if e["retryable"]} == {"upstream_unavailable"}


def test_each_call_costs_a_request_and_nothing_is_paginated():
    for name, function in _functions().items():
        assert function["cost"] == {"per": "request"}, name
        assert "pagination" not in function and "async" not in function, name


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), THREECX)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "threecx.{name}",' in module.source, name
    assert 'baseUrl: "{base_url}/xapi/v1",' in module.source
    table = python.declared([checked])["threecx"]
    assert set(table) == set(_functions())
    assert DECLARED.get("threecx") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
