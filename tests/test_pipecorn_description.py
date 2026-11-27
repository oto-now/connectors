"""La description `pipecorn` 1.0.0 : celle du client (`oto.tools.pipecorn`), sur l'hôte principal.

Une fonction par méthode du client, sous son nom, sauf deux, nommées dans la description : `enrich_contact`
(l'enrichissement synchrone, servi sur un autre hôte, quand une description n'a qu'une racine) et `probe` (le nom que
le client donne à `get_credits`, la même requête). Les arguments sont ceux que la méthode nomme, sauf `phone_type`
(que le client envoie en liste d'un élément), `custom` (un objet libre que Pipecorn renvoie) et le `timeout` des
quatre recherches synchrones (le délai propre de l'appelant), laissés au client.
Écarts nommés, gardés ici (`NARROWER`) : une adresse de profil LinkedIn, requise pour un téléphone ; un email sans
profil a ses deux noms et un domaine ou une société ; les codes pays en capitales ; chaque sorte une fois.
Refusé, des deux côtés : l'email personnel d'un tiers ; et, des réponses des quatre recherches synchrones (profil,
société, technologies, offres), tout champ de contact (`output.strip`, `without_contacts` côté client).
Coût : au pire une trouvaille par sorte demandée (`results`), le prix par sorte restant au service qui facture ;
asynchrone, relevé par `get_enrichment` avec le seul `enrichment_id`. Les recherches synchrones, en crédits : 1 par
requête (profil, société), 3 (technologies), au pire 100 pour les offres (le plafond de `limit`, un crédit par offre).
"""
from __future__ import annotations

import copy
import inspect
import json
from unittest.mock import MagicMock

import jsonschema
import pytest
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.pipecorn import client as pipecorn_client
from oto.tools.pipecorn.client import PipecornClient

PIPECORN = CONNECTORS / "pipecorn" / "connector.yaml"

ID = "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a"
LOOKUPS = {"enrich_person", "enrich_account", "company_stack", "company_hiring"}
CLASSES = {"read": {"get_enrichment", "get_credits", "get_account_info"}, "write": {"submit_enrichment"} | LOOKUPS,
           "sensitive": set()}
LEFT_TO_THE_CLIENT = {"enrich_contact", "probe"}
LEFT_ARGUMENTS = {"submit_enrichment": {"phone_type", "custom"},
                  **{name: {"timeout"} for name in LOOKUPS}}
URL = "https://www.linkedin.com/in/janedoe/"
COMPANY_PAGE = "https://www.linkedin.com/company/example/"
VALUES = {"enrichment_type": ["phone", "email"], "linkedin_url": URL, "firstname": "Jane", "lastname": "Doe",
          "domain": "example.com", "company_name": "Example", "phone_country_codes": ["FR", "BE"],
          "idempotency_key": "k-1", "enrichment_id": ID, "first_name": "Jane", "last_name": "Doe", "name": "Example",
          "country_code": "FR", "posted_within_days": 90, "limit": 20, "full_description": True,
          "job_title_keywords": ["sales"], "excluded_job_title_keywords": ["intern"], "job_country_codes": ["FR"]}
#: A company's LinkedIn page, not a profile.
COMPANY_VALUES = {"linkedin_url": COMPANY_PAGE}
#: A person by its profile OR by its names: the client sends the profile alone.
ONE_WAY_IN = {"enrich_person": [("linkedin_url",), ("first_name", "last_name", "company_name")]}
NARROWER = {
    "submit_enrichment": [
        {"enrichment_type": ["phone"], "linkedin_url": "jane"},
        {"enrichment_type": ["phone"], "firstname": "Jane", "lastname": "Doe", "domain": "example.com"},
        {"enrichment_type": ["email"], "firstname": "Jane", "domain": "example.com", "linkedin_url": "jane"},
        {"enrichment_type": ["phone", "phone"], "linkedin_url": URL},
        {"enrichment_type": ["phone"], "linkedin_url": URL, "phone_country_codes": ["fr"]},
    ],
    "enrich_person": [
        {"linkedin_url": URL, "first_name": "Jane"},
        {"first_name": "Jane", "last_name": "Doe"},
        {"linkedin_url": COMPANY_PAGE},
        {"first_name": " Jane", "last_name": "Doe", "company_name": "Example"},
    ],
    "enrich_account": [{}, {"linkedin_url": URL}, {"domain": "example.com", "country_code": "fr"}],
    "company_stack": [{}, {"name": "Example "}],
    "company_hiring": [
        {"domain": "example.com", "job_title_keywords": []},
        {"domain": "example.com", "job_country_codes": ["fr"]},
        {"domain": "example.com", "excluded_job_title_keywords": [" intern"]},
    ],
}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(PIPECORN.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(PipecornClient, inspect.isfunction) if not n.startswith("_")}


def _parameters(method: str) -> dict[str, inspect.Parameter]:
    return {p: v for p, v in inspect.signature(getattr(PipecornClient, method)).parameters.items() if p != "self"}


