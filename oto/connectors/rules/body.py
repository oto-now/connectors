"""Les règles du corps d'une requête (règles 20, 22 et 23 de la fabrique, docs/conception/corps-de-requete.md), hors de
toute description : chaque fonction rend le premier défaut, `(code, …, raison)`, ou `None`.

Elles vivent dans la lib, une seule copie : la fabrique (`fabrique/descriptions.py`) les applique à chaque
`connectors/<nom>/connector.yaml`, et le profil Custom API (`oto.connectors.custom_api`) à une définition d'org.
"""
from __future__ import annotations

import re

PATH_PARAM = re.compile(r"\{([^{}]*)\}")
PLACES = ("query", "body", "headers", "files")
# Les méthodes qui n'envoient pas de corps : l'argument qui forme le corps entier n'y a pas de place (règle 20).
NO_BODY_METHODS = ("GET",)


def body_argument(call: dict) -> str | None:
    """L'argument qui forme le corps entier (`call.body` en texte, règle 20), ou `None` pour un corps en table."""
    body = call.get("body")
    return body if isinstance(body, str) else None


def body_entries(table: dict, at: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    """Les entrées d'un corps en table, à toute profondeur (règle 22) : le chemin des clés côté API et l'argument, dans
    l'ordre du fichier ; une table emboîtée vide n'en donne aucune."""
    entries: list[tuple[tuple[str, ...], str]] = []
    for key, value in table.items():
        if isinstance(value, dict):
            entries += body_entries(value, at + (key,))
        else:
            entries.append((at + (key,), value))
    return entries


def argument_table(call: dict, kind: str) -> dict:
    """La table d'une place de `call` (`query`, `body`, `headers`, `files`) : nom côté API → argument ; vide quand la place
    est absente, ou quand le corps est un argument entier (`body_argument`), qui n'a pas de nom côté API. Celle du corps
    est mise à plat (`body_entries`) : un chemin emboîté s'y nomme par ses clés jointes d'un point, pour les messages."""
    table = call.get(kind)
    if not isinstance(table, dict):
        return {}
    if kind == "body":
        return {".".join(path): argument for path, argument in body_entries(table)}
    return table


# Les refus de la règle 20, les mêmes des deux côtés (cas communs `connectors/_cases/body.yaml`), dans l'ordre où ils
# se jugent.
BODY_REFUSALS = ("body_argument_unknown", "body_argument_not_strict", "body_argument_optional", "body_argument_method",
                 "body_argument_elsewhere", "body_argument_encoded", "body_argument_not_alone", "body_argument_paginated")


# Les clés libres de l'argument du corps entier, à sa racine seulement : au lieu de `additionalProperties: false`,
# chaque clé que `properties` ne déclare pas est marquée donnée personnelle (règle 15 : l'hôte ne journalise pas sa
# valeur), de l'un des deux genres que la description choisit. Les clés déclarées gardent leur schéma.
# - le texte (décidé par le mainteneur le 2026-10-22) : `{type: string, personal: true}` ;
# - la valeur simple (décidé par le mainteneur le 2026-10-22) : un texte, un nombre ou un vrai/faux, pour des champs que le
#   tiers type (les champs personnalisés d'un CRM). `number`, pas `integer` : un champ nombre du tiers prend une
#   décimale, et JSON ne distingue pas `2` de `2.0`. Une date reste un texte ; `null`, un objet, une liste restent
#   refusés. Les trois types dans cet ordre, l'écriture unique que les deux juges comparent telle quelle.
# La forme exacte, rien d'autre : ni `true`, ni un autre type, ni un schéma sans `personal`, ni un mot de plus.
FREE_TEXT_KEYS = {"type": "string", "personal": True}
FREE_SIMPLE_KEYS = {"type": ["string", "number", "boolean"], "personal": True}


def free_keys(additional: object) -> bool:
    """`additionalProperties` qui admet des clés libres, toutes donnée personnelle : exactement `FREE_TEXT_KEYS` ou
    `FREE_SIMPLE_KEYS`, `personal` le booléen `true` (jamais `1`, que `==` confondrait)."""
    return (isinstance(additional, dict) and additional.keys() == FREE_TEXT_KEYS.keys()
            and additional["personal"] is True
            and any(additional["type"] == form["type"] for form in (FREE_TEXT_KEYS, FREE_SIMPLE_KEYS)))


def strict_object(schema: object) -> bool:
    """Un objet strict : `type: object`, `additionalProperties: false` ou des clés libres toutes donnée personnelle
    (`free_keys`), au moins une propriété déclarée, aucune clé admise par motif (`patternProperties` rouvrirait
    l'objet)."""
    if not isinstance(schema, dict):
        return False
    additional = schema.get("additionalProperties")
    return (schema.get("type") == "object" and (additional is False or free_keys(additional))
            and isinstance(schema.get("properties"), dict) and len(schema["properties"]) >= 1
            and "patternProperties" not in schema)


def body_argument_refusal(function: dict) -> tuple[str, str] | None:
    """Le premier défaut d'un corps formé par un argument entier, `(code, raison)`, ou `None` : la règle 20, hors de
    toute description, que les cas communs rejouent."""
    call = function["call"]
    argument = body_argument(call)
    if argument is None:
        return None
    properties = function["input"]["properties"]
    if argument not in properties:
        return ("body_argument_unknown", "is not an input argument")
    if not strict_object(properties[argument]):
        return ("body_argument_not_strict",
                "must be a strict object (type: object, additionalProperties: false or free keys "
                "{type: string, personal: true} or {type: [string, number, boolean], personal: true}, at least one "
                "declared property, no patternProperties): the body is what the agent may send, never a free bag")
    if argument not in function["input"].get("required", []):
        return ("body_argument_optional", "must be required: the whole body is never absent")
    if call["method"] in NO_BODY_METHODS:
        return ("body_argument_method", f"needs a method that sends a body, not {call['method']}")
    elsewhere = PATH_PARAM.findall(call["path"])
    for kind in ("query", "headers", "files"):
        elsewhere += argument_table(call, kind).values()
    if argument in elsewhere:
        return ("body_argument_elsewhere", "is the whole body, so it goes nowhere else (path, query, headers, files)")
    if argument in (call.get("encode") or {}):
        return ("body_argument_encoded", "is sent as the JSON object it is, never encoded")
    if "body" in (call.get("constants") or {}) or "files" in call:
        return ("body_argument_not_alone", "is the whole body: no body constant nor file part joins it")
    if "pagination" in function:
        return ("body_argument_paginated", "a function whose body is one argument is not paginated")
    return None


# Les refus de la règle 22, les mêmes des deux côtés (cas communs `connectors/_cases/nested_body.yaml`), dans l'ordre où
# ils se jugent.
NESTED_BODY_REFUSALS = ("body_nested_empty", "body_key_twice", "body_nested_multipart")


def _empty_table(table: dict, at: tuple[str, ...]) -> tuple[str, ...] | None:
    """Le chemin de la première table emboîtée vide, ou `None`."""
    for key, value in table.items():
        if isinstance(value, dict):
            found = (at + (key,)) if not value else _empty_table(value, at + (key,))
            if found is not None:
                return found
    return None


def _clash(table: dict, constant: object, at: tuple[str, ...]) -> tuple[str, ...] | None:
    """Le premier chemin où la table et la constante du corps se rencontrent autrement que par deux objets : un argument
    là où la constante a une valeur, ou une table emboîtée sous une constante qui n'est pas un objet ; `None` sinon."""
    if not isinstance(constant, dict):
        return None
    for key, value in table.items():
        if key not in constant:
            continue
        if not (isinstance(value, dict) and isinstance(constant[key], dict)):
            return at + (key,)
        found = _clash(value, constant[key], at + (key,))
        if found is not None:
            return found
    return None


def body_table_refusal(function: dict) -> tuple[str, str, str] | None:
    """Le premier défaut d'un corps en table, `(code, chemin, raison)`, ou `None` : la règle 22, hors de toute
    description, que les cas communs rejouent ; un corps formé d'un argument entier relève de la règle 20."""
    call = function["call"]
    table = call.get("body")
    if not isinstance(table, dict):
        return None
    empty = _empty_table(table, ())
    if empty is not None:
        return ("body_nested_empty", ".".join(empty), "is a nested table without any entry: it places nothing")
    clash = _clash(table, (call.get("constants") or {}).get("body"), ())
    if clash is not None:
        return ("body_key_twice", ".".join(clash),
                "is both a body constant and an argument, or one is under the other: they meet only through objects")
    if "files" in call:
        nested = next((path for path, _ in body_entries(table) if len(path) > 1), None)
        if nested is not None:
            return ("body_nested_multipart", ".".join(nested),
                    "is nested in a multipart body, whose parts are texts: a file part rules out a nested table")
    return None


# Les refus de la règle 23, les mêmes des deux côtés (cas communs `connectors/_cases/list_body.yaml`), dans l'ordre où
# ils se jugent.
LIST_BODY_REFUSALS = ("body_list_without_body", "body_list_method", "body_list_multipart")


def body_list_refusal(function: dict) -> tuple[str, str] | None:
    """Le premier défaut d'un corps en liste (`call.body_list`), `(code, raison)`, ou `None` : la règle 23, hors de toute
    description, que les cas communs rejouent."""
    call = function["call"]
    if call.get("body_list") is not True:
        return None
    if not (call.get("body") or "body" in (call.get("constants") or {})):
        return ("body_list_without_body", "sends the body as a list, and the function has no body to put in it")
    if call["method"] in NO_BODY_METHODS:
        return ("body_list_method", f"needs a method that sends a body, not {call['method']}")
    if "files" in call:
        return ("body_list_multipart", "a multipart body (files) is a form, never a JSON list")
    return None

