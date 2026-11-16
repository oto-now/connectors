"""La fabrique : règles hors schéma, JSON Schema d'entrée recopié, ajouts du format, refus, sortie à jour.

La sortie TypeScript commitée (`ts/src/`) doit être celle que la fabrique produit des
descriptions : sinon `test_committed_output_is_current` échoue et dit de lancer
`python -m fabrique`.
"""
from __future__ import annotations

import copy
import pathlib
import re

import pytest
import yaml

from fabrique import body_cases, nested_body_cases, python
from fabrique.build import generate, stale
from fabrique.descriptions import CONNECTORS, DescriptionError, check, served
from fabrique.typescript import NotGenerated, connector_module, function_source, js_block

SELLSY = CONNECTORS / "sellsy" / "connector.yaml"
NOTION = CONNECTORS / "notion" / "connector.yaml"
PENNYLANE = CONNECTORS / "pennylane" / "connector.yaml"


def _load(path: pathlib.Path) -> dict:
    return copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))


# --- Sortie commitée -------------------------------------------------------------


def test_committed_output_is_current():
    assert stale(generate()) == [], "ts/src, oto/connectors/declared.py or docs/carte-du-parc.md is out of date: run `python -m fabrique`"


def test_every_description_generates_a_module():
    result = generate()
    assert {m.connector for m in result.modules} == {p.parent.name for p in CONNECTORS.glob("*/connector.yaml")}
    assert all(m.generated for m in result.modules)


def test_every_function_is_generated():
    result = generate()
    assert [s.function for m in result.modules for s in m.skipped] == []


# --- Règle 1 : un argument, un seul endroit ---------------------------------------


def _get_estimate(d):
    return next(f for f in d["functions"] if f["name"] == "get_estimate")


@pytest.mark.parametrize("mutate, message", [
    (lambda d: _get_estimate(d)["call"].update(body={"id": "id"}), "goes to two places"),
    (lambda d: _get_estimate(d)["call"].update(path="/estimates/{estimate_id}"), "the path names 'estimate_id'"),
    (lambda d: _get_estimate(d)["call"]["query"].update(other="nope"), "names 'nope', which is not an input argument"),
    (lambda d: _get_estimate(d)["call"].pop("query"), "go nowhere in call"),
    (lambda d: _get_estimate(d)["input"].update(required=[]), "path argument 'id' must be required"),
    (lambda d: d["functions"][0]["pagination"].update(request_param="cursor"), "request_param 'cursor' is not an input argument"),
], ids=["two_places", "path_unknown", "query_unknown", "unplaced", "optional_path", "pagination_unknown"])
def test_argument_rule_refuses(mutate, message):
    description = _load(SELLSY)
    mutate(description)
    with pytest.raises(DescriptionError, match=message):
        check(description, SELLSY)


# --- Règle 20 : le corps entier, un argument objet strict -------------------------

BODY_CASES = CONNECTORS / "body_cases" / "connector.yaml"


def _create_lead() -> dict:
    """La fonction des cas communs dont le corps entier est l'argument `lead` (`connectors/_cases/body.yaml`)."""
    loaded = body_cases.load()
    return copy.deepcopy(next(f for f in loaded.description.data["functions"] if f["name"] == "create_lead"))


def _whole_body() -> dict:
    return body_cases.alone(body_cases.load(), _create_lead())


def test_whole_body_is_the_place_of_its_argument():
    """L'argument du corps entier a sa place (règle 1) : la description passe, sans « go nowhere »."""
    check(_whole_body(), BODY_CASES)


def test_whole_body_leaves_the_other_arguments_to_place():
    description = _whole_body()
    description["functions"][0]["input"]["properties"]["note"] = {"type": "string"}
    with pytest.raises(DescriptionError, match=r"argument\(s\) note go nowhere in call"):
        check(description, BODY_CASES)


@pytest.mark.parametrize("body", ["Lead", 3, ["lead"], ""], ids=["not_an_identifier", "number", "list", "empty"])
def test_whole_body_is_named_by_an_identifier(body):
    description = _whole_body()
    description["functions"][0]["call"]["body"] = body
    with pytest.raises(DescriptionError, match="does not match the description schema at functions/0/call/body"):
        check(description, BODY_CASES)


def test_whole_body_beside_an_account_identity():
    """La règle 8 lit le corps entier sans nom côté API : une fonction qui en porte un passe sous un compte rattaché."""
    description = _load(LINKEDIN)
    description["functions"].append(_create_lead())
    check(description, LINKEDIN)
    description["functions"][-1]["call"]["body"] = "account_id"
    with pytest.raises(DescriptionError, match="call.body 'account_id': body_argument_unknown"):
        check(description, LINKEDIN)


def test_whole_body_is_generated_as_the_argument_name():
    source = function_source("body_cases", _create_lead())
    assert '    body: "lead",\n' in source
    assert '"additionalProperties": false' in source


# --- Règle 22 : le corps emboîté --------------------------------------------------

NESTED_BODY_CASES = CONNECTORS / "nested_body_cases" / "connector.yaml"


def _nested(name: str) -> dict:
    """Une fonction des cas communs du corps emboîté (`connectors/_cases/nested_body.yaml`), seule sous leur connecteur."""
    loaded = nested_body_cases.load()
    function = next(f for f in loaded.description.data["functions"] if f["name"] == name)
    return nested_body_cases.alone(loaded, function)


def test_nested_body_places_its_arguments():
    """Chaque argument d'une table emboîtée a sa place (règle 1), à côté de la constante de même parent."""
    check(_nested("update_issue"), NESTED_BODY_CASES)


def test_nested_body_names_a_path_in_rule_1():
    description = _nested("update_issue")
    description["functions"][0]["call"]["query"] = {"team": "team_id"}
    with pytest.raises(DescriptionError, match=r"argument 'team_id' goes to two places \(query 'team' and body 'variables.input.teamId'\)"):
        check(description, NESTED_BODY_CASES)
    description = _nested("update_issue")
    description["functions"][0]["call"]["body"]["variables"]["input"]["state"] = "state_id"
    with pytest.raises(DescriptionError, match=r"body 'variables.input.state' names 'state_id', which is not an input argument"):
        check(description, NESTED_BODY_CASES)


@pytest.mark.parametrize("value", [3, ["title"], "Title", None], ids=["number", "list", "not_an_identifier", "null"])
def test_nested_body_values_are_arguments_or_maps(value):
    description = _nested("update_issue")
    description["functions"][0]["call"]["body"]["variables"]["input"]["title"] = value
    with pytest.raises(DescriptionError, match="does not match the description schema at functions/0/call/body"):
        check(description, NESTED_BODY_CASES)


def test_nested_body_key_bearing_the_account_identity():
    """La règle 8 lit toute clé d'un chemin emboîté : aucune ne porte le nom de l'identité d'un compte rattaché."""
    description = _load(LINKEDIN)
    function = _nested("update_issue")["functions"][0]
    description["functions"].append(function)
    check(description, LINKEDIN)
    function["call"]["body"]["variables"] = {"account_id": {"id": "issue_id"}}
    with pytest.raises(DescriptionError, match="body parameter 'account_id' bears the name of the account identity"):
        check(description, LINKEDIN)


def test_nested_body_is_generated_as_is():
    source = function_source("nested_body_cases", _nested("update_issue")["functions"][0])
    assert ('    body: {"variables": {"id": "issue_id", "input": {"title": "title", "teamId": "team_id", '
            '"priority": "priority"}}},\n') in source


# --- Règle 2 : une référence de auth nomme un champ de credential -----------------


@pytest.mark.parametrize("path, reference", [(SELLSY, "client_secret"), (NOTION, "token")])
def test_auth_reference_must_name_a_credential_field(path, reference):
    description = _load(path)
    description["connector"]["auth"][reference] = "missing_field"
    with pytest.raises(DescriptionError, match=rf"auth\.{reference} names 'missing_field', which is not a credential field"):
        check(description, path)


def _jwt_bearer(path: pathlib.Path) -> dict:
    description = _load(path)
    description["connector"]["auth"] = {"kind": "oauth2_jwt_bearer", "token_url": "https://oauth2.example.test/token",
                                        "scopes": ["https://api.example.test/auth/read"], "key": "service_account_key"}
    description["connector"]["credential"] = [{"name": "service_account_key", "label": "Service account key (JSON)", "secret": True}]
    return description


def test_jwt_bearer_passes_and_is_carried_as_described():
    """Un compte de service : un seul champ, secret ; la sortie porte `tokenUrl`, `scopes` et `key`, tels quels."""
    source = connector_module(check(_jwt_bearer(NOTION), NOTION)).source
    assert ('"kind": "oauth2_jwt_bearer"' in source and '"tokenUrl": "https://oauth2.example.test/token"' in source
            and '"key": "service_account_key"' in source)


@pytest.mark.parametrize("mutate, message", [
    (lambda c: c["auth"].update(key="missing_field"), r"auth\.key names 'missing_field', which is not a credential field"),
    (lambda c: c["credential"].append({"name": "other", "label": "Other", "secret": True}),
     "auth.kind oauth2_jwt_bearer takes a single credential field, the key file named by auth.key"),
    (lambda c: c["credential"][0].update(secret=False), r"auth\.key names 'service_account_key', which must be a secret field"),
], ids=["key_not_a_field", "second_field", "key_not_secret"])
def test_jwt_bearer_rule_2_refuses(mutate, message):
    """Règle 2 : en `oauth2_jwt_bearer`, `key` nomme le seul champ de `credential`, secret."""
    description = _jwt_bearer(NOTION)
    mutate(description["connector"])
    with pytest.raises(DescriptionError, match=message):
        check(description, NOTION)


def test_jwt_bearer_authorization_header_is_set_once():
    """Règle 3 : l'authentification pose `Authorization` ; un en-tête constant ne le reprend pas."""
    description = _jwt_bearer(NOTION)
    description["connector"].setdefault("headers", {})["Authorization"] = "x"
    with pytest.raises(DescriptionError, match="Authorization"):
        check(description, NOTION)


def test_basic_username_must_name_a_credential_field():
    """`username` est facultatif ; déclaré, il nomme un champ de `credential` (règle 2)."""
    description = _load(AIRCALL)
    description["connector"]["auth"]["username"] = "missing_field"
    with pytest.raises(DescriptionError, match=r"auth\.username names 'missing_field', which is not a credential field"):
        check(description, AIRCALL)


def test_basic_without_username_passes_and_is_carried_without_it():
    """Sans `username`, l'identifiant est vide : la définition générée n'a pas de `username`."""
    description = _load(AIRCALL)
    del description["connector"]["auth"]["username"]
    description["connector"]["credential"] = [f for f in description["connector"]["credential"] if f["name"] == "api_token"]
    source = connector_module(check(description, AIRCALL)).source
    assert '"kind": "basic"' in source and '"password": "api_token"' in source
    assert "username" not in source


def test_schema_violation_is_refused_before_the_rules():
    description = _load(SELLSY)
    del description["functions"][0]["class"]
    with pytest.raises(DescriptionError, match="does not match the description schema"):
        check(description, SELLSY)


def test_valid_descriptions_pass():
    for path in sorted(CONNECTORS.glob("*/connector.yaml")):
        check(_load(path), path)


# --- Retirer de la sortie (règle 18) ---------------------------------------------------------


