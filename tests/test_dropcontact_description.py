"""La description `dropcontact` 1.0.0 : celle du client existant (`oto.tools.dropcontact`).

Une fonction par méthode du client, sous son nom ; les arguments sont ceux que la méthode nomme, sauf
`custom_callback_url` de `submit`, laissé au client (`LEFT_TO_THE_CLIENT`) : il fait poster les résultats, des données
personnelles, à une adresse au choix de l'appelant. Les clés envoyées à Dropcontact (chemin, query, corps) sont les
siennes. Écart nommé, gardé ici (`NARROWER`) : chaque contact porte l'une des combinaisons qui l'identifient (email,
linkedin, prénom + nom + société, nom complet + société), que le client n'exige pas. Le plafond de 15 ko par contact
reste au client.
Lot asynchrone : `submit`, une écriture qui dépense des crédits (1 par contact au pire), relevée par `fetch` avec le
seul `request_id` ; `check_credits`, un contact vide que Dropcontact ne facture pas, sert de sonde à solde.
Rien n'est retiré des réponses : aucune ne porte de secret ni d'adresse ouverte sans connexion. Le client ne change pas.
"""
from __future__ import annotations

import copy
import inspect
import json
from unittest import mock

import jsonschema
import pytest
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.dropcontact import client as dropcontact_client
from oto.tools.dropcontact.client import DropcontactClient

DROPCONTACT = CONNECTORS / "dropcontact" / "connector.yaml"

CLASSES = {"read": {"fetch", "check_credits"}, "write": {"submit"}, "sensitive": set()}
LEFT_TO_THE_CLIENT = {"submit": {"custom_callback_url"}}
PERSONAL_FIELDS = {"email", "first_name", "last_name", "full_name", "phone", "linkedin"}
NARROWER = {
    "submit": [{"contacts": [{}]}, {"contacts": [{"first_name": "Jane", "last_name": "Doe"}]},
               {"contacts": [{"full_name": "Jane Doe"}]}, {"contacts": [{"company": "Example"}]}],
}
VALUES = {
    "contacts": [{"first_name": "Jane", "last_name": "Doe", "company": "Example", "website": "example.com",
                  "custom_fields": {"crm_id": "1"}}],
    "siren": True, "language": "en", "request_id": "req-0", "force_results": True,
}
# Ce que Dropcontact fait d'une valeur absente : le client n'envoie pas le défaut, l'hôte l'envoie.
API_DEFAULTS = {"siren": False, "forceResults": False}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(DROPCONTACT.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(DropcontactClient, inspect.isfunction) if not n.startswith("_")}


def _parameters(method: str) -> dict[str, inspect.Parameter]:
    return {p: v for p, v in inspect.signature(getattr(DropcontactClient, method)).parameters.items() if p != "self"}


class _Answer:
    def __init__(self, body=None, status_code=200):
        self._body = {"success": True, "request_id": "req-0", "credits_left": 7} if body is None else body
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = json.dumps(self._body)
        self.headers = {}
        self.is_redirect = False

    def json(self):
        return copy.deepcopy(self._body)


def _sent_by_the_client(name: str, arguments: dict, answer=None, status_code=200):
    sent = []

    def request(method):
        def send(url, **kwargs):
            sent.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {}),
                         "json": kwargs.get("json"), "headers": kwargs.get("headers"),
                         "timeout": kwargs.get("timeout"), "allow_redirects": kwargs.get("allow_redirects")})
            return _Answer(answer, status_code)
        return send

    with mock.patch.object(dropcontact_client.requests, "post", request("POST")), \
            mock.patch.object(dropcontact_client.requests, "get", request("GET")):
        returned = getattr(DropcontactClient(api_key="k"), name)(**arguments)
    assert len(sent) == 1, name
    return sent[0], returned


def _without_api_defaults(values: dict | None) -> dict:
    return {k: v for k, v in (values or {}).items() if API_DEFAULTS.get(k, object()) != v}


def _described(function: dict, arguments: dict) -> tuple:
    call = function["call"]
    path = call["path"].format(**arguments)
    query = {api: arguments[a] for api, a in call.get("query", {}).items() if a in arguments}
    query = {k: str(v).lower() if isinstance(v, bool) else v for k, v in _without_api_defaults(query).items()}
    body = {api: arguments[a] for api, a in call.get("body", {}).items() if a in arguments}
    body.update(call.get("constants", {}).get("body", {}))
    return call["method"], DropcontactClient.BASE_URL + path, query, _without_api_defaults(body) or None


def _client_view(sent: dict) -> tuple:
    return sent["method"], sent["url"], sent["params"], sent["json"]


def _valid(function: dict, arguments: dict) -> bool:
    return jsonschema.Draft202012Validator(function["input"]).is_valid(arguments)


def test_the_description_is_version_1_0_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.1"
    check(description, DROPCONTACT)


def test_one_function_per_client_method_under_its_name():
    assert set(_functions()) == _public_methods()


def test_the_connector_says_what_the_client_does():
    """Clé dans X-Access-Token ; délai de 30 s, celui du client et le défaut du format ; 60 requêtes par seconde ;
    les trois modes d'oto 1 ; aucune redirection suivie."""
    connector = _description()["connector"]
    assert connector["base_url"] == DropcontactClient.BASE_URL
    assert connector["auth"] == {"kind": "api_key", "in": "header", "name": "X-Access-Token", "key": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "API key", "secret": True}]
    sent, _ = _sent_by_the_client("check_credits", {})
    assert sent["headers"]["X-Access-Token"] == "k"
    assert sent["timeout"] == 30 and "timeout_s" not in connector
    assert sent["allow_redirects"] is False
    assert connector["rate_limit"] == {"requests": 60, "seconds": 1}
    assert connector["modes"] == ["platform", "byo_user", "byo_org"]


