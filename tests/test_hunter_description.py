"""La description `hunter` 1.0.0 : celle du client existant (`oto.tools.hunter`).

Une fonction par méthode du client, sous son nom ; les arguments sont ceux que la méthode nomme ; les clés envoyées à
Hunter (chemin, query) sont les siennes. Écarts nommés, gardés ici (`NARROWER`) : `limit` borné à 10 (le client prend
jusqu'aux 100 de Hunter), pour qu'une requête ne coûte jamais plus que le pire cas déclaré ; le nom de la personne
exigé par `email_finder` (prénom et nom, ou nom complet), que le client n'exige pas et que Hunter refuserait.
Tout appel qui dépense des crédits Hunter est une écriture ; la lecture du compte, gratuite, sert de sonde à solde.
Hunter inverse la convention : 403 = débit (rejouable), 429 = crédits de la période épuisés (marque le compte).
Rien n'est retiré des réponses : aucune ne porte de secret ni d'adresse ouverte sans connexion. Le client ne change pas.
"""
from __future__ import annotations

import copy
import inspect
import json
from unittest import mock

import jsonschema
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.hunter import client as hunter_client
from oto.tools.hunter.client import HunterClient

HUNTER = CONNECTORS / "hunter" / "connector.yaml"

CLASSES = {
    "read": {"account_info"},
    # Chacune dépense des crédits Hunter (une recherche, une vérification).
    "write": {"domain_search", "email_finder", "email_verifier"},
    "sensitive": set(),
}
PERSONAL = {"email_finder": {"first_name", "last_name", "full_name"}, "email_verifier": {"email"}}
# Ce que le client envoie sans que l'appel le donne : la valeur par défaut de Hunter, que l'hôte n'envoie pas.
CLIENT_DEFAULTS = {"domain_search": {"limit": 10}}
NARROWER = {
    "domain_search": [{"domain": "example.com", "limit": 11}, {"domain": "example.com", "limit": 100}],
    "email_finder": [{"domain": "example.com"}, {"domain": "example.com", "first_name": "Jane"}],
}
VALUES = {"domain": "example.com", "limit": 5, "first_name": "Jane", "last_name": "Doe", "full_name": "Jane Doe",
          "email": "jane.doe@example.com"}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(HUNTER.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(HunterClient, inspect.isfunction) if not n.startswith("_")}


def _parameters(method: str) -> dict[str, inspect.Parameter]:
    return {p: v for p, v in inspect.signature(getattr(HunterClient, method)).parameters.items() if p != "self"}


class _Answer:
    ok = True

    def __init__(self, body=None, status_code=200):
        self._body = {} if body is None else body
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = json.dumps(self._body)

    def json(self):
        return copy.deepcopy(self._body)


def _sent_by_the_client(name: str, arguments: dict, answer=None, status_code=200):
    sent = []

    def get(url, **kwargs):
        sent.append({"url": url, "params": dict(kwargs.get("params") or {}), "headers": kwargs.get("headers"),
                     "timeout": kwargs.get("timeout")})
        return _Answer(answer, status_code)

    with mock.patch.object(hunter_client.requests, "get", get):
        returned = getattr(HunterClient(api_key="k"), name)(**arguments)
    assert len(sent) == 1, name
    return sent[0], returned


def _described(function: dict, arguments: dict) -> tuple:
    call = function["call"]
    assert call["method"] == "GET"
    query = {api: arguments[a] for api, a in call.get("query", {}).items() if a in arguments}
    return HunterClient.BASE_URL + call["path"], query


def _client_view(name: str, sent: dict, arguments: dict) -> tuple:
    params = dict(sent["params"])
    for key, value in CLIENT_DEFAULTS.get(name, {}).items():
        if key not in arguments:
            assert params.pop(key) == value, (name, key)
    return sent["url"], params


def _valid(function: dict, arguments: dict) -> bool:
    return jsonschema.Draft202012Validator(function["input"]).is_valid(arguments)


def test_the_description_is_version_1_0_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.1"
    check(description, HUNTER)


def test_one_function_per_client_method_under_its_name():
    assert set(_functions()) == _public_methods()


def test_the_connector_says_what_the_client_does():
    """Clé en Bearer, jamais en query ; délai de lecture de 60 s, celui du client ; le rythme le plus bas de Hunter
    (10 par seconde, le vérificateur) ; les trois modes d'oto 1 ; sonde par le compte, à solde."""
    connector = _description()["connector"]
    assert connector["base_url"] == HunterClient.BASE_URL
    assert connector["auth"] == {"kind": "bearer", "token": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "API key", "secret": True}]
    sent, _ = _sent_by_the_client("account_info", {})
    assert sent["headers"] == {"Authorization": "Bearer k"}
    assert "api_key" not in sent["params"]
    assert sent["timeout"] == (10, connector["timeout_s"]) == hunter_client._HTTP_TIMEOUT
    assert connector["rate_limit"] == {"requests": 10, "seconds": 1}
    assert connector["modes"] == ["platform", "byo_user", "byo_org"]
    assert "headers" not in connector and "query_arrays" not in connector


