"""The takeover of oto 1's Custom API definitions, once: `from_oto1(definition_oto1) -> (document | None, report)`.

PURE: no I/O. `definition_oto1` is one application as oto 1 keeps it, its non-secret parts only:

    {"account": "Acme Payroll",                       # the application's name, the one its keys are set under
     "connection": {"base_url", "auth_mode", "header_name", "query_param", "token_url", "scope", "doc_path",
                    "session", "label"},              # the non-secret fields, those that apply to the mode
     "definition": {"label", "description", "logo_domain", "headers", "variables", "operations"}}

The document is a Custom API definition (`profile.validate` finds nothing in it), or `None` when anything blocks. The
report names each value deduced (`deduced`), each part of oto 1 left behind without blocking (`dropped`) and each
problem that blocks (`blocking`, with its code), never a secret value:

- deduced: the name (`custom_` + the application's name in snake_case), `version` 1.0.0, `api_version` `unspecified`,
  the modes (a person's or an org's key), the class from the verb (GET reads, POST/PUT/PATCH write, DELETE is sensitive,
  destroys and is confirmed by « `<METHOD> <path>`: irreversible. »), each argument renamed in snake_case (its name on
  the API side stays in `call`), the place of each argument (oto 1's rule: `in`, else the path when it cites it, else
  the query for GET and DELETE, the body otherwise), each `variables` value turned into a constant of `call` (in the
  path, or in `constants`), the argument leaving the input, the example `{}` of an action without required argument,
  and the standard error table (`errors.standard_errors`), written out;
- dropped: `description` and `logo_domain` (not yet in the format), `doc_path` (the raw calls stay in oto 1), a variable
  no action uses, a refusal's `retryable: true` (the format has none);
- blocking: an address in `http://` or towards an internal host, the `session` and `visitor` modes, an action with a
  required argument and no example, two arguments that give the same snake_case name, a secret in the export, and any
  problem the profile finds in the document built.
"""

from __future__ import annotations

import copy
from typing import Any, Optional
from urllib.parse import quote, unquote

from . import problems as p
from .errors import standard_errors
from .naming import class_of, confirmation, identifier
from .problems import Problem, dotted
from .profile import PREFIX, validate

KEYS = ("account", "connection", "definition")
DEFINITION_KEYS = ("label", "description", "logo_domain", "headers", "variables", "operations")
CONNECTION_KEYS = (
    "base_url",
    "auth_mode",
    "header_name",
    "query_param",
    "doc_path",
    "token_url",
    "scope",
    "session",
    "label",
)
#: What a key brings in oto 1: never in an export of definitions. The secret ones block, the others are left behind.
SECRET_FIELDS = ("token", "password", "client_secret")
KEY_FIELDS = ("username", "client_id")
UNSUPPORTED_MODES = ("session", "visitor")
VERSION = "1.0.0"
API_VERSION = "unspecified"
CREDENTIAL_LABELS = {
    "token": "API key",
    "username": "Username",
    "password": "Password",
    "client_id": "Client ID",
    "client_secret": "Client secret",
}


class _Report:
    def __init__(self, account: str):
        self.account = account
        self.deduced: list[dict] = []
        self.dropped: list[dict] = []
        self.blocking: list[Problem] = []

    def deduce(self, path: str, value: Any, why: str) -> None:
        self.deduced.append({"path": path, "value": value, "why": why})

    def drop(self, path: str, why: str) -> None:
        self.dropped.append({"path": path, "why": why})

    def block(self, code: str, path: str, message: str) -> None:
        self.blocking.append(Problem(code, path, message))

    def as_dict(self) -> dict:
        return {
            "account": self.account,
            "status": "blocked" if self.blocking else "converted",
            "deduced": self.deduced,
            "dropped": self.dropped,
            "blocking": [problem.as_dict() for problem in p.unique(self.blocking)],
        }


def _credential(*names: str, secret: tuple[str, ...]) -> list[dict]:
    return [{"name": name, "label": CREDENTIAL_LABELS[name], "secret": name in secret} for name in names]