TYPEFORM_STRIP = CONNECTORS / "typeform" / "connector.yaml"
AIRCALL = CONNECTORS / "aircall" / "connector.yaml"


@pytest.mark.parametrize("path, function, output", [
    (AIRCALL, "get_call", {"strip": ["call.recording", "call.voicemail"],
                           "sample": {"call": {"id": 1, "recording": "https://files.example.test/r.mp3",
                                               "voicemail": "https://files.example.test/v.mp3"}}}),
    (AIRCALL, "list_calls", {"items": "calls", "strip": ["recording", "voicemail"]}),
    (TYPEFORM_STRIP, "get_webhook", {"strip": ["secret", "secret_hint", "config.secret"],
                                     "sample": {"secret": "s-0", "secret_hint": "s", "config": {"secret": "s-0"}}}),
], ids=["wrapped_answer", "relative_to_items", "prefix_of_a_key"])
def test_strip_paths_pass_and_are_carried_as_is(path, function, output):
    description = _load(path)
    _fn(description, function)["output"] = output
    module = connector_module(check(description, path))
    strip = ", ".join(f'"{key}"' for key in output["strip"])
    assert f"strip: [{strip}]" in module.source
    assert "sample" not in module.source and "files.example.test" not in module.source


def test_sample_reaches_neither_output():
    description = _load(AIRCALL)
    _fn(description, "get_call")["output"] = {
        "strip": ["call.recording"], "sample": {"call": {"recording": "https://files.example.test/r.mp3"}}}
    checked = check(description, AIRCALL)
    assert "files.example.test" not in connector_module(checked).source
    assert "files.example.test" not in python.python_source([checked])


@pytest.mark.parametrize("output, at, code", [
    ({"strip": ["call.recording"]}, "output.strip 'call.recording'", "strip_path_unsampled"),
    ({"strip": ["call.recording"], "sample": {"call": {"id": 1}}}, "output.strip 'call.recording'", "strip_path_absent"),
    ({"strip": ["recording"], "sample": {"recording": "x"}}, "output.sample", "strip_sample_unused"),
], ids=["unsampled", "absent", "unused"])
def test_sample_rule_refuses(output, at, code):
    description = _load(AIRCALL)
    _fn(description, "get_call")["output"] = output
    with pytest.raises(DescriptionError, match=re.escape(f"function get_call: {at}: {code}")):
        check(description, AIRCALL)


@pytest.mark.parametrize("strip, refused, code", [
    (["config", "config.secret"], "config.secret", "strip_path_overlaps"),
    (["config.secret", "config"], "config.secret", "strip_path_overlaps"),
    (["a.b", "a.b.c"], "a.b.c", "strip_path_overlaps"),
], ids=["under_another", "under_a_later_one", "deeper"])
def test_strip_rule_refuses(strip, refused, code):
    description = _load(TYPEFORM_STRIP)
    _fn(description, "get_webhook")["output"]["strip"] = strip
    message = f"function get_webhook: output.strip {refused!r}: {code}"
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, TYPEFORM_STRIP)


# --- Génération : refus nommés, pagination -----------------------------------------


def test_input_schema_is_copied_as_is():
    """Aucune traduction : le schéma servi est celui de la description, ancres YAML résolues, quels que soient ses mots-clés."""
    function = next(f for f in _load(NOTION)["functions"] if f["name"] == "edit_page_markdown")
    source = function_source("notion", function)
    assert "  schema: " + js_block(function["input"], 1) + ",\n" in source
    assert '"oneOf": [' in source and '"not": {' in source


@pytest.mark.parametrize("mutate", [
    lambda i: i.update(additionalProperties=True),
    lambda i: i.pop("additionalProperties"),
    lambda i: i.update(type="array"),
], ids=["open", "no_additional_properties", "not_an_object"])
def test_root_not_strict_is_skipped_and_named(mutate):
    """La seule exigence que la fabrique vérifie encore elle-même, au cas où le schéma du format la laisserait passer."""
    description = check(_load(NOTION), NOTION)
    mutate(description.data["functions"][0]["input"])
    module = connector_module(description)
    reason = "input: the root must be a strict object (type: object, additionalProperties: false)"
    assert [(s.function, s.reason) for s in module.skipped] == [("notion.search_workspace", reason)]
    assert f"// - notion.search_workspace: {reason}" in module.source
    assert "export const searchWorkspace" not in module.source
    assert "export const getPage" in module.source


def test_pagination_adds_its_arguments_and_refuses_a_clash():
    function = _load(NOTION)["functions"][0]
    source = function_source("notion", function)
    assert '"all_pages": {"type": "boolean"' in source and '"maximum": 10' in source
    function["input"]["properties"]["all_pages"] = {"type": "boolean"}
    with pytest.raises(NotGenerated, match="pagination adds"):
        function_source("notion", function)


def test_handwritten_function_is_not_generated():
    function = _load(NOTION)["functions"][0]
    del function["call"]
    function["handwritten"] = {"python": {"module": "m", "function": "f"}, "typescript": {"file": "f.ts", "export": "f"}}
    with pytest.raises(NotGenerated, match="handwritten"):
        function_source("notion", function)


# --- Ajouts du format : sortie ----------------------------------------------------


def _pennylane(name: str) -> dict:
    return next(f for f in _load(PENNYLANE)["functions"] if f["name"] == name)


def test_connector_carries_headers_rate_and_probe():
    notion = connector_module(check(_load(NOTION), NOTION)).source
    assert 'headers: {"Notion-Version": "2025-09-03"},' in notion
    pennylane = connector_module(check(_load(PENNYLANE), PENNYLANE)).source
    assert "rateLimit: { requests: 4, intervalMs: 1000 }," in pennylane
    assert 'probe: { function: "pennylane.get_company", nonEmpty: ["scopes"] },' in pennylane


def test_probe_carries_its_balance():
    """Une sonde à solde porte son chemin, son seuil et son unité ; une sonde sans solde ne change pas."""
    description = _load(PENNYLANE)
    _fn(description, "get_company").pop("cost")
    description["connector"]["probe"]["balance"] = {"path": "credits.remaining", "min": 1, "unit": "credits"}
    source = connector_module(check(description, PENNYLANE)).source
    assert ('probe: { function: "pennylane.get_company", nonEmpty: ["scopes"], '
            'balance: { path: "credits.remaining", min: 1, unit: "credits" } },') in source
    description["connector"]["probe"] = {"function": "get_company", "balance": {"path": "a.0.b", "min": 0.5, "unit": "EUR"}}
    source = connector_module(check(description, PENNYLANE)).source
    assert 'probe: { function: "pennylane.get_company", nonEmpty: [], balance: { path: "a.0.b", min: 0.5, unit: "EUR" } },' in source


def test_request_carries_constants_encoding_and_stop_flag():
    source = function_source("pennylane", _pennylane("list_customers"))
    assert 'encode: {"filter": "json"},' in source
    assert 'more: "has_more"' in source
    source = function_source("pennylane", _pennylane("create_customer_invoice"))
    assert 'constants: {"body": {"draft": true}},' in source


def test_checks_and_expectations_are_carried():
    source = function_source("pennylane", _pennylane("create_ledger_entry"))
    assert 'checks: [\n    { kind: "equal_sums", refusal: "entry_unbalanced", items: "ledger_entry_lines", fields: ["debit", "credit"] },' in source
    source = function_source("pennylane", _pennylane("get_quote_pdf_link"))
    assert 'expect: [\n    { kind: "non_empty", refusal: "pdf_missing", path: "public_file_url" },' in source


def test_delete_carries_its_body():
    source = function_source("pennylane", _pennylane("unletter_ledger_entry_lines"))
    assert 'method: "DELETE"' in source
    assert 'body: {"ledger_entry_lines": "ledger_entry_lines", "unbalanced_lettering_strategy": "unbalanced_lettering_strategy"},' in source


# --- Règles 3 à 5 : constantes, contrôles, sonde ------------------------------------


def _fn(d, name):
    return next(f for f in d["functions"] if f["name"] == name)


@pytest.mark.parametrize("path, mutate, message", [
    (PENNYLANE, lambda d: _fn(d, "create_customer_invoice")["call"]["constants"]["body"].update(label=True),
     "call.body 'label': body_key_twice"),
    (NOTION, lambda d: d["connector"]["headers"].update(Authorization="x"),
     "header 'Authorization' is set twice"),
    (NOTION, lambda d: _fn(d, "get_page")["call"].update(constants={"headers": {"notion-version": "x"}}),
     "header 'notion-version' is set twice"),
    (PENNYLANE, lambda d: _fn(d, "get_product")["call"].update(encode={"product_id": "json"}),
     "encode names 'product_id', which is not placed in query, body or headers"),
    (PENNYLANE, lambda d: _fn(d, "create_ledger_entry")["checks"][0].update(refusal="nope"),
     "equal_sums names refusal 'nope', which the function does not declare"),
    (PENNYLANE, lambda d: _fn(d, "create_ledger_entry")["checks"][0].update(fields=["debit", "amount"]),
     "equal_sums needs 'ledger_entry_lines' to be a list argument whose items declare debit, amount"),
    (PENNYLANE, lambda d: _fn(d, "create_ledger_entry")["checks"][0].update(items="label"),
     "equal_sums needs 'label' to be a list argument"),
    (PENNYLANE, lambda d: _fn(d, "get_quote_pdf_link")["expect"][0].update(refusal="nope"),
     "non_empty names refusal 'nope'"),
    (PENNYLANE, lambda d: d["connector"]["probe"].update(function="nope"),
     "probe names 'nope', which is not a function of the connector"),
    (PENNYLANE, lambda d: d["connector"]["probe"].update(function="get_product"),
     "probe 'get_product' must be a read function with a call and no required argument"),
    (PENNYLANE, lambda d: d["connector"]["probe"].update(function="create_customer"),
     "probe 'create_customer' must be a read function"),
    (PENNYLANE, lambda d: d["connector"]["probe"].update(balance={"path": "credits.remaining", "min": 1, "unit": "credits"}),
     "probe 'get_company' reads a balance, so it must declare no cost"),
], ids=["constant_and_argument", "header_and_auth", "header_case", "encode_unplaced", "check_refusal",
        "check_fields", "check_not_a_list", "expect_refusal", "probe_unknown", "probe_required", "probe_write",
        "probe_balance_with_cost"])
def test_added_rules_refuse(path, mutate, message):
    description = _load(path)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, path)


def test_probe_without_balance_may_cost():
    """Le coût n'est refusé qu'à la fonction d'une sonde qui lit un solde."""
    description = _load(PENNYLANE)
    assert _fn(description, "get_company")["cost"] == {"per": "request"}
    check(description, PENNYLANE)


# --- Comptes : réglages, adresses par réglage, clé en query, consentement ------------

TYPEFORM = CONNECTORS / "typeform" / "connector.yaml"
MICROSOFT = CONNECTORS / "microsoft" / "connector.yaml"


def _lucca_like(d):
    """Sellsy, l'adresse tirée d'un sous-domaine saisi par l'admin."""
    d["connector"]["settings"] = [{"name": "domain", "label": "Domain", "type": "text", "pattern": "^[a-z0-9-]+$"}]
    d["connector"]["base_url"] = "https://{domain}.example.net/api"


