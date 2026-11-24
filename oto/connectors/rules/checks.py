"""Les règles d'une description que le schéma JSON ne sait pas dire et dont le profil Custom API a besoin : les
arguments (règle 1), l'authentification (2), un nom côté API, une valeur (3), les contrôles (4), la sonde (5), `act`
(9), le signal de refus (10), le délai (11), `personal` (15) et `strip` (18). Le texte de chaque règle est dans
l'en-tête de `fabrique/descriptions.py`.

Une seule copie : la fabrique les applique à chaque `connectors/<nom>/connector.yaml`, le profil Custom API
(`oto.connectors.custom_api`) à une définition d'org. Chacune lève `DescriptionError` au premier défaut, sur une
description déjà conforme au schéma.
"""
from __future__ import annotations

from .body import (PATH_PARAM, PLACES, argument_table, body_argument, body_argument_refusal, body_list_refusal,
                   body_table_refusal)

# Le délai d'un appel quand le connecteur n'en déclare pas (le `default` du schéma).
DEFAULT_TIMEOUT_S = 30
AUTH_REFERENCES = ("key", "token", "username", "password", "client_id", "client_secret")


class DescriptionError(ValueError):
    """Une description refusée : le message dit le fichier, la fonction et la règle."""



def check_arguments(function: dict, where: str) -> None:
    """Règle 1 : un argument va à un seul endroit, et tout endroit nomme un argument."""
    call = function.get("call")
    if call is None:
        return
    name = function["name"]
    declared = set(function["input"]["properties"])
    required = set(function["input"].get("required", []))
    placed: dict[str, str] = {}

    def place(argument: str, at: str) -> None:
        if argument not in declared:
            raise DescriptionError(f"{where}: function {name}: {at} names {argument!r}, which is not an input argument")
        if argument in placed:
            raise DescriptionError(
                f"{where}: function {name}: argument {argument!r} goes to two places ({placed[argument]} and {at})"
            )
        placed[argument] = at

    whole = body_argument(call)
    refusal = body_argument_refusal(function)
    if refusal is not None:
        code, reason = refusal
        raise DescriptionError(f"{where}: function {name}: call.body {whole!r}: {code}: {reason}")
    if whole is not None:
        place(whole, "the whole body")
    nested = body_table_refusal(function)
    if nested is not None:
        code, path, reason = nested
        raise DescriptionError(f"{where}: function {name}: call.body {path!r}: {code}: {reason}")
    listed = body_list_refusal(function)
    if listed is not None:
        code, reason = listed
        raise DescriptionError(f"{where}: function {name}: call.body_list: {code}: {reason}")
    for argument in PATH_PARAM.findall(call["path"]):
        place(argument, "the path")
        if argument not in required:
            raise DescriptionError(f"{where}: function {name}: path argument {argument!r} must be required")
    for kind in PLACES:
        for api_name, argument in argument_table(call, kind).items():
            place(argument, f"{kind} {api_name!r}")
    pagination = function.get("pagination")
    unplaced = declared - set(placed)
    if unplaced:
        raise DescriptionError(
            f"{where}: function {name}: argument(s) {', '.join(sorted(unplaced))} go nowhere in call"
        )
    if pagination and pagination["request_param"] not in declared:
        raise DescriptionError(
            f"{where}: function {name}: pagination request_param {pagination['request_param']!r} is not an input argument"
        )
    for argument in call.get("encode") or {}:
        if argument not in placed or placed[argument] == "the path" or placed[argument].startswith("files "):
            raise DescriptionError(
                f"{where}: function {name}: encode names {argument!r}, which is not placed in query, body or headers"
                " (a file is never encoded)"
            )


