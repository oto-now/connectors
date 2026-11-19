"""The Custom API profile: a definition IS a `connector.yaml` of the format, restricted to what a simple JSON REST call
needs and to what never touches the resources of the host (docs/conception/custom-api.md).

`validate(document) -> list[Problem]` judges, and returns every problem, each with its `custom_api_*` code:

1. the profile itself, on any value: the keys it admits (`custom_api_key_not_allowed`, the key named), the
   authentication modes (`custom_api_auth_not_supported`), every address in https towards an external host
   (`custom_api_base_url_not_https`, `custom_api_base_url_internal`: the host is read as written, without DNS; the
   host guards the resolved address at each call), the reserved prefix (`custom_api_name_reserved`), no header that
   authenticates or routes (`custom_api_header_authenticates`), the bounds (`custom_api_too_large`) and the examples
   (`custom_api_example_missing`);
2. the schema of the format, `connectors/connector.schema.json`, each error with its path (`custom_api_schema_invalid`),
   except where the profile already named the problem;
3. on a document the schema accepts: each example against its function's input (`custom_api_example_invalid`); then,
   when nothing above was found, the rules of the format the profile needs (`oto.connectors.rules`, the same copy the
   fabrique applies to the shared connectors), each broken rule as `custom_api_rule_broken`, its own code in `rule`
   when it has one. A rule judges a document the profile admits: on a refused one, it would only repeat the refusal
   (a constant `Authorization` header is also « set twice »).

An empty list: the definition can be saved, and the engines run it (`oauth2_user` aside in the lib's). The host raises
the `version` at each save.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Callable
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from oto.tools.common.url_guard import non_public

from ..rules.body import BODY_REFUSALS, LIST_BODY_REFUSALS, NESTED_BODY_REFUSALS
from ..rules.checks import (
    DescriptionError,
    _auth_header,
    check_act,
    check_arguments,
    check_auth,
    check_checks,
    check_constants,
    check_personal,
    check_probe,
    check_signals,
    check_timeout,
    strip_refusal,
)
from . import problems as p
from .problems import Problem, dotted
from .schema import schema

#: The reserved prefix of a Custom API's name and namespace; no shared connector bears it (guard of the fabrique).
PREFIX = "custom_"
#: Bounds taken over from oto 1.
MAX_FUNCTIONS = 100
MAX_BYTES = 256 * 1024

TOP_KEYS = ("connector", "functions")
CONNECTOR_KEYS = (
    "name",
    "label",
    "namespace",
    "version",
    "api_version",
    "description",
    "logo_domain",
    "base_url",
    "auth",
    "credential",
    "modes",
    "timeout_s",
    "query_arrays",
    "headers",
    "rate_limit",
    "probe",
    "errors",
)
FUNCTION_KEYS = (
    "name",
    "class",
    "act",
    "effect",
    "description",
    "input",
    "examples",
    "refusals",
    "checks",
    "expect",
    "call",
    "pagination",
    "output",
    "timeout_s",
    "confirm",
)
AUTH_KINDS = ("none", "api_key", "bearer", "basic", "oauth2_client_credentials", "oauth2_user")
#: Keys of `auth` that need settings or access levels, which the profile has not.
AUTH_REFUSED = ("token_urls", "access_levels", "scope_implies", "default_level", "from_token")
MODES = ("byo_user", "byo_org")
#: Inside an admitted block, what is refused: a file in transit, a file rendered, a balance read by the probe.
CALL_REFUSED = ("files",)
OUTPUT_REFUSED = ("file",)
PROBE_REFUSED = ("balance",)
#: Headers that carry an identity or route the request: never a constant nor an argument of a definition.
FORBIDDEN_HEADERS = ("authorization", "cookie", "set-cookie", "host", "proxy-authorization", "x-api-key", "api-key")
#: Host names that only resolve inside a network.
INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".intranet", ".lan", ".home.arpa", ".corp", ".private")
#: The rules that have their own code, read in the message of a broken one.
CODED_RULES = BODY_REFUSALS + NESTED_BODY_REFUSALS + LIST_BODY_REFUSALS
WHERE = "definition"


def internal_host(host: str) -> bool:
    """A host that is not on the public internet, as written: an address that is not public, a name without a dot,
    `localhost` or a suffix kept for internal networks."""
    host = host.lower().rstrip(".")
    try:
        return non_public(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        pass
    return "." not in host or host == "localhost" or host.endswith(INTERNAL_SUFFIXES)


def _foreign_keys(value: object, admitted: tuple[str, ...], at: tuple, why: str) -> list[Problem]:
    if not isinstance(value, dict):
        return []
    return [
        Problem(p.KEY_NOT_ALLOWED, dotted(at + (key,)), f"{key!r} is not admitted in a Custom API definition: {why}")
        for key in value
        if key not in admitted
    ]


def _refused_keys(value: object, refused: tuple[str, ...], at: tuple, why: str) -> list[Problem]:
    if not isinstance(value, dict):
        return []
    return [
        Problem(p.KEY_NOT_ALLOWED, dotted(at + (key,)), f"{key!r} is not admitted in a Custom API definition: {why}")
        for key in refused
        if key in value
    ]


def _address(url: object, at: tuple) -> list[Problem]:
    if not isinstance(url, str):
        return []
    if "{" in url:
        return [
            Problem(
                p.KEY_NOT_ALLOWED,
                dotted(at),
                "a Custom API address is fixed: a template cites settings, which a definition has not",
            )
        ]
    parts = urlsplit(url)
    if parts.scheme != "https":
        return [Problem(p.BASE_URL_NOT_HTTPS, dotted(at), "the address must start with https://")]
    if not parts.hostname or internal_host(parts.hostname):
        return [
            Problem(
                p.BASE_URL_INTERNAL,
                dotted(at),
                "the address must name a host on the public internet, never a private, loopback or internal one",
            )
        ]
    return []


def _auth_header_name(auth: object) -> str | None:
    try:
        return _auth_header(auth) if isinstance(auth, dict) else None
    except KeyError:
        return None


def _headers(headers: object, at: tuple, auth_header: str | None) -> list[Problem]:
    if not isinstance(headers, dict):
        return []
    out = []
    for name in headers:
        low = str(name).lower()
        if low in FORBIDDEN_HEADERS or low.startswith("proxy-") or (auth_header and low == auth_header.lower()):
            out.append(
                Problem(
                    p.HEADER_AUTHENTICATES,
                    dotted(at + (name,)),
                    f"the header {name!r} authenticates or routes the request: the secret lives in the "
                    "vault, never in a definition",
                )
            )
    return out


def _connector_problems(connector: object) -> list[Problem]:
    if not isinstance(connector, dict):
        return []
    at = ("connector",)
    out = _foreign_keys(
        connector, CONNECTOR_KEYS, at, "it touches the resources of the host or goes beyond simple REST"
    )
    for key in ("name", "namespace"):
        value = connector.get(key)
        if isinstance(value, str) and not value.startswith(PREFIX):
            out.append(
                Problem(
                    p.NAME_RESERVED,
                    dotted(at + (key,)),
                    f"a Custom API's {key} starts with the reserved prefix {PREFIX!r}",
                )
            )
    auth = connector.get("auth")
    if isinstance(auth, dict):
        kind = auth.get("kind")
        if isinstance(kind, str) and kind not in AUTH_KINDS:
            out.append(
                Problem(
                    p.AUTH_NOT_SUPPORTED,
                    dotted(at + ("auth", "kind")),
                    f"{kind!r} is not a Custom API authentication ({', '.join(AUTH_KINDS)})",
                )
            )
        out += _refused_keys(auth, AUTH_REFUSED, at + ("auth",), "a definition has neither settings nor access levels")
        for key in ("token_url", "authorize_url"):
            out += _address(auth.get(key), at + ("auth", key))
        identity = auth.get("identity")
        if isinstance(identity, dict):
            out += _address(identity.get("url"), at + ("auth", "identity", "url"))
    out += _address(connector.get("base_url"), at + ("base_url",))
    modes = connector.get("modes")
    if isinstance(modes, list):
        out += [
            Problem(
                p.KEY_NOT_ALLOWED,
                dotted(at + ("modes", i)),
                f"the mode {mode!r} is not admitted: a definition is held by a person or an org ({', '.join(MODES)})",
            )
            for i, mode in enumerate(modes)
            if mode not in MODES
        ]
    credential = connector.get("credential")
    if isinstance(credential, list):
        out += [
            Problem(
                p.KEY_NOT_ALLOWED,
                dotted(at + ("credential", i, "holder")),
                "a field held by the host is not admitted: every field of a definition comes with the account",
            )
            for i, field in enumerate(credential)
            if isinstance(field, dict) and field.get("holder", "account") != "account"
        ]
    out += _refused_keys(connector.get("probe"), PROBE_REFUSED, at + ("probe",), "a Custom API has no paid balance")
    out += _headers(connector.get("headers"), at + ("headers",), _auth_header_name(auth))
    return out


def _function_problems(function: object, i: int, auth_header: str | None) -> list[Problem]:
    if not isinstance(function, dict):
        return []
    at = ("functions", i)
    out = _foreign_keys(function, FUNCTION_KEYS, at, "it touches the resources of the host or goes beyond simple REST")
    call = function.get("call")
    out += _refused_keys(call, CALL_REFUSED, at + ("call",), "a Custom API sends JSON, never a file")
    out += _refused_keys(
        function.get("output"), OUTPUT_REFUSED, at + ("output",), "a Custom API renders JSON, never a file"
    )
    if isinstance(call, dict):
        out += _headers(call.get("headers"), at + ("call", "headers"), auth_header)
        out += _headers(
            (call.get("constants") or {}).get("headers") if isinstance(call.get("constants"), dict) else None,
            at + ("call", "constants", "headers"),
            auth_header,
        )
    examples = function.get("examples")
    if not isinstance(examples, list) or not examples:
        out.append(
            Problem(
                p.EXAMPLE_MISSING,
                dotted(at + ("examples",)),
                "each action has at least one example, which its input accepts",
            )
        )
    return out


def _size_problems(document: dict) -> list[Problem]:
    out = []
    functions = document.get("functions")
    if isinstance(functions, list) and len(functions) > MAX_FUNCTIONS:
        out.append(Problem(p.TOO_LARGE, "functions", f"{len(functions)} functions, {MAX_FUNCTIONS} at most"))
    try:
        size = len(json.dumps(document, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return out + [Problem(p.SCHEMA_INVALID, "", "the definition is not a JSON document")]
    if size > MAX_BYTES:
        out.append(Problem(p.TOO_LARGE, "", f"the definition weighs {size} bytes, {MAX_BYTES} at most"))
    return out


def _asks_refused_keys(error) -> bool:
    """A `required` error that asks only for keys the profile refuses at that place (`scopes` of a function, once
    `access_levels` is there): the refusal of their cause already names the problem."""
    if error.validator != "required" or not isinstance(error.instance, dict):
        return False
    at = tuple(error.absolute_path)
    admitted = {(): TOP_KEYS, ("connector",): CONNECTOR_KEYS}.get(at)
    if admitted is None and len(at) == 2 and at[0] == "functions":
        admitted = FUNCTION_KEYS
    missing = [name for name in error.validator_value if name not in error.instance]
    return admitted is not None and bool(missing) and all(name not in admitted for name in missing)


def _error_paths(error) -> list[tuple]:
    """What a schema error designates: the missing or unexpected keys by their own path, the value's path otherwise."""
    at = tuple(error.absolute_path)
    if error.validator == "required" and isinstance(error.instance, dict):
        return [at + (name,) for name in error.validator_value if name not in error.instance] or [at]
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        declared = error.schema.get("properties", {})
        return [at + (key,) for key in error.instance if key not in declared] or [at]
    return [at]