def _server_like(d):
    """Sellsy, l'adresse saisie en entier par l'admin, et le jeton demandé à cette adresse."""
    d["connector"]["settings"] = [{"name": "server", "label": "Server address", "type": "url"}]
    d["connector"]["base_url"] = "{server}/api"
    d["connector"]["auth"]["token_url"] = "{server}/connect/token"


def _consent(d, **changes):
    d["connector"]["auth"] = {
        "kind": "oauth2_user", "authorize_url": "https://login.example/authorize", "token_url": "https://login.example/token",
        "client_auth": "basic", "refresh": "refresh_token", "rotates": True,
        "identity": {"function": "list_estimates", "path": "data.0.owner"},
    } | changes
    del d["connector"]["credential"]


@pytest.mark.parametrize("mutate", [_lucca_like, _server_like], ids=["text_in_host", "url_at_head"])
def test_templates_over_settings_pass(mutate):
    description = _load(SELLSY)
    mutate(description)
    check(description, SELLSY)


def test_instance_url_from_the_token_passes_and_is_carried():
    description = _load(SELLSY)
    _consent(description, from_token=["instance_url"])
    description["connector"]["base_url"] = "{instance_url}/services/data/v62.0"
    source = connector_module(check(description, SELLSY)).source
    assert '"fromToken": ["instance_url"]' in source
    assert 'baseUrl: "{instance_url}/services/data/v62.0",' in source


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["connector"].update(base_url="https://{domain}.example.net"),
     "base_url cites {domain}, which is not a declared setting (none declared)"),
    (lambda d: (_lucca_like(d), d["connector"].update(base_url="{domain}/api")),
     "base_url starts with {domain}, which is not a url setting"),
    (lambda d: (_server_like(d), d["connector"].update(base_url="https://api.example.net/{server}")),
     "base_url cites the url setting {server} elsewhere than at its head"),
    (lambda d: (_lucca_like(d), d["connector"]["auth"].update(token_url="https://{tenant}.example.net/token")),
     "auth.token_url cites {tenant}, which is not a declared setting (domain)"),
    (lambda d: (_lucca_like(d), d["connector"].update(base_url="https://api.example.net")),
     "setting(s) domain cited by no URL"),
    (lambda d: (_lucca_like(d), d["connector"]["settings"].append({"name": "client_id", "label": "x", "type": "url"})),
     "name(s) declared twice among credential, settings and from_token: client_id"),
    (lambda d: d["connector"].update(settings=[{"name": "region", "label": "Region", "type": "choice", "choices": ["us", "eu"], "default": "fr"}]),
     "setting 'region': default 'fr' is not one of its choices"),
    (lambda d: (_lucca_like(d), d["connector"].pop("base_url"), d["connector"].update(base_urls={"setting": "domain", "values": {"a": "https://a.example", "b": "https://b.example"}})),
     "base_urls.setting names 'domain', which is not a choice setting"),
    (lambda d: (d["connector"].update(settings=[{"name": "region", "label": "Region", "type": "choice", "choices": ["us", "eu"]}]),
                d["connector"].pop("base_url"),
                d["connector"].update(base_urls={"setting": "region", "values": {"us": "https://a.example", "fr": "https://b.example"}})),
     "base_urls.values must give a URL to each choice of 'region' (us, eu), and to nothing else"),
    (lambda d: (_consent(d, from_token=["instance_url"]), d["connector"]["auth"].update(token_url="{instance_url}/token")),
     "auth.token_url cites {instance_url}, which is not a declared setting (none declared)"),
    (lambda d: _consent(d, identity={"function": "get_estimate", "path": "id"}),
     "auth.identity 'get_estimate' must be a read function with a call and no required argument"),
    (lambda d: _consent(d, identity={"function": "nope", "path": "id"}),
     "auth.identity names 'nope', which is not a function of the connector"),
    (lambda d: (d["connector"].update(auth={"kind": "api_key", "in": "query", "name": "limit", "key": "client_id"})),
     "function list_estimates: query 'limit' is set twice (the authentication and the function)"),
], ids=["undeclared", "text_at_head", "url_not_at_head", "token_url_undeclared", "unused", "name_twice", "bad_default",
        "urls_on_text", "urls_not_each_choice", "token_from_token", "identity_required_argument", "identity_unknown",
        "query_key_clash"])
def test_account_rules_refuse(mutate, message):
    description = _load(SELLSY)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, SELLSY)


def test_oauth2_user_without_credential_is_refused_by_the_schema():
    description = _load(SELLSY)
    _consent(description)
    description["connector"]["credential"] = [{"name": "refresh_token", "label": "Refresh token", "secret": True}]
    with pytest.raises(DescriptionError, match="does not match the description schema at connector"):
        check(description, SELLSY)


def test_connector_carries_settings_urls_and_consent():
    typeform = connector_module(check(_load(TYPEFORM), TYPEFORM)).source
    assert '"setting": "region", "values": {"us": "https://api.typeform.com"' in typeform
    assert '"choices": ["us", "eu", "eu2"]' in typeform and '"default": "us"' in typeform
    microsoft = connector_module(check(_load(MICROSOFT), MICROSOFT)).source
    for fragment in ('"kind": "oauth2_user"', '"clientAuth": "body"', '"refresh": "refresh_token"', '"rotates": true',
                     '"identity": {"function": "microsoft.get_me", "path": "userPrincipalName"}', "credential: [],"):
        assert fragment in microsoft
    sellsy = connector_module(check(_load(SELLSY), SELLSY)).source
    assert '"clientAuth": "body"' in sellsy and '"tokenRequest": "json"' in sellsy


# --- Règle 7 : niveaux d'accès (docs/conception/niveaux-d-acces.md) -----------------

GMAIL = CONNECTORS / "gmail" / "connector.yaml"
OUTPUT = CONNECTORS.parent / "ts" / "src"
READ = "Estimates.READ"
ALL = "Estimates.ALL"
SETTINGS = "Settings.READ"


def _levels(d, **changes):
    """Sellsy en consentement à deux niveaux : `full` accorde `Estimates.ALL`, qui implique `Estimates.READ`."""
    _consent(d, access_levels={"read": {"scopes": [READ, SETTINGS]}, "full": {"scopes": [ALL, SETTINGS]}},
             scope_implies={ALL: [READ]}, **changes)
    for function in d["functions"]:
        function["scopes"] = [READ]


def test_access_levels_pass_and_are_carried():
    description = _load(SELLSY)
    _levels(description)
    _fn(description, "get_estimate")["scopes"] = [READ, SETTINGS]
    module = connector_module(check(description, SELLSY))
    assert '"accessLevels": {' in module.source and '"read": {"scopes": ["Estimates.READ", "Settings.READ"]}' in module.source
    assert '"scopeImplies": {"Estimates.ALL": ["Estimates.READ"]}' in module.source
    assert '  class: "read",\n  act: false,\n  scopes: ["Estimates.READ", "Settings.READ"],\n  description: ' in module.source
    assert '"scopes":' not in module.source.split("auth: {")[1].split("accessLevels")[0]


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["connector"]["auth"]["scope_implies"].update({"Records.ALL": [READ]}),
     "auth.scope_implies: 'Records.ALL' is the scope of no access level"),
    (lambda d: (d["connector"]["auth"]["access_levels"]["full"]["scopes"].append("Everything"),
                d["connector"]["auth"]["scope_implies"].update({"Everything": [ALL]})),
     "auth.scope_implies: 'Everything' implies a scope that implies Estimates.READ, which 'Everything' must then list too"),
    (lambda d: d["connector"]["auth"]["access_levels"]["read"]["scopes"].append("Users.READ"),
     "access level 'read' asks for Users.READ, which level 'full' does not cover"),
    (lambda d: _fn(d, "get_estimate").update(scopes=[READ, "Users.READ"]),
     "function get_estimate: scope(s) Users.READ not covered by level 'full'"),
    (lambda d: (d["connector"]["auth"]["access_levels"]["read"].update(scopes=[SETTINGS]),),
     "auth.identity 'list_estimates' is not served at access level 'read'"),
    (lambda d: (d["connector"].update(probe={"function": "list_estimates"}),
                d["connector"]["auth"].update(identity={"function": "list_estimates", "path": "data"}),
                d["connector"]["auth"]["access_levels"].update(support={"scopes": [SETTINGS]})),
     "auth.identity 'list_estimates' is not served at access level 'support'"),
], ids=["implies_unused", "implies_not_closed", "level_beyond_full", "function_beyond_full", "identity_not_served",
        "identity_not_served_at_every_level"])
def test_access_level_rule_refuses(mutate, message):
    description = _load(SELLSY)
    _levels(description)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, SELLSY)


def test_probe_must_be_served_at_every_level():
    description = _load(SELLSY)
    _levels(description)
    description["connector"]["probe"] = {"function": "list_estimates"}
    _fn(description, "list_estimates")["scopes"] = [READ]
    check(description, SELLSY)
    description["connector"]["auth"]["identity"] = {"function": "list_estimates", "path": "data"}
    _fn(description, "list_estimates")["scopes"] = [ALL]
    with pytest.raises(DescriptionError, match=re.escape("auth.identity 'list_estimates' is not served at access level 'read'")):
        check(description, SELLSY)


def test_closed_implication_passes():
    """Un droit impliqué qui en implique d'autres : la clé qui l'implique les liste aussi, et la lecture en un pas suffit."""
    description = _load(SELLSY)
    _levels(description)
    auth = description["connector"]["auth"]
    auth["access_levels"]["full"]["scopes"] = ["Everything", SETTINGS]
    auth["access_levels"]["read"]["scopes"] = [ALL, SETTINGS]
    auth["scope_implies"] = {"Everything": [ALL, READ], ALL: [READ]}
    check(description, SELLSY)


def test_gmail_levels_serve_reads_at_read_and_everything_at_full():
    """Ce que l'hôte filtre : au niveau `read`, les lectures seules ; à `full`, tout, comme sans niveaux."""
    description = check(_load(GMAIL), GMAIL)
    auth = description.data["connector"]["auth"]
    levels, implies = auth["access_levels"], auth["scope_implies"]
    functions = description.data["functions"]
    at_read = {f["name"] for f in functions if served(f, levels["read"]["scopes"], implies)}
    assert at_read == {f["name"] for f in functions if f["class"] == "read"}
    assert all(served(f, levels["full"]["scopes"], implies) for f in functions)


@pytest.mark.parametrize("path", [p for p in sorted(CONNECTORS.glob("*/connector.yaml"))
                                  if "access_levels" not in yaml.safe_load(p.read_text(encoding="utf-8"))["connector"]["auth"]],
                         ids=lambda p: p.parent.name)
def test_connector_without_levels_is_unchanged(path):
    """Un connecteur sans niveaux sort à l'octet près comme avant : son module est celui qui est commité, sans `accessLevels`
    ni `scopes` de fonction."""
    module = connector_module(check(_load(path), path))
    assert "accessLevels" not in module.source and "scopeImplies" not in module.source
    assert "\n  scopes: " not in module.source
    assert module.source == (OUTPUT / f"{module.connector}.ts").read_text(encoding="utf-8")


# --- Identité lue à une adresse complète (règles 5 et 7) ----------------------------

SHEETS = CONNECTORS / "google_sheets" / "connector.yaml"
USERINFO = "https://openid.example/userinfo"
OPENID = ["openid", "email"]