def _auth(connection: dict, report: _Report) -> tuple[Optional[dict], list[dict]]:
    """`(auth, credential)` for oto 1's mode; the vault's field names are kept, no secret is rewritten."""
    mode = connection.get("auth_mode")
    if mode in UNSUPPORTED_MODES:
        report.block(
            p.AUTH_NOT_SUPPORTED,
            "connection.auth_mode",
            f"the {mode} mode has no form in the format yet: the application stays in oto 1",
        )
        return None, []
    if mode == "bearer":
        return {"kind": "bearer", "token": "token"}, _credential("token", secret=("token",))
    if mode in ("header", "query"):
        name = connection.get("header_name" if mode == "header" else "query_param")
        return ({"kind": "api_key", "in": mode, "name": name, "key": "token"}, _credential("token", secret=("token",)))
    if mode == "basic":
        return (
            {"kind": "basic", "username": "username", "password": "password"},
            _credential("username", "password", secret=("password",)),
        )
    if mode == "oauth2":
        auth = {
            "kind": "oauth2_client_credentials",
            "token_url": connection.get("token_url"),
            "token_request": "form",
            "client_auth": "body",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }
        if connection.get("scope"):
            auth["scope"] = connection["scope"]
        report.deduce(
            "connector.auth",
            "token_request: form, client_auth: body",
            "oto 1 asks its token with a form body, the client in the body",
        )
        return auth, _credential("client_id", "client_secret", secret=("client_secret",))
    if mode == "none":
        return {"kind": "none"}, []
    report.block(p.AUTH_NOT_SUPPORTED, "connection.auth_mode", f"{mode!r} is not an authentication mode of oto 1")
    return None, []


def _location(op: dict, arg: str) -> str:
    """Where oto 1 sends `arg`: its `in`, else the path when the template cites it, else the query for GET and DELETE,
    the body otherwise."""
    declared = (op.get("in") or {}).get(arg)
    if declared:
        return declared
    if "{" + arg + "}" in op["path"]:
        return "path"
    return "query" if op["method"].upper() in ("GET", "DELETE") else "body"


def _typed(value: str, schema: dict) -> Any:
    """A variable is text in oto 1; an argument typed integer, number or boolean took it converted, when it converts."""
    kind = (schema or {}).get("type")
    try:
        if kind == "integer":
            return int(value)
        if kind == "number":
            return float(value)
    except (TypeError, ValueError):
        return value
    if kind == "boolean" and str(value).lower() in ("true", "false"):
        return str(value).lower() == "true"
    return value


def _variables(definition: dict) -> dict[str, str]:
    """The variables that fill an argument: those with a value (oto 1 ignores an empty one)."""
    out = {}
    for name, variable in (definition.get("variables") or {}).items():
        if isinstance(variable, dict) and variable.get("value") not in (None, ""):
            out[name] = variable["value"]
    return out


