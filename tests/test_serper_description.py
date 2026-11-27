"""La description `serper` 1.0.0 : celle du client existant (`oto.tools.serper`), sans rien y changer.

Une fonction par méthode du client qui envoie une seule requête à `google.serper.dev`, sous son nom, avec ses noms
d'arguments et les clés que le client envoie à Serper. Restent hors du fichier, au client écrit à la main : le scrape
(un autre hôte, et un refus local des sources fermées à l'extraction) et les compositions de plusieurs appels
(`census_maps`, `reviews_all`, `batch_search`, `get_suggestions`). Les gardes ci-dessous lisent le client lui-même :
un endpoint, un argument ou une clé qu'il ajoute sans la description fait échouer le test.
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
from oto.tools.serper import client as serper_client
from oto.tools.serper.client import SerperClient

SERPER = CONNECTORS / "serper" / "connector.yaml"

# Les méthodes publiques du client qui ne sont pas une requête unitaire à google.serper.dev, et pourquoi.
LEFT_OUT = {
    "scrape_page": "another host (scrape.serper.dev) and a local refusal of hosts closed to extraction",
    "census_maps": "several /maps calls: a grid of anchors, pages, deduplication",
    "reviews_all": "several /reviews calls following nextPageToken, capped by max_reviews",
    "batch_search": "several /search calls, one per query",
    "get_suggestions": "/autocomplete flattened to a list of strings, every failure read as no suggestion",
}
# Les arguments d'une méthode que sa fonction ne prend pas, et pourquoi.
ARGUMENTS_LEFT_OUT = {
    "search": {"site_filter": "the client writes it into q as site:<domain>; the agent writes it in query"},
}
# Une valeur par argument, pour un appel qui les donne tous.
VALUES = {
    "query": "coffee shops", "num": 20, "page": 2, "location": "Paris, France", "country": "fr",
    "language": "fr", "tbs": "qdr:w", "autocorrect": False, "ll": "@48.856600,2.352200,14z",
    "place_id": "place-0", "cid": "1234567890", "fid": "0x0:0x1", "sort_by": "newest", "topic_id": "topic-0",
    "next_page_token": "page-token-0", "url": "https://images.example.test/photo.jpg",
}
COST = {"per": "request", "unit": "credits", "settle": {"path": "credits"}}
# Une page Maps se facture 3 crédits (mesuré par oto 1) : son pire cas n'est jamais sous le réel.
COSTS = {"search_maps": {**COST, "quantity": 3}}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(SERPER.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _client_endpoints() -> dict[str, str]:
    """Chaque méthode publique du client qui appelle `_request` sur un endpoint de google.serper.dev, et cet endpoint."""
    endpoints = {}
    for name, method in inspect.getmembers(SerperClient, inspect.isfunction):
        if name.startswith("_"):
            continue
        found = re.findall(r'self\._request\("(/[a-z]+)"', inspect.getsource(method))
        if found:
            assert len(found) == 1, f"{name} calls several endpoints: {found}"
            endpoints[name] = found[0]
    return endpoints


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(SerperClient, inspect.isfunction) if not n.startswith("_")}


class _Answer:
    status_code = 200
    headers: dict = {}

    def json(self):
        return {"credits": 1}


def _payload_sent_by_the_client(name: str, arguments: dict) -> tuple[str, dict]:
    """L'adresse et le corps JSON que la méthode du client envoie pour ces arguments, sans réseau."""
    sent = []

    def post(url, **kwargs):
        sent.append((url, kwargs["json"]))
        return _Answer()

    client = SerperClient(api_key="k")
    client._min_interval = 0.0
    client.session.post = post
    getattr(client, name)(**arguments)
    assert len(sent) == 1, name
    return sent[0]


def _body_described(function: dict, arguments: dict) -> dict:
    body = function["call"].get("body", {})
    return {api: arguments[argument] for api, argument in body.items() if argument in arguments}


def test_the_description_is_version_1_1_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.1.1"
    check(description, SERPER)


