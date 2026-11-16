"""Les cas communs de `output.strip` : `connectors/_cases/strip.yaml`, joués par le paquet de l'hôte.

Le fichier porte un bloc `connector` synthétique, des cas et des sorties refusées. Un cas porte sa fonction en forme
source (`call`, `output` avec `strip`), la réponse JSON simulée de l'API et la sortie taillée qu'en reçoit l'agent
(`shaped`) ; une fonction qui retire un chemin à points porte son exemple de réponse (`output.sample`, règle 19), que la
sortie TypeScript ne porte pas. Une sortie refusée porte un bloc `output`, le chemin que le refus nomme (`null` pour un
refus de l'exemple lui-même) et un refus de la règle 18 ou de la règle 19. La fabrique assemble les fonctions des cas en une description, contrôlée par le schéma et les règles de toute
description, vérifie que chaque sortie refusée l'est bien, avec ce code et ce chemin, puis convertit la description
comme une description :

- en TypeScript, `ts/src/strip-cases.ts` : le module de ce connecteur (`connector_module`), les trois refus de la
  règle 18, la liste des cas et celle des sorties refusées par la règle 18 (celles de la règle 19 jugent l'exemple, que
  l'hôte ne voit jamais : elles ne sortent pas), que les tests du paquet de l'hôte jouent contre son moteur. Des données de
  test : l'index ne le réexporte pas, le paquet `ts/` l'expose sous `./strip-cases` ;
- en Python, rien : `tests/test_strip_cases.py` joue les cas contre le moteur de la lib (`oto.connectors.engine`), un
  faux tiers rendant la réponse de chaque cas.

`python -m fabrique --check` échoue quand le module TypeScript commité ne suit plus le fichier.
"""
from __future__ import annotations

import copy
import pathlib
from dataclasses import dataclass

import yaml
from jsonschema import Draft202012Validator

from .cases import case_functions
from .descriptions import (CONNECTORS, REPO, SAMPLE_REFUSALS, STRIP_REFUSALS, Description, DescriptionError,
                           check_rules, check_schema, output_refusal)
from .typescript import camel, connector_module, js, js_block

CASES = CONNECTORS / "_cases" / "strip.yaml"
OUTPUT_NAME = "strip-cases.ts"
REFUSALS = STRIP_REFUSALS
# La fonction qui porte une sortie refusée, le temps de la contrôler : celle du premier cas.
CARRIER = "get_webhook"

# La forme d'un fichier de cas ; `connector` et chaque fonction se valident ensuite comme une description.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["connector", "cases", "refused"],
    "properties": {
        "connector": {"type": "object"},
        "cases": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "function", "answer", "shaped"],
                "properties": {
                    "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "function": {"type": "object"},
                    "answer": {},
                    "shaped": {},
                },
            },
        },
        "refused": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "output", "path", "refusal"],
                "properties": {
                    "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "output": {"type": "object", "required": ["strip"],
                               "properties": {"strip": {"type": "array", "items": {"type": "string"}}}},
                    "path": {"type": ["string", "null"]},
                    "refusal": {"enum": list(REFUSALS + SAMPLE_REFUSALS)},
                },
            },
        },
    },
}


@dataclass(frozen=True)
class StripCases:
    description: Description
    cases: list[dict]
    refused: list[dict]

    @property
    def connector(self) -> str:
        return self.description.name