def _function(op: dict, i: int, variables: dict[str, str], used: set[str], report: _Report) -> dict:
    at = f"definition.operations.{i}"
    method = op["method"].upper()
    inp = op.get("input") or {}
    properties = inp.get("properties") or {}
    required = list(inp.get("required") or [])
    for key in inp:
        if key not in ("type", "properties", "required"):
            report.drop(f"{at}.input.{key}", "the input of the format is a strict object: only its properties pass")
    taken: dict[str, str] = {}
    for arg in properties:
        name = identifier(arg, "arg")
        if name in taken:
            report.block(
                p.ARGUMENT_NAME_COLLISION,
                f"{at}.input.properties",
                f"{taken[name]!r} and {arg!r} both become {name!r}: rename one in oto 1 first",
            )
        taken[name] = arg
    path = op["path"]
    call: dict = {"method": method}
    places: dict[str, dict] = {"query": {}, "body": {}}
    constants: dict[str, dict] = {"query": {}, "body": {}}
    kept: dict[str, dict] = {}
    for arg, schema in properties.items():
        name = identifier(arg, "arg")
        where = _location(op, arg)
        if arg in variables:
            used.add(arg)
            value = _typed(variables[arg], schema)
            report.deduce(
                f"{at}.input.properties.{arg}", value, f"the variable {arg!r} becomes a constant of the {where}"
            )
            if where == "path":
                raw = str(value)
                if any(v in ("", ".", "..") or "/" in v or "\\" in v for v in (raw, unquote(raw))):
                    report.block(
                        p.VARIABLE_INVALID,
                        f"definition.variables.{arg}",
                        "the variable fills a path argument and must be one path segment",
                    )
                path = path.replace("{" + arg + "}", quote(raw, safe=""))
            else:
                constants[where][arg] = value
            continue
        kept[name] = copy.deepcopy(schema)
        if name != arg:
            report.deduce(
                f"{at}.input.properties.{arg}", name, "argument names are snake_case; the API's name stays in call"
            )
        if where == "path":
            path = path.replace("{" + arg + "}", "{" + name + "}")
        else:
            places[where][arg] = name
    call["path"] = path
    for where in ("query", "body"):
        if places[where]:
            call[where] = places[where]
    if constants["query"] or constants["body"]:
        call["constants"] = {where: values for where, values in constants.items() if values}
    names_required = [identifier(arg, "arg") for arg in required if arg not in variables]
    function: dict = {"name": op["name"]}
    if op.get("class") == "read":
        function["class"] = "read"
    else:
        implied = class_of(method)
        function.update(implied)
        report.deduce(f"{at}.class", implied["class"], f"from the verb {method}")
        if implied["class"] == "sensitive":
            function["confirm"] = confirmation(method, path)
            report.deduce(f"{at}.confirm", function["confirm"]["summary"], "a DELETE is confirmed")
    function["description"] = op["description"]
    function["input"] = {"type": "object", "additionalProperties": False, "properties": kept}
    if names_required:
        function["input"]["required"] = names_required
        report.block(
            p.EXAMPLE_MISSING,
            f"{at}.examples",
            f"the action {op['name']!r} has required arguments ({', '.join(names_required)}) and no example: "
            "write one that its input accepts",
        )
    else:
        function["examples"] = [{"title": "Without arguments", "input": {}}]
        report.deduce(f"{at}.examples", {}, "no required argument: the empty call is its example")
    refusals = []
    for j, refusal in enumerate(op.get("refusals") or []):
        if refusal.get("retryable") is True:
            report.drop(f"{at}.refusals.{j}.retryable", "a refusal of the format is not retried")
        refusals.append({k: refusal[k] for k in ("code", "when", "body", "message") if k in refusal})
    if refusals:
        function["refusals"] = refusals
    function["call"] = call
    if op.get("output"):
        function["output"] = {k: op["output"][k] for k in ("items", "strip", "projection") if k in op["output"]}
    return function


def _connector(account: str, connection: dict, definition: dict, report: _Report) -> dict:
    name = PREFIX + identifier(account, "app")
    report.deduce("connector.name", name, f"the reserved prefix {PREFIX!r} and the application's name in snake_case")
    label = (definition.get("label") or connection.get("label") or account).strip()
    connector: dict = {"name": name, "label": label, "namespace": name, "version": VERSION, "api_version": API_VERSION}
    report.deduce("connector.version", VERSION, "first version of the definition")
    report.deduce("connector.api_version", API_VERSION, "oto 1 does not say the API's version")
    connector["base_url"] = str(connection.get("base_url") or "").rstrip("/")
    auth, credential = _auth(connection, report)
    connector["auth"] = auth if auth is not None else {"kind": str(connection.get("auth_mode"))}
    if credential:
        connector["credential"] = credential
    connector["modes"] = ["byo_user", "byo_org"]
    report.deduce("connector.modes", connector["modes"], "a key of a member, a team or the org")
    if definition.get("headers"):
        connector["headers"] = dict(definition["headers"])
    connector["errors"] = standard_errors(label, (auth or {}).get("kind", "none"))
    report.deduce("connector.errors", [entry["code"] for entry in connector["errors"]], "the standard error table")
    for key in ("description", "logo_domain"):
        if definition.get(key):
            report.drop(f"definition.{key}", "not in the format yet")
    if connection.get("doc_path"):
        report.drop("connection.doc_path", "the raw calls, the documentation route included, stay in oto 1")
    return connector


