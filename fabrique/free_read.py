"""Règle 28 : la lecture libre (`free_read: true`, docs/conception/format-de-description.md, « Lecture libre »).

Une fonction qui porte `free_read: true` est gratuite chez le tiers et sans effet, ni chez lui ni chez nous : un hôte
peut l'exécuter sans demander l'accord à chaque appel. La marque n'est admise que là où la description permet de le
vérifier ; chaque défaut a son code, jugé dans l'ordre de `FREE_READ_REFUSALS` :

- `free_read_handwritten` : une fonction écrite à la main ne dit ni sa méthode ni son corps ;
- `free_read_not_read` : la classe n'est pas `read` ;
- `free_read_acts` : la fonction déclare `act: true` (un `effect` n'est dit que d'une fonction sensible, que le schéma
  et `free_read_not_read` refusent déjà) ;
- `free_read_cost` : elle déclare un coût ;
- `free_read_background` : elle est une fonction longue ;
- `free_read_async` : elle soumet un travail que le tiers tient (une soumission peut consommer un quota du tiers,
  docs/conception/travaux-longs.md) ;
- `free_read_method` : sa méthode n'est pas `GET` ;
- `free_read_body` : elle envoie un corps (table, argument du corps entier, constante du corps, parties fichier, corps
  en liste) ;
- `free_read_paginated_quota` : elle est paginée sur un connecteur qui déclare un `quota` (décompté à chaque appel) ou un
  `rate_limit` (que l'hôte tient par credential à chaque requête, chaque page comprise).
"""
from __future__ import annotations

from oto.connectors.rules.checks import DescriptionError

RULE = 28

FREE_READ_REFUSALS = (
    "free_read_handwritten",
    "free_read_not_read",
    "free_read_acts",
    "free_read_cost",
    "free_read_background",
    "free_read_async",
    "free_read_method",
    "free_read_body",
    "free_read_paginated_quota",
)


def _sends_body(call: dict) -> bool:
    constants = call.get("constants") or {}
    return bool(call.get("body") or constants.get("body") or call.get("files") or call.get("body_list"))


def free_read_refusal(connector: dict, function: dict) -> tuple[str, str] | None:
    """Le premier défaut d'une lecture libre, `(code, raison)`, ou `None` (aussi pour une fonction qui n'en est pas une)."""
    if function.get("free_read") is not True:
        return None
    if "handwritten" in function:
        return ("free_read_handwritten", "a handwritten function does not tell its method nor its body")
    if function["class"] != "read":
        return ("free_read_not_read", f"is said of a read function only, not of class {function['class']}")
    if function.get("act") is True:
        return ("free_read_acts", "a function that commits or destroys (act: true) is not free")
    if "cost" in function:
        return ("free_read_cost", "a function that declares a cost is not free")
    if function.get("background") is True:
        return ("free_read_background", "a background function is not free")
    if "async" in function:
        return ("free_read_async", "a function that submits a job the vendor holds is not free")
    call = function["call"]
    if call["method"] != "GET":
        return ("free_read_method", f"is said of a GET only, not of {call['method']}")
    if _sends_body(call):
        return ("free_read_body", "a function that sends a body is not free")
    if "pagination" in function:
        for key in ("quota", "rate_limit"):
            if key in connector:
                return ("free_read_paginated_quota", f"is paginated on a connector that declares {key}: every page counts")
    return None


def check_free_read(connector: dict, function: dict, where: str) -> None:
    """Règle 28 : `free_read: true` n'est porté que par une lecture gratuite et sans effet, vérifiable dans la description."""
    refusal = free_read_refusal(connector, function)
    if refusal is not None:
        code, reason = refusal
        raise DescriptionError(f"{where}: function {function['name']}: free_read (rule {RULE}): {code}: {reason}")