def check_auth(connector: dict, where: str) -> None:
    """Règle 2 : une référence de `auth` désigne un champ de `credential` existant ; en `oauth2_jwt_bearer`, le seul
    champ, secret. En `oauth2_user`, l'indicateur de ressource ne se déclare que par `resource`, jamais dans
    `authorize_params` : l'hôte le pose à l'autorisation, à l'échange du code et à chaque renouvellement."""
    auth = connector["auth"]
    if "resource" in auth.get("authorize_params", {}):
        raise DescriptionError(
            f"{where}: auth.authorize_params.resource: declare the resource indicator (RFC 8707) as auth.resource, "
            "which the host sets at the authorization, the code exchange and each renewal; remove it from authorize_params"
        )
    fields = {field["name"] for field in connector.get("credential", [])}
    for reference in AUTH_REFERENCES:
        if reference in auth and auth[reference] not in fields:
            raise DescriptionError(
                f"{where}: auth.{reference} names {auth[reference]!r}, which is not a credential field "
                f"({', '.join(sorted(fields)) or 'none declared'})"
            )
    if auth["kind"] == "oauth2_jwt_bearer":
        others = sorted(fields - {auth["key"]})
        if others:
            raise DescriptionError(
                f"{where}: auth.kind oauth2_jwt_bearer takes a single credential field, the key file named by auth.key "
                f"({auth['key']!r}); remove {', '.join(others)}"
            )
        field = next(f for f in connector["credential"] if f["name"] == auth["key"])
        if field["secret"] is not True:
            raise DescriptionError(f"{where}: auth.key names {auth['key']!r}, which must be a secret field (secret: true)")


#: The authentications sent in `Authorization` (a bearer token, or basic).
BEARER_KINDS = ("bearer", "basic", "oauth2_client_credentials", "oauth2_user", "oauth2_jwt_bearer", "host_delegated")


def _auth_header(auth: dict) -> str | None:
    if auth["kind"] == "api_key":
        return auth["name"] if auth["in"] == "header" else None
    if auth["kind"] in BEARER_KINDS:
        return "Authorization"
    return None


def _auth_query(auth: dict) -> str | None:
    return auth["name"] if auth["kind"] == "api_key" and auth["in"] == "query" else None


def check_constants(connector: dict, function: dict, where: str) -> None:
    """Règle 3 : un nom côté API, une seule valeur."""
    call = function.get("call")
    if call is None:
        return
    name = function["name"]
    constants = call.get("constants") or {}
    # Au corps, la règle 22 juge la constante et la table à toute profondeur (`body_key_twice`).
    for api_name in constants.get("query") or {}:
        if api_name in argument_table(call, "query"):
            raise DescriptionError(f"{where}: function {name}: query {api_name!r} is both a constant and an argument")
    auth_query = _auth_query(connector["auth"])
    if auth_query and (auth_query in (call.get("query") or {}) or auth_query in (constants.get("query") or {})):
        raise DescriptionError(
            f"{where}: function {name}: query {auth_query!r} is set twice (the authentication and the function)"
        )
    seen: dict[str, str] = {}
    auth_header = _auth_header(connector["auth"])
    if auth_header:
        seen[auth_header.lower()] = "the authentication"
    sources = [
        ("the connector headers", connector.get("headers") or {}),
        ("the function constant headers", constants.get("headers") or {}),
        ("the function headers", call.get("headers") or {}),
    ]
    for source, headers in sources:
        for header in headers:
            if header.lower() in seen:
                raise DescriptionError(
                    f"{where}: function {name}: header {header!r} is set twice ({seen[header.lower()]} and {source})"
                )
            seen[header.lower()] = source


def act(function: dict) -> bool:
    """Ce qui engage ou détruit : `act` déclaré, sinon déduit de la classe (`read` → false, `write` et `sensitive` → true)."""
    return function.get("act", function["class"] != "read")


def check_act(function: dict, where: str) -> None:
    """Règle 9 : `act` ne contredit pas la classe, et `effect` n'est dit que d'une fonction sensible."""
    declared = function.get("act")
    if declared is False and function["class"] == "sensitive":
        raise DescriptionError(f"{where}: function {function['name']}: a sensitive function cannot declare act: false")
    if declared is True and function["class"] == "read":
        raise DescriptionError(f"{where}: function {function['name']}: a read function cannot declare act: true")
    if "effect" in function and function["class"] != "sensitive":
        raise DescriptionError(f"{where}: function {function['name']}: effect is said of a sensitive function only")


