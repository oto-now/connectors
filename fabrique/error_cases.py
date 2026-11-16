"""Les cas communs de la table d'erreurs : `connectors/_cases/errors.yaml`, joués par le paquet de l'hôte.

Le fichier porte un bloc `connector` synthétique (sa table d'erreurs, conditions sur le corps `equals` et `contains`
comprises), une fonction, et des cas : une réponse du tiers (statut, corps JSON ou texte) et l'entrée qu'elle retient
(code et marque du compte), ou aucune. La fabrique assemble le connecteur et la fonction en une description, contrôlée par
le schéma et les règles de toute description (la règle 10 comprise), puis la convertit comme une description :

- en TypeScript, `ts/src/error-cases.ts` : le module de ce connecteur (`connector_module`) et la liste des cas, que les
  tests du paquet de l'hôte jouent contre son moteur. Des données de test : l'index ne le réexporte pas, le paquet `ts/`
  l'expose sous `./error-cases` ;
- en Python, rien : `tests/test_error_cases.py` joue les cas contre le moteur de la lib (`oto.connectors.engine`), un
  faux tiers rendant la réponse de chaque cas.

`python -m fabrique --check` échoue quand le module TypeScript commité ne suit plus le fichier.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

import yaml
from jsonschema import Draft202012Validator

from .descriptions import CONNECTORS, REPO, Description, DescriptionError, check_rules, check_schema
from .typescript import connector_module, js, js_block

CASES = CONNECTORS / "_cases" / "errors.yaml"
OUTPUT_NAME = "error-cases.ts"

_ENTRY = {"type": "object", "additionalProperties": False, "required": ["code", "marks_account"],
          "properties": {"code": {"type": "string", "minLength": 1},
                         "marks_account": {"enum": ["credential_rejected", "quota_exhausted", None]}}}

# La forme d'un fichier de cas ; `connector` et `function` se valident ensuite comme une description.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["connector", "function", "cases"],
    "properties": {
        "connector": {"type": "object"},
        "function": {"type": "object"},
        "cases": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "answer", "entry"],
                "properties": {
                    "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "answer": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["status"],
                        "properties": {"status": {"type": "integer", "minimum": 100, "maximum": 599},
                                       "json": {}, "text": {"type": "string"}},
                        "oneOf": [{"required": ["json"]}, {"required": ["text"]}],
                    },
                    "entry": {"oneOf": [_ENTRY, {"type": "null"}]},
                },
            },
        },
    },
}


@dataclass(frozen=True)
class ErrorCases:
    description: Description
    cases: list[dict]

    @property
    def connector(self) -> str:
        return self.description.name

    @property
    def errors(self) -> list[dict]:
        return self.description.data["connector"]["errors"]


def _relative(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _check_case(errors: list[dict], case: dict, where: str) -> None:
    """L'entrée attendue est une entrée de la table : son code, avec sa marque."""
    entry = case["entry"]
    if entry is None:
        return
    declared = {(e["code"], e.get("marks_account")) for e in errors}
    if (entry["code"], entry["marks_account"]) not in declared:
        raise DescriptionError(f"{where}: case {case['name']}: entry {entry['code']!r} with mark {entry['marks_account']!r} "
                               "is no entry of the table")


def load(path: pathlib.Path = CASES) -> ErrorCases:
    """Le fichier lu, sa forme contrôlée, son connecteur contrôlé comme une description, chaque attendu dans la table."""
    where = _relative(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    error = next(iter(sorted(Draft202012Validator(SCHEMA).iter_errors(data), key=lambda e: list(e.absolute_path))), None)
    if error is not None:
        at = "/".join(str(p) for p in error.absolute_path) or "(root)"
        raise DescriptionError(f"{where}: does not match the error cases form at {at}: {error.message}")
    names = [case["name"] for case in data["cases"]]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise DescriptionError(f"{where}: case name(s) declared twice: {', '.join(twice)}")
    description = {"connector": data["connector"], "functions": [data["function"]]}
    check_schema(description, where)
    check_rules(description, where)
    for case in data["cases"]:
        _check_case(data["connector"]["errors"], case, where)
    return ErrorCases(description=Description(path=path, data=description), cases=data["cases"])


CASE_TYPE = '''
/**
 * A common case of the error table (connectors/_cases/errors.yaml): an answer of the vendor, its status and its body,
 * JSON (`json`) or not (`text`), and the entry of `connector.errors` it selects (its code and account mark), or `null`
 * when no entry applies (a success under a 2xx; otherwise the engine's own refusal, which the cases do not fix).
 */
export type ErrorCase = {
  readonly name: string
  readonly answer: { readonly status: number; readonly json?: unknown; readonly text?: string }
  readonly entry: { readonly code: string; readonly marksAccount: AccountMark | null } | null
}
'''


def _case_source(case: dict) -> str:
    entry = case["entry"]
    expected = None if entry is None else {"code": entry["code"], "marksAccount": entry["marks_account"]}
    entries = [("name", js(case["name"])), ("answer", js_block(case["answer"], 2)), ("entry", js(expected))]
    return "  {\n" + "\n".join(f"    {key}: {value}," for key, value in entries) + "\n  }"


def typescript_source(error_cases: ErrorCases) -> str:
    """`ts/src/error-cases.ts` : le module du connecteur des cas, puis le type d'un cas et la liste des cas."""
    source = _relative(error_cases.description.path)
    module = connector_module(error_cases.description, source=source)
    if module.skipped:
        raise DescriptionError(f"{source}: function(s) not generated: {', '.join(s.function for s in module.skipped)}")
    head = module.source.replace('import { defineConnector, defineFunction } from "./types"',
                                 'import { defineConnector, defineFunction, type AccountMark } from "./types"', 1)
    if head == module.source:
        raise DescriptionError(f"{source}: the generated module no longer imports from ./types as expected")
    rows = ",\n".join(_case_source(case) for case in error_cases.cases)
    return (head + CASE_TYPE + "\n/** The common cases of the error table, in the order of the file. */\n"
            + "export const errorCases: readonly ErrorCase[] = [\n" + rows + ",\n]\n")