def _url_identity(d, levels=True):
    if levels:
        _levels(d)
        for level in d["connector"]["auth"]["access_levels"].values():
            level["scopes"] += OPENID
    else:
        _consent(d, scopes=[READ, *OPENID])
    d["connector"]["auth"]["identity"] = {"url": USERINFO, "path": "email", "scopes": list(OPENID)}


@pytest.mark.parametrize("levels", [True, False], ids=["with_levels", "without_levels"])
def test_url_identity_passes_and_is_carried_as_is(levels):
    description = _load(SELLSY)
    _url_identity(description, levels)
    source = connector_module(check(description, SELLSY)).source
    assert '"identity": {"url": "https://openid.example/userinfo", "path": "email", "scopes": ["openid", "email"]}' in source


@pytest.mark.parametrize("levels, mutate, message", [
    (True, lambda d: d["connector"]["auth"]["access_levels"]["read"]["scopes"].remove("email"),
     "auth.identity 'https://openid.example/userinfo' is not served at access level 'read': its scopes (openid, email) are not all covered"),
    (True, lambda d: d["connector"]["auth"]["identity"].update(scopes=["openid", "profile"]),
     "auth.identity 'https://openid.example/userinfo' is not served at access level 'read'"),
    (False, lambda d: d["connector"]["auth"].update(scopes=[READ, "openid"]),
     "auth.identity needs email, which auth.scopes does not ask for"),
    (False, lambda d: d["connector"]["auth"].pop("scopes"),
     "auth.identity needs email, openid, which auth.scopes does not ask for"),
], ids=["level_without_identity_scope", "identity_scope_in_no_level", "scopes_without_identity_scope", "no_scopes"])
def test_url_identity_rules_refuse(levels, mutate, message):
    description = _load(SELLSY)
    _url_identity(description, levels)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, SELLSY)


def test_sheets_identity_is_read_at_an_address_and_levels_split_reads():
    description = check(_load(SHEETS), SHEETS)
    auth = description.data["connector"]["auth"]
    assert auth["identity"] == {"url": "https://openidconnect.googleapis.com/v1/userinfo", "path": "email", "scopes": OPENID}
    levels, implies = auth["access_levels"], auth["scope_implies"]
    functions = description.data["functions"]
    assert {f["name"] for f in functions if served(f, levels["read"]["scopes"], implies)} == {
        f["name"] for f in functions if f["class"] == "read"}
    assert all(served(f, levels["full"]["scopes"], implies) for f in functions)


def test_microsoft_levels_serve_every_function_at_read():
    """Les treize fonctions de `microsoft` sont des lectures : le niveau `read` les sert toutes, avec les droits *.Read.All."""
    description = check(_load(MICROSOFT), MICROSOFT)
    auth = description.data["connector"]["auth"]
    assert "scopes" not in auth
    read = auth["access_levels"]["read"]["scopes"]
    assert not any("ReadWrite" in scope for scope in read)
    assert all(served(f, read, auth["scope_implies"]) for f in description.data["functions"])



# --- Niveaux d'accès d'un compte de service (règle 7) ---------------------------------

SEARCH_CONSOLE = CONNECTORS / "google_search_console" / "connector.yaml"
WEBMASTERS = "https://www.googleapis.com/auth/webmasters"
WEBMASTERS_READONLY = "https://www.googleapis.com/auth/webmasters.readonly"


def test_search_console_levels_read_by_default_and_add_sitemap_at_full():
    """Deux niveaux : `read` par défaut sert les lectures ; `full` sert tout, `add_sitemap` exige le droit d'écriture."""
    description = check(_load(SEARCH_CONSOLE), SEARCH_CONSOLE)
    auth = description.data["connector"]["auth"]
    assert auth["kind"] == "oauth2_jwt_bearer" and "scopes" not in auth
    assert auth["access_levels"] == {"read": {"scopes": [WEBMASTERS_READONLY]}, "full": {"scopes": [WEBMASTERS]}}
    assert auth["scope_implies"] == {WEBMASTERS: [WEBMASTERS_READONLY]} and auth["default_level"] == "read"
    functions = {f["name"]: f for f in description.data["functions"]}
    assert functions["add_sitemap"]["scopes"] == [WEBMASTERS] and functions["add_sitemap"]["class"] == "write"
    at_read = {name for name, f in functions.items() if served(f, auth["access_levels"]["read"]["scopes"], auth["scope_implies"])}
    assert at_read == {name for name, f in functions.items() if f["class"] == "read"} and "add_sitemap" not in at_read
    assert all(served(f, auth["access_levels"]["full"]["scopes"], auth["scope_implies"]) for f in functions.values())
    source = connector_module(description).source
    assert '"defaultLevel": "read"' in source and '"accessLevels": {' in source
    assert f'scopes: ["{WEBMASTERS}"],' in source


@pytest.mark.parametrize("mutate, message", [
    (lambda a, f: a.update(default_level="admin"), "auth.default_level 'admin' is not a declared access level"),
    (lambda a, f: f[0].update(scopes=[WEBMASTERS]), "probe 'list_sites' is not served at access level 'read'"),
    (lambda a, f: a.pop("scope_implies"),
     f"access level 'read' asks for {WEBMASTERS_READONLY}, which level 'full' does not cover"),
], ids=["default_level_unknown", "probe_not_served_at_read", "full_without_implication"])
def test_search_console_level_rules_refuse(mutate, message):
    description = _load(SEARCH_CONSOLE)
    mutate(description["connector"]["auth"], description["functions"])
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, SEARCH_CONSOLE)

# --- Règle 8 : compte de l'hôte et compte rattaché -----------------------------------

LINKEDIN = CONNECTORS / "linkedin_unipile" / "connector.yaml"
ACCOUNT_ID = "{account_id}"


def test_hosted_account_passes_and_is_carried():
    """Tous les champs du credential fournis par l'hôte : la description reste valide ; la sortie dit `holder` de chaque
    champ et l'identité du compte."""
    description = _load(LINKEDIN)
    assert all(field["holder"] == "host" for field in description["connector"]["credential"])
    source = connector_module(check(description, LINKEDIN)).source
    assert 'credential: [{"name": "api_key", "label": "Unipile API key", "secret": true, "holder": "host"}],' in source
    assert 'accountIdentity: {"name": "account_id", "label": "LinkedIn account", "pattern": "^[A-Za-z0-9_-]{1,64}$"},' in source
    assert 'baseUrl: "https://api.unipile.com/v2/{account_id}",' in source
    assert '"account_id"' not in source.split("export const connector")[0]


def test_holder_defaults_to_account_in_the_output():
    sellsy = connector_module(check(_load(SELLSY), SELLSY)).source
    assert '"secret": true, "holder": "account"}' in sellsy
    assert "accountIdentity" not in sellsy


@pytest.mark.parametrize("mutate", [
    lambda d: d["connector"].update(base_url="https://api.example.net/v2/{account_id}/linkedin"),
    lambda d: (d["connector"].update(settings=[{"name": "server", "label": "Server", "type": "url"}]),
               d["connector"].update(base_url="{server}/v2/{account_id}")),
    lambda d: (d["connector"].pop("base_url"),
               d["connector"].update(settings=[{"name": "region", "label": "Region", "type": "choice", "choices": ["us", "eu"]}]),
               d["connector"].update(base_urls={"setting": "region", "values": {
                   "us": "https://us.example.net/v2/{account_id}", "eu": "https://eu.example.net/v2/{account_id}"}})),
], ids=["in_the_path", "after_a_url_setting", "in_each_base_urls"])
def test_account_identity_after_the_host_passes(mutate):
    description = _load(LINKEDIN)
    mutate(description)
    check(description, LINKEDIN)


def _get_profile(d):
    return _fn(d, "get_profile")


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["connector"].update(base_url="https://api.unipile.com/v2"),
     "base_url does not cite the account identity {account_id}"),
    (lambda d: (d["connector"].pop("base_url"),
                d["connector"].update(settings=[{"name": "region", "label": "Region", "type": "choice", "choices": ["us", "eu"]}]),
                d["connector"].update(base_urls={"setting": "region", "values": {
                    "us": "https://us.example.net/v2/{account_id}", "eu": "https://eu.example.net/v2"}})),
     "base_urls.values.eu does not cite the account identity {account_id}"),
    (lambda d: (_get_profile(d)["input"]["properties"].update(account_id={"type": "string"}),
                _get_profile(d)["call"]["query"].update(account_id="account_id")),
     "function get_profile: input argument 'account_id' bears the name of the account identity"),
    (lambda d: _get_profile(d)["call"].update(path="/users/{identifier}/{account_id}"),
     "function get_profile: the path cites the account identity {account_id}"),
    (lambda d: _get_profile(d)["call"].update(query={"account_id": "with_sections"}),
     "function get_profile: query parameter 'account_id' bears the name of the account identity"),
    (lambda d: _fn(d, "send_message")["call"].update(body={"account_id": "text"}),
     "function send_message: body parameter 'account_id' bears the name of the account identity"),
    (lambda d: _get_profile(d)["call"].update(headers={"Account_Id": "with_sections"}),
     "function get_profile: headers parameter 'account_id' bears the name of the account identity"),
    (lambda d: _get_profile(d)["call"].update(constants={"query": {"account_id": "x"}}),
     "function get_profile: constant query 'account_id' bears the name of the account identity"),
    (lambda d: d["connector"].update(settings=[{"name": "account_id", "label": "Account", "type": "text", "pattern": "^[a-z]+$"}]),
     "account_identity 'account_id' is also the name of a setting"),
    (lambda d: d["connector"]["credential"].append({"name": "account_id", "label": "Account", "secret": False}),
     "account_identity 'account_id' is also the name of a credential field"),
    (lambda d: d["connector"].update(base_url="{account_id}/v2"),
     "base_url cites the account identity {account_id} elsewhere than after the host"),
    (lambda d: d["connector"].update(base_url="https://{account_id}.unipile.com/v2"),
     "base_url cites the account identity {account_id} elsewhere than after the host"),
    (lambda d: (d["connector"].pop("base_url"),
                d["connector"].update(settings=[{"name": "region", "label": "Region", "type": "choice", "choices": ["us", "eu"]}]),
                d["connector"].update(base_urls={"setting": "region", "values": {
                    "us": "https://us.example.net/v2/{account_id}", "eu": "https://eu.example.net/{region}/{account_id}"}})),
     "base_urls.values cites {region}, which is not the account identity"),
    (lambda d: (d["connector"].update(auth={"kind": "oauth2_client_credentials", "token_url": "https://login.example/{account_id}/token",
                                            "token_request": "form", "client_auth": "body", "client_id": "api_key",
                                            "client_secret": "api_key"})),
     "auth.token_url cites the account identity {account_id}, which only the base URL cites"),
], ids=["not_cited", "not_cited_in_each_base_urls", "input_argument", "path_parameter", "query_parameter",
        "body_parameter", "header_parameter", "constant", "setting_name", "credential_name", "at_the_head", "in_the_host",
        "base_urls_cites_a_setting", "token_url"])
def test_account_identity_rule_refuses(mutate, message):
    description = _load(LINKEDIN)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, LINKEDIN)


def test_base_urls_cite_nothing_without_an_account_identity():
    description = _load(SELLSY)
    description["connector"].pop("base_url")
    description["connector"]["settings"] = [{"name": "region", "label": "Region", "type": "choice", "choices": ["us", "eu"]}]
    description["connector"]["base_urls"] = {"setting": "region", "values": {
        "us": "https://us.example.net/{account_id}", "eu": "https://eu.example.net"}}
    with pytest.raises(DescriptionError, match=re.escape("base_urls.values cites {account_id}, which is not the account identity")):
        check(description, SELLSY)


