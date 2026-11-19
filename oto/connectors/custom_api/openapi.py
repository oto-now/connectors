"""The import of an OpenAPI document: candidates for a Custom API definition, never saved as such.

`from_openapi(document)` takes OpenAPI 3.0 or 3.1, or Swagger 2.0, in JSON or in YAML (a text, or the parsed object),
5 MB and 100 operations at most, and returns:

    {"connector": {...},             # the block proposed (`openapi_connector.propose`)
     "deduced": [...], "problems": [...],                 # of that block
     "candidates": [{"function": {...}, "deduced": [...], "problems": [...]}],
     "skipped": [{"path", "method", "code", "reason"}]}

Each candidate is a function of the format: its name from `operationId` in snake_case (or from the method and the
path), its class from the verb, its arguments in snake_case (the API's names stay in `call`): the path and query
parameters, the header parameters (in `call.headers`), the properties of a JSON object body (`allOf` merged). Its
example comes from the document's `example`/`examples`, only when the input accepts it; without one, and with a
required argument, the candidate carries `custom_api_example_missing`, and the screen asks for one before saving. Two
candidates of the same name: the second is renamed and carries `openapi_name_collision`, to confirm. Neither
`output.items` nor the pagination is guessed.

An operation the format cannot say is skipped, its code named: `openapi_method_not_supported`, `openapi_body_not_json`
(form, multipart), `openapi_body_not_object` (a bare list, `oneOf` at the root), `openapi_cookie_parameter` (a required
cookie; an optional one is left out, and said so), `openapi_ref_external`, `openapi_ref_too_deep`,
`openapi_ref_missing`, `custom_api_argument_name_collision`, `openapi_too_many_operations`. A document that is none
raises `OpenAPIRefused` (`openapi_not_a_document`, `openapi_too_large`).

`read_url(url)` reads a document by its address: https towards a public host, no redirect followed, 5 MB at most.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

import requests
import yaml
from jsonschema import Draft202012Validator

from oto.tools.common.url_guard import assert_public_https_url

from . import problems as p
from .naming import class_of, confirmation, identifier
from .openapi_connector import COOKIE_PARAMETER, propose
from .openapi_refs import RefRefused, example_of, inline, resolve
from .problems import Problem
from .profile import FORBIDDEN_HEADERS, MAX_FUNCTIONS

MAX_BYTES = 5 * 1024 * 1024
METHODS = ("get", "post", "put", "patch", "delete")
PATH_ITEM_KEYS = ("parameters", "summary", "description", "servers", "$ref")
MAX_DESCRIPTION = 1000
URL_TIMEOUT = (5, 20)
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
NOT_A_DOCUMENT = "openapi_not_a_document"
TOO_LARGE = "openapi_too_large"
METHOD_NOT_SUPPORTED = "openapi_method_not_supported"
BODY_NOT_JSON = "openapi_body_not_json"
BODY_NOT_OBJECT = "openapi_body_not_object"
NAME_COLLISION = "openapi_name_collision"
TOO_MANY_OPERATIONS = "openapi_too_many_operations"


class OpenAPIRefused(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Skip(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def parse(document: Any) -> dict:
    """The document as an object: JSON or YAML text, or already parsed; `OpenAPIRefused` when it is none."""
    if isinstance(document, (str, bytes)):
        raw = document.encode("utf-8") if isinstance(document, str) else document
        if len(raw) > MAX_BYTES:
            raise OpenAPIRefused(TOO_LARGE, f"the document weighs more than {MAX_BYTES // (1024 * 1024)} MB")
        try:
            document = json.loads(raw)
        except ValueError:
            try:
                document = yaml.safe_load(raw)
            except yaml.YAMLError as error:
                raise OpenAPIRefused(NOT_A_DOCUMENT, f"neither JSON nor YAML: {error}") from error
    if not isinstance(document, dict):
        raise OpenAPIRefused(NOT_A_DOCUMENT, "the document is not an object")
    if not (str(document.get("openapi", "")).startswith("3") or str(document.get("swagger", "")) == "2.0"):
        raise OpenAPIRefused(NOT_A_DOCUMENT, "neither OpenAPI 3 (openapi: 3.x) nor Swagger 2.0 (swagger: '2.0')")
    return document


def read_url(url: str, *, session: Optional[requests.Session] = None) -> str:
    """The text of the document at `url`, read without following a redirect, 5 MB at most."""
    try:
        assert_public_https_url(url)
    except ValueError as refused:
        raise OpenAPIRefused(NOT_A_DOCUMENT, f"the address is refused: {refused}") from refused
    response = (session or requests.Session()).get(url, allow_redirects=False, timeout=URL_TIMEOUT, stream=True)
    with response:
        if response.status_code != 200:
            raise OpenAPIRefused(NOT_A_DOCUMENT, f"the address answered HTTP {response.status_code}, not the document")
        body = b""
        for chunk in response.iter_content(64 * 1024):
            body += chunk
            if len(body) > MAX_BYTES:
                raise OpenAPIRefused(TOO_LARGE, f"the document weighs more than {MAX_BYTES // (1024 * 1024)} MB")
    return body.decode("utf-8", errors="replace")


def _parameters(document: dict, item: dict, operation: dict) -> list[dict]:
    """The parameters of an operation, its path's included, the operation's winning on the same place and name."""
    found: dict[tuple, dict] = {}
    for node in [*(item.get("parameters") or []), *(operation.get("parameters") or [])]:
        parameter, _ = resolve(document, node)
        if isinstance(parameter, dict) and parameter.get("name"):
            found[(parameter.get("in"), parameter["name"])] = parameter
    return list(found.values())


def _parameter_schema(document: dict, parameter: dict, swagger: bool) -> dict:
    if swagger:
        schema = {
            k: parameter[k]
            for k in (
                "type",
                "format",
                "enum",
                "items",
                "minimum",
                "maximum",
                "pattern",
                "minLength",
                "maxLength",
                "default",
            )
            if k in parameter
        }
    else:
        schema = inline(document, parameter.get("schema") or {})
    description = parameter.get("description")
    if isinstance(description, str) and description.strip():
        schema = {**schema, "description": description.strip()[:MAX_DESCRIPTION]}
    return schema


def _body(document: dict, operation: dict, parameters: list[dict], swagger: bool) -> tuple[Optional[dict], Any]:
    """The JSON object schema of the body and its example, `(None, None)` without a body; `_Skip` otherwise."""
    holder: Any = None
    if swagger:
        if any(parameter.get("in") == "formData" for parameter in parameters):
            raise _Skip(BODY_NOT_JSON, "the body is a form")
        body = next((parameter for parameter in parameters if parameter.get("in") == "body"), None)
        if body is None:
            return None, None
        schema, holder = body.get("schema"), body.get("schema")
    else:
        request_body, _ = resolve(document, operation.get("requestBody"))
        if not request_body:
            return None, None
        content = request_body.get("content") or {}
        media = next((m for m in content if m.split(";")[0].strip() == "application/json" or m.endswith("+json")), None)
        if media is None:
            raise _Skip(BODY_NOT_JSON, f"the body is {', '.join(content) or 'without content'}, not JSON")
        holder = content[media]
        schema = (content[media] or {}).get("schema")
    raw = schema or {}
    schema = inline(document, raw)
    if schema.get("allOf"):
        merged: dict = {"type": "object", "properties": {}, "required": []}
        for part in schema["allOf"]:
            merged["properties"].update(part.get("properties") or {})
            merged["required"] += part.get("required") or []
        schema = merged
    if (
        schema.get("type", "object") != "object"
        or not isinstance(schema.get("properties"), dict)
        or any(key in schema for key in ("oneOf", "anyOf"))
    ):
        raise _Skip(BODY_NOT_OBJECT, "the body is not a JSON object with declared properties")
    found, example = example_of(document, holder) if holder is not None else (False, None)
    return schema, (example if found else _property_examples(document, raw))


def _property_examples(document: dict, schema: Any) -> Optional[dict]:
    """The example a body object gives through its properties' own examples, when it gives none of its own."""
    schema, _ = resolve(document, schema)
    out = {}
    for name, sub in ((schema or {}).get("properties") or {}).items():
        found, value = example_of(document, sub)
        if found:
            out[name] = value
    return out or None


