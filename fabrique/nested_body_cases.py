"""Les cas communs du corps emboîté : `connectors/_cases/nested_body.yaml`, joués par le paquet de l'hôte.

Le fichier a la forme de celui du corps entier (`body_cases`) : un bloc `connector` synthétique, des cas, des entrées
rejetées et des fonctions refusées. Un cas porte sa fonction en forme source (`call.body` en table, emboîtée, à côté d'une
constante du corps), les arguments d'un appel (`input`) et la requête que l'hôte envoie : chemin, query, en-têtes posés
par les arguments et corps JSON. Une fonction refusée est valide contre le schéma des descriptions et refusée par la
règle 22, avec son code, jamais par une autre règle.

La fabrique assemble les fonctions des cas en une description, contrôlée par le schéma et les règles de toute
description, vérifie chaque cas (ses arguments valident l'entrée), chaque entrée rejetée et chaque fonction refusée,
puis convertit la description :

- en TypeScript, `ts/src/nested-body-cases.ts` : le module de ce connecteur, les trois refus de la règle 22, les
  fonctions refusées, la liste des cas, celle des entrées rejetées et celle des fonctions refusées, que les tests du
  paquet de l'hôte jouent contre son moteur et `registerConnectors`. Des données de test : l'index ne le réexporte pas,
  le paquet `ts/` l'expose sous `./nested-body-cases` ;
- en Python, rien : `tests/test_nested_body_cases.py` joue les cas contre le moteur de la lib (`oto.connectors.engine`),
  qui envoie chaque requête à un faux tiers.

`python -m fabrique --check` échoue quand le module TypeScript commité ne suit plus le fichier.
"""
from __future__ import annotations

import copy
import pathlib
from dataclasses import dataclass

import yaml
from jsonschema import Draft202012Validator

from .body_cases import SCHEMA as BODY_SCHEMA
from .body_cases import _case_source, _refused_identifier, _refused_source, _rejected_source, _relative
from .cases import case_functions
from .descriptions import (CONNECTORS, NESTED_BODY_REFUSALS, Description, DescriptionError, body_table_refusal,
                           check_rules, check_schema)
from .typescript import NotGenerated, connector_module, function_definition, js

CASES = CONNECTORS / "_cases" / "nested_body.yaml"
OUTPUT_NAME = "nested-body-cases.ts"
REFUSALS = NESTED_BODY_REFUSALS

# La forme du fichier du corps entier, aux refus de la règle 22 près.
SCHEMA = copy.deepcopy(BODY_SCHEMA)
SCHEMA["properties"]["refused"]["items"]["properties"]["refusal"] = {"enum": list(REFUSALS)}


@dataclass(frozen=True)
class NestedBodyCases:
    description: Description
    cases: list[dict]
    rejected: list[dict]
    refused: list[dict]

    @property
    def connector(self) -> str:
        return self.description.name


def alone(loaded: NestedBodyCases, function: dict) -> dict:
    """Une description qui ne porte que `function`, sous le connecteur des cas."""
    return {"connector": copy.deepcopy(loaded.description.data["connector"]), "functions": [copy.deepcopy(function)]}


def _check_case(case: dict, where: str) -> None:
    """Un cas : sa fonction a un corps en table, ses arguments valident l'entrée."""
    at = f"{where}: case {case['name']}"
    function = case["function"]
    if not isinstance(function["call"].get("body"), dict):
        raise DescriptionError(f"{at}: a case function has a body map (call.body)")
    error = next(iter(Draft202012Validator(function["input"]).iter_errors(case["input"])), None)
    if error is not None:
        raise DescriptionError(f"{at}: input does not match the function input: {error.message}")


def _check_rejected(functions: dict[str, dict], rejected: dict, where: str) -> None:
    at = f"{where}: rejected input {rejected['name']}"
    function = functions.get(rejected["function"])
    if function is None:
        raise DescriptionError(f"{at}: names {rejected['function']!r}, which is not a case function")
    if Draft202012Validator(function["input"]).is_valid(rejected["input"]):
        raise DescriptionError(f"{at} is accepted by the input of {rejected['function']!r}")


def _check_refused(loaded: NestedBodyCases, refused: dict, where: str) -> None:
    """Une fonction refusée l'est par la règle 22, avec son code ; jamais par le schéma ni par une autre règle."""
    at = f"{where}: refused function {refused['name']}"
    description = alone(loaded, refused["function"])
    try:
        check_schema(description, where)
    except DescriptionError as error:
        raise DescriptionError(f"{at} must match the description schema: {error}") from None
    expected = body_table_refusal(refused["function"])
    try:
        check_rules(description, where)
    except DescriptionError as error:
        if expected is None:
            raise DescriptionError(f"{at} is refused by another rule than 22: {error}") from None
    else:
        raise DescriptionError(f"{at} is accepted by the fabrique")
    if expected[0] != refused["refusal"]:
        raise DescriptionError(f"{at}: the fabrique answers {expected[0]}, the file says {refused['refusal']}")


