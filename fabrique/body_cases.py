"""Les cas communs du corps entier : `connectors/_cases/body.yaml`, joués par le paquet de l'hôte.

Le fichier porte un bloc `connector` synthétique, des cas, des entrées rejetées et des fonctions refusées. Un cas porte sa
fonction en forme source (`call.body` qui nomme un argument), les arguments d'un appel (`input`) et la requête que l'hôte
envoie : chemin, query, en-têtes posés par les arguments et corps JSON, l'objet donné tel quel. Une entrée rejetée nomme
une fonction des cas et des arguments que son schéma d'entrée refuse : rien ne part. Une fonction refusée est valide
contre le schéma des descriptions et refusée par la règle 20, avec son code, jamais par une autre règle.

La fabrique assemble les fonctions des cas en une description, contrôlée par le schéma et les règles de toute
description, vérifie chaque cas (ses arguments valident l'entrée), chaque entrée rejetée (l'entrée la refuse) et chaque
fonction refusée, puis convertit la description comme une description :

- en TypeScript, `ts/src/body-cases.ts` : le module de ce connecteur (`connector_module`), les huit refus de la règle 20,
  les fonctions refusées (une définition chacune, hors du connecteur), la liste des cas, celle des entrées rejetées et
  celle des fonctions refusées, que les tests du paquet de l'hôte jouent contre son moteur et `registerConnectors`. Des
  données de test : l'index ne le réexporte pas, le paquet `ts/` l'expose sous `./body-cases` ;
- en Python, rien : `tests/test_body_cases.py` joue les cas contre le moteur de la lib (`oto.connectors.engine`), qui
  envoie chaque requête à un faux tiers.

`python -m fabrique --check` échoue quand le module TypeScript commité ne suit plus le fichier.
"""
from __future__ import annotations

import copy
import pathlib
from dataclasses import dataclass

import yaml
from jsonschema import Draft202012Validator

from .cases import case_functions
from .descriptions import (BODY_REFUSALS, CONNECTORS, REPO, Description, DescriptionError, body_argument,
                           body_argument_refusal, check_rules, check_schema)
from .typescript import NotGenerated, camel, connector_module, function_definition, js, js_block

CASES = CONNECTORS / "_cases" / "body.yaml"
OUTPUT_NAME = "body-cases.ts"
REFUSALS = BODY_REFUSALS

_NAME = {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"}

# La forme d'un fichier de cas ; `connector` et chaque fonction se valident ensuite comme une description.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["connector", "cases", "rejected", "refused"],
    "properties": {
        "connector": {"type": "object"},
        "cases": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "function", "input", "request"],
                "properties": {
                    "name": _NAME,
                    "function": {"type": "object"},
                    "input": {"type": "object"},
                    "request": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["path", "query", "headers", "body"],
                        "properties": {
                            "path": {"type": "string"},
                            "query": {"type": "array", "items": {"type": "array", "prefixItems": [{"type": "string"}, {"type": "string"}],
                                                                  "minItems": 2, "maxItems": 2}},
                            "headers": {"type": "object", "additionalProperties": {"type": "string"},
                                        "propertyNames": {"pattern": "^[a-z0-9-]+$"}},
                            "body": {"type": "object"},
                        },
                    },
                },
            },
        },
        "rejected": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "function", "input"],
                "properties": {"name": _NAME, "function": _NAME, "input": {"type": "object"}},
            },
        },
        "refused": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "function", "refusal"],
                "properties": {"name": _NAME, "function": {"type": "object"}, "refusal": {"enum": list(REFUSALS)}},
            },
        },
    },
}


@dataclass(frozen=True)
class BodyCases:
    description: Description
    cases: list[dict]
    rejected: list[dict]
    refused: list[dict]

    @property
    def connector(self) -> str:
        return self.description.name