def _answer(body=None):
    r = MagicMock()
    r.status_code = 200
    r.headers = {}
    body = {"enrichment_id": ID, "status": "pending"} if body is None else body
    r.content = json.dumps(body).encode()
    r.text = r.content.decode()
    r.json.return_value = copy.deepcopy(body)
    return r


def _argument_sets(name: str, function: dict) -> list[dict]:
    """Every argument the function takes, each with its value — split by way in where there is only one."""
    values = {**VALUES, **(COMPANY_VALUES if name in LOOKUPS - {"enrich_person"} else {})}
    everything = {a: values[a] for a in function["input"]["properties"]}
    if name not in ONE_WAY_IN:
        return [everything]
    return [{a: everything[a] for a in way} for way in ONE_WAY_IN[name]]


def _sent_by_the_client(name: str, arguments: dict) -> dict:
    client = PipecornClient(api_key="k")
    client.session.request = MagicMock(return_value=_answer())
    arguments = dict(arguments)
    positional = [arguments.pop("enrichment_type")] if "enrichment_type" in arguments else []
    if name == "get_enrichment":
        positional = [arguments.pop("enrichment_id")]
    getattr(client, name)(*positional, **arguments)
    assert client.session.request.call_count == 1, name
    (method, url), kwargs = client.session.request.call_args
    return {"method": method, "url": url, "json": kwargs.get("json"), "headers": kwargs.get("headers"),
            "timeout": kwargs.get("timeout"), "allow_redirects": kwargs.get("allow_redirects"),
            "session_headers": dict(client.session.headers)}


def _described(function: dict, arguments: dict) -> tuple:
    call = function["call"]
    path = call["path"].format(**arguments)
    body = {api: arguments[a] for api, a in call.get("body", {}).items() if a in arguments}
    headers = {api: arguments[a] for api, a in call.get("headers", {}).items() if a in arguments}
    return call["method"], PipecornClient.BASE_URL + path, body or None, headers or None


def _client_view(sent: dict) -> tuple:
    return sent["method"], sent["url"], sent["json"], sent["headers"]


def _valid(function: dict, arguments: dict) -> bool:
    return jsonschema.Draft202012Validator(function["input"]).is_valid(arguments)


def test_the_description_is_version_1_0_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.1"
    check(description, PIPECORN)


def test_one_function_per_client_method_under_its_name_but_two_named():
    assert set(_functions()) == _public_methods() - LEFT_TO_THE_CLIENT
    comment = PIPECORN.read_text(encoding="utf-8").split("\nconnector:", 1)[0]
    for name in LEFT_TO_THE_CLIENT | {"phone_type", "custom", "personal_email", "timeout"}:
        assert name in comment, name


def test_the_connector_says_what_the_client_does():
    """Hôte principal ; clé dans X-API-KEY ; le délai de lecture du client (60 s) ; les trois modes d'oto 1 ; aucune
    redirection suivie."""
    connector = _description()["connector"]
    assert connector["base_url"] == PipecornClient.BASE_URL != PipecornClient.SYNC_URL
    assert connector["auth"] == {"kind": "api_key", "in": "header", "name": "X-API-KEY", "key": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "API key", "secret": True}]
    sent = _sent_by_the_client("get_credits", {})
    assert sent["session_headers"]["X-API-KEY"] == "k"
    assert sent["timeout"] == pipecorn_client.HTTP_TIMEOUT and connector["timeout_s"] == pipecorn_client.HTTP_TIMEOUT[1]
    assert sent["allow_redirects"] is False
    assert connector["modes"] == ["platform", "byo_user", "byo_org"]


def test_each_function_takes_the_arguments_its_client_method_names():
    for name, function in _functions().items():
        parameters = {p: v for p, v in _parameters(name).items() if p not in LEFT_ARGUMENTS.get(name, set())}
        assert set(function["input"]["properties"]) == set(parameters), name
        required = {p for p, v in parameters.items() if v.default is inspect.Parameter.empty}
        assert set(function["input"].get("required", [])) == required, name


def test_each_argument_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        for arguments in _argument_sets(name, function):
            assert _valid(function, arguments), (name, arguments)
            assert _client_view(_sent_by_the_client(name, arguments)) == _described(function, arguments), name


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent = _sent_by_the_client(name, example["input"])
            assert _client_view(sent) == _described(function, example["input"]), (name, example["title"])


def test_where_the_schema_is_narrower_than_the_client_it_is_named():
    functions = _functions()
    for name, cases in NARROWER.items():
        for case in cases:
            assert not _valid(functions[name], case), (name, case)