# --- Règle 9 : `act`, ce qui engage ou détruit ---------------------------------------


@pytest.mark.parametrize("klass, declared, resolved", [
    ("read", None, False), ("write", None, True), ("sensitive", None, True),
    ("read", False, False), ("write", False, False), ("write", True, True), ("sensitive", True, True),
], ids=lambda v: str(v))
def test_act_resolves_from_the_class(klass, declared, resolved):
    function = _load(SELLSY)["functions"][1]
    function["class"] = klass
    if klass == "sensitive":
        function["confirm"] = {"summary": "Do it to {id}."}
    if declared is not None:
        function["act"] = declared
    assert f"  class: {klass!r},\n  act: {str(resolved).lower()},\n".replace("'", '"') in function_source("sellsy", function)


@pytest.mark.parametrize("klass, declared, message", [
    ("sensitive", False, "function get_estimate: a sensitive function cannot declare act: false"),
    ("read", True, "function get_estimate: a read function cannot declare act: true"),
], ids=["sensitive_not_act", "read_act"])
def test_act_rule_refuses(klass, declared, message):
    description = _load(SELLSY)
    function = _get_estimate(description)
    function["class"] = klass
    function["act"] = declared
    if klass == "sensitive":
        function["confirm"] = {"summary": "Do it to {id}."}
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, SELLSY)


def test_linkedin_sends_act_and_reads_do_not():
    module = connector_module(check(_load(LINKEDIN), LINKEDIN)).source
    for name in ("sendInvitation", "sendMessage", "endorseProfile"):
        assert f'export const {name} = defineFunction({{\n  name: ' in module
    # Les trois envois (invitation, message, recommandation) côté action ; vingt-quatre lectures et le choix d'un
    # contrat, une écriture qui reste du côté lecture.
    assert module.count("  act: true,\n") == 3 and module.count("  act: false,\n") == 25


# --- Règle 10 : le signal de refus du service ----------------------------------------

META_ADS = CONNECTORS / "meta_ads" / "connector.yaml"
GRAPHQL_REFUSAL = {"path": "errors.0.extensions.code", "equals": ["UNAUTHENTICATED"]}


def _error(d, status):
    return next(e for e in d["connector"]["errors"] if e["status"] == status)


@pytest.mark.parametrize("mutate", [
    lambda d: _error(d, 402).update(marks_account="quota_exhausted"),
    lambda d: d["connector"]["errors"].insert(0, {"status": 200, "body": GRAPHQL_REFUSAL, "code": "key_refused",
                                                  "message": "The key is refused.", "retryable": False,
                                                  "marks_account": "credential_rejected"}),
    lambda d: d["connector"]["errors"].insert(0, {"status": [400, 403], "body": {"path": "error", "equals": ["invalid_key"]},
                                                  "code": "key_refused", "message": "The key is refused.", "retryable": False,
                                                  "marks_account": "credential_rejected"}),
    lambda d: _get_estimate(d)["refusals"].append({"code": "credits_spent", "when": 429, "message": "Credits spent.",
                                                   "body": {"path": "reason", "equals": ["monthly_quota"]},
                                                   "marks_account": "quota_exhausted"}),
], ids=["status_402", "graphql_under_200", "body_under_400_and_403", "refusal_body_under_429"])
def test_account_signal_passes_and_is_carried(mutate):
    description = _load(SELLSY)
    mutate(description)
    source = connector_module(check(description, SELLSY)).source
    assert "marksAccount: " in source


def test_meta_ads_marks_on_graph_codes_under_a_400_before_the_plain_400():
    errors = _load(META_ADS)["connector"]["errors"]
    assert errors[0] == {"status": 400, "body": {"path": "error.code", "equals": [190, 102]}, "code": "authorization_expired",
                         "retryable": False, "marks_account": "credential_rejected",
                         "message": errors[0]["message"]}
    assert errors[1]["status"] == 400 and "body" not in errors[1] and "marks_account" not in errors[1]
    source = connector_module(check(_load(META_ADS), META_ADS)).source
    assert 'body: {"path": "error.code", "equals": [190, 102]}, marksAccount: "credential_rejected" }' in source


def test_nothing_marks_by_default():
    sellsy = connector_module(check(_load(SELLSY), SELLSY)).source
    assert "marksAccount" not in sellsy and 'body: {"path"' not in sellsy


@pytest.mark.parametrize("mutate, message", [
    (lambda d: _error(d, [401, 403]).update(marks_account="credential_rejected"),
     "errors 'access_rejected': a 403 without a body condition cannot mark credential_rejected"),
    (lambda d: _error(d, 429).update(marks_account="quota_exhausted"),
     "errors 'rate_limited': a 429 without a body condition cannot mark quota_exhausted"),
    (lambda d: _error(d, 402).update(marks_account="quota_exhausted", retryable=True),
     "errors 'plan_quota_reached': an entry that marks the account is not retryable"),
    (lambda d: d["connector"]["errors"].insert(0, {"status": 200, "code": "key_refused", "message": "Refused.", "retryable": False}),
     "errors 'key_refused': a 2xx status needs a body condition"),
    (lambda d: d["connector"]["errors"].append({"status": 400, "body": {"path": "error", "equals": ["invalid_key"]},
                                                "code": "key_refused", "message": "Refused.", "retryable": False,
                                                "marks_account": "credential_rejected"}),
     "errors 'key_refused': its body condition comes after an entry without one for status 400, so it is never read"),
    (lambda d: _get_estimate(d)["refusals"].append({"code": "key_refused", "when": "the error code is invalid_key",
                                                    "message": "Refused.", "marks_account": "credential_rejected"}),
     "function get_estimate: refusal 'key_refused': a refusal with body or marks_account needs an HTTP status in when"),
    (lambda d: _get_estimate(d)["refusals"].append({"code": "key_refused", "when": "the error code is invalid_key",
                                                    "message": "Refused.", "body": {"path": "error", "equals": ["invalid_key"]}}),
     "function get_estimate: refusal 'key_refused': a refusal with body or marks_account needs an HTTP status in when"),
    (lambda d: _get_estimate(d)["refusals"].append({"code": "forbidden", "when": 403, "message": "Refused.",
                                                    "marks_account": "credential_rejected"}),
     "function get_estimate: refusal 'forbidden': a 403 without a body condition cannot mark credential_rejected"),
    (lambda d: _get_estimate(d)["refusals"].append({"code": "empty", "when": 204, "message": "Nothing."}),
     "function get_estimate: refusal 'empty': a 2xx status needs a body condition"),
], ids=["403_alone", "429_alone", "retryable", "2xx_without_body", "shadowed", "mark_on_text_when", "body_on_text_when",
        "refusal_403_alone", "refusal_2xx_without_body"])
def test_account_signal_rule_refuses(mutate, message):
    description = _load(SELLSY)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, SELLSY)


# --- Règle 15 : la donnée personnelle ----------------------------------------------


def test_personal_property_passes_and_is_carried_as_is():
    description = _load(SELLSY)
    estimate = _get_estimate(description)
    estimate["input"]["properties"]["id"]["personal"] = True
    estimate["input"]["properties"]["fields"]["items"]["personal"] = True
    source = function_source("sellsy", _get_estimate(check(description, SELLSY).data))
    assert '"id": {"type": "integer", "personal": true}' in source
    assert source.count('"personal": true') == 2


def test_a_property_named_personal_is_not_a_mark():
    description = _load(SELLSY)
    estimate = _get_estimate(description)
    estimate["input"]["properties"]["personal"] = {"type": "string", "default": "no"}
    estimate["call"]["query"]["personal"] = "personal"
    check(description, SELLSY)


@pytest.mark.parametrize("mutate, message", [
    (lambda f: f["input"].update(personal=True), "personal marks a property of input, not input itself"),
    (lambda f: f["input"]["properties"]["id"].update(personal="yes"), "input.properties.id: personal must be true"),
    (lambda f: f["input"]["properties"]["id"].update(personal=False), "input.properties.id: personal must be true"),
    (lambda f: f["input"]["properties"]["embed"]["items"]["anyOf"][1].update(personal=1),
     "input.properties.embed.items.anyOf[1]: personal must be true"),
    (lambda f: f.update(output={"schema": {"type": "object", "additionalProperties": False,
                                           "properties": {"email": {"type": "string", "personal": True}}}}),
     "output.schema.properties.email: personal marks an input argument, not an output field"),
], ids=["root", "not_a_boolean", "false", "nested", "output"])
def test_misplaced_personal_is_refused(mutate, message):
    description = _load(SELLSY)
    mutate(_get_estimate(description))
    with pytest.raises(DescriptionError, match=re.escape(f"function get_estimate: {message}")):
        check(description, SELLSY)


# --- Règle 11 : le délai propre à une fonction ---------------------------------------


@pytest.mark.parametrize("own, connector", [(300, None), (10, None), (60, 120), (300, 120)],
                         ids=["longer_than_default", "shorter_than_default", "shorter", "longer"])
def test_function_timeout_passes_and_is_carried(own, connector):
    description = _load(SELLSY)
    if connector is None:
        description["connector"].pop("timeout_s", None)
    else:
        description["connector"]["timeout_s"] = connector
    _get_estimate(description)["timeout_s"] = own
    source = connector_module(check(description, SELLSY)).source
    assert f"  timeoutMs: {own * 1000},\n" in source
    assert f"  timeoutMs: {(connector or 30) * 1000},\n" in source


def test_function_without_timeout_carries_none():
    source = function_source("sellsy", _get_estimate(_load(SELLSY)))
    assert "timeoutMs" not in source


@pytest.mark.parametrize("connector", [None, 120], ids=["default", "declared"])
def test_function_timeout_equal_to_the_connector_is_refused(connector):
    description = _load(SELLSY)
    if connector is None:
        description["connector"].pop("timeout_s", None)
    else:
        description["connector"]["timeout_s"] = connector
    _get_estimate(description)["timeout_s"] = connector or 30
    with pytest.raises(DescriptionError, match=re.escape(
            f"function get_estimate: timeout_s {connector or 30} is the connector's timeout: declare none")):
        check(description, SELLSY)


def test_linkedin_company_page_has_its_own_timeout():
    module = connector_module(check(_load(LINKEDIN), LINKEDIN)).source
    company = module.split("export const getCompany = ")[1].split("export const ")[0]
    assert "  timeoutMs: 60000,\n" in company
    # Le connecteur, la page d'entreprise, et les huit recherches (60 s structurées, 75 s par adresse, comme le client).
    assert module.count("  timeoutMs: ") == 10 and "  timeoutMs: 120000,\n" in module
    assert module.count("  timeoutMs: 60000,\n") == 6 and module.count("  timeoutMs: 75000,\n") == 3


# --- Règle 12 : le coût, pire cas et quantité relue ------------------------------------


def _with_lists(d):
    """`get_estimate` avec deux listes requises d'au moins un élément : de quoi coûter à l'élément."""
    fn = _get_estimate(d)
    fn["input"]["required"] = ["id", "embed", "fields"]
    fn["input"]["properties"]["embed"]["minItems"] = 1
    fn["input"]["properties"]["fields"] = {**fn["input"]["properties"]["fields"], "minItems": 1}
    fn["examples"][0]["input"]["fields"] = ["id"]
    return fn