def _shape(definition_oto1: object, report: _Report) -> bool:
    if not isinstance(definition_oto1, dict):
        report.block(p.SCHEMA_INVALID, "", "an application of oto 1 is an object with account, connection, definition")
        return False
    for key in definition_oto1:
        if key not in KEYS:
            report.block(p.KEY_NOT_ALLOWED, key, f"{key!r} is not part of an application ({', '.join(KEYS)})")
    connection = definition_oto1.get("connection")
    definition = definition_oto1.get("definition")
    if not isinstance(connection, dict) or not isinstance(definition, dict):
        report.block(p.SCHEMA_INVALID, "", "connection and definition are objects")
        return False
    for key in connection:
        if key in SECRET_FIELDS:
            report.block(p.KEY_NOT_ALLOWED, f"connection.{key}", "a secret has no place in an export of definitions")
        elif key in KEY_FIELDS:
            report.drop(f"connection.{key}", "a field of each key, not of the application")
        elif key not in CONNECTION_KEYS:
            report.block(p.KEY_NOT_ALLOWED, f"connection.{key}", f"{key!r} is not a connection field of oto 1")
    for key in definition:
        if key not in DEFINITION_KEYS:
            report.block(p.KEY_NOT_ALLOWED, f"definition.{key}", f"{key!r} is not a key of oto 1's definitions")
    operations = definition.get("operations")
    if not isinstance(operations, list) or not all(isinstance(op, dict) for op in operations):
        report.block(p.SCHEMA_INVALID, "definition.operations", "operations is a list of actions")
        return False
    return True


def from_oto1(definition_oto1: dict) -> tuple[Optional[dict], dict]:
    """`(document, report)`: the Custom API definition of one application of oto 1, or `None` when anything blocks,
    and what the report says of it (module docstring)."""
    account = str((definition_oto1 or {}).get("account") or "") if isinstance(definition_oto1, dict) else ""
    report = _Report(account)
    if not _shape(definition_oto1, report):
        return None, report.as_dict()
    connection, definition = definition_oto1["connection"], definition_oto1["definition"]
    connector = _connector(account, connection, definition, report)
    variables = _variables(definition)
    used: set[str] = set()
    functions = [_function(op, i, variables, used, report) for i, op in enumerate(definition["operations"])]
    for name in sorted(set((definition.get("variables") or {})) - used):
        report.drop(f"definition.variables.{name}", "no action has an argument of this name")
    document = {"connector": connector, "functions": functions}
    names = _names(document)
    named = {(problem.code, problem.path) for problem in report.blocking}
    unsupported = any(problem.code == p.AUTH_NOT_SUPPORTED for problem in report.blocking)
    collision = any(problem.code == p.ARGUMENT_NAME_COLLISION for problem in report.blocking)
    for problem in validate(document):
        path = names.get(problem.path, problem.path)
        if problem.code == p.EXAMPLE_MISSING or (problem.code, path) in named:
            continue  # already named by the takeover, in oto 1's words
        if unsupported and problem.code == p.SCHEMA_INVALID and problem.path in ("connector", "connector.auth"):
            continue  # the shape of an authentication the format has not: already named as not supported
        if collision and problem.code == p.RULE_BROKEN:
            continue  # two arguments merged into one: the rules only repeat the collision
        report.blocking.append(Problem(problem.code, path, problem.message, problem.rule))
    return (None if report.blocking else document), report.as_dict()


def _names(document: dict) -> dict[str, str]:
    """A path of the document said in oto 1's words, where it has one: the connector's address and authentication."""
    return {
        "connector.base_url": "connection.base_url",
        "connector.auth.token_url": "connection.token_url",
        "connector.auth.kind": "connection.auth_mode",
        "connector.headers": "definition.headers",
        **{
            dotted(("connector", "headers", h)): dotted(("definition", "headers", h))
            for h in document["connector"].get("headers", {})
        },
    }
