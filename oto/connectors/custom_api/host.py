"""The host profile: the functions of a host's own API, described in OpenAPI, served beside oto's under the person's
token (docs/conception/profil-hote.md).

`from_host_openapi(document, *, reserved) -> {"definition": dict | None, "problems": [...]}` reads the document by the
walk of the import (`openapi.walk`, `openapi.parts`), and is strict where the import proposes: a document with a
problem gives no definition, and every problem is named, each with its code, its place and its message. `reserved` is
the set of the namespaces the caller already serves (its own functions, its connectors): a namespace of the host may
be none of them.

The definition:

    {"host": {"functions": "/me/functions", "roles": {...}, "families": {...}},
     "functions": {"projet.lister": {"label", "family", "roles", "summary", "scope"}},   # in the document's order
     "connectors": [<a document of the format per namespace, in order of appearance>]}

Each operation is a function named by its `operationId`, `namespace.verb`, kept as it is: the namespace is its
connector, the verb its name. Its class, effect, examples and output are its `x-oto-*` extensions; its description is
the operation's. Each connector reads the host's address from the setting `address`, followed by the relative server
of the document, and authenticates by `host_delegated`: the token comes from the host that runs the function, for the
issuer and the resource the document names (`x-oto`). A sensitive function is confirmed by the API's own two steps
(`confirm: {by: host}`): `confirm`, a query parameter reserved to that protocol, is never an argument. A family of
`x-oto` is its label, or `{label, scope}`: the OAuth scope the token of its functions is asked with, carried by each of
them (`scope`, `None` for a family without one).
"""

from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlsplit

from . import problems as p
from .errors import standard_errors
from .naming import identifier
from .openapi import OpenAPIRefused, _parameters, _Skip, parse, parts, walk
from .openapi_refs import RefRefused
from .openapi_connector import SERVER_VARIABLES
from .problems import Problem
from .profile import _example_problems, _rule_problems, _schema_problems, internal_host

OPENAPI_VERSION = "host_openapi_version"
SERVER_NOT_RELATIVE = "host_server_not_relative"
ROOT_INVALID = "host_root_invalid"
EXTENSION_UNKNOWN = "host_extension_unknown"
EXTENSION_MISSING = "host_extension_missing"
EXTENSION_INVALID = "host_extension_invalid"
OPERATION_ID_INVALID = "host_operation_id_invalid"
NAME_COLLISION = "host_name_collision"
NAMESPACE_RESERVED = "host_namespace_reserved"
NAMESPACE_ACT = "host_namespace_act"
SUMMARY_INVALID = "host_summary_invalid"
DESCRIPTION_INVALID = "host_description_invalid"
CLASS_MISMATCH = "host_class_mismatch"
INPUT_NOT_CLOSED = "host_input_not_closed"
EXAMPLE_MISSING = "host_example_missing"
EXAMPLE_INVALID = "host_example_invalid"
CONFIRM_RESERVED = "host_confirm_reserved"
CONFIRM_MISSING = "host_confirm_missing"
SCHEMA_INVALID = "host_schema_invalid"
RULE_BROKEN = "host_rule_broken"
SCOPE_INVALID = "host_scope_invalid"
#: The codes of the profile itself; a document also gets those of the import (`openapi_*`).
CODES = (
    OPENAPI_VERSION,
    SERVER_NOT_RELATIVE,
    ROOT_INVALID,
    EXTENSION_UNKNOWN,
    EXTENSION_MISSING,
    EXTENSION_INVALID,
    OPERATION_ID_INVALID,
    NAME_COLLISION,
    NAMESPACE_RESERVED,
    NAMESPACE_ACT,
    SUMMARY_INVALID,
    DESCRIPTION_INVALID,
    CLASS_MISMATCH,
    INPUT_NOT_CLOSED,
    EXAMPLE_MISSING,
    EXAMPLE_INVALID,
    CONFIRM_RESERVED,
    CONFIRM_MISSING,
    SCHEMA_INVALID,
    RULE_BROKEN,
    SCOPE_INVALID,
)
#: The judges of the format, their codes said in the profile's.
JUDGED = {p.SCHEMA_INVALID: SCHEMA_INVALID, p.EXAMPLE_INVALID: EXAMPLE_INVALID, p.RULE_BROKEN: RULE_BROKEN}

