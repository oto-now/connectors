"""Les fichiers de description de connecteur respectent leur schéma.

Chaque `connectors/<nom>/connector.yaml` est validé contre
`connectors/connector.schema.json` (JSON Schema 2020-12), et chaque exemple d'une
fonction contre le schéma d'entrée de cette fonction. Des variantes invalides du
fichier Sellsy prouvent que la validation refuse ce qu'elle doit refuser.
"""
from __future__ import annotations

import copy
import json
import pathlib

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = pathlib.Path(__file__).resolve().parent.parent / "connectors"
SCHEMA = json.loads((ROOT / "connector.schema.json").read_text(encoding="utf-8"))
DESCRIPTIONS = sorted(ROOT.glob("*/connector.yaml"))


def _load(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _validate(description: dict) -> None:
    Draft202012Validator(SCHEMA).validate(description)
    for function in description["functions"]:
        validator = Draft202012Validator(function["input"])
        for example in function["examples"]:
            validator.validate(example["input"])


def test_schema_is_valid_json_schema():
    Draft202012Validator.check_schema(SCHEMA)


def test_sellsy_description_is_present():
    assert ROOT / "sellsy" / "connector.yaml" in DESCRIPTIONS


@pytest.mark.parametrize("path", DESCRIPTIONS, ids=lambda p: p.parent.name)
def test_description_is_valid(path):
    description = _load(path)
    _validate(description)
    assert description["connector"]["name"] == path.parent.name


def _sellsy() -> dict:
    return copy.deepcopy(_load(ROOT / "sellsy" / "connector.yaml"))


def _drop_class(d):
    del d["functions"][0]["class"]


def _unknown_key(d):
    d["connector"]["unknown"] = True


def _call_and_handwritten(d):
    d["functions"][0]["handwritten"] = {
        "python": {"module": "oto.tools.sellsy.client", "function": "list_estimates"},
        "typescript": {"file": "sellsy.ts", "export": "listEstimates"},
    }


def _neither_call_nor_handwritten(d):
    del d["functions"][1]["call"]


def _sensitive_without_confirm(d):
    d["functions"][0]["class"] = "sensitive"


def _input_not_strict(d):
    d["functions"][0]["input"]["additionalProperties"] = True


def _no_examples(d):
    d["functions"][0]["examples"] = []


def _example_outside_input(d):
    d["functions"][0]["examples"][0]["input"]["limit"] = 1000


def _example_unknown_argument(d):
    d["functions"][1]["examples"][0]["input"]["nope"] = 1


def _embed_not_documented(d):
    d["functions"][1]["examples"][0]["input"]["embed"] = ["owner"]


def _auth_missing_token_url(d):
    del d["connector"]["auth"]["token_url"]


def _credential_missing(d):
    del d["connector"]["credential"]


def _quota_without_platform(d):
    d["connector"]["quota"] = {"per_month": 1000}


def _bad_version(d):
    d["connector"]["version"] = "1.0"


def _bad_exposure(d):
    d["exposure"]["mode"] = "per_tool"


def _output_schema_not_strict(d):
    d["functions"][0]["output"]["schema"]["additionalProperties"] = True


def _output_schema_no_properties(d):
    del d["functions"][0]["output"]["schema"]["properties"]


def _rate_limit_without_window(d):
    d["connector"]["rate_limit"] = {"requests": 4}


def _constant_header_not_a_string(d):
    d["connector"]["headers"] = {"X-Api-Version": 2}


def _unknown_encoding(d):
    d["functions"][0]["call"]["encode"] = {"limit": "csv"}


def _unknown_check_kind(d):
    d["functions"][0]["checks"] = [{"kind": "positive", "refusal": "invalid_request", "items": "limit", "fields": ["a", "b"]}]


def _expectation_without_path(d):
    d["functions"][1]["expect"] = [{"kind": "non_empty", "refusal": "estimate_not_found"}]


def _probe_extra_key(d):
    d["connector"]["probe"] = {"function": "get_estimate", "covers": "quota"}


def _empty_stop_flag(d):
    d["functions"][0]["pagination"]["more"] = ""


def _client_auth_missing(d):
    del d["connector"]["auth"]["client_auth"]


def _token_url_and_token_urls(d):
    d["connector"]["auth"]["token_urls"] = {"setting": "region", "values": {"us": "https://a.example", "eu": "https://b.example"}}


def _base_url_and_base_urls(d):
    d["connector"]["base_urls"] = {"setting": "region", "values": {"us": "https://a.example", "eu": "https://b.example"}}


def _single_choice(d):
    d["connector"]["settings"] = [{"name": "region", "label": "Region", "type": "choice", "choices": ["us"]}]


def _text_without_pattern(d):
    d["connector"]["settings"] = [{"name": "domain", "label": "Domain", "type": "text"}]


def _text_pattern_not_anchored(d):
    d["connector"]["settings"] = [{"name": "domain", "label": "Domain", "type": "text", "pattern": "[a-z]+"}]


def _url_setting_with_choices(d):
    d["connector"]["settings"] = [{"name": "server", "label": "Server", "type": "url", "choices": ["a", "b"]}]


def _template_trailing_slash(d):
    d["connector"]["base_url"] = "https://{domain}.example.net/"


def _api_key_query_with_prefix(d):
    d["connector"]["auth"] = {"kind": "api_key", "in": "query", "name": "key", "prefix": "Key ", "key": "client_id"}


def _basic_without_password(d):
    d["connector"]["auth"] = {"kind": "basic", "username": "client_id"}


def _basic_with_empty_username(d):
    d["connector"]["auth"] = {"kind": "basic", "username": "", "password": "client_secret"}


def _api_key_without_place(d):
    d["connector"]["auth"] = {"kind": "api_key", "name": "X-Key", "key": "client_id"}


def _oauth2_user(d, **changes):
    d["connector"]["auth"] = {
        "kind": "oauth2_user", "authorize_url": "https://login.example/authorize", "token_url": "https://login.example/token",
        "client_auth": "body", "refresh": "refresh_token", "rotates": False,
        "identity": {"function": "list_estimates", "path": "data"},
    } | changes
    del d["connector"]["credential"]


def _oauth2_user_with_token_credential(d):
    _oauth2_user(d)
    d["connector"]["credential"] = [{"name": "access_token", "label": "Access token", "secret": True}]


def _oauth2_user_with_client_id(d):
    _oauth2_user(d, client_id="client_id")


def _oauth2_user_without_identity(d):
    _oauth2_user(d)
    del d["connector"]["auth"]["identity"]


def _rotates_without_refresh_token(d):
    _oauth2_user(d, refresh="none")


def _exchange_without_its_grant(d):
    _oauth2_user(d, refresh="exchange")
    del d["connector"]["auth"]["rotates"]


def _authorize_param_set_by_the_host(d):
    _oauth2_user(d, authorize_params={"redirect_uri": "https://evil.example"})


def _unknown_pkce(d):
    _oauth2_user(d, pkce="plain")


def _jwt_bearer(d, **changes):
    """Sellsy en compte de service : un seul champ, le fichier JSON de la clé."""
    d["connector"]["auth"] = {
        "kind": "oauth2_jwt_bearer", "token_url": "https://oauth2.example.test/token",
        "scopes": ["https://api.example.test/auth/read"], "key": "service_account_key",
    } | changes
    d["connector"]["credential"] = [{"name": "service_account_key", "label": "Service account key (JSON)", "secret": True}]


def _jwt_bearer_without_token_url(d):
    _jwt_bearer(d)
    del d["connector"]["auth"]["token_url"]


def _jwt_bearer_without_scopes(d):
    _jwt_bearer(d)
    del d["connector"]["auth"]["scopes"]


def _jwt_bearer_with_empty_scopes(d):
    _jwt_bearer(d, scopes=[])


def _jwt_bearer_without_key(d):
    _jwt_bearer(d)
    del d["connector"]["auth"]["key"]


def _jwt_bearer_token_url_template(d):
    _jwt_bearer(d, token_url="https://{tenant}.example.test/token")


def _jwt_bearer_token_url_with_query(d):
    _jwt_bearer(d, token_url="https://oauth2.example.test/token?tenant=x")


def _jwt_bearer_token_url_not_https(d):
    _jwt_bearer(d, token_url="http://oauth2.example.test/token")


def _jwt_bearer_with_token_urls(d):
    _jwt_bearer(d)
    d["connector"]["auth"]["token_urls"] = {"setting": "region", "values": {"us": "https://us.example.test/token"}}


def _jwt_bearer_with_levels(d):
    """Sellsy en compte de service à niveaux : `read` par défaut, `full` qui implique la lecture."""
    _jwt_bearer(d)
    auth = d["connector"]["auth"]
    del auth["scopes"]
    auth.update(access_levels={"read": {"scopes": [READ]}, "full": {"scopes": [ALL]}}, scope_implies={ALL: [READ]},
                default_level="read")
    for function in d["functions"]:
        function["scopes"] = [READ]


def _jwt_bearer_with_levels_and_scopes(d):
    _jwt_bearer_with_levels(d)
    d["connector"]["auth"]["scopes"] = [ALL]


def _jwt_bearer_with_levels_without_full(d):
    _jwt_bearer_with_levels(d)
    d["connector"]["auth"]["access_levels"] = {"read": {"scopes": [READ]}, "write": {"scopes": [ALL]}}


def _jwt_bearer_function_scopes_without_levels(d):
    _jwt_bearer(d)
    d["functions"][0]["scopes"] = ["https://api.example.test/auth/read"]


def _jwt_bearer_scope_implies_without_levels(d):
    _jwt_bearer(d, scope_implies={ALL: [READ]})


def _jwt_bearer_default_level_without_levels(d):
    _jwt_bearer(d, default_level="full")


def _jwt_bearer_function_without_scopes_with_levels(d):
    _jwt_bearer_with_levels(d)
    del d["functions"][1]["scopes"]


def _jwt_bearer_with_sub(d):
    _jwt_bearer(d, sub="someone@example.test")


def _jwt_bearer_without_credential(d):
    _jwt_bearer(d)
    del d["connector"]["credential"]


def test_jwt_bearer_mutation_base_is_valid():
    """Les variantes `oauth2_jwt_bearer` ci-dessous partent d'une description valide : chacune n'échoue que par sa faute."""
    description = _sellsy()
    _jwt_bearer(description)
    _validate(description)


def test_jwt_bearer_with_access_levels_is_valid():
    """Un compte de service peut déclarer des niveaux d'accès, comme un consentement, et un niveau par défaut."""
    description = _sellsy()
    _jwt_bearer_with_levels(description)
    _validate(description)


@pytest.mark.parametrize("auth", [
    {"kind": "basic", "username": "client_id", "password": "client_secret"},
    {"kind": "basic", "password": "client_secret"},
], ids=["basic", "basic_without_username"])
def test_basic_auth_is_valid(auth):
    """`username` est facultatif : absent, l'identifiant est vide (une clé seule, en mot de passe)."""
    description = _sellsy()
    description["connector"]["auth"] = auth
    _validate(description)


def test_oauth2_user_mutation_base_is_valid():
    """Les variantes `oauth2_user` ci-dessous partent d'une description valide : chacune n'échoue que par sa faute."""
    description = _sellsy()
    _oauth2_user(description)
    _validate(description)


@pytest.mark.parametrize("mutate", [
    _drop_class, _unknown_key, _call_and_handwritten, _neither_call_nor_handwritten,
    _sensitive_without_confirm, _input_not_strict, _no_examples, _example_outside_input,
    _example_unknown_argument, _embed_not_documented, _auth_missing_token_url,
    _credential_missing, _quota_without_platform, _bad_version, _bad_exposure,
    _output_schema_not_strict, _output_schema_no_properties,
    _rate_limit_without_window, _constant_header_not_a_string, _unknown_encoding, _unknown_check_kind,
    _expectation_without_path, _probe_extra_key, _empty_stop_flag,
    _client_auth_missing, _token_url_and_token_urls, _base_url_and_base_urls, _single_choice, _text_without_pattern,
    _text_pattern_not_anchored, _url_setting_with_choices, _template_trailing_slash, _api_key_query_with_prefix,
    _api_key_without_place, _basic_without_password, _basic_with_empty_username, _oauth2_user_with_token_credential, _oauth2_user_with_client_id,
    _oauth2_user_without_identity, _rotates_without_refresh_token, _exchange_without_its_grant,
    _authorize_param_set_by_the_host, _unknown_pkce,
    _jwt_bearer_without_token_url, _jwt_bearer_without_scopes, _jwt_bearer_with_empty_scopes, _jwt_bearer_without_key,
    _jwt_bearer_token_url_template, _jwt_bearer_token_url_with_query, _jwt_bearer_token_url_not_https,
    _jwt_bearer_with_token_urls, _jwt_bearer_with_sub, _jwt_bearer_without_credential,
    _jwt_bearer_with_levels_and_scopes, _jwt_bearer_with_levels_without_full, _jwt_bearer_function_scopes_without_levels,
    _jwt_bearer_scope_implies_without_levels, _jwt_bearer_default_level_without_levels,
    _jwt_bearer_function_without_scopes_with_levels,
], ids=lambda f: f.__name__.lstrip("_"))
def test_invalid_description_is_rejected(mutate):
    description = _sellsy()
    mutate(description)
    with pytest.raises(ValidationError):
        _validate(description)


# --- Niveaux d'accès (docs/conception/niveaux-d-acces.md) ----------------------------

READ = "Estimates.READ"
ALL = "Estimates.ALL"


def _with_levels(d):
    """Sellsy en consentement d'une personne, avec un niveau `read` et un niveau `full`."""
    _oauth2_user(d, access_levels={"read": {"scopes": [READ]}, "full": {"scopes": [ALL]}}, scope_implies={ALL: [READ]})
    for function in d["functions"]:
        function["scopes"] = [READ]


def test_access_levels_base_is_valid():
    """Les variantes ci-dessous partent d'une description à niveaux valide : chacune n'échoue que par sa faute."""
    description = _sellsy()
    _with_levels(description)
    _validate(description)


def test_scope_implies_is_optional():
    description = _sellsy()
    _with_levels(description)
    del description["connector"]["auth"]["scope_implies"]
    description["connector"]["auth"]["access_levels"]["full"]["scopes"] = [ALL, READ]
    _validate(description)


@pytest.mark.parametrize("path", [p for p in DESCRIPTIONS if "access_levels" not in _load(p)["connector"]["auth"]],
                         ids=lambda p: p.parent.name)
def test_connector_without_levels_declares_no_function_scopes(path):
    """Un connecteur sans niveaux ne change pas : ni `scope_implies`, ni `scopes` sur une fonction."""
    description = _load(path)
    assert "scope_implies" not in description["connector"]["auth"]
    assert all("scopes" not in function for function in description["functions"])


def _levels_and_scopes(d):
    _with_levels(d)
    d["connector"]["auth"]["scopes"] = [ALL]


def _levels_without_full(d):
    _with_levels(d)
    d["connector"]["auth"]["access_levels"] = {"read": {"scopes": [READ]}, "write": {"scopes": [ALL]}}


def _full_level_only(d):
    _with_levels(d)
    d["connector"]["auth"]["access_levels"] = {"full": {"scopes": [ALL]}}


def _level_without_scopes(d):
    _with_levels(d)
    d["connector"]["auth"]["access_levels"]["read"] = {}


def _level_with_empty_scopes(d):
    _with_levels(d)
    d["connector"]["auth"]["access_levels"]["read"]["scopes"] = []


def _level_unknown_key(d):
    _with_levels(d)
    d["connector"]["auth"]["access_levels"]["read"]["default"] = True


def _level_name_not_an_identifier(d):
    _with_levels(d)
    d["connector"]["auth"]["access_levels"]["Read only"] = {"scopes": [READ]}


def _scope_implies_without_levels(d):
    _oauth2_user(d, scopes=[ALL], scope_implies={ALL: [READ]})


def _scope_implies_empty(d):
    _with_levels(d)
    d["connector"]["auth"]["scope_implies"] = {ALL: []}


def _levels_outside_a_consent(d):
    d["connector"]["auth"]["access_levels"] = {"read": {"scopes": [READ]}, "full": {"scopes": [ALL]}}


def _function_scopes_without_levels(d):
    d["functions"][0]["scopes"] = [READ]


def _function_without_scopes_with_levels(d):
    _with_levels(d)
    del d["functions"][1]["scopes"]


def _function_with_empty_scopes(d):
    _with_levels(d)
    d["functions"][1]["scopes"] = []


@pytest.mark.parametrize("mutate", [
    _levels_and_scopes, _levels_without_full, _full_level_only, _level_without_scopes, _level_with_empty_scopes,
    _level_unknown_key, _level_name_not_an_identifier, _scope_implies_without_levels, _scope_implies_empty,
    _levels_outside_a_consent, _function_scopes_without_levels, _function_without_scopes_with_levels,
    _function_with_empty_scopes,
], ids=lambda f: f.__name__.lstrip("_"))
def test_invalid_access_levels_are_rejected(mutate):
    description = _sellsy()
    mutate(description)
    with pytest.raises(ValidationError):
        _validate(description)


# --- Identité lue à une adresse complète --------------------------------------------

USERINFO = "https://openid.example/userinfo"


def _with_url_identity(d):
    """Sellsy à niveaux, l'identité lue sur un service d'identité commun, dont chaque niveau demande les droits."""
    _with_levels(d)
    auth = d["connector"]["auth"]
    auth["identity"] = {"url": USERINFO, "path": "email", "scopes": ["openid", "email"]}
    for level in auth["access_levels"].values():
        level["scopes"] += ["openid", "email"]


def test_url_identity_base_is_valid():
    description = _sellsy()
    _with_url_identity(description)
    _validate(description)


def _url_identity_without_scopes(d):
    _with_url_identity(d)
    del d["connector"]["auth"]["identity"]["scopes"]


def _url_identity_with_empty_scopes(d):
    _with_url_identity(d)
    d["connector"]["auth"]["identity"]["scopes"] = []


def _url_identity_with_a_function(d):
    _with_url_identity(d)
    d["connector"]["auth"]["identity"]["function"] = "list_estimates"


def _url_identity_not_https(d):
    _with_url_identity(d)
    d["connector"]["auth"]["identity"]["url"] = "http://openid.example/userinfo"


def _url_identity_with_a_query(d):
    _with_url_identity(d)
    d["connector"]["auth"]["identity"]["url"] = USERINFO + "?fields=email"


def _url_identity_with_a_template(d):
    _with_url_identity(d)
    d["connector"]["auth"]["identity"]["url"] = "https://{domain}.example/userinfo"


def _url_identity_without_path(d):
    _with_url_identity(d)
    del d["connector"]["auth"]["identity"]["path"]


@pytest.mark.parametrize("mutate", [
    _url_identity_without_scopes, _url_identity_with_empty_scopes, _url_identity_with_a_function, _url_identity_not_https,
    _url_identity_with_a_query, _url_identity_with_a_template, _url_identity_without_path,
], ids=lambda f: f.__name__.lstrip("_"))
def test_invalid_url_identity_is_rejected(mutate):
    description = _sellsy()
    mutate(description)
    with pytest.raises(ValidationError):
        _validate(description)


# --- Compte de l'hôte et compte rattaché ---------------------------------------------

IDENTITY = {"name": "account_id", "label": "Hosted account", "pattern": "^[A-Za-z0-9_-]{1,64}$"}


def _hosted(d):
    """Sellsy, chaque champ du credential fourni par l'hôte, et un compte rattaché cité par l'adresse de base."""
    for field in d["connector"]["credential"]:
        field["holder"] = "host"
    d["connector"]["account_identity"] = dict(IDENTITY)
    d["connector"]["base_url"] += "/{account_id}"


def test_hosted_account_base_is_valid():
    """Les variantes ci-dessous partent d'une description valide : chacune n'échoue que par sa faute."""
    description = _sellsy()
    _hosted(description)
    _validate(description)


def test_holder_defaults_to_account():
    description = _sellsy()
    description["connector"]["credential"][0]["holder"] = "account"
    _validate(description)


def _holder_unknown(d):
    _hosted(d)
    d["connector"]["credential"][0]["holder"] = "organisation"


def _host_holder_in_a_consent(d):
    _oauth2_user(d)
    d["connector"]["credential"] = [{"name": "api_key", "label": "API key", "secret": True, "holder": "host"}]


def _identity_pattern_not_anchored(d):
    _hosted(d)
    d["connector"]["account_identity"]["pattern"] = "[A-Za-z0-9_-]+"


def _identity_name_not_an_identifier(d):
    _hosted(d)
    d["connector"]["account_identity"]["name"] = "Account-Id"


def _identity_without_pattern(d):
    _hosted(d)
    del d["connector"]["account_identity"]["pattern"]


def _identity_unknown_key(d):
    _hosted(d)
    d["connector"]["account_identity"]["default"] = "abc"


@pytest.mark.parametrize("mutate", [
    _holder_unknown, _host_holder_in_a_consent, _identity_pattern_not_anchored, _identity_name_not_an_identifier,
    _identity_without_pattern, _identity_unknown_key,
], ids=lambda f: f.__name__.lstrip("_"))
def test_invalid_hosted_account_is_rejected(mutate):
    description = _sellsy()
    mutate(description)
    with pytest.raises(ValidationError):
        _validate(description)


def test_act_is_a_boolean():
    description = _sellsy()
    description["functions"][0]["act"] = False
    _validate(description)
    description["functions"][0]["act"] = "no"
    with pytest.raises(ValidationError):
        _validate(description)


# --- Signal de refus du service ------------------------------------------------------


def _signal(d, **entry):
    """Une entrée de la table d'erreurs qui marque le compte, sous une condition sur le corps."""
    d["connector"]["errors"].insert(0, {"status": 400, "code": "key_refused", "message": "The key is refused.", "retryable": False,
                                        "body": {"path": "error.code", "equals": ["invalid_key", 190, True]},
                                        "marks_account": "credential_rejected", **entry})


def test_account_signal_base_is_valid():
    """Les variantes ci-dessous partent d'une description valide : chacune n'échoue que par sa faute."""
    description = _sellsy()
    _signal(description)
    _signal(description, code="credits_spent", marks_account="quota_exhausted",
            body={"path": "message", "contains": ["insufficient credits", "ran out of"]})
    description["functions"][0].setdefault("refusals", []).append(
        {"code": "credits_spent", "when": 402, "message": "Credits spent.", "marks_account": "quota_exhausted"})
    _validate(description)


@pytest.mark.parametrize("mutate", [
    lambda d: _signal(d, marks_account="revoked"),
    lambda d: _signal(d, body={"path": "error.code"}),
    lambda d: _signal(d, body={"path": "error.code", "equals": []}),
    lambda d: _signal(d, body={"path": "error.code", "equals": [{"code": 1}]}),
    lambda d: _signal(d, body={"path": "error.code", "equals": ["a"], "matches": "b"}),
    lambda d: _signal(d, body={"path": "", "equals": ["a"]}),
    lambda d: _signal(d, body={"path": "message", "equals": ["a"], "contains": ["b"]}),
    lambda d: _signal(d, body={"path": "message", "contains": []}),
    lambda d: _signal(d, body={"path": "message", "contains": [""]}),
    lambda d: _signal(d, body={"path": "message", "contains": [42]}),
    lambda d: _signal(d, body={"path": "message", "contains": "insufficient credits"}),
    lambda d: _signal(d, body={"path": "message", "contains": ["a", "a"]}),
], ids=["unknown_mark", "body_without_equals", "equals_empty", "equals_not_a_scalar", "body_unknown_key", "empty_path",
        "equals_and_contains", "contains_empty", "contains_empty_text", "contains_not_a_text", "contains_not_a_list",
        "contains_twice"])
def test_invalid_account_signal_is_rejected(mutate):
    description = _sellsy()
    mutate(description)
    with pytest.raises(ValidationError):
        _validate(description)


# --- Sonde à solde ---------------------------------------------------------------------


def _balance(d, **balance):
    """Une sonde qui lit le solde d'un compte payant."""
    d["connector"]["probe"] = {"function": "list_estimates", "balance": {"path": "credits.remaining", "min": 1, "unit": "credits", **balance}}


def test_probe_balance_base_is_valid():
    """Les variantes ci-dessous partent d'une description valide : chacune n'échoue que par sa faute."""
    description = _sellsy()
    _balance(description)
    _validate(description)
    _balance(description, path="accounts.0.balance", min=0.5)
    _validate(description)
    _balance(description, min=0)
    _validate(description)


@pytest.mark.parametrize("mutate", [
    lambda d: _balance(d, path=""),
    lambda d: _balance(d, path=".credits"),
    lambda d: _balance(d, path="credits."),
    lambda d: _balance(d, path="credits..remaining"),
    lambda d: _balance(d, path="credits remaining"),
    lambda d: _balance(d, min="1"),
    lambda d: _balance(d, min=-1),
    lambda d: _balance(d, unit=""),
    lambda d: _balance(d, covers="quota"),
    lambda d: d["connector"].update(probe={"function": "list_estimates", "balance": {"path": "credits", "unit": "credits"}}),
    lambda d: d["connector"].update(probe={"function": "list_estimates", "balance": {"path": "credits", "min": 1}}),
], ids=["empty_path", "leading_dot", "trailing_dot", "empty_segment", "space_in_path", "min_not_a_number", "min_negative",
        "empty_unit", "unknown_key", "without_min", "without_unit"])
def test_invalid_probe_balance_is_rejected(mutate):
    description = _sellsy()
    mutate(description)
    with pytest.raises(ValidationError):
        _validate(description)


# --- Délai propre à une fonction -------------------------------------------------------


def test_function_timeout_base_is_valid():
    """Même type et mêmes bornes que le délai du connecteur ; l'égalité avec lui est une règle de la fabrique."""
    description = _sellsy()
    description["functions"][0]["timeout_s"] = 1
    _validate(description)
    description["functions"][0]["timeout_s"] = 300
    _validate(description)


@pytest.mark.parametrize("value", [0, -5, 1.5, "60", None, 301],
                         ids=["zero", "negative", "not_an_integer", "text", "null", "above_300"])
def test_invalid_function_timeout_is_rejected(value):
    description = _sellsy()
    description["functions"][0]["timeout_s"] = value
    with pytest.raises(ValidationError):
        _validate(description)


def test_background_base_is_valid():
    """`background` ne vaut que `true` ; son délai propre et ses exclusions sont la règle 21 de la fabrique."""
    description = _sellsy()
    description["functions"][1]["background"] = True
    _validate(description)


@pytest.mark.parametrize("value", [False, "true", 1, None], ids=["false", "text", "number", "null"])
def test_invalid_background_is_rejected(value):
    description = _sellsy()
    description["functions"][1]["background"] = value
    with pytest.raises(ValidationError):
        _validate(description)


def test_connector_timeout_has_the_same_bounds():
    description = _sellsy()
    description["connector"]["timeout_s"] = 300
    _validate(description)
    description["connector"]["timeout_s"] = 301
    with pytest.raises(ValidationError):
        _validate(description)


# --- Coût : pire cas, quantité relue ---------------------------------------------------


def _cost(d, **cost):
    """Le coût de `get_estimate` ; le schéma seul : l'argument nommé et sa forme sont la règle 12 de la fabrique."""
    d["functions"][1]["cost"] = cost


@pytest.mark.parametrize("cost", [
    {"per": "request"},
    {"per": "page"},
    {"per": "request", "unit": "credits"},
    {"per": "request", "unit": "credits", "settle": {"path": "credits_used"}},
    {"per": "item", "items": "embed", "unit": "credits"},
    {"per": "item", "items": "embed", "times": "fields", "unit": "fields", "settle": {"path": "data.0.found"}},
    {"per": "item", "items": "embed", "weights": {"field": "fields", "values": {"a": 1, "b": 10}}, "unit": "credits", "settle": {"path": "cost.credits"}},
    {"per": "request", "quantity": 3, "unit": "credits"},
    {"per": "page", "quantity": 2, "unit": "credits", "settle": {"path": "credits_used"}},
    {"per": "request", "quantity": 40000, "unit": "microdollars", "settle": {"path": "tasks.0.cost", "scale": 1000000}},
], ids=["request", "page", "request_unit", "request_settle", "item", "item_times_settle", "item_weights_settle",
        "request_quantity", "page_quantity_settle", "request_settle_scale"])
def test_cost_base_is_valid(cost):
    """Les variantes ci-dessous partent d'une description valide : chacune n'échoue que par sa faute."""
    description = _sellsy()
    _cost(description, **cost)
    _validate(description)


@pytest.mark.parametrize("cost", [
    {"per": "call"},
    {"per": "item", "unit": "credits"},
    {"per": "item", "items": "embed"},
    {"per": "request", "items": "embed"},
    {"per": "request", "times": "fields"},
    {"per": "item", "times": "fields", "unit": "credits"},
    {"per": "request", "settle": {"path": "credits_used"}},
    {"per": "request", "unit": "credits", "settle": {}},
    {"per": "request", "unit": "credits", "settle": {"path": ".credits"}},
    {"per": "request", "unit": "credits", "settle": {"path": "credits used"}},
    {"per": "request", "unit": "credits", "settle": {"path": "credits_used", "max": 3}},
    {"per": "request", "unit": ""},
    {"per": "item", "items": "Embed", "unit": "credits"},
    {"per": "request", "amount": 2},
    {"per": "request", "weights": {"field": "fields", "values": {"a": 1, "b": 10}}},
    {"per": "item", "items": "embed", "times": "fields", "weights": {"field": "fields", "values": {"a": 1, "b": 10}}, "unit": "credits"},
    {"per": "item", "items": "embed", "weights": {"field": "fields", "values": {"a": 1, "b": 10}}},
    {"per": "item", "items": "embed", "weights": {"field": "fields"}, "unit": "credits"},
    {"per": "item", "items": "embed", "weights": {"values": {"a": 1}}, "unit": "credits"},
    {"per": "item", "items": "embed", "weights": {"field": "fields", "values": {}}, "unit": "credits"},
    {"per": "item", "items": "embed", "weights": {"field": "fields", "values": {"a": 0}}, "unit": "credits"},
    {"per": "item", "items": "embed", "weights": {"field": "fields", "values": {"a": -1}}, "unit": "credits"},
    {"per": "item", "items": "embed", "weights": {"field": "fields", "values": {"a": 1.5}}, "unit": "credits"},
    {"per": "item", "items": "embed", "weights": {"field": "fields", "values": {"a": "1"}}, "unit": "credits"},
    {"per": "item", "items": "embed", "weights": {"field": "fields", "values": {"": 1}}, "unit": "credits"},
    {"per": "item", "items": "embed", "weights": {"field": "Fields", "values": {"a": 1}}, "unit": "credits"},
    {"per": "item", "items": "embed", "weights": {"field": "fields", "values": {"a": 1}, "default": 1}, "unit": "credits"},
    {"per": "item", "items": "embed", "quantity": 3, "unit": "credits"},
    {"per": "request", "quantity": 3},
    {"per": "request", "quantity": 1, "unit": "credits"},
    {"per": "request", "quantity": 0, "unit": "credits"},
    {"per": "request", "quantity": -3, "unit": "credits"},
    {"per": "request", "quantity": 2.5, "unit": "credits"},
    {"per": "request", "quantity": "3", "unit": "credits"},
    {"per": "request", "unit": "microdollars", "settle": {"path": "cost", "scale": 1}},
    {"per": "request", "unit": "microdollars", "settle": {"path": "cost", "scale": 0}},
    {"per": "request", "unit": "microdollars", "settle": {"path": "cost", "scale": 1.5}},
    {"per": "request", "unit": "microdollars", "settle": {"path": "cost", "scale": "1000000"}},
], ids=["unknown_per", "item_without_items", "item_without_unit", "items_per_request", "times_per_request",
        "times_without_items", "settle_without_unit", "settle_without_path", "settle_leading_dot", "settle_space",
        "settle_unknown_key", "empty_unit", "items_not_an_identifier", "unknown_key",
        "weights_per_request", "weights_with_times", "weights_without_unit", "weights_without_values",
        "weights_without_field", "weights_empty_table", "weight_zero", "weight_negative", "weight_not_an_integer",
        "weight_text", "weight_empty_value", "weights_field_not_an_identifier", "weights_unknown_key",
        "quantity_per_item", "quantity_without_unit", "quantity_one", "quantity_zero", "quantity_negative",
        "quantity_not_an_integer", "quantity_text", "scale_one", "scale_zero", "scale_not_an_integer", "scale_text"])
def test_invalid_cost_is_rejected(cost):
    description = _sellsy()
    _cost(description, **cost)
    with pytest.raises(ValidationError):
        _validate(description)


# --- Travail asynchrone ----------------------------------------------------------------


def _async(d, **declared):
    """`list_estimates` soumet, `get_estimate` relève ; le schéma seul : la relève elle-même est la règle 13."""
    d["functions"][0]["async"] = {"collect": "get_estimate", "job_id": {"from": "data.0.id", "to": "id"}, **declared}


def test_async_base_is_valid():
    description = _sellsy()
    _async(description)
    _validate(description)
    _async(description, within_s=86400)
    _validate(description)
    description["functions"][0]["cost"] = {"per": "request", "unit": "credits", "settle": {"path": "used"}}
    _async(description, within_s=600)
    _validate(description)
    _async(description, no_deadline=True)
    _validate(description)


@pytest.mark.parametrize("mutate", [
    lambda d: _async(d, within_s=0),
    lambda d: _async(d, within_s=86401),
    lambda d: _async(d, within_s=1.5),
    lambda d: _async(d, within_s="600"),
    lambda d: _async(d, poll_s=30),
    lambda d: _async(d, job_id={"from": "id"}),
    lambda d: _async(d, job_id={"from": "data..id", "to": "id"}),
    lambda d: _async(d, job_id={"from": "id", "to": "id", "as": "text"}),
    lambda d: d["functions"][0].update({"async": {"job_id": {"from": "id", "to": "id"}}}),
    lambda d: (_async(d), d["functions"][0].update(cost={"per": "request", "unit": "credits", "settle": {"path": "used"}})),
    lambda d: _async(d, no_deadline=False),
    lambda d: _async(d, no_deadline="true"),
    lambda d: _async(d, within_s=600, no_deadline=True),
    lambda d: (_async(d, no_deadline=False), d["functions"][0].update(cost={"per": "request", "unit": "credits", "settle": {"path": "used"}})),
], ids=["within_zero", "within_above_a_day", "within_not_an_integer", "within_text", "unknown_key", "job_id_without_to",
        "job_id_empty_segment", "job_id_unknown_key", "without_collect", "settled_without_within", "no_deadline_false",
        "no_deadline_text", "no_deadline_and_within", "settled_no_deadline_false"])
def test_invalid_async_is_rejected(mutate):
    description = _sellsy()
    mutate(description)
    with pytest.raises(ValidationError):
        _validate(description)


# --- Fichiers d'un corps multipart -------------------------------------------------------


def _files(d, files):
    """`get_estimate` envoie son `id` en partie fichier ; le schéma seul : la place et la méthode sont la règle 14."""
    d["functions"][1]["call"]["files"] = files


def test_files_base_is_valid():
    description = _sellsy()
    _files(description, {"file": "id"})
    _validate(description)


@pytest.mark.parametrize("files", [{}, {"file": "Id"}, {"file": ""}, {"file": 1}, ["id"]],
                         ids=["empty", "not_an_identifier", "empty_argument", "not_text", "a_list"])
def test_invalid_files_are_rejected(files):
    description = _sellsy()
    _files(description, files)
    with pytest.raises(ValidationError):
        _validate(description)


# --- Un fichier rendu (output.file) ------------------------------------------------------
# Le schéma dit la forme ; ce qui croise d'autres clés (pagination, `path` selon `from`, gabarit, types exécutables,
# `expect`) est la règle 17 de la fabrique (`tests/test_fabrique.py`).

FILE = {"from": "url", "path": "pdf_link", "types": {"pdf": ["application/pdf"]}, "name": {"template": "estimate-{id}.pdf"}}


def _file(d, **changes):
    """`get_estimate` rend un fichier : `output.file` à la place de son schéma de sortie."""
    file = {**copy.deepcopy(FILE), **changes}
    d["functions"][1]["output"] = {"file": {key: value for key, value in file.items() if value is not None}}


@pytest.mark.parametrize("changes", [
    {},
    {"from": "body", "path": None, "name": {"header": "Content-Disposition", "template": "estimate-{id}.pdf"}},
    {"auth": "account", "max_bytes": 1},
    {"auth": "none", "max_bytes": 52428800},
    {"from": "base64url", "name": {"path": "file.name", "template": "estimate-{id}.pdf"}},
    {"types": {"mp3": ["audio/mpeg", "audio/mp3"], "m4a": ["audio/mp4", "audio/x-m4a"], "wav": ["audio/wav"]},
     "name": {"template": "call-{id}.mp3"}},
], ids=["url", "body_header", "account_one_byte", "none_ceiling", "base64url_field", "audio"])
def test_file_output_base_is_valid(changes):
    description = _sellsy()
    _file(description, **changes)
    _validate(description)


@pytest.mark.parametrize("changes", [
    {"from": "stream"},
    {"from": None},
    {"types": None},
    {"types": {}},
    {"types": {"pdf": []}},
    {"types": {"PDF": ["application/pdf"]}},
    {"types": {"averyveryverylongx": ["application/pdf"]}},
    {"types": {"p.f": ["application/pdf"]}},
    {"types": {"pdf": ["application/pdf; charset=binary"]}},
    {"types": {"pdf": ["pdf"]}},
    {"types": {"pdf": ["Application/PDF"]}},
    {"types": {"pdf": ["application/pdf", "application/pdf"]}},
    {"name": None},
    {"name": {}},
    {"name": {"header": "Content-Disposition"}},
    {"name": {"template": ""}},
    {"name": {"template": "estimate-{id}.pdf", "other": "x"}},
    {"name": {"header": "Content Disposition", "template": "estimate-{id}.pdf"}},
    {"auth": "secret"},
    {"max_bytes": 0},
    {"max_bytes": 52428801},
    {"max_bytes": 1.5},
    {"max_bytes": "1"},
    {"path": ".pdf_link"},
    {"transit_hours": 48},
], ids=["from_unknown", "from_missing", "types_missing", "types_empty", "type_without_mime", "extension_upper",
        "extension_too_long", "extension_dot", "mime_with_parameters", "mime_malformed", "mime_upper", "mime_twice",
        "name_missing", "name_empty", "template_missing", "template_empty", "name_unknown_key", "header_malformed",
        "auth_unknown", "max_bytes_zero", "max_bytes_beyond_50_mb", "max_bytes_not_integer", "max_bytes_text",
        "path_malformed", "duration_declared"])
def test_invalid_file_output_is_rejected(changes):
    description = _sellsy()
    _file(description, **changes)
    with pytest.raises(ValidationError):
        _validate(description)


# --- Retirer de la sortie (output.strip) ---------------------------------------------------------
# Le schéma dit la forme d'un chemin et refuse un doublon ; le chemin sous un autre est la règle 18 de la fabrique
# (`tests/test_fabrique.py`), qui nomme aussi chaque refus par son code (`tests/test_strip_cases.py`).


def _strip(d, strip):
    """`get_estimate` retire des clés de sa sortie, à côté de son schéma de sortie."""
    d["functions"][1]["output"]["strip"] = strip


@pytest.mark.parametrize("strip", [["pdf_link"], ["company.pdf_link", "rows.pdf_link"], ["related.0"], ["a key"]],
                         ids=["top_level", "paths", "numeric_segment_is_a_key", "space_in_a_key"])
def test_strip_base_is_valid(strip):
    description = _sellsy()
    _strip(description, strip)
    _validate(description)


@pytest.mark.parametrize("sample", [{"company": {"pdf_link": "https://files.example.test/e.pdf"}}, [], None, "x"],
                         ids=["object", "list", "null", "text"])
def test_sample_is_any_json(sample):
    """`output.sample` : n'importe quel JSON pour le schéma ; ce qu'il doit porter est la règle 19 de la fabrique."""
    description = _sellsy()
    _strip(description, ["company.pdf_link"])
    description["functions"][1]["output"]["sample"] = sample
    _validate(description)


@pytest.mark.parametrize("strip", [[], [""], [".company"], ["company."], ["company..pdf_link"], ["."],
                                   ["pdf_link", "pdf_link"], "pdf_link"],
                         ids=["empty", "empty_path", "leading_dot", "trailing_dot", "empty_segment", "only_a_dot",
                              "twice", "not_a_list"])
def test_invalid_strip_is_rejected(strip):
    description = _sellsy()
    _strip(description, strip)
    with pytest.raises(ValidationError):
        _validate(description)