def test_each_single_request_method_of_the_client_is_a_function_of_the_same_name_and_endpoint():
    functions = _functions()
    assert {name: (f["call"]["method"], f["call"]["path"]) for name, f in functions.items()} == {
        name: ("POST", endpoint) for name, endpoint in _client_endpoints().items()}


def test_what_the_description_leaves_out_is_named_with_its_reason():
    assert _public_methods() - set(_functions()) == set(LEFT_OUT)


def test_the_connector_says_what_the_client_does():
    connector = _description()["connector"]
    assert connector["base_url"] == SerperClient.BASE_URL
    assert connector["auth"] == {"kind": "api_key", "in": "header", "name": "X-API-KEY", "key": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "API key", "secret": True}]
    assert connector["timeout_s"] == serper_client._HTTP_TIMEOUT[1]
    rate = connector["rate_limit"]
    assert rate["seconds"] / rate["requests"] == SerperClient(api_key="k")._min_interval
    assert "probe" not in connector, "every function requires an argument: no probe can be declared"


def test_each_function_takes_the_arguments_of_its_client_method():
    for name, function in _functions().items():
        parameters = set(inspect.signature(getattr(SerperClient, name)).parameters) - {"self"}
        properties = set(function["input"]["properties"])
        assert properties <= parameters, name
        assert parameters - properties == set(ARGUMENTS_LEFT_OUT.get(name, {})), name


def test_each_argument_goes_in_the_body_under_the_key_the_client_sends():
    for name, function in _functions().items():
        arguments = {argument: VALUES[argument] for argument in function["input"]["properties"]}
        url, payload = _payload_sent_by_the_client(name, arguments)
        assert url == SerperClient.BASE_URL + function["call"]["path"], name
        assert payload == _body_described(function, arguments), name
        assert set(function["call"]["body"].values()) == set(function["input"]["properties"]), name


def test_each_example_sends_what_the_client_sends_for_it():
    """Le client pose `num` 10 et `page` 1 quand l'appel ne les donne pas : les défauts de Serper, déclarés au schéma."""
    for name, function in _functions().items():
        properties = function["input"]["properties"]
        for example in function["examples"]:
            _, payload = _payload_sent_by_the_client(name, example["input"])
            described = _body_described(function, example["input"])
            api_name = {argument: api for api, argument in function["call"]["body"].items()}
            defaults = {api_name[argument]: properties[argument]["default"]
                        for argument in properties if "default" in properties[argument]}
            assert payload == {**defaults, **described}, (name, example["title"])


def test_each_call_costs_the_credits_serper_reports():
    for name, function in _functions().items():
        assert function["cost"] == COSTS.get(name, COST), name
        assert function["class"] == "read" and "act" not in function, name


def test_the_service_signals_an_empty_account_and_a_refused_key():
    """Serper dit un compte vide par un 400 « Not enough credits » (relevé par le client, `_post`), et une clé refusée
    par un 403 « Unauthorized », jamais un 401 : seules ces deux réponses, et un 402, marquent le compte."""
    errors = _description()["connector"]["errors"]
    marked = {e["code"]: (e["status"], e.get("body"), e["marks_account"]) for e in errors if "marks_account" in e}
    assert marked == {
        "credits_exhausted": (400, {"path": "message", "equals": ["Not enough credits"]}, "quota_exhausted"),
        "payment_required": (402, None, "quota_exhausted"),
        "key_refused": (403, {"path": "message", "equals": ["Unauthorized"]}, "credential_rejected"),
    }
    assert not any(e["retryable"] for e in errors if "marks_account" in e)
    assert {e["code"] for e in errors if e["retryable"]} == {"rate_limited", "upstream_unavailable"}


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), SERPER)
    module = connector_module(checked)
    for name in _client_endpoints():
        assert f'name: "serper.{name}",' in module.source, name
    assert "scrape" not in module.source
    table = python.declared([checked])["serper"]
    assert table == {name: {"cost": COSTS.get(name, COST)} for name in _client_endpoints()}
    assert DECLARED.get("serper") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