OPERATION_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
URI = re.compile(r"^[a-z][a-z0-9+.-]*:\S+$")
#: An OAuth scope (RFC 6749 §3.3): tokens of printable ASCII but the space, `"` and `\`, separated by one space.
SCOPE = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]+( [\x21\x23-\x5B\x5D-\x7E]+)*$")
MAX_OPERATION_ID = 64
MAX_SUMMARY = 90
MAX_DESCRIPTION = 1000
ROOT = "x-oto"
ROOT_KEYS = ("functions", "issuer", "resource", "roles", "families")
FAMILY_KEYS = ("label", "scope")
EXTENSIONS = ("x-oto-class", "x-oto-effect", "x-oto-examples", "x-oto-roles", "x-oto-label", "x-oto-family", "x-oto-output")
REQUIRED = ("x-oto-class", "x-oto-roles", "x-oto-label", "x-oto-family")
CLASSES = ("read", "write", "sensitive")
EFFECTS = ("send", "destroy")
OUTPUT_KEYS = ("items", "strip", "projection")
ACT_SUFFIX = "_act"
#: The query parameter of the two steps of a sensitive function, never an argument.
CONFIRM = "confirm"
SETTING = {"name": "address", "label": "Address of the host", "type": "url"}
VERSION = "1.0.0"