def _relative(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def alone(body_cases: BodyCases, function: dict) -> dict:
    """Une description qui ne porte que `function`, sous le connecteur des cas."""
    return {"connector": copy.deepcopy(body_cases.description.data["connector"]), "functions": [copy.deepcopy(function)]}


def _check_case(case: dict, where: str) -> None:
    """Un cas : sa fonction forme son corps d'un argument, ses arguments valident l'entrée, le corps attendu est l'objet
    donné."""
    at = f"{where}: case {case['name']}"
    function = case["function"]
    argument = body_argument(function["call"])
    if argument is None:
        raise DescriptionError(f"{at}: a case function names one argument as its whole body (call.body)")
    error = next(iter(Draft202012Validator(function["input"]).iter_errors(case["input"])), None)
    if error is not None:
        raise DescriptionError(f"{at}: input does not match the function input: {error.message}")
    if case["request"]["body"] != case["input"][argument]:
        raise DescriptionError(f"{at}: request.body must be the object given as {argument!r}, exactly")


def _check_rejected(functions: dict[str, dict], rejected: dict, where: str) -> None:
    at = f"{where}: rejected input {rejected['name']}"
    function = functions.get(rejected["function"])
    if function is None:
        raise DescriptionError(f"{at}: names {rejected['function']!r}, which is not a case function")
    if Draft202012Validator(function["input"]).is_valid(rejected["input"]):
        raise DescriptionError(f"{at} is accepted by the input of {rejected['function']!r}")


def _check_refused(loaded: BodyCases, refused: dict, where: str) -> None:
    """Une fonction refusée l'est par la règle 20, avec son code ; jamais par le schéma ni par une autre règle."""
    at = f"{where}: refused function {refused['name']}"
    description = alone(loaded, refused["function"])
    try:
        check_schema(description, where)
    except DescriptionError as error:
        raise DescriptionError(f"{at} must match the description schema: {error}") from None
    expected = body_argument_refusal(refused["function"])
    try:
        check_rules(description, where)
    except DescriptionError as error:
        if expected is None:
            raise DescriptionError(f"{at} is refused by another rule than 20: {error}") from None
    else:
        raise DescriptionError(f"{at} is accepted by the fabrique")
    if expected[0] != refused["refusal"]:
        raise DescriptionError(f"{at}: the fabrique answers {expected[0]}, the file says {refused['refusal']}")


def load(path: pathlib.Path = CASES) -> BodyCases:
    """Le fichier lu, sa forme contrôlée, ses fonctions contrôlées comme une description, ses cas, ses entrées rejetées
    et ses fonctions refusées vérifiés."""
    where = _relative(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    error = next(iter(sorted(Draft202012Validator(SCHEMA).iter_errors(data), key=lambda e: list(e.absolute_path))), None)
    if error is not None:
        at = "/".join(str(p) for p in error.absolute_path) or "(root)"
        raise DescriptionError(f"{where}: does not match the body cases form at {at}: {error.message}")
    names = [entry["name"] for entry in data["cases"] + data["rejected"] + data["refused"]]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise DescriptionError(f"{where}: case name(s) declared twice: {', '.join(twice)}")
    description = {"connector": data["connector"], "functions": case_functions(data["cases"], where)}
    check_schema(description, where)
    check_rules(description, where)
    for case in data["cases"]:
        _check_case(case, where)
    functions = {function["name"]: function for function in description["functions"]}
    for rejected in data["rejected"]:
        _check_rejected(functions, rejected, where)
    loaded = BodyCases(description=Description(path=path, data=description), cases=data["cases"],
                       rejected=data["rejected"], refused=data["refused"])
    for refused in data["refused"]:
        _check_refused(loaded, refused, where)
    missing = sorted(set(REFUSALS) - {refused["refusal"] for refused in data["refused"]})
    if missing:
        raise DescriptionError(f"{where}: no refused function for {', '.join(missing)}")
    return loaded


CASE_TYPE = '''
/** The refusals of a whole body (rule 20 of the fabrique), the same on both sides, in the order they are judged. */
export const bodyRefusals = {refusals} as const

export type BodyRefusal = (typeof bodyRefusals)[number]

/**
 * A common case of a whole body (connectors/_cases/body.yaml): the arguments of a call of a function of `connector`,
 * and the request the host sends: its path, its query pairs, the headers the arguments set (names in lower case) and
 * its JSON body, the object given, exactly.
 */
export type BodyCase = {{
  readonly name: string
  readonly function: (typeof connector.functions)[number]
  readonly input: {{ readonly [argument: string]: unknown }}
  readonly request: {{
    readonly path: string
    readonly query: readonly (readonly [string, string])[]
    readonly headers: {{ readonly [header: string]: string }}
    readonly body: {{ readonly [key: string]: unknown }}
  }}
}}

/** Arguments the input schema of a function of `connector` refuses: nothing is sent. */
export type BodyRejectedInput = {{
  readonly name: string
  readonly function: (typeof connector.functions)[number]
  readonly input: {{ readonly [argument: string]: unknown }}
}}

/** A function of the case connector that rule 20 refuses, and the refusal. Never in `connector.functions`. */
export type BodyRefusedFunction = {{
  readonly name: string
  readonly function: ConnectorFunction
  readonly refusal: BodyRefusal
}}
'''


def _entries(entries: list[tuple[str, str]]) -> str:
    return "  {\n" + "\n".join(f"    {key}: {value}," for key, value in entries) + "\n  }"


def _case_source(case: dict) -> str:
    request = case["request"]
    query = "[" + ", ".join(f"[{js(name)}, {js(value)}]" for name, value in request["query"]) + "]"
    rows = [("path", js(request["path"])), ("query", query), ("headers", js(request["headers"])),
            ("body", js_block(request["body"], 3))]
    return _entries([
        ("name", js(case["name"])),
        ("function", camel(case["function"]["name"])),
        ("input", js_block(case["input"], 2)),
        ("request", "{\n" + "\n".join(f"      {key}: {value}," for key, value in rows) + "\n    }"),
    ])


def _rejected_source(rejected: dict) -> str:
    return _entries([("name", js(rejected["name"])), ("function", camel(rejected["function"])),
                     ("input", js_block(rejected["input"], 2))])


def _refused_identifier(refused: dict) -> str:
    return camel(f"refused_{refused['name']}")


def _refused_source(refused: dict) -> str:
    return _entries([("name", js(refused["name"])), ("function", _refused_identifier(refused)),
                     ("refusal", js(refused["refusal"]))])


def typescript_source(body_cases: BodyCases) -> str:
    """`ts/src/body-cases.ts` : le module du connecteur des cas, les refus, les types, les fonctions refusées, puis les
    cas, les entrées rejetées et les fonctions refusées en listes."""
    source = _relative(body_cases.description.path)
    module = connector_module(body_cases.description, source=source)
    if module.skipped:
        raise DescriptionError(f"{source}: function(s) not generated: {', '.join(s.function for s in module.skipped)}")
    head = module.source.replace('import { defineConnector, defineFunction } from "./types"',
                                 'import { defineConnector, defineFunction, type ConnectorFunction } from "./types"', 1)
    if head == module.source:
        raise DescriptionError(f"{source}: the generated module no longer imports from ./types as expected")
    refused_definitions = []
    for refused in body_cases.refused:
        try:
            definition = function_definition(body_cases.connector, refused["function"])
        except NotGenerated as error:
            raise DescriptionError(f"{source}: refused function {refused['name']}: not generated: {error}") from None
        refused_definitions.append(f"export const {_refused_identifier(refused)} = {definition}\n")
    cases = ",\n".join(_case_source(case) for case in body_cases.cases)
    rejected = ",\n".join(_rejected_source(entry) for entry in body_cases.rejected)
    refused = ",\n".join(_refused_source(entry) for entry in body_cases.refused)
    return (head + CASE_TYPE.format(refusals=js(list(REFUSALS)))
            + "\n// The functions rule 20 refuses, each alone under the case connector, never in connector.functions.\n"
            + "\n".join(refused_definitions)
            + "\n/** The common cases of a whole body, in the order of the file. */\n"
            + "export const bodyCases: readonly BodyCase[] = [\n" + cases + ",\n]\n"
            + "\n/** The inputs the schema refuses before anything is sent, in the order of the file. */\n"
            + "export const bodyRejectedInputs: readonly BodyRejectedInput[] = [\n" + rejected + ",\n]\n"
            + "\n/** The functions rule 20 refuses, in the order of the file. */\n"
            + "export const bodyRefusedFunctions: readonly BodyRefusedFunction[] = [\n" + refused + ",\n]\n")