def test_each_function_takes_the_arguments_its_client_method_names():
    for name, function in _functions().items():
        parameters = {p: v for p, v in _parameters(name).items() if p not in LEFT_TO_THE_CLIENT.get(name, set())}
        assert set(function["input"]["properties"]) == set(parameters), name
        required = {p for p, v in parameters.items() if v.default is inspect.Parameter.empty}
        assert set(function["input"].get("required", [])) == required, name


def test_each_argument_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        everything = {a: VALUES[a] for a in function["input"]["properties"]}
        required = {a: VALUES[a] for a in function["input"].get("required", [])}
        falsy = {**everything, **{a: False for a, s in function["input"]["properties"].items()
                                  if s.get("type") == "boolean"}}
        for arguments in (everything, required, falsy):
            assert _valid(function, arguments), (name, arguments)
            sent, _ = _sent_by_the_client(name, arguments)
            assert _client_view(sent) == _described(function, arguments), (name, arguments)


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent, _ = _sent_by_the_client(name, example["input"])
            assert _client_view(sent) == _described(function, example["input"]), (name, example["title"])


def test_where_the_schema_is_narrower_than_the_client_it_is_named():
    functions = _functions()
    for name, cases in NARROWER.items():
        for case in cases:
            assert not _valid(functions[name], case), (name, case)
            _sent_by_the_client(name, case)


def test_the_batch_bounds_are_the_client_ones():
    contacts = _functions()["submit"]["input"]["properties"]["contacts"]
    assert (contacts["minItems"], contacts["maxItems"]) == (1, dropcontact_client.MAX_CONTACTS_PER_BATCH)
    documented = {"email", "first_name", "last_name", "full_name", "phone", "company", "website", "num_siren",
                  "siret", "linkedin", "company_linkedin", "country", "job", "custom_fields"}
    assert set(contacts["items"]["properties"]) == documented
    for field in documented - {"custom_fields"}:
        assert field in inspect.getdoc(DropcontactClient.submit), field


def test_submit_is_a_paid_write_collected_by_fetch_and_the_others_are_free_reads():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        assert "act" not in function and "confirm" not in function and "background" not in function, name
        assert "pagination" not in function and "output" not in function, name
    submit = functions["submit"]
    assert submit["cost"] == {"per": "item", "items": "contacts", "unit": "credits"}
    assert submit["async"] == {"collect": "fetch", "job_id": {"from": "request_id", "to": "request_id"}}
    assert submit["expect"] == [{"kind": "non_empty", "refusal": "request_id_missing", "path": "request_id"}]
    for name in CLASSES["read"]:
        assert "cost" not in functions[name] and "Free" in functions[name]["description"], name


def test_check_credits_sends_the_client_empty_contact():
    sent, returned = _sent_by_the_client("check_credits", {})
    assert sent["json"] == {"data": [{}]} == _functions()["check_credits"]["call"]["constants"]["body"]
    assert returned["credits_left"] == 7


def test_every_field_that_names_a_person_is_personal():
    functions = _functions()
    for name, function in functions.items():
        assert not any(s.get("personal") for s in function["input"]["properties"].values()), name
    items = functions["submit"]["input"]["properties"]["contacts"]["items"]
    personal = {p for p, s in items["properties"].items() if s.get("personal")}
    assert personal == PERSONAL_FIELDS
    assert items["properties"]["custom_fields"]["additionalProperties"] == {"type": "string", "personal": True}


def test_the_probe_reads_the_credits_left_and_costs_nothing():
    connector = _description()["connector"]
    assert connector["probe"] == {"function": "check_credits",
                                  "balance": {"path": "credits_left", "min": 1, "unit": "credits"}}
    assert "cost" not in _functions()["check_credits"]
    assert "0 credits" in inspect.getdoc(dropcontact_client)


def test_nothing_is_stripped_and_the_client_returns_the_data_as_is():
    data = [{"first_name": "Jane", "email": [{"email": "jane.doe@example.com", "qualification": "nominative@pro"}],
             "website": "https://example.com", "linkedin": "https://www.linkedin.com/in/janedoe/"}]
    _, returned = _sent_by_the_client("fetch", {"request_id": "req-0"},
                                      {"success": True, "data": data, "credits_left": 6})
    assert returned == {"done": True, "data": data, "credits_left": 6}


def test_the_marks_and_the_retries():
    """401 marque `credential_rejected`, 403 (quota du jeton) `quota_exhausted` ; seul le 429 se rejoue ; un 5xx
    ne se rejoue pas, un lot a pu être facturé."""
    errors = _description()["connector"]["errors"]
    marks = [(e["status"], e.get("body"), e["marks_account"]) for e in errors if "marks_account" in e]
    assert marks == [(401, None, "credential_rejected"), (403, None, "quota_exhausted")]
    assert {e["code"] for e in errors if e["retryable"]} == {"rate_limited"}
    assert [e["status"] for e in errors if e["code"] == "upstream_unavailable"] == [[500, 503, 504, 524]]
    with pytest.raises(UpstreamHTTPError) as raised:
        _sent_by_the_client("check_credits", {}, {"error": True}, 403)
    assert raised.value.status_code == 403


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), DROPCONTACT)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "dropcontact.{name}",' in module.source, name
    assert not module.skipped
    table = python.declared([checked])["dropcontact"]
    assert set(table) == CLASSES["write"]
    assert DECLARED.get("dropcontact") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