def _candidate(document: dict, path: str, method: str, item: dict, operation: dict) -> dict:
    swagger = "swagger" in document
    deduced: list[dict] = []
    properties: dict = {}
    required: list[str] = []
    places: dict[str, dict] = {"query": {}, "headers": {}, "body": {}}
    renamed: dict[str, str] = {}
    example: dict = {}

    def argument(api_name: str, schema: dict, is_required: bool) -> str:
        name = identifier(api_name, "arg")
        if name in properties:
            raise _Skip(p.ARGUMENT_NAME_COLLISION, f"two arguments become {name!r}")
        properties[name] = schema
        if is_required:
            required.append(name)
        return name

    parameters = _parameters(document, item, operation)
    for parameter in parameters:
        where, api_name = parameter.get("in"), parameter["name"]
        if where in ("body", "formData"):
            continue
        if where == "cookie":
            if parameter.get("required"):
                raise _Skip(COOKIE_PARAMETER, f"the cookie {api_name!r} is required")
            deduced.append({"path": f"cookie.{api_name}", "value": None, "why": "an optional cookie is left out"})
            continue
        if where == "header" and str(api_name).lower() in FORBIDDEN_HEADERS:
            deduced.append(
                {"path": f"header.{api_name}", "value": None, "why": "the authentication sends it, from the account"}
            )
            continue
        if where not in ("path", "query", "header"):
            raise _Skip(p.SCHEMA_INVALID, f"the parameter {api_name!r} is in {where!r}")
        name = argument(
            api_name,
            _parameter_schema(document, parameter, swagger),
            where == "path" or bool(parameter.get("required")),
        )
        if where == "path":
            renamed[api_name] = name
        else:
            places["headers" if where == "header" else where][api_name] = name
        found, value = example_of(document, parameter)
        if found:
            example[name] = value
    body, body_example = _body(document, operation, parameters, swagger)
    if body is not None:
        body_required = set(body.get("required") or [])
        for api_name, schema in body["properties"].items():
            places["body"][api_name] = argument(api_name, schema, api_name in body_required)
            if isinstance(body_example, dict) and api_name in body_example:
                example[places["body"][api_name]] = body_example[api_name]
    for api_name in _placeholders(path):
        if api_name not in renamed:
            renamed[api_name] = argument(api_name, {"type": "string"}, True)
            deduced.append(
                {
                    "path": f"input.{renamed[api_name]}",
                    "value": {"type": "string"},
                    "why": "the path cites it, the document does not declare it",
                }
            )
    for api_name, name in renamed.items():
        path = path.replace("{" + api_name + "}", "{" + name + "}")
    return _function(document, path, method, operation, properties, required, places, example, deduced)