def _blank(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip()


def _labels(value: Any) -> bool:
    """A table of identifiers to texts, not empty: the roles of the root."""
    return (
        isinstance(value, dict)
        and bool(value)
        and all(IDENTIFIER.match(str(key)) and not _blank(text) for key, text in value.items())
    )


def _families(value: Any, problems: list[Problem]) -> dict | None:
    """The families of the root, each a label or `{label, scope}`; `None` with a fault, each fault named."""
    at = f"{ROOT}.families"
    if not (isinstance(value, dict) and value and all(isinstance(k, str) and IDENTIFIER.match(k) for k in value)):
        problems.append(Problem(ROOT_INVALID, at, "the families, a table of identifiers to a label or to "
                                "{label, scope}, not empty"))
        return None
    found = len(problems)
    for key, family in value.items():
        if isinstance(family, str):
            if _blank(family):
                problems.append(Problem(ROOT_INVALID, f"{at}.{key}", "the label of the family, not blank"))
            continue
        if not isinstance(family, dict):
            problems.append(Problem(ROOT_INVALID, f"{at}.{key}", "a label, or {label, scope}"))
            continue
        problems += [
            Problem(EXTENSION_UNKNOWN, f"{at}.{key}.{name}", f"{name!r} is not a key of a family "
                    f"({', '.join(FAMILY_KEYS)})")
            for name in family
            if name not in FAMILY_KEYS
        ]
        if _blank(family.get("label")):
            problems.append(Problem(ROOT_INVALID, f"{at}.{key}.label", "the label of the family, not blank"))
        scope = family.get("scope")
        if not (isinstance(scope, str) and SCOPE.fullmatch(scope)):
            problems.append(Problem(SCOPE_INVALID, f"{at}.{key}.scope", f"{scope!r} is not an OAuth scope "
                                    "(RFC 6749 §3.3): tokens of printable ASCII but \" and \\, separated by one space; "
                                    "a family without a scope is written as its label alone"))
    return None if len(problems) > found else dict(value)


def _scope(family: Any) -> str | None:
    """The OAuth scope of a family of the root, `None` for a family written as its label alone."""
    return family["scope"] if isinstance(family, dict) else None


def _unknown(node: dict, at: str, known: tuple) -> list[Problem]:
    where = f"{at}." if at else ""
    return [
        Problem(EXTENSION_UNKNOWN, f"{where}{key}", f"{key!r} is not an extension oto reads here "
                f"({', '.join(known) or 'none'})")
        for key in node
        if isinstance(key, str) and key.startswith("x-oto-") and key not in known
    ]


def _root(document: dict, problems: list[Problem]) -> dict:
    """The `x-oto` block, each of its faults named; the values found sound, by key."""
    root = document.get(ROOT)
    if not isinstance(root, dict):
        problems.append(Problem(ROOT_INVALID, ROOT, "the document has no x-oto block: functions, issuer, resource, "
                                "roles, families"))
        return {}
    problems += [
        Problem(EXTENSION_UNKNOWN, f"{ROOT}.{key}", f"{key!r} is not a key of x-oto ({', '.join(ROOT_KEYS)})")
        for key in root
        if key not in ROOT_KEYS
    ]
    sound: dict = {}
    functions = root.get("functions")
    if isinstance(functions, str) and re.match(r"^/[^/{}?#\s][^{}?#\s]*$", functions):
        sound["functions"] = functions
    else:
        problems.append(Problem(ROOT_INVALID, f"{ROOT}.functions", "the path of the person's functions, relative "
                                "to the address: /me/functions"))
    issuer = root.get("issuer")
    split = urlsplit(issuer) if isinstance(issuer, str) else None
    if (
        split is not None
        and split.scheme == "https"
        and split.hostname
        and not internal_host(split.hostname)
        and not any(c in issuer for c in "{}?# ")
    ):
        sound["issuer"] = issuer
    else:
        problems.append(Problem(ROOT_INVALID, f"{ROOT}.issuer", "the issuer of the tokens, a fixed https address "
                                "towards a public host, as its tokens name it"))
    resource = root.get("resource")
    if isinstance(resource, str) and URI.match(resource):
        sound["resource"] = resource
    else:
        problems.append(Problem(ROOT_INVALID, f"{ROOT}.resource", "the resource indicator of the API at the issuer, "
                                "an absolute URI"))
    if _labels(root.get("roles")):
        sound["roles"] = dict(root["roles"])
    else:
        problems.append(Problem(ROOT_INVALID, f"{ROOT}.roles", "the roles, a table of identifiers to texts, not empty"))
    families = _families(root.get("families"), problems)
    if families is not None:
        sound["families"] = families
    return sound


def _server(document: dict, problems: list[Problem]) -> str:
    """The relative server of the document, without its final slash; `""` without one."""
    if "swagger" in document or not str(document.get("openapi", "")).startswith("3."):
        problems.append(Problem(OPENAPI_VERSION, "openapi", "a host's API is described in OpenAPI 3.1 (3.0 accepted)"))
        return ""
    servers = document.get("servers")
    if servers in (None, []):
        return ""
    if not isinstance(servers, list) or len(servers) != 1 or not isinstance(servers[0], dict):
        problems.append(Problem(SERVER_NOT_RELATIVE, "servers", "one relative server at most: the address is the "
                                "organisation's, the document the same for all"))
        return ""
    url = servers[0].get("url")
    if isinstance(url, str) and ("{" in url or servers[0].get("variables")):
        problems.append(Problem(SERVER_VARIABLES, "servers.0", f"{url!r} has variables, which a host's document has "
                                "not: the address is a setting of the organisation"))
        return ""
    if not isinstance(url, str) or not url.startswith("/") or url.startswith("//"):
        problems.append(Problem(SERVER_NOT_RELATIVE, "servers.0", f"{url!r} is not a relative path (/api/v2): the "
                                "address is a setting of the organisation"))
        return ""
    return url.rstrip("/")


def _texts(operation: dict, at: str, problems: list[Problem]) -> None:
    summary, description = operation.get("summary"), operation.get("description")
    if _blank(summary) or len(summary.strip()) > MAX_SUMMARY:
        problems.append(Problem(SUMMARY_INVALID, f"{at}.summary", f"the line of the index, 1 to {MAX_SUMMARY} "
                                "characters"))
    if _blank(description) or len(description.strip()) > MAX_DESCRIPTION:
        problems.append(Problem(DESCRIPTION_INVALID, f"{at}.description", f"the contract the assistant reads, 1 to "
                                f"{MAX_DESCRIPTION} characters"))


def _extensions(operation: dict, method: str, at: str, root: dict, problems: list[Problem]) -> None:
    """The faults of the operation's `x-oto-*` extensions, but its examples."""
    problems += _unknown(operation, at, EXTENSIONS)
    problems += [
        Problem(EXTENSION_MISSING, f"{at}.{key}", f"{key} is required")
        for key in REQUIRED
        if key not in operation
    ]
    cls = operation.get("x-oto-class")
    if "x-oto-class" in operation and cls not in CLASSES:
        problems.append(Problem(EXTENSION_INVALID, f"{at}.x-oto-class", f"{cls!r} is not a class ({', '.join(CLASSES)})"))
    elif cls in CLASSES and (cls == "read") != (method == "get"):
        problems.append(Problem(CLASS_MISMATCH, f"{at}.x-oto-class", f"{method.upper()} is {cls!r}: GET reads, the "
                                "other verbs write (write or sensitive)"))
    if "x-oto-effect" in operation and (operation["x-oto-effect"] not in EFFECTS or cls != "sensitive"):
        problems.append(Problem(EXTENSION_INVALID, f"{at}.x-oto-effect", f"a sensitive function's effect "
                                f"({', '.join(EFFECTS)})"))
    if "x-oto-label" in operation and _blank(operation["x-oto-label"]):
        problems.append(Problem(EXTENSION_INVALID, f"{at}.x-oto-label", "the label shown to the person, not blank"))
    family = operation.get("x-oto-family")
    if "x-oto-family" in operation and "families" in root and not (isinstance(family, str) and family in root["families"]):
        problems.append(Problem(EXTENSION_INVALID, f"{at}.x-oto-family", f"{family!r} is not a family of x-oto"))
    roles = operation.get("x-oto-roles")
    if "x-oto-roles" in operation and (
        not isinstance(roles, list)
        or not roles
        or len(set(map(str, roles))) != len(roles)
        or ("roles" in root and any(not (isinstance(role, str) and role in root["roles"]) for role in roles))
    ):
        problems.append(Problem(EXTENSION_INVALID, f"{at}.x-oto-roles", "a list of roles of x-oto, each once, "
                                "not empty"))
    output = operation.get("x-oto-output")
    if "x-oto-output" in operation and (not isinstance(output, dict) or any(k not in OUTPUT_KEYS for k in output)):
        problems.append(Problem(EXTENSION_INVALID, f"{at}.x-oto-output", f"the output block: {', '.join(OUTPUT_KEYS)}"))


def _examples(operation: dict, arguments: dict, at: str, problems: list[Problem]) -> list[dict]:
    """The examples, their keys from the API's names to the arguments' (an unknown key stays, for the input to refuse
    it)."""
    examples = operation.get("x-oto-examples")
    if examples is None or examples == []:
        problems.append(Problem(EXAMPLE_MISSING, f"{at}.x-oto-examples", "at least one example, the arguments as the "
                                "assistant passes them"))
        return []
    if not isinstance(examples, list) or not all(isinstance(example, dict) for example in examples):
        problems.append(Problem(EXTENSION_INVALID, f"{at}.x-oto-examples", "a list of arguments, each an object"))
        return []
    return [
        {"title": f"Example {i + 1}", "input": {arguments.get(key, key): value for key, value in example.items()}}
        for i, example in enumerate(examples)
    ]


def _impostors(document: dict, item: dict, operation: dict, found) -> list[str]:
    """The names, other than the reserved query parameter itself, that would carry `confirm`: any argument (path,
    query, header, body) or cookie whose name, normalised as an argument's, is `confirm` (`Confirm`, `CONFIRM`…)."""
    names = list(found.arguments) + [
        parameter["name"] for parameter in _parameters(document, item, operation) if parameter.get("in") == "cookie"
    ]
    return [name for name in names if identifier(str(name), "arg") == CONFIRM]


def _confirm(document: dict, item: dict, operation: dict, found, cls: Any, at: str, problems: list[Problem]) -> None:
    parameter = found.left_out.get(CONFIRM)
    problems += [
        Problem(CONFIRM_RESERVED, at, f"{name!r} would carry confirm, which is reserved to the protocol of the two "
                "steps: the query parameter confirm only")
        for name in _impostors(document, item, operation, found)
    ]
    if cls != "sensitive":
        if parameter is not None:
            problems.append(Problem(CONFIRM_RESERVED, f"{at}.parameters", "confirm is the parameter of a sensitive "
                                    "function only"))
        return
    if parameter is None or (parameter.get("schema") or {}).get("type", parameter.get("type")) != "boolean":
        problems.append(Problem(CONFIRM_MISSING, f"{at}.parameters", "a sensitive function declares confirm, a "
                                "boolean query parameter"))
    if "202" not in {str(status) for status in operation.get("responses") or {}}:
        problems.append(Problem(CONFIRM_MISSING, f"{at}.responses", "a sensitive function answers 202 "
                                "{ status: needs_confirmation, summary } when called without confirm"))


def _operation(document: dict, path: str, method: str, item: dict, operation: dict, root: dict,
               reserved: frozenset) -> dict:
    at = f"paths.{path}.{method}"
    problems: list[Problem] = []
    if "servers" in item or "servers" in operation:
        problems.append(Problem(SERVER_NOT_RELATIVE, at, "a server per path or operation is not followed: one "
                                "document, one address"))
    name = operation.get("operationId")
    namespace = verb = None
    if not isinstance(name, str) or not OPERATION_ID.match(name) or len(name) > MAX_OPERATION_ID:
        problems.append(Problem(OPERATION_ID_INVALID, f"{at}.operationId", f"{name!r} is not namespace.verb, in "
                                f"lowercase letters, digits and _, {MAX_OPERATION_ID} characters at most"))
    else:
        namespace, verb = name.split(".")
        if namespace in reserved:
            problems.append(Problem(NAMESPACE_RESERVED, f"{at}.operationId", f"the namespace {namespace!r} is "
                                    "already served"))
        if namespace.endswith(ACT_SUFFIX):
            problems.append(Problem(NAMESPACE_ACT, f"{at}.operationId", f"{namespace!r} ends with {ACT_SUFFIX!r}, the "
                                    "suffix of the side that acts"))
    _texts(operation, at, problems)
    _extensions(operation, method, at, root, problems)
    try:
        found = parts(document, path, method, item, operation, leave_out=(CONFIRM,))
    except (_Skip, RefRefused) as refused:
        problems.append(Problem(refused.code, at, refused.reason))
        return {"at": at, "name": name, "namespace": None, "problems": problems}
    cls = operation.get("x-oto-class")
    _confirm(document, item, operation, found, cls, at, problems)
    if found.body is not None and found.body.get("additionalProperties") is not False:
        problems.append(Problem(INPUT_NOT_CLOSED, f"{at}.requestBody", "the JSON body is closed: "
                                "additionalProperties: false"))
    examples = _examples(operation, found.arguments, at, problems)
    function: dict = {"name": verb, "class": cls}
    if "x-oto-effect" in operation:
        function["effect"] = operation["x-oto-effect"]
    function["description"] = str(operation.get("description") or "").strip()
    function["input"] = {"type": "object", "additionalProperties": False, "properties": found.properties}
    if found.required:
        function["input"]["required"] = list(dict.fromkeys(found.required))
    function["examples"] = examples
    call: dict = {"method": method.upper(), "path": found.path}
    call.update({where: table for where, table in found.places.items() if table})
    function["call"] = call
    if isinstance(operation.get("x-oto-output"), dict):
        function["output"] = dict(operation["x-oto-output"])
    if cls == "sensitive":
        function["confirm"] = {"by": "host"}
    family = operation.get("x-oto-family")
    meta = {
        "label": operation.get("x-oto-label"),
        "family": family,
        "roles": operation.get("x-oto-roles"),
        "summary": str(operation.get("summary") or "").strip(),
        "scope": _scope(root.get("families", {}).get(family)) if isinstance(family, str) else None,
    }
    return {"at": at, "name": name, "namespace": namespace, "function": function, "meta": meta, "problems": problems}


def _judged(document: dict, places: list[str]) -> list[Problem]:
    """The judges of the format on a connector built: the schema, the examples, the rules; each problem said at the
    place of its operation in the OpenAPI document, its code in the profile's."""
    found, matches = _schema_problems(document, [])
    if matches:
        found = _example_problems(document)
        if not found:
            found = _rule_problems(document)
    out = []
    for problem in found:
        path = problem.path
        head = re.match(r"^functions\.(\d+)(\..*)?$", path)
        if head:
            path = places[int(head.group(1))] + (head.group(2) or "")
        out.append(Problem(JUDGED[problem.code], path, problem.message, rule=problem.rule))
    return out


def _connector(namespace: str, info: dict, server: str, root: dict) -> dict:
    title = str(info.get("title")).strip()
    return {
        "name": namespace,
        "label": title,
        "namespace": namespace,
        "version": VERSION,
        "api_version": str(info.get("version")),
        "base_url": "{" + SETTING["name"] + "}" + server,
        "settings": [dict(SETTING)],
        "auth": {"kind": "host_delegated", "issuer": root.get("issuer"), "resource": root.get("resource")},
        "modes": ["byo_user"],
        "errors": standard_errors(title, "host_delegated"),
    }


def from_host_openapi(document: Any, *, reserved: Iterable[str]) -> dict:
    """The definition a host's OpenAPI document gives, or its problems (module docstring)."""
    try:
        document = parse(document)
    except OpenAPIRefused as refused:
        return {"definition": None, "problems": [Problem(refused.code, "", refused.message).as_dict()]}
    problems: list[Problem] = []
    server = _server(document, problems)
    root = _root(document, problems)
    info = document.get("info") if isinstance(document.get("info"), dict) else {}
    for key in ("title", "version"):
        if _blank(str(info.get(key) or "")):
            problems.append(Problem(ROOT_INVALID, f"info.{key}", f"the document's {key}"))
    problems += _unknown(document, "", ())
    taken = frozenset(reserved)
    built, skipped = walk(
        document, lambda path, method, item, operation: _operation(document, path, method, item, operation, root, taken)
    )
    problems += [
        Problem(s["code"], f"paths.{s['path']}" + ("" if s["method"] == "*" else f".{s['method'].lower()}"), s["reason"])
        for s in skipped
    ]
    seen: set[str] = set()
    connectors: dict[str, dict] = {}
    for operation in built:
        problems += operation["problems"]
        name = operation["name"]
        if operation["namespace"] is None:
            continue
        if name in seen:
            problems.append(Problem(NAME_COLLISION, f"{operation['at']}.operationId", f"{name!r} is declared twice"))
            continue
        seen.add(name)
        connectors.setdefault(operation["namespace"], {"functions": [], "places": []})
        connectors[operation["namespace"]]["functions"].append(operation["function"])
        connectors[operation["namespace"]]["places"].append(operation["at"])
    if problems:
        return {"definition": None, "problems": [problem.as_dict() for problem in p.unique(problems)]}
    documents = []
    for namespace, found in connectors.items():
        built_document = {"connector": _connector(namespace, info, server, root), "functions": found["functions"]}
        problems += _judged(built_document, found["places"])
        documents.append(built_document)
    if problems:
        return {"definition": None, "problems": [problem.as_dict() for problem in p.unique(problems)]}
    definition = {
        "host": {"functions": root["functions"], "roles": root["roles"], "families": root["families"]},
        "functions": {operation["name"]: operation["meta"] for operation in built},
        "connectors": documents,
    }
    return {"definition": definition, "problems": []}