def _relative(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def carrying(strip_cases: StripCases, output: dict) -> dict:
    """La description des cas réduite à la fonction porteuse, dont la sortie devient `output`."""
    carrier = copy.deepcopy(next(f for f in strip_cases.description.data["functions"] if f["name"] == CARRIER))
    carrier["output"] = copy.deepcopy(output)
    return {"connector": copy.deepcopy(strip_cases.description.data["connector"]), "functions": [carrier]}


def _check_case(case: dict, where: str) -> None:
    if not (case["function"].get("output") or {}).get("strip"):
        raise DescriptionError(f"{where}: case {case['name']}: a case function strips (output.strip)")


def _check_refused(loaded: StripCases, refused: dict, where: str) -> None:
    """Une sortie refusée l'est par la règle 18 ou 19, avec son code et son chemin ; jamais par une autre règle."""
    at = f"{where}: refused output {refused['name']}"
    expected = output_refusal(refused["output"])
    try:
        check_rules(carrying(loaded, refused["output"]), where)
    except DescriptionError as error:
        if expected is None:
            raise DescriptionError(f"{at} is refused by another rule than 18 or 19: {error}") from None
    else:
        raise DescriptionError(f"{at} is accepted by the fabrique")
    code, path, _ = expected
    if (code, path) != (refused["refusal"], refused["path"]):
        raise DescriptionError(
            f"{at}: the fabrique answers {code} on {path!r}, the file says {refused['refusal']} on {refused['path']!r}")


def load(path: pathlib.Path = CASES) -> StripCases:
    """Le fichier lu, sa forme contrôlée, ses fonctions contrôlées comme une description, ses refus vérifiés."""
    where = _relative(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    error = next(iter(sorted(Draft202012Validator(SCHEMA).iter_errors(data), key=lambda e: list(e.absolute_path))), None)
    if error is not None:
        at = "/".join(str(p) for p in error.absolute_path) or "(root)"
        raise DescriptionError(f"{where}: does not match the strip cases form at {at}: {error.message}")
    names = [entry["name"] for entry in data["cases"] + data["refused"]]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise DescriptionError(f"{where}: case name(s) declared twice: {', '.join(twice)}")
    description = {"connector": data["connector"], "functions": case_functions(data["cases"], where)}
    if CARRIER not in {f["name"] for f in description["functions"]}:
        raise DescriptionError(f"{where}: no case carries {CARRIER!r}, the function a refused output is checked on")
    check_schema(description, where)
    check_rules(description, where)
    for case in data["cases"]:
        _check_case(case, where)
    loaded = StripCases(description=Description(path=path, data=description), cases=data["cases"],
                        refused=data["refused"])
    for refused in data["refused"]:
        _check_refused(loaded, refused, where)
    return loaded


CASE_TYPE = '''
/** The refusals of output.strip (rule 18 of the fabrique), the same on both sides. */
export const stripRefusals = {refusals} as const

export type StripRefusal = (typeof stripRefusals)[number]

/**
 * A common case of output.strip (connectors/_cases/strip.yaml): the JSON answer of a call of a function of `connector`,
 * and what the agent receives: `strip`, then `projection`, applied to the answer, or, with `items`, to each element of
 * the list at that path (`shaped` is then that list).
 */
export type StripCase = {{
  readonly name: string
  readonly function: (typeof connector.functions)[number]
  readonly answer: unknown
  readonly shaped: unknown
}}

/** An `output` refused on any function (rule 18), the path the refusal names, and the refusal. */
export type StripRefusedOutput = {{
  readonly name: string
  readonly output: Output
  readonly path: string
  readonly refusal: StripRefusal
}}
'''


def _case_source(case: dict) -> str:
    entries = [
        ("name", js(case["name"])),
        ("function", camel(case["function"]["name"])),
        ("answer", js_block(case["answer"], 2)),
        ("shaped", js_block(case["shaped"], 2)),
    ]
    return "  {\n" + "\n".join(f"    {key}: {value}," for key, value in entries) + "\n  }"


def _refused_source(refused: dict) -> str:
    output = "{ " + ", ".join(f"{key}: {js(refused['output'][key])}" for key in ("items", "strip", "projection")
                              if key in refused["output"]) + " }"
    entries = [("name", js(refused["name"])), ("output", output), ("path", js(refused["path"])),
               ("refusal", js(refused["refusal"]))]
    return "  {\n" + "\n".join(f"    {key}: {value}," for key, value in entries) + "\n  }"


def typescript_source(strip_cases: StripCases) -> str:
    """`ts/src/strip-cases.ts` : le module du connecteur des cas, puis les refus, les types, les cas et les refusés."""
    source = _relative(strip_cases.description.path)
    module = connector_module(strip_cases.description, source=source)
    if module.skipped:
        raise DescriptionError(f"{source}: function(s) not generated: {', '.join(s.function for s in module.skipped)}")
    head = module.source.replace('import { defineConnector, defineFunction } from "./types"',
                                 'import { defineConnector, defineFunction, type Output } from "./types"', 1)
    if head == module.source:
        raise DescriptionError(f"{source}: the generated module no longer imports from ./types as expected")
    cases = ",\n".join(_case_source(case) for case in strip_cases.cases)
    refused = ",\n".join(_refused_source(entry) for entry in strip_cases.refused if entry["refusal"] in REFUSALS)
    return (head + CASE_TYPE.format(refusals=js(list(REFUSALS)))
            + "\n/** The common cases of output.strip, in the order of the file. */\n"
            + "export const stripCases: readonly StripCase[] = [\n" + cases + ",\n]\n"
            + "\n/** The outputs the fabrique refuses (rule 18), in the order of the file. */\n"
            + "export const stripRefusedOutputs: readonly StripRefusedOutput[] = [\n" + refused + ",\n]\n")