def _covered(path: tuple, named: list[str], branches: bool) -> bool:
    """A path the profile already named: the path itself or one under it; for an error of `oneOf`/`anyOf`
    (`branches`), also a path that the profile named inside the value (`connector.auth.kind` for `connector.auth`)."""
    text = dotted(path)
    for other in named:
        if other == "" or text == other or text.startswith(other + "."):
            return True
        if branches and text and other.startswith(text + "."):
            return True
    return False


def _schema_problems(document: dict, named: list[str]) -> tuple[list[Problem], bool]:
    """The schema errors the profile did not already name, and whether the document matches the schema at all."""
    errors = sorted(Draft202012Validator(schema()).iter_errors(document), key=lambda e: list(map(str, e.absolute_path)))
    out = []
    for error in errors:
        branches = error.validator in ("oneOf", "anyOf")
        if _asks_refused_keys(error) or all(_covered(path, named, branches) for path in _error_paths(error)):
            continue
        out.append(Problem(p.SCHEMA_INVALID, dotted(error.absolute_path), error.message))
    return out, not errors


def _example_problems(document: dict) -> list[Problem]:
    out = []
    for i, function in enumerate(document["functions"]):
        validator = Draft202012Validator(function["input"])
        for j, example in enumerate(function["examples"]):
            error = next(iter(validator.iter_errors(example["input"])), None)
            if error is not None:
                out.append(
                    Problem(
                        p.EXAMPLE_INVALID,
                        dotted(("functions", i, "examples", j)),
                        f"the example {example['title']!r} does not match the input: {error.message}",
                    )
                )
    return out