def test_cost_per_item_passes_and_is_carried():
    description = _load(SELLSY)
    _with_lists(description)["cost"] = {"per": "item", "items": "embed", "times": "fields", "unit": "fields",
                                        "settle": {"path": "meta.found"}}
    source = connector_module(check(description, SELLSY)).source
    assert '  cost: { per: "item", items: "embed", times: "fields", unit: "fields", settle: { path: "meta.found" } },\n' in source


def test_cost_quantity_is_carried():
    description = _load(SELLSY)
    _get_estimate(description)["cost"] = {"per": "request", "quantity": 3, "unit": "credits", "settle": {"path": "credits_used"}}
    source = function_source("sellsy", _get_estimate(check(description, SELLSY).data))
    assert '  cost: { per: "request", quantity: 3, unit: "credits", settle: { path: "credits_used" } },\n' in source
    table = python.declared([check(description, SELLSY)])["sellsy"]
    assert table["get_estimate"]["cost"]["quantity"] == 3


def test_body_condition_contains_is_carried_as_is():
    description = _load(SELLSY)
    description["connector"]["errors"].insert(0, {
        "status": 422, "body": {"path": "message", "contains": ["insufficient credits"]}, "code": "credits_spent",
        "message": "Out of credits.", "retryable": False, "marks_account": "quota_exhausted"})
    source = connector_module(check(description, SELLSY)).source
    assert 'body: {"path": "message", "contains": ["insufficient credits"]}, marksAccount: "quota_exhausted" }' in source


def test_cost_without_new_keys_is_carried_as_before():
    source = function_source("sellsy", _get_estimate(_load(SELLSY)))
    assert '  cost: { per: "request" },\n' in source
    description = _load(SELLSY)
    _get_estimate(description)["cost"] = {"per": "request", "unit": "credits", "settle": {"path": "credits_used"}}
    source = function_source("sellsy", _get_estimate(check(description, SELLSY).data))
    assert '  cost: { per: "request", unit: "credits", settle: { path: "credits_used" } },\n' in source


@pytest.mark.parametrize("mutate, message", [
    (lambda d: _with_lists(d)["cost"].update(per="item", items="nope", unit="u"), "cost.items names 'nope', which is not an input argument"),
    (lambda d: _with_lists(d)["cost"].update(per="item", items="id", unit="u"), "cost.items 'id' must be a required list argument"),
    (lambda d: (_with_lists(d)["input"].update(required=["id", "fields"]), _get_estimate(d)["cost"].update(per="item", items="embed", unit="u")),
     "cost.items 'embed' must be a required list argument"),
    (lambda d: (_with_lists(d)["input"]["properties"]["embed"].pop("minItems"), _get_estimate(d)["cost"].update(per="item", items="embed", unit="u")),
     "cost.items 'embed' must be a required list argument"),
    (lambda d: (_with_lists(d)["input"]["properties"]["embed"].update(minItems=0), _get_estimate(d)["cost"].update(per="item", items="embed", unit="u")),
     "cost.items 'embed' must be a required list argument"),
    (lambda d: _with_lists(d)["cost"].update(per="item", items="embed", times="embed", unit="u"), "cost.times names the items argument 'embed' again"),
    (lambda d: (_with_lists(d)["input"].update(required=["id", "embed"]), _get_estimate(d)["cost"].update(per="item", items="embed", times="fields", unit="u")),
     "cost.times 'fields' must be a required list argument"),
    (lambda d: (d["functions"][0]["input"].update(required=["fields"]), d["functions"][0]["input"]["properties"].update(
        fields={"type": "array", "items": {"type": "string"}, "minItems": 1}), d["functions"][0]["examples"][0]["input"].update(fields=["id"]),
                d["functions"][0].update(cost={"per": "item", "items": "fields", "unit": "u"})),
     "a paginated function costs per request or per page, without settle"),
    (lambda d: d["functions"][0].update(cost={"per": "page", "unit": "u", "settle": {"path": "used"}}),
     "a paginated function costs per request or per page, without settle"),
], ids=["items_unknown", "items_not_a_list", "items_not_required", "items_without_min", "items_min_zero", "times_is_items",
        "times_not_required", "item_paginated", "settle_paginated"])
def test_cost_rule_refuses(mutate, message):
    description = _load(SELLSY)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, SELLSY)


@pytest.mark.parametrize("cost", [
    {"per": "request", "unit": "credits", "settle": {"path": "used"}},
    {"per": "page"},
], ids=["settled", "page"])
def test_balance_probe_refuses_any_cost(cost):
    """Règle 5, quel que soit le coût : on ne paie pas pour savoir si l'on peut payer."""
    description = _load(PENNYLANE)
    _fn(description, "get_company")["cost"] = cost
    description["connector"]["probe"]["balance"] = {"path": "credits.remaining", "min": 1, "unit": "credits"}
    with pytest.raises(DescriptionError, match=re.escape("reads a balance, so it must declare no cost")):
        check(description, PENNYLANE)


# --- Règle 16 : le poids d'une valeur ----------------------------------------------------

FULLENRICH = CONNECTORS / "fullenrich" / "connector.yaml"


def _contact(d):
    """Le schéma d'un contact de `fullenrich.submit`, dont `enrich_fields` porte les valeurs pesées."""
    return _fn(d, "submit")["input"]["properties"]["contacts"]["items"]


def test_weighted_cost_passes_and_is_carried():
    source = function_source("fullenrich", _fn(check(_load(FULLENRICH), FULLENRICH).data, "submit"))
    assert ('  cost: { per: "item", items: "contacts", weights: { field: "enrich_fields", values: '
            '{"contact.work_emails": 1, "contact.personal_emails": 3, "contact.phones": 10} }, unit: "credits", '
            'settle: { path: "cost.credits" } },\n') in source
    assert '  async: { collect: "fullenrich.fetch", jobId: { from: "enrichment_id", to: "enrichment_id" }, noDeadline: true },\n' in source


@pytest.mark.parametrize("mutate, message", [
    (lambda d: _fn(d, "submit")["cost"]["weights"].update(field="nope"), "cost.weights.field 'nope' must be a property of each element of 'contacts'"),
    (lambda d: _contact(d).update(required=["first_name", "last_name"]), "cost.weights.field 'enrich_fields' must be a required list of each element"),
    (lambda d: _contact(d)["properties"]["enrich_fields"].pop("uniqueItems"), "must be a required list of each element"),
    (lambda d: _contact(d)["properties"]["enrich_fields"].update(minItems=0), "must be a required list of each element"),
    (lambda d: _contact(d)["properties"]["enrich_fields"]["items"].pop("enum"), "its allowed values (items.enum, texts) must be exactly the keys"),
    (lambda d: _fn(d, "submit")["cost"]["weights"]["values"].pop("contact.phones"), "must be exactly the keys of cost.weights.values"),
    (lambda d: _fn(d, "submit")["cost"]["weights"]["values"].update({"contact.company": 2}), "must be exactly the keys of cost.weights.values"),
], ids=["field_unknown", "field_not_required", "field_not_unique", "field_min_zero", "field_without_enum",
        "allowed_value_without_weight", "weight_of_no_allowed_value"])
def test_weighted_cost_rule_refuses(mutate, message):
    description = _load(FULLENRICH)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, FULLENRICH)


# --- Règle 13 : le travail asynchrone ----------------------------------------------------

AMPLITUDE = CONNECTORS / "amplitude" / "connector.yaml"


def _submit(d):
    return _fn(d, "request_cohort_download")


def test_async_pairs_are_carried():
    for path, submit, collect, job in [
        (META_ADS, "startInsightsReport", "meta_ads.get_report_status", "report_run_id"),
        (AMPLITUDE, "requestCohortDownload", "amplitude.get_cohort_download_status", "request_id"),
        (CONNECTORS / "fod" / "connector.yaml", "urbanStartRegulationIngestion", "fod.urban_get_regulation_ingestion", "idurba"),
    ]:
        module = connector_module(check(_load(path), path)).source
        block = module.split(f"export const {submit} = ")[1].split("export const ")[0]
        assert f'  async: {{ collect: "{collect}", jobId: {{ from: "{job}", to: "{job}" }} }},\n' in block
        assert module.count("  async: ") == 1


def test_async_settled_carries_its_delay():
    description = _load(AMPLITUDE)
    _submit(description).update(cost={"per": "request", "unit": "downloads", "settle": {"path": "used"}})
    _submit(description)["async"]["within_s"] = 600
    source = function_source("amplitude", _submit(check(description, AMPLITUDE).data))
    assert '  async: { collect: "amplitude.get_cohort_download_status", jobId: { from: "request_id", to: "request_id" }, withinMs: 600000 },\n' in source


@pytest.mark.parametrize("mutate, message", [
    (lambda d: _submit(d)["async"].update(collect="nope"), "async.collect names 'nope', which is not another function"),
    (lambda d: _submit(d)["async"].update(collect="request_cohort_download"), "which is not another function"),
    (lambda d: _fn(d, "get_cohort_download_status").update({"class": "write"}),
     "async.collect 'get_cohort_download_status' must be a read function with a call, not asynchronous itself"),
    (lambda d: _fn(d, "get_cohort_download_status").update({"async": {"collect": "list_cohorts", "job_id": {"from": "a", "to": "b"}}}),
     "not asynchronous itself"),
    (lambda d: _submit(d)["async"]["job_id"].update(to="cohort_id"), "async.job_id.to 'cohort_id' must be the only required argument"),
    (lambda d: _fn(d, "get_cohort_download_status")["input"].update(required=[]), "must be the only required argument"),
    (lambda d: _submit(d)["async"].update(within_s=60), "async.within_s 60 must exceed the timeout of the submission (60 s)"),
    (lambda d: (_submit(d).update(cost={"per": "request", "unit": "u", "settle": {"path": "used"}}), _submit(d)["async"].update(within_s=600),
                _fn(d, "get_cohort_download_status").update(cost={"per": "request", "unit": "u", "settle": {"path": "used"}})),
     "which must be neither paginated nor settled on its own"),
], ids=["collect_unknown", "collect_itself", "collect_not_a_read", "collect_asynchronous", "to_not_required",
        "to_not_the_only_one", "within_not_beyond_timeout", "collect_settled"])
def test_async_rule_refuses(mutate, message):
    description = _load(AMPLITUDE)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, AMPLITUDE)


# --- Règle 14 : les fichiers d'un corps multipart -------------------------------------------

PENNYLANE_FIRM = CONNECTORS / "pennylane_firm" / "connector.yaml"


def _upload(d):
    return _fn(d, "upload_dms_file")


def test_files_are_carried_only_where_declared():
    module = connector_module(check(_load(PENNYLANE_FIRM), PENNYLANE_FIRM)).source
    block = module.split("export const uploadDmsFile = ")[1].split("export const ")[0]
    assert '    body: {"parent_folder_id": "parent_folder_id", "name": "name"},\n    headers: {},\n    files: {"file": "file"},\n' in block
    assert module.count("    files: ") == 1
    sellsy = connector_module(check(_load(SELLSY), SELLSY)).source
    assert "files:" not in sellsy