def _placeholders(path: str) -> list[str]:
    return PLACEHOLDER.findall(path)


def _function(document, path, method, operation, properties, required, places, example, deduced) -> dict:
    problems: list[Problem] = []
    if isinstance(operation.get("operationId"), str) and operation["operationId"].strip():
        name = identifier(operation["operationId"], "op")
    else:
        parts = [part for part in path.split("/") if part and not part.startswith("{")]
        name = identifier(f"{method}_{'_'.join(parts) or 'root'}", "op")
        deduced.append({"path": "name", "value": name, "why": "the operation has no operationId"})
    texts = [
        t.strip() for t in (operation.get("summary"), operation.get("description")) if isinstance(t, str) and t.strip()
    ]
    description = " — ".join(dict.fromkeys(texts)) or f"{method.upper()} {path}"
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 1].rstrip() + "…"
    function: dict = {"name": name, **class_of(method)}
    deduced.append({"path": "class", "value": function["class"], "why": f"from the verb {method.upper()}"})
    if function["class"] == "sensitive":
        function["confirm"] = confirmation(method, path)
    function["description"] = description
    function["input"] = {"type": "object", "additionalProperties": False, "properties": properties}
    if required:
        function["input"]["required"] = list(dict.fromkeys(required))
    examples = _examples(function["input"], example, deduced, problems)
    if examples:
        function["examples"] = examples
    call: dict = {"method": method.upper(), "path": path}
    call.update({where: table for where, table in places.items() if table})
    function["call"] = call
    return {"function": function, "deduced": deduced, "problems": problems}


def _examples(schema: dict, example: dict, deduced: list[dict], problems: list[Problem]) -> list[dict]:
    validator = Draft202012Validator(schema)
    if example and not any(validator.iter_errors(example)):
        deduced.append({"path": "examples", "value": example, "why": "the document's example, which the input accepts"})
        return [{"title": "From the document", "input": example}]
    if not schema.get("required"):
        deduced.append({"path": "examples", "value": {}, "why": "no required argument: the empty call is its example"})
        return [{"title": "Without arguments", "input": {}}]
    problems.append(Problem(p.EXAMPLE_MISSING, "examples", "no example the input accepts: write one before saving"))
    return []


def from_openapi(document: Any) -> dict:
    """Candidates for a Custom API definition (module docstring)."""
    document = parse(document)
    connector, connector_deduced, connector_problems = propose(document)
    candidates: list[dict] = []
    skipped: list[dict] = []
    names: set[str] = set()
    for path, node in (document.get("paths") or {}).items():
        try:
            item, _ = resolve(document, node)
        except RefRefused as refused:
            skipped.append({"path": path, "method": "*", "code": refused.code, "reason": refused.reason})
            continue
        for method, operation in (item or {}).items():
            if method in PATH_ITEM_KEYS or str(method).startswith("x-"):
                continue
            entry = {"path": path, "method": str(method).upper()}
            if method not in METHODS:
                skipped.append({**entry, "code": METHOD_NOT_SUPPORTED, "reason": f"{method.upper()} is not supported"})
                continue
            if len(candidates) >= MAX_FUNCTIONS:
                skipped.append({**entry, "code": TOO_MANY_OPERATIONS, "reason": f"beyond {MAX_FUNCTIONS} operations"})
                continue
            try:
                candidate = _candidate(document, path, method, item, operation if isinstance(operation, dict) else {})
            except (_Skip, RefRefused) as refused:
                skipped.append({**entry, "code": refused.code, "reason": refused.reason})
                continue
            function = candidate["function"]
            if function["name"] in names:
                base, n = function["name"], 2
                while f"{base[:58]}_{n}" in names:
                    n += 1
                function["name"] = f"{base[:58]}_{n}"
                candidate["problems"].append(
                    Problem(NAME_COLLISION, "name", f"{base!r} is taken: {function['name']!r} is proposed, to confirm")
                )
            names.add(function["name"])
            candidates.append(candidate)
    return {
        "connector": connector,
        "deduced": connector_deduced,
        "problems": [problem.as_dict() for problem in connector_problems],
        "candidates": [{**c, "problems": [problem.as_dict() for problem in c["problems"]]} for c in candidates],
        "skipped": skipped,
    }