def _rule(at: str, judge: Callable[[], None]) -> list[Problem]:
    try:
        judge()
    except DescriptionError as broken:
        message = str(broken).removeprefix(f"{WHERE}: ")
        rule = next((code for code in CODED_RULES if f": {code}: " in message), None)
        return [Problem(p.RULE_BROKEN, at, message, rule=rule)]
    return []


def _rule_problems(document: dict) -> list[Problem]:
    connector = document["connector"]
    out = _rule("connector.auth", lambda: check_auth(connector, WHERE))
    for i, function in enumerate(document["functions"]):
        at = dotted(("functions", i))
        out += _rule(at, lambda: check_arguments(function, WHERE))
        out += _rule(at, lambda: check_constants(connector, function, WHERE))
        out += _rule(at, lambda: check_checks(function, WHERE))
        out += _rule(at, lambda: check_act(function, WHERE))
        out += _rule(at, lambda: check_timeout(connector, function, WHERE))
        out += _rule(at, lambda: check_personal(function, WHERE))
        strip = (function.get("output") or {}).get("strip")
        refused = strip_refusal(strip) if strip else None
        if refused is not None:
            code, path, reason = refused
            out.append(Problem(p.RULE_BROKEN, f"{at}.output.strip", f"{path!r}: {code}: {reason}", rule=code))
    names = [function["name"] for function in document["functions"]]
    out += [
        Problem(p.RULE_BROKEN, "functions", f"function name {name!r} declared twice")
        for name in sorted({n for n in names if names.count(n) > 1})
    ]
    out += _rule("connector", lambda: check_probe(document, WHERE))
    out += _rule("connector.errors", lambda: check_signals(document, WHERE))
    return out


def validate(document: object) -> list[Problem]:
    """Every problem of a Custom API definition, in the order of the judges; `[]` when it can be saved."""
    if not isinstance(document, dict):
        return [Problem(p.SCHEMA_INVALID, "", "a definition is an object with connector and functions")]
    out = _size_problems(document)
    out += _foreign_keys(document, TOP_KEYS, (), "a definition is a connector and its functions, nothing else")
    out += _connector_problems(document.get("connector"))
    auth_header = _auth_header_name(
        (document.get("connector") or {}).get("auth") if isinstance(document.get("connector"), dict) else None
    )
    functions = document.get("functions")
    if isinstance(functions, list):
        for i, function in enumerate(functions):
            out += _function_problems(function, i, auth_header)
    if any(problem.code == p.TOO_LARGE for problem in out):
        return p.unique(out)
    schema_problems, matches = _schema_problems(document, [problem.path for problem in out])
    out += schema_problems
    if not matches:
        return p.unique(out)
    out += _example_problems(document)
    if not out:
        out += _rule_problems(document)
    return p.unique(out)