def check_timeout(connector: dict, function: dict, where: str) -> None:
    """Règle 11 : le délai d'une fonction n'est pas celui du connecteur."""
    own = function.get("timeout_s")
    if own is not None and own == connector.get("timeout_s", DEFAULT_TIMEOUT_S):
        raise DescriptionError(
            f"{where}: function {function['name']}: timeout_s {own} is the connector's timeout: declare none"
        )


# Les refus de la règle 18, les mêmes des deux côtés (cas communs `connectors/_cases/strip.yaml`).
STRIP_REFUSALS = ("strip_path_malformed", "strip_path_overlaps", "strip_path_twice")


def strip_refusal(paths: list[str]) -> tuple[str, str, str] | None:
    """Le premier défaut d'une liste `strip`, `(code, chemin, raison)`, ou `None` : la règle 18 hors de toute description,
    que les cas communs rejouent."""
    seen: set[str] = set()
    for path in paths:
        if any(key == "" for key in path.split(".")):
            return ("strip_path_malformed", path,
                    "a path is keys separated by dots, each non-empty (no leading, trailing or doubled dot)")
        if path in seen:
            return ("strip_path_twice", path, "declared twice")
        seen.add(path)
    for path in paths:
        above = next((other for other in paths if path.startswith(other + ".")), None)
        if above is not None:
            return ("strip_path_overlaps", path, f"lies under {above!r}, which is already removed whole")
    return None



# Les mots d'un JSON Schema dont la valeur est un schéma, une liste de schémas, ou des schémas par nom : la donnée
# personnelle se cherche là seulement, jamais dans `examples`, `default`, `const` ni `enum`, et le nom d'une propriété
# (une propriété qui s'appellerait `personal`) n'est jamais lu comme un mot.
SCHEMA_KEYWORDS = ("items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames",
                   "unevaluatedItems", "unevaluatedProperties")
SCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")
SCHEMA_MAPS = ("properties", "patternProperties", "$defs", "dependentSchemas")


def _personal_marks(schema: object, at: str) -> list[tuple[str, object]]:
    """Chaque `personal` d'un schéma, avec sa place (`input.properties.email`)."""
    if not isinstance(schema, dict):
        return []
    found = [(at, schema["personal"])] if "personal" in schema else []
    for key in SCHEMA_KEYWORDS:
        found += _personal_marks(schema.get(key), f"{at}.{key}")
    for key in SCHEMA_LISTS:
        for index, child in enumerate(schema.get(key) or []):
            found += _personal_marks(child, f"{at}.{key}[{index}]")
    for key in SCHEMA_MAPS:
        for name, child in (schema.get(key) or {}).items():
            found += _personal_marks(child, f"{at}.{key}.{name}")
    return found


def check_personal(function: dict, where: str) -> None:
    """Règle 15 : `personal` marque une propriété de l'entrée, toujours `true` ; ni la racine ni la sortie."""
    name = function["name"]
    for at, value in _personal_marks(function["input"], "input"):
        if at == "input":
            raise DescriptionError(f"{where}: function {name}: personal marks a property of input, not input itself")
        if value is not True:
            raise DescriptionError(f"{where}: function {name}: {at}: personal must be true (declare it only where it applies)")
    schema = (function.get("output") or {}).get("schema")
    for at, _ in _personal_marks(schema, "output.schema"):
        raise DescriptionError(f"{where}: function {name}: {at}: personal marks an input argument, not an output field")


def _statuses(entry: dict) -> list[int]:
    status = entry["status"] if "status" in entry else entry["when"]
    return status if isinstance(status, list) else [status]


