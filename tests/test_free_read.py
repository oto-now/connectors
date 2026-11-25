"""Règle 28 : la lecture libre (`free_read: true`), refusée partout où la description ne permet pas de la vérifier.

Les variantes partent du fichier Sellsy : `get_estimate` (une lecture `GET` sans pagination) et `list_estimates` (une
lecture `GET` paginée), toutes deux sans leur coût par requête, qu'une lecture libre ne déclare pas.
"""
from __future__ import annotations

import copy
import pathlib
import re

import pytest
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, Description, DescriptionError, check
from fabrique.free_read import FREE_READ_REFUSALS
from fabrique.typescript import function_source

SELLSY = CONNECTORS / "sellsy" / "connector.yaml"


def _sellsy() -> dict:
    return copy.deepcopy(yaml.safe_load(SELLSY.read_text(encoding="utf-8")))


def _fn(d: dict, name: str) -> dict:
    return next(f for f in d["functions"] if f["name"] == name)


def _free(d: dict, name: str = "get_estimate") -> dict:
    """La fonction nommée, marquée lecture libre et sans coût : acceptée telle quelle."""
    function = _fn(d, name)
    function.pop("cost")
    function["free_read"] = True
    return function


def test_a_free_read_passes_and_is_carried_to_both_outputs():
    description = _sellsy()
    _free(description)
    _free(description, "list_estimates")
    checked = check(description, SELLSY)
    source = function_source("sellsy", _fn(checked.data, "get_estimate"))
    assert '  act: false,\n  freeRead: true,\n' in source
    table = python.declared([checked])["sellsy"]
    assert table["get_estimate"] == {"free_read": True}
    assert table["list_estimates"] == {"free_read": True}


def test_a_function_without_the_mark_carries_none():
    description = _sellsy()
    assert "freeRead" not in function_source("sellsy", _fn(description, "get_estimate"))
    assert "free_read" not in python.declared([Description(SELLSY, description)])["sellsy"]["get_estimate"]


def test_no_description_is_marked_yet():
    marked = [f"{p.parent.name}.{f['name']}" for p in sorted(CONNECTORS.glob("*/connector.yaml"))
              for f in yaml.safe_load(p.read_text(encoding="utf-8"))["functions"] if "free_read" in f]
    assert marked == []


def _handwritten(f: dict) -> None:
    del f["call"]
    f["handwritten"] = {"python": {"module": "m", "function": "f"}, "typescript": {"file": "f.ts", "export": "f"}}


def _paginated(d: dict) -> dict:
    return _free(d, "list_estimates")


def _quota(d: dict) -> None:
    _paginated(d)
    d["connector"]["modes"].append("platform")
    d["connector"]["quota"] = {"requests_per_day": 100}


def _rate_limit(d: dict) -> None:
    _paginated(d)
    d["connector"]["rate_limit"] = {"requests": 4, "seconds": 1}


VARIANTS = [
    ("handwritten", lambda d: _handwritten(_free(d)), "free_read_handwritten"),
    ("write", lambda d: _free(d).update({"class": "write"}), "free_read_not_read"),
    ("sensitive", lambda d: _free(d).update({"class": "sensitive", "confirm": {"summary": "Read {id}."}}),
     "free_read_not_read"),
    ("act", lambda d: _free(d).update(act=True), "free_read_acts"),
    ("cost", lambda d: _free(d).update(cost={"per": "request"}), "free_read_cost"),
    ("background", lambda d: _free(d).update(background=True, timeout_s=120), "free_read_background"),
    ("async", lambda d: _free(d).update({"async": {"collect": "get_estimate", "job_id": {"from": "id", "to": "id"}}}),
     "free_read_async"),
    ("post", lambda d: _free(d)["call"].update(method="POST"), "free_read_method"),
    ("delete", lambda d: _free(d)["call"].update(method="DELETE"), "free_read_method"),
    ("body", lambda d: _free(d)["call"].update(body={"field": "fields"}, query={"embed": "embed"}), "free_read_body"),
    ("body_constant", lambda d: _free(d)["call"].update(constants={"body": {"draft": True}}), "free_read_body"),
    ("quota", _quota, "free_read_paginated_quota"),
    ("rate_limit", _rate_limit, "free_read_paginated_quota"),
]


@pytest.mark.parametrize("mutate, code", [(m, c) for _, m, c in VARIANTS], ids=[i for i, _, _ in VARIANTS])
def test_free_read_rule_refuses(mutate, code):
    description = _sellsy()
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(f"free_read (rule 28): {code}:")):
        check(description, SELLSY)


def test_every_code_is_covered():
    assert {c for _, _, c in VARIANTS} == set(FREE_READ_REFUSALS)


def test_quota_or_rate_limit_leave_an_unpaginated_free_read_alone():
    description = _sellsy()
    _free(description)
    description["connector"]["rate_limit"] = {"requests": 4, "seconds": 1}
    check(description, SELLSY)


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_free_read_is_true_or_absent(value):
    description = _sellsy()
    _free(description)["free_read"] = value
    with pytest.raises(DescriptionError, match="does not match the description schema at functions/1/free_read"):
        check(description, SELLSY)