@pytest.mark.parametrize("mutate, message", [
    (lambda d: _upload(d)["call"].update(method="GET"), "call.files needs a method that sends a body (POST, PUT, PATCH)"),
    (lambda d: _upload(d)["call"].update(method="DELETE"), "call.files needs a method that sends a body"),
    (lambda d: _upload(d).update(pagination={"kind": "cursor", "request_param": "name", "next": "next", "max_pages": 2}),
     "a function that sends files is not paginated"),
    (lambda d: _upload(d)["input"]["properties"].update(file={"type": ["string", "null"]}), "file argument 'file' must be type: string"),
    (lambda d: _upload(d)["call"].update(encode={"file": "json"}), "encode names 'file'"),
    (lambda d: _upload(d)["call"]["files"].update(name="name"), "argument 'name' goes to two places"),
    (lambda d: _upload(d)["call"]["files"].update({"parent_folder_id": "file"}), "argument 'file' goes to two places"),
    (lambda d: (_upload(d)["call"]["files"].update({"name": "file"}), _upload(d)["call"]["files"].pop("file")),
     "multipart part 'name' is both a file and a body value"),
    (lambda d: _upload(d)["call"].update(constants={"body": {"file": "x"}}), "multipart part 'file' is both a file and a body value"),
    (lambda d: _upload(d)["call"]["files"].update(other="nope"), "files 'other' names 'nope', which is not an input argument"),
], ids=["get", "delete", "paginated", "not_text", "encoded", "two_places", "two_parts", "part_also_body",
        "part_also_constant", "unknown_argument"])
def test_files_rule_refuses(mutate, message):
    description = _load(PENNYLANE_FIRM)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, PENNYLANE_FIRM)


# --- Règle 17 : le fichier rendu (output.file) -------------------------------------------------

FILE = {"from": "url", "path": "pdf_link", "types": {"pdf": ["application/pdf"]}, "name": {"template": "estimate-{id}.pdf"}}


def _rendered(d, **changes):
    """`get_estimate` rend un fichier : `output.file` à la place de sa sortie."""
    file = {**copy.deepcopy(FILE), **changes}
    _get_estimate(d)["output"] = {"file": {key: value for key, value in file.items() if value is not None}}
    return _get_estimate(d)


def test_file_output_is_carried_with_its_defaults_resolved():
    description = _load(SELLSY)
    _rendered(description)
    source = function_source("sellsy", _get_estimate(check(description, SELLSY).data))
    assert ('  output: { file: { from: "url", path: "pdf_link", auth: "none", types: {"pdf": ["application/pdf"]}, '
            'name: { template: "estimate-{id}.pdf" }, maxBytes: 52428800 } },\n') in source


def test_file_output_from_body_carries_neither_path_nor_auth():
    description = _load(SELLSY)
    _rendered(description, **{"from": "body", "path": None, "max_bytes": 1000,
                              "types": {"pdf": ["application/pdf"], "zip": ["application/zip"]},
                              "name": {"header": "Content-Disposition", "template": "estimate-{id}.pdf"}})
    source = function_source("sellsy", _get_estimate(check(description, SELLSY).data))
    assert ('  output: { file: { from: "body", types: {"pdf": ["application/pdf"], "zip": ["application/zip"]}, '
            'name: { header: "Content-Disposition", template: "estimate-{id}.pdf" }, maxBytes: 1000 } },\n') in source


def test_file_output_from_base64_carries_its_name_field_and_account_auth_only_for_url():
    description = _load(SELLSY)
    _rendered(description, **{"from": "base64", "path": "file.data", "name": {"path": "file.name", "template": "e-{id}.pdf"}})
    source = function_source("sellsy", _get_estimate(check(description, SELLSY).data))
    assert ('  output: { file: { from: "base64", path: "file.data", types: {"pdf": ["application/pdf"]}, '
            'name: { path: "file.name", template: "e-{id}.pdf" }, maxBytes: 52428800 } },\n') in source
    description = _load(SELLSY)
    _rendered(description, auth="account")
    source = function_source("sellsy", _get_estimate(check(description, SELLSY).data))
    assert 'from: "url", path: "pdf_link", auth: "account", ' in source


def test_file_output_goes_to_the_python_table_with_the_function_timeout():
    from fabrique import python

    description = _load(SELLSY)
    _rendered(description, auth="account", max_bytes=2048)
    _get_estimate(description)["timeout_s"] = 120
    table = python.declared([check(description, SELLSY)])
    assert table["sellsy"]["get_estimate"] == {
        "cost": {"per": "request"},
        "file": {"from": "url", "path": "pdf_link", "auth": "account", "types": {"pdf": ["application/pdf"]},
                 "name": {"template": "estimate-{id}.pdf"}, "max_bytes": 2048},
        "timeout_s": 120,
    }
    description = _load(SELLSY)
    _rendered(description, **{"from": "body", "path": None})
    _get_estimate(description).pop("cost")
    entry = python.declared([check(description, SELLSY)])["sellsy"]["get_estimate"]
    assert entry == {"file": {"from": "body", "types": {"pdf": ["application/pdf"]}, "name": {"template": "estimate-{id}.pdf"},
                              "max_bytes": 52428800}, "timeout_s": 30}


def test_only_the_declared_files_are_carried():
    carried = {}
    for path in CONNECTORS.glob("*/connector.yaml"):
        checked = check(_load(path), path)
        for function in checked.data["functions"]:
            if "file" in function.get("output", {}):
                carried[f"{checked.data['connector']['name']}.{function['name']}"] = function["output"]["file"]["from"]
    assert carried == {"aircall.get_call_recording": "url", "aircall.get_call_voicemail": "url",
                       "allo.get_call_recording": "url", "mistral.transcribe": "json",
                       "openai.edit_image": "base64", "openai.generate_image": "base64",
                       "threecx.download_recording": "body"}


def _paginated_file(d):
    d["functions"][0]["output"] = {"file": copy.deepcopy(FILE) | {"name": {"template": "estimates.pdf"}}}


@pytest.mark.parametrize("mutate, message", [
    (lambda d: _rendered(d)["output"].update(schema={"type": "object", "additionalProperties": False, "properties": {"id": {"type": "integer"}}}),
     "output.file: file_output_not_alone: excludes items, strip, projection and schema"),
    (lambda d: _rendered(d)["output"].update(items="data"), "output.file: file_output_not_alone: excludes items"),
    (lambda d: _rendered(d)["output"].update(strip=["a"]), "output.file: file_output_not_alone: excludes items"),
    (lambda d: _rendered(d)["output"].update(projection=["a"]), "output.file: file_output_not_alone: excludes items"),
    (_paginated_file, "a function that renders a file is not paginated"),
    (lambda d: _rendered(d, path=None), "output.file.path is required unless from: body"),
    (lambda d: _rendered(d, **{"from": "base64", "path": None}), "output.file.path is required unless from: body"),
    (lambda d: _rendered(d, **{"from": "body"}), "output.file.path is refused with from: body"),
    (lambda d: _rendered(d, **{"from": "body", "path": None, "auth": "none"}),
     "output.file: file_auth_not_url: auth is only for from: url"),
    (lambda d: _rendered(d, **{"from": "base64", "auth": "account"}), "output.file: file_auth_not_url: auth is only for from: url"),
    (lambda d: _rendered(d, **{"from": "base64", "name": {"header": "Content-Disposition", "template": "e-{id}.pdf"}}),
     "output.file.name.header is only for from: body or url"),
    (lambda d: _rendered(d, **{"from": "base64url", "name": {"header": "X-Name", "template": "e-{id}.pdf"}}),
     "output.file.name.header is only for from: body or url"),
    (lambda d: _rendered(d, **{"from": "body", "path": None, "name": {"path": "name", "template": "e-{id}.pdf"}}),
     "output.file.name.path is refused with from: body"),
    (lambda d: _rendered(d, name={"template": "estimate-{fields}.pdf"}),
     "output.file.name.template cites 'fields', which is not a required input argument"),
    (lambda d: _rendered(d, name={"template": "estimate-{nope}.pdf"}),
     "output.file.name.template cites 'nope', which is not a required input argument"),
    (lambda d: _rendered(d, name={"template": "estimate-{id}.doc"}),
     "output.file.name.template must end with a declared extension (.pdf)"),
    (lambda d: _rendered(d, name={"template": "estimate-{id}"}), "must end with a declared extension"),
    (lambda d: _rendered(d, name={"template": "estimate.{id}"}), "must end with a declared extension"),
    (lambda d: _rendered(d, types={"pdf": ["application/pdf"], "html": ["text/html"]}),
     "output.file.types: 'text/html' is a type a browser renders as a page or runs"),
    (lambda d: _rendered(d, types={"pdf": ["application/pdf"], "svg": ["image/svg+xml"]}), "'image/svg+xml' is a type a browser"),
    (lambda d: _rendered(d, types={"pdf": ["application/pdf"], "xml": ["application/xml"]}), "'application/xml' is a type a browser"),
    (lambda d: _rendered(d, types={"pdf": ["application/pdf"], "xml": ["text/xml"]}), "'text/xml' is a type a browser"),
    (lambda d: _rendered(d, types={"pdf": ["application/pdf"], "xhtml": ["application/xhtml+xml"]}), "is a type a browser"),
    (lambda d: _rendered(d, types={"pdf": ["application/pdf"], "js": ["text/javascript"]}), "'text/javascript' is a type a browser"),
    (lambda d: _rendered(d, types={"pdf": ["application/pdf"], "mjs": ["application/javascript"]}), "is a type a browser"),
    (lambda d: _rendered(d, types={"pdf": ["application/pdf", "application/ecmascript"]}), "is a type a browser"),
    (lambda d: _rendered(d, max_bytes=0), "does not match the description schema"),
    (lambda d: _rendered(d, max_bytes=52428801), "does not match the description schema"),
    (lambda d: _rendered(d, **{"from": "body", "path": None}).update(
        expect=[{"kind": "non_empty", "refusal": "estimate_not_found", "path": "id"}]),
     "expect is refused with output.file from: body, which has no JSON"),
    (lambda d: _rendered(d, **{"from": "json", "path": None, "types": None, "auth": "none",
                               "name": {"template": "estimate-{id}.json"}}),
     "output.file: file_auth_not_url: auth is only for from: url"),
    (lambda d: _rendered(d, **{"from": "json", "name": {"template": "estimate-{id}.json"}}),
     "output.file: file_json_types: types is refused with from: json, whose type is application/json"),
    (lambda d: _rendered(d, **{"from": "json", "types": None, "name": {"template": "estimate-{id}.pdf"}}),
     "output.file: file_json_name: the name of a JSON file is its template alone, which ends with .json"),
    (lambda d: _rendered(d, **{"from": "json", "types": None, "name": {"template": "estimate-{id}.JSON"}}),
     "output.file: file_json_name"),
    (lambda d: _rendered(d, **{"from": "json", "types": None,
                               "name": {"header": "Content-Disposition", "template": "estimate-{id}.json"}}),
     "output.file: file_json_name"),
    (lambda d: _rendered(d, **{"from": "json", "types": None, "name": {"path": "name", "template": "estimate-{id}.json"}}),
     "output.file: file_json_name"),
    (lambda d: _rendered(d, **{"from": "json", "types": None, "name": {"template": "estimate-{id}.json"},
                               "max_bytes": 4194305}),
     "output.file: file_json_max_bytes: max_bytes is 4194304 at most with from: json"),
    (lambda d: _rendered(d, **{"from": "json", "types": None, "name": {"template": "estimate-{nope}.json"}}),
     "output.file.name.template cites 'nope', which is not a required input argument"),
    (lambda d: _rendered(d, **{"from": "json", "types": None, "name": {"template": "estimate-{id}.json"}})["output"].update(
        strip=["a"]),
     "output.file: file_output_not_alone"),
    (lambda d: _rendered(d, **{"from": "json", "path": None, "name": {"template": "estimate-{id}.json"}}),
     "output.file: file_json_types"),
], ids=["with_schema", "with_items", "with_strip", "with_projection", "paginated", "url_without_path",
        "base64_without_path", "body_with_path", "auth_with_body", "auth_with_base64", "json_with_auth", "json_with_types",
        "json_template_pdf", "json_template_upper_case", "json_named_by_header", "json_named_by_field",
        "json_max_bytes_beyond_4_mb", "json_template_unknown_argument", "json_with_strip", "json_types_without_path",
        "header_with_base64",
        "header_with_base64url", "name_path_with_body", "template_optional_argument", "template_unknown_argument",
        "template_undeclared_extension", "template_without_extension", "template_extension_from_argument",
        "type_html", "type_svg", "type_xml", "type_text_xml", "type_xhtml", "type_javascript", "type_application_javascript",
        "type_ecmascript", "max_bytes_zero", "max_bytes_beyond", "expect_with_body"])