def test_a_third_party_personal_email_is_refused_by_the_schema_and_the_client():
    function = _functions()["submit_enrichment"]
    assert function["input"]["properties"]["enrichment_type"]["items"] == {"enum": ["email", "phone"]}
    assert not _valid(function, {"enrichment_type": ["personal_email"], "linkedin_url": URL})
    with pytest.raises(ValueError, match="personal email"):
        _sent_by_the_client("submit_enrichment", {"enrichment_type": ["personal_email"], "linkedin_url": URL})


def test_submit_and_the_lookups_are_paid_writes_and_the_others_are_free_reads():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    submit = functions["submit_enrichment"]
    assert submit["cost"] == {"per": "item", "items": "enrichment_type", "unit": "results"}
    assert submit["async"] == {"collect": "get_enrichment",
                               "job_id": {"from": "enrichment_id", "to": "enrichment_id"}}
    assert submit["expect"] == [{"kind": "non_empty", "refusal": "enrichment_id_missing", "path": "enrichment_id"}]
    for name in CLASSES["read"]:
        assert "cost" not in functions[name] and "Free" in functions[name]["description"], name
    # Le barème de Pipecorn, dit à l'agent : par trouvaille, rien sur un échec.
    for words in ("3 credits a professional email", "30 a", "only when a mobile is found", "nothing on a miss"):
        assert words in " ".join(submit["description"].split()), words


def test_each_lookup_declares_pipecorn_s_grid_in_credits():
    """Profil et société : 1 crédit par requête (rien sur un échec, 404) ; technologies : 3 ; offres : 1 par offre
    rendue, au pire le plafond de `limit` (100) — le format ne compte pas un argument entier."""
    functions = _functions()
    assert {n: functions[n]["cost"] for n in LOOKUPS} == {
        "enrich_person": {"per": "request", "unit": "credits"},
        "enrich_account": {"per": "request", "unit": "credits"},
        "company_stack": {"per": "request", "quantity": 3, "unit": "credits"},
        "company_hiring": {"per": "request", "quantity": 100, "unit": "credits"},
    }
    assert functions["company_hiring"]["input"]["properties"]["limit"]["maximum"] == 100
    assert list(pipecorn_client.HIRING_WINDOWS) == functions["company_hiring"]["input"]["properties"][
        "posted_within_days"]["enum"]
    for name, words in (("enrich_person", "1 credit when a person is resolved, nothing on a miss"),
                        ("enrich_account", "1 credit when a company is resolved, nothing on a miss"),
                        ("company_stack", "3 credits per request"),
                        ("company_hiring", "1 credit per job returned")):
        assert words in " ".join(functions[name]["description"].split()), name
        assert "async" not in functions[name], name


def test_no_lookup_serves_a_contact_field():
    """Un profil, une société ou l'équipe d'une offre n'est payé pour aucun contact : chaque champ d'email ou de
    téléphone que la description retire est un de ceux que `without_contacts` retire, et le même jeu partout."""
    functions = _functions()
    contacts = functions["enrich_person"]["output"]["strip"]
    assert functions["enrich_account"]["output"]["strip"] == contacts
    assert functions["company_hiring"]["output"]["strip"] == [f"jobs.hiring_team.{k}" for k in contacts]
    assert {"email", "personal_email", "personal_emails", "phone", "mobile_phone"} <= set(contacts)
    assert pipecorn_client.without_contacts({k: "x" for k in contacts}) == {}
    assert "output" not in functions["company_stack"]


def test_every_field_that_names_a_person_is_personal():
    functions = _functions()
    personal = {n: {p for p, s in f["input"]["properties"].items() if s.get("personal")} for n, f in functions.items()}
    assert {n: p for n, p in personal.items() if p} == {
        "submit_enrichment": {"linkedin_url", "firstname", "lastname"},
        "enrich_person": {"linkedin_url", "first_name", "last_name"}}


def test_the_probe_reads_the_enrichment_balance_and_costs_nothing():
    assert _description()["connector"]["probe"] == {
        "function": "get_credits", "balance": {"path": "credits.enrichment", "min": 1, "unit": "credits"}}
    assert "cost" not in _functions()["get_credits"]


def test_the_marks_and_the_retries():
    """401 marque `credential_rejected` ; crédits épuisés (402, ou le corps d'une relève `failed`) et plafond
    hebdomadaire (403, ou la relève) `quota_exhausted` ; seul le 429 se rejoue."""
    errors = _description()["connector"]["errors"]
    marks = [(e["status"], (e.get("body") or {}).get("equals"), e["marks_account"]) for e in errors
             if "marks_account" in e]
    assert marks == [([200, 402], ["insufficient_credits"], "quota_exhausted"),
                     ([200, 403], ["workspace_limit_exceeded"], "quota_exhausted"),
                     (401, None, "credential_rejected")]
    assert {e["code"] for e in errors if e["retryable"]} == {"rate_limited"}


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), PIPECORN)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "pipecorn.{name}",' in module.source, name
    assert not module.skipped
    table = python.declared([checked])["pipecorn"]
    assert set(table) == CLASSES["write"]
    assert DECLARED.get("pipecorn") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