def _check_signal_table(entries: list[dict], at: str, where: str) -> None:
    """Règle 10, sur une table (`errors` du connecteur, ou les refus d'une fonction)."""
    plain: set[int] = set()
    for entry in entries:
        label = f"{at} {entry['code']!r}"
        if "status" not in entry and not isinstance(entry["when"], int):
            if "body" in entry or "marks_account" in entry:
                raise DescriptionError(
                    f"{where}: {label}: a refusal with body or marks_account needs an HTTP status in when, not a text condition"
                )
            continue
        statuses = _statuses(entry)
        body = "body" in entry
        mark = entry.get("marks_account")
        if not body and any(200 <= status < 300 for status in statuses):
            raise DescriptionError(f"{where}: {label}: a 2xx status needs a body condition, or every success would be refused")
        if mark == "credential_rejected" and not body and 403 in statuses:
            raise DescriptionError(
                f"{where}: {label}: a 403 without a body condition cannot mark credential_rejected (a missing right is not a refused key)"
            )
        if mark == "quota_exhausted" and not body and 429 in statuses:
            raise DescriptionError(
                f"{where}: {label}: a 429 without a body condition cannot mark quota_exhausted (a rate limit is not spent credits)"
            )
        if mark is not None and entry.get("retryable") is True:
            raise DescriptionError(f"{where}: {label}: an entry that marks the account is not retryable")
        shadowed = sorted(plain.intersection(statuses))
        if body and shadowed:
            raise DescriptionError(
                f"{where}: {label}: its body condition comes after an entry without one for status "
                f"{', '.join(map(str, shadowed))}, so it is never read"
            )
        if not body:
            plain.update(statuses)


def check_signals(data: dict, where: str) -> None:
    """Règle 10 : le signal de refus du service, dans la table du connecteur et dans les refus de chaque fonction."""
    _check_signal_table(data["connector"]["errors"], "errors", where)
    for function in data["functions"]:
        _check_signal_table(function.get("refusals", []), f"function {function['name']}: refusal", where)


def check_checks(function: dict, where: str) -> None:
    """Règle 4 : un contrôle nomme un refus de sa fonction, et `equal_sums` une liste qui a ses deux champs."""
    name = function["name"]
    refusals = {refusal["code"] for refusal in function.get("refusals", [])}
    for check in function.get("checks", []) + function.get("expect", []):
        if check["refusal"] not in refusals:
            raise DescriptionError(
                f"{where}: function {name}: {check['kind']} names refusal {check['refusal']!r}, which the function does not declare"
            )
        if check["kind"] == "equal_sums":
            argument = function["input"]["properties"].get(check["items"])
            item = (argument or {}).get("items") if (argument or {}).get("type") == "array" else None
            missing = [f for f in check["fields"] if f not in ((item or {}).get("properties") or {})]
            if item is None or missing:
                raise DescriptionError(
                    f"{where}: function {name}: equal_sums needs {check['items']!r} to be a list argument whose items "
                    f"declare {', '.join(check['fields'])}"
                )


def _plain_read(data: dict, role: str, name: str, where: str) -> None:
    function = next((f for f in data["functions"] if f["name"] == name), None)
    if function is None:
        raise DescriptionError(f"{where}: {role} names {name!r}, which is not a function of the connector")
    if function["class"] != "read" or "call" not in function or function["input"].get("required"):
        raise DescriptionError(
            f"{where}: {role} {name!r} must be a read function with a call and no required argument"
        )


def check_probe(data: dict, where: str) -> None:
    """Règle 5 : la sonde et l'identité d'un consentement sont des lectures par `call`, appelables sans argument ; la
    fonction d'une sonde qui lit un solde ne coûte rien."""
    probe = data["connector"].get("probe")
    if probe is not None:
        _plain_read(data, "probe", probe["function"], where)
        function = next(f for f in data["functions"] if f["name"] == probe["function"])
        if "balance" in probe and "cost" in function:
            raise DescriptionError(
                f"{where}: probe {probe['function']!r} reads a balance, so it must declare no cost: one never pays to learn whether one can pay"
            )
    auth = data["connector"]["auth"]
    identity = auth.get("identity")
    if identity is not None and "function" in identity:
        _plain_read(data, "auth.identity", identity["function"], where)
    if identity is not None and "url" in identity and "access_levels" not in auth:
        beyond = sorted(set(identity["scopes"]) - set(auth.get("scopes", [])))
        if beyond:
            raise DescriptionError(f"{where}: auth.identity needs {', '.join(beyond)}, which auth.scopes does not ask for")