def test_file_output_rule_refuses(mutate, message):
    description = _load(SELLSY)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, SELLSY)


@pytest.mark.parametrize("role, message", [
    ("request_cohort_download", "a function that renders a file is not asynchronous"),
    ("get_cohort_download_status", "collects the asynchronous job of 'request_cohort_download'"),
], ids=["asynchronous", "collect"])
def test_file_output_is_neither_asynchronous_nor_a_collect(role, message):
    description = _load(AMPLITUDE)
    function = _fn(description, role)
    argument = function["input"]["required"][0] if function["input"].get("required") else None
    template = f"cohort-{{{argument}}}.csv" if argument else "cohort.csv"
    function["output"] = {"file": {"from": "body", "types": {"csv": ["text/csv"]}, "name": {"template": template}}}
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, AMPLITUDE)


@pytest.mark.parametrize("role, message", [
    ("request_cohort_download", "a function that renders a file is not asynchronous"),
    ("get_cohort_download_status", "collects the asynchronous job of 'request_cohort_download'"),
], ids=["asynchronous", "collect"])
def test_a_json_file_is_neither_asynchronous_nor_a_collect(role, message):
    description = _load(AMPLITUDE)
    function = _fn(description, role)
    argument = function["input"]["required"][0] if function["input"].get("required") else None
    template = f"cohort-{{{argument}}}.json" if argument else "cohort.json"
    function["output"] = {"file": {"from": "json", "name": {"template": template}}}
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, AMPLITUDE)


@pytest.mark.parametrize("changes", [{}, {"from": "json", "path": None, "types": None,
                                          "name": {"template": "estimate-{id}.json"}}], ids=["url", "json"])
def test_a_background_function_renders_a_file(changes):
    """Règle 17 : une fonction longue (règle 21) rend un fichier, qui vient du même appel ; ni la table Python ni le
    fichier rendu n'en disent rien de plus."""
    description = _load(SELLSY)
    function = _rendered(description, **changes)
    function.update(background=True, timeout_s=120)
    checked = check(description, SELLSY)
    source = function_source("sellsy", _get_estimate(checked.data))
    assert "  timeoutMs: 120000,\n  background: true,\n" in source
    assert "  output: { file: { from: " in source
    entry = python.declared([checked])["sellsy"]["get_estimate"]
    assert "background" not in entry and entry["timeout_s"] == 120 and "file" in entry


@pytest.mark.parametrize("role, message", [
    ("request_cohort_download", "function request_cohort_download: background: background_async"),
    ("get_cohort_download_status", "function get_cohort_download_status: background: background_collect"),
], ids=["asynchronous", "collect"])
def test_a_background_file_is_still_neither_asynchronous_nor_a_collect(role, message):
    """Règle 17 : `background` n'ouvre pas le fichier rendu à une fonction asynchrone ni à sa relève, que la règle 21
    refuse d'abord."""
    description = _load(AMPLITUDE)
    function = _fn(description, role)
    argument = function["input"]["required"][0] if function["input"].get("required") else None
    template = f"cohort-{{{argument}}}.json" if argument else "cohort.json"
    function["output"] = {"file": {"from": "json", "name": {"template": template}}}
    function.update(background=True, timeout_s=120)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, AMPLITUDE)


def test_a_function_without_background_carries_none():
    source = function_source("sellsy", _get_estimate(_load(SELLSY)))
    assert "background" not in source


def test_a_paginated_function_renders_no_json_file():
    description = _load(SELLSY)
    description["functions"][0]["output"] = {"file": {"from": "json", "name": {"template": "estimates.json"}}}
    with pytest.raises(DescriptionError, match="a function that renders a file is not paginated"):
        check(description, SELLSY)


def test_a_json_file_is_carried_without_types_with_its_4_mb_default():
    from fabrique import python

    description = _load(SELLSY)
    _rendered(description, **{"from": "json", "path": None, "types": None, "name": {"template": "estimate-{id}.json"}})
    checked = check(description, SELLSY)
    source = function_source("sellsy", _get_estimate(checked.data))
    assert '  output: { file: { from: "json", name: { template: "estimate-{id}.json" }, maxBytes: 4194304 } },\n' in source
    assert python.declared([checked])["sellsy"]["get_estimate"]["file"] == {
        "from": "json", "name": {"template": "estimate-{id}.json"}, "max_bytes": 4194304}


def test_a_json_file_carries_its_path_its_bound_and_admits_expect():
    description = _load(SELLSY)
    _rendered(description, **{"from": "json", "path": "data.result", "types": None, "max_bytes": 4194304,
                              "name": {"template": "estimate-{id}.json"}})
    _get_estimate(description)["expect"] = [{"kind": "non_empty", "refusal": "estimate_not_found", "path": "id"}]
    source = function_source("sellsy", _get_estimate(check(description, SELLSY).data))
    assert ('  output: { file: { from: "json", path: "data.result", name: { template: "estimate-{id}.json" }, '
            'maxBytes: 4194304 } },\n') in source


def test_a_json_file_needs_no_types_but_every_other_file_does():
    description = _load(SELLSY)
    _rendered(description, **{"from": "body", "path": None, "types": None})
    with pytest.raises(DescriptionError, match="does not match the description schema"):
        check(description, SELLSY)


# --- Les cas communs du fichier rendu ----------------------------------------------------------

FILE_REFUSALS = {"file_missing", "file_download_refused", "file_too_large", "file_type_refused", "file_unreadable",
                 "file_foreign_origin"}


def test_file_cases_cover_the_format():
    from fabrique import file_cases

    loaded = file_cases.load()
    functions = [case["function"] for case in loaded.cases]
    files = [f["output"]["file"] for f in functions]
    assert {f["from"] for f in files} == {"body", "url", "base64", "base64url", "json"}
    assert {f.get("auth", "none") for f in files if f["from"] == "url"} == {"none", "account"}
    assert {case["refusal"] for case in loaded.cases if "refusal" in case} == FILE_REFUSALS
    assert file_cases.REFUSALS == tuple(sorted(FILE_REFUSALS))
    assert {r["refusal"] for r in loaded.refused} == set(file_cases.DECLARATION_REFUSALS)
    assert file_cases.DECLARATION_REFUSALS == ("file_output_not_alone", "file_auth_not_url", "file_json_types",
                                               "file_json_name", "file_json_max_bytes")
    names = {case["name"] for case in loaded.cases}
    for expected in ("name_by_filename_star", "name_by_filename", "name_by_field", "name_by_template",
                     "undeclared_extension_falls_to_template", "content_length_beyond", "body_beyond_without_content_length",
                     "html_under_200_refused", "octet_stream_admitted", "url_absent", "url_empty", "url_http",
                     "url_foreign_origin_under_account", "download_403", "base64_unreadable", "json_whole_answer",
                     "json_branch_at_path", "json_path_absent", "json_path_empty_text", "json_beyond_the_bound",
                     "json_numbers_as_the_host_writes_them"):
        assert expected in names, expected


def _file_cases_write(tmp_path, mutate):
    from fabrique import file_cases

    data = yaml.safe_load(file_cases.CASES.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "file.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _file_case(data, name):
    return next(case for case in data["cases"] if case["name"] == name)


def _refused_file(data, name):
    return next(r for r in data["refused"] if r["name"] == name)["function"]["output"]["file"]


@pytest.mark.parametrize("mutate, message", [
    (lambda d: _file_case(d, "name_by_template")["function"]["output"]["file"].update(path="x"),
     "output.file.path is refused with from: body"),
    (lambda d: _file_case(d, "name_by_template").update(refusal="file_lost"), "does not match the file cases form"),
    (lambda d: _file_case(d, "name_by_template").pop("file"), "does not match the file cases form"),
    (lambda d: _file_case(d, "url_empty").pop("answer"), "does not match the file cases form"),
    (lambda d: _file_case(d, "name_by_template").update(download={"status": 200, "headers": {}, "text": "x"}),
     "only a function with from: url downloads"),
    (lambda d: _file_case(d, "name_by_template")["function"].pop("output"), "a case function renders a file"),
    (lambda d: _file_case(d, "json_whole_answer")["file"].pop("text"), "the file of a function with from: json says its bytes"),
    (lambda d: _file_case(d, "json_whole_answer")["file"].update(size=3), "file.size is not the size of file.text"),
    (lambda d: next(r for r in d["refused"] if r["name"] == "json_with_types").update(refusal="file_json_name"),
     "refused function json_with_types: the fabrique answers file_json_types, the file says file_json_name"),
    (lambda d: next(r for r in d["refused"] if r["name"] == "json_with_types")["function"]["output"]["file"].pop("types"),
     "refused function json_with_types is accepted by the fabrique"),
    (lambda d: next(r for r in d["refused"] if r["name"] == "json_with_types")["function"].update(name="Bad Name"),
     "refused function json_with_types must match the description schema"),
    (lambda d: _refused_file(d, "json_with_types").update(name={"template": "report-{nope}.json"}) or
     _refused_file(d, "json_with_types").pop("types"),
     "refused function json_with_types is refused by another rule than the coded ones of 17"),
    (lambda d: next(r for r in d["refused"] if r["name"] == "json_with_types")["function"].update(
        call={"method": "GET", "path": "/reports/{id}/{nope}"}),
     "refused function json_with_types is refused by another rule than the coded ones of 17"),
    (lambda d: d.update(refused=[r for r in d["refused"] if r["refusal"] != "file_json_max_bytes"]),
     "no refused function for file_json_max_bytes"),
], ids=["rule_17", "unknown_refusal", "no_outcome", "no_answer", "download_without_url", "no_file", "json_without_text",
        "json_size_not_text", "refused_other_code", "refused_accepted", "refused_off_schema", "refused_by_another_rule",
        "refused_first_by_another_rule",
        "refused_code_missing"])
def test_a_file_case_is_checked_like_a_description(tmp_path, mutate, message):
    from fabrique import file_cases

    with pytest.raises(DescriptionError, match=re.escape(message)):
        file_cases.load(_file_cases_write(tmp_path, mutate))