def load(path: pathlib.Path = CASES) -> NestedBodyCases:
    """Le fichier lu, sa forme contrôlée, ses fonctions contrôlées comme une description, ses cas, ses entrées rejetées
    et ses fonctions refusées vérifiés."""
    where = _relative(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    error = next(iter(sorted(Draft202012Validator(SCHEMA).iter_errors(data), key=lambda e: list(e.absolute_path))), None)
    if error is not None:
        at = "/".join(str(p) for p in error.absolute_path) or "(root)"
        raise DescriptionError(f"{where}: does not match the nested body cases form at {at}: {error.message}")
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
    loaded = NestedBodyCases(description=Description(path=path, data=description), cases=data["cases"],
                             rejected=data["rejected"], refused=data["refused"])
    for refused in data["refused"]:
        _check_refused(loaded, refused, where)
    missing = sorted(set(REFUSALS) - {refused["refusal"] for refused in data["refused"]})
    if missing:
        raise DescriptionError(f"{where}: no refused function for {', '.join(missing)}")
    return loaded


CASE_TYPE = '''
/** The refusals of a nested body (rule 22 of the fabrique), the same on both sides, in the order they are judged. */
export const nestedBodyRefusals = {refusals} as const

export type NestedBodyRefusal = (typeof nestedBodyRefusals)[number]

/**
 * A common case of a nested body (connectors/_cases/nested_body.yaml): the arguments of a call of a function of
 * `connector`, and the request the host sends: its path, its query pairs, the headers the arguments set (names in lower
 * case) and its JSON body, the body constant with each given argument at its key path.
 */
export type NestedBodyCase = {{
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
export type NestedBodyRejectedInput = {{
  readonly name: string
  readonly function: (typeof connector.functions)[number]
  readonly input: {{ readonly [argument: string]: unknown }}
}}

/** A function of the case connector that rule 22 refuses, and the refusal. Never in `connector.functions`. */
export type NestedBodyRefusedFunction = {{
  readonly name: string
  readonly function: ConnectorFunction
  readonly refusal: NestedBodyRefusal
}}
'''


def typescript_source(loaded: NestedBodyCases) -> str:
    """`ts/src/nested-body-cases.ts` : le module du connecteur des cas, les refus, les types, les fonctions refusées,
    puis les cas, les entrées rejetées et les fonctions refusées en listes."""
    source = _relative(loaded.description.path)
    module = connector_module(loaded.description, source=source)
    if module.skipped:
        raise DescriptionError(f"{source}: function(s) not generated: {', '.join(s.function for s in module.skipped)}")
    head = module.source.replace('import { defineConnector, defineFunction } from "./types"',
                                 'import { defineConnector, defineFunction, type ConnectorFunction } from "./types"', 1)
    if head == module.source:
        raise DescriptionError(f"{source}: the generated module no longer imports from ./types as expected")
    refused_definitions = []
    for refused in loaded.refused:
        try:
            definition = function_definition(loaded.connector, refused["function"])
        except NotGenerated as error:
            raise DescriptionError(f"{source}: refused function {refused['name']}: not generated: {error}") from None
        refused_definitions.append(f"export const {_refused_identifier(refused)} = {definition}\n")
    cases = ",\n".join(_case_source(case) for case in loaded.cases)
    rejected = ",\n".join(_rejected_source(entry) for entry in loaded.rejected)
    refused = ",\n".join(_refused_source(entry) for entry in loaded.refused)
    return (head + CASE_TYPE.format(refusals=js(list(REFUSALS)))
            + "\n// The functions rule 22 refuses, each alone under the case connector, never in connector.functions.\n"
            + "\n".join(refused_definitions)
            + "\n/** The common cases of a nested body, in the order of the file. */\n"
            + "export const nestedBodyCases: readonly NestedBodyCase[] = [\n" + cases + ",\n]\n"
            + "\n/** The inputs the schema refuses before anything is sent, in the order of the file. */\n"
            + "export const nestedBodyRejectedInputs: readonly NestedBodyRejectedInput[] = [\n" + rejected + ",\n]\n"
            + "\n/** The functions rule 22 refuses, in the order of the file. */\n"
            + "export const nestedBodyRefusedFunctions: readonly NestedBodyRefusedFunction[] = [\n" + refused + ",\n]\n")