def test_each_function_takes_the_arguments_its_client_method_names():
    for name, function in _functions().items():
        parameters = _parameters(name)
        assert set(function["input"]["properties"]) == set(parameters), name
        required = {p for p, v in parameters.items() if v.default is inspect.Parameter.empty}
        assert set(function["input"].get("required", [])) == required, name


def test_each_argument_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        everything = {a: VALUES[a] for a in function["input"]["properties"]}
        cases = [everything] if name != "email_finder" else [
            {"domain": VALUES["domain"], "first_name": "Jane", "last_name": "Doe"},
            {"domain": VALUES["domain"], "full_name": "Jane Doe"}, everything]
        cases.append({a: VALUES[a] for a in function["input"].get("required", [])})
        for arguments in cases:
            if not _valid(function, arguments):
                continue
            sent, _ = _sent_by_the_client(name, arguments)
            assert _client_view(name, sent, arguments) == _described(function, arguments), (name, arguments)


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent, _ = _sent_by_the_client(name, example["input"])
            assert _client_view(name, sent, example["input"]) == _described(function, example["input"]), (
                name, example["title"])


def test_where_the_schema_is_narrower_than_the_client_it_is_named():
    functions = _functions()
    for name, cases in NARROWER.items():
        for case in cases:
            assert not _valid(functions[name], case), (name, case)
            _sent_by_the_client(name, case)


def test_paid_calls_are_writes_on_the_act_side_and_the_account_is_a_free_read():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        assert "act" not in function and "confirm" not in function, name
        assert "pagination" not in function and "output" not in function, name
        if function["class"] == "write":
            assert function["cost"] == {"per": "request"}, name
            assert "credit" in function["description"] or "verification" in function["description"], name
        else:
            assert "cost" not in function and "Free" in function["description"], name


def test_the_domain_search_never_costs_more_than_one_request():
    """1 crédit par tranche de 10 emails rendus : `limit` borné à 10, une requête est le pire cas."""
    limit = _functions()["domain_search"]["input"]["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"]) == (1, 10)
    assert "1 credit per 10 emails" in inspect.getdoc(HunterClient.domain_search)


def test_every_argument_that_names_a_person_is_personal():
    for name, function in _functions().items():
        personal = {p for p, s in function["input"]["properties"].items() if s.get("personal")}
        assert personal == PERSONAL.get(name, set()), name


def test_the_probe_reads_the_remaining_searches_and_costs_nothing():
    connector = _description()["connector"]
    assert connector["probe"] == {"function": "account_info", "balance": {
        "path": "data.requests.searches.remaining", "min": 1, "unit": "searches"}}
    assert "cost" not in _functions()["account_info"]


def test_nothing_is_stripped_and_the_client_returns_the_answer_as_is():
    answer = {"data": {"domain": "example.com", "emails": [{"value": "jane.doe@example.com", "sources": [
        {"domain": "example.com", "uri": "https://example.com/team"}]}]}, "meta": {"results": 1}}
    _, returned = _sent_by_the_client("domain_search", {"domain": "example.com"}, answer)
    assert returned == answer


def test_hunter_inverted_statuses_are_said_as_the_client_says_them():
    """403 = débit, rejouable ; 429 = crédits épuisés, final, qui marque le compte sous le message documenté ;
    401 = clé refusée, marque le compte ; 5xx non rejouable, comme le client (`retryable` faux)."""
    errors = _description()["connector"]["errors"]
    marks = [(e["status"], e.get("body"), e["marks_account"]) for e in errors if "marks_account" in e]
    assert marks == [(401, None, "credential_rejected"),
                     (429, {"path": "errors.0.details", "contains": ["usage limit"]}, "quota_exhausted")]
    by_status = {}
    for entry in errors:
        for status in entry["status"] if isinstance(entry["status"], list) else [entry["status"]]:
            by_status.setdefault(status, []).append(entry)
    assert {e["retryable"] for e in by_status[403]} == {True}
    assert {e["retryable"] for e in by_status[429]} == {False}
    assert {e["retryable"] for e in by_status[500]} == {False}
    assert {e["code"] for e in errors if e["retryable"]} == {"rate_limited"}
    assert by_status[451][0]["code"] == "claimed_email"
    for status, retryable in ((403, True), (429, False), (500, False)):
        try:
            _sent_by_the_client("account_info", {}, {"errors": [{"id": "x", "code": status,
                                                                  "details": "You have reached your usage limit."}]},
                                status)
        except hunter_client.HunterError as error:
            assert (error.status_code, error.retryable) == (status, retryable)
        else:
            raise AssertionError(status)


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), HUNTER)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "hunter.{name}",' in module.source, name
    assert not module.skipped
    table = python.declared([checked])["hunter"]
    assert set(table) == CLASSES["write"]
    assert DECLARED.get("hunter") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
