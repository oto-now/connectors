"""Les cas communs de l'authentification : `connectors/_cases/auth.yaml`, joués par le paquet de l'hôte.

Un cas porte un bloc `auth` et un `credential` en forme source, la valeur de chaque champ, et ce que l'authentification
ajoute à la requête : ses en-têtes (noms en minuscules) et ses paramètres de query. La fabrique contrôle chaque cas comme
le bloc `connector` d'une description (le schéma, puis les règles de toute description, la règle 2 comprise), puis écrit
`ts/src/auth-cases.ts` : `auth` et `credential` convertis comme dans un module de connecteur, et la liste des cas, que les
tests du paquet de l'hôte jouent contre son moteur. Des données de test : l'index ne le réexporte pas, le paquet `ts/`
l'expose sous `./auth-cases`. En Python, rien : `tests/test_auth_cases.py` joue les cas contre le moteur de la lib
(`oto.connectors.engine`), qui signe chaque requête qu'il envoie à un faux tiers.

`python -m fabrique --check` échoue quand le module TypeScript commité ne suit plus le fichier.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

import yaml
from jsonschema import Draft202012Validator

from .descriptions import CONNECTORS, REPO, DescriptionError, check_rules, check_schema
from .typescript import HEADER, _auth, _credential_field, js, js_block

CASES = CONNECTORS / "_cases" / "auth.yaml"
OUTPUT_NAME = "auth-cases.ts"
CONNECTOR = "auth_cases"
# Les sortes signées du seul credential : un échange de jeton ou un consentement n'est pas un cas.
KINDS = ("api_key", "bearer", "basic")

_STRINGS = {"type": "object", "additionalProperties": {"type": "string"}}
_LOWER_NAMES = {"type": "object", "propertyNames": {"pattern": "^[a-z0-9-]+$"}, "additionalProperties": {"type": "string"}}

# La forme d'un fichier de cas ; `auth` et `credential` se valident ensuite comme le bloc d'une description.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cases"],
    "properties": {
        "cases": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "auth", "credential", "values", "headers", "query"],
                "properties": {
                    "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "auth": {"type": "object", "required": ["kind"], "properties": {"kind": {"enum": list(KINDS)}}},
                    "credential": {"type": "array"},
                    "values": _STRINGS,
                    "headers": _LOWER_NAMES,
                    "query": _STRINGS,
                },
            },
        },
    },
}


@dataclass(frozen=True)
class AuthCases:
    path: pathlib.Path
    cases: list[dict]


def _relative(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def description(case: dict) -> dict:
    """La description minimale qui porte le compte d'un cas : son `auth` et son `credential`, une lecture sans coût."""
    return {
        "connector": {
            "name": CONNECTOR, "label": "Auth cases", "namespace": CONNECTOR, "version": "1.0.0", "api_version": "v1",
            "base_url": "https://api.example.test/v1", "auth": case["auth"], "credential": case["credential"],
            "modes": ["byo_org"],
            "errors": [{"status": 429, "code": "rate_limited", "retryable": True, "message": "Rate limit reached: retry later."}],
        },
        "functions": [{
            "name": "get_record", "class": "read", "description": "Get one record by its id.",
            "input": {"type": "object", "additionalProperties": False, "required": ["id"], "properties": {"id": {"type": "string"}}},
            "examples": [{"title": "One record", "input": {"id": "r-1"}}],
            "call": {"method": "GET", "path": "/records/{id}"},
        }],
    }


def load(path: pathlib.Path = CASES) -> AuthCases:
    """Le fichier lu, sa forme contrôlée, chaque cas contrôlé comme le compte d'une description."""
    where = _relative(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    error = next(iter(sorted(Draft202012Validator(SCHEMA).iter_errors(data), key=lambda e: list(e.absolute_path))), None)
    if error is not None:
        at = "/".join(str(p) for p in error.absolute_path) or "(root)"
        raise DescriptionError(f"{where}: does not match the auth cases form at {at}: {error.message}")
    names = [case["name"] for case in data["cases"]]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise DescriptionError(f"{where}: case name(s) declared twice: {', '.join(twice)}")
    for case in data["cases"]:
        at = f"{where}: case {case['name']}"
        checked = description(case)
        check_schema(checked, at)
        check_rules(checked, at)
        fields = sorted(field["name"] for field in case["credential"])
        if sorted(case["values"]) != fields:
            raise DescriptionError(f"{at}: values give exactly one value to each credential field ({', '.join(fields)})")
    return AuthCases(path=path, cases=data["cases"])


CASE_TYPE = '''
/**
 * A common case of authentication (connectors/_cases/auth.yaml): the auth block and the credential of a connector, the
 * value of each credential field, and what authentication adds to the request: its headers (names in lowercase, values
 * exact) and its query parameters.
 */
export type AuthCase = {
  readonly name: string
  readonly auth: Auth
  readonly credential: readonly CredentialField[]
  readonly values: { readonly [field: string]: string }
  readonly headers: { readonly [header: string]: string }
  readonly query: { readonly [param: string]: string }
}
'''


def _case_source(case: dict) -> str:
    entries = [
        ("name", js(case["name"])),
        ("auth", _auth(CONNECTOR, case["auth"])),
        ("credential", js([_credential_field(field) for field in case["credential"]])),
        ("values", js_block(case["values"], 2)),
        ("headers", js_block(case["headers"], 2)),
        ("query", js_block(case["query"], 2)),
    ]
    return "  {\n" + "\n".join(f"    {key}: {value}," for key, value in entries) + "\n  }"


def typescript_source(auth_cases: AuthCases) -> str:
    """`ts/src/auth-cases.ts` : le type d'un cas et la liste des cas."""
    head = [HEADER.format(source=_relative(auth_cases.path)), 'import type { Auth, CredentialField } from "./types"']
    rows = ",\n".join(_case_source(case) for case in auth_cases.cases)
    return ("\n".join(head) + "\n" + CASE_TYPE + "\n/** The common cases of authentication, in the order of the file. */\n"
            + "export const authCases: readonly AuthCase[] = [\n" + rows + ",\n]\n")
