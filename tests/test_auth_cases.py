"""Les cas communs de l'authentification (`connectors/_cases/auth.yaml`), joués côté Python, une ligne par cas.

Ils se jouent contre le moteur de la lib (`oto.connectors.engine`), qui signe la requête d'une fonction sans argument et
l'envoie à un faux tiers (`engine_fake`), à la sémantique du format (`docs/conception/compte.md`) ; le paquet de l'hôte
joue les mêmes cas, sortis par la fabrique dans `ts/src/auth-cases.ts`, contre son moteur.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

import engine_fake
from fabrique import auth_cases
from fabrique.descriptions import DescriptionError

LOADED = auth_cases.load()
BASE_URL = "https://api.example.test/v1"
#: Ce que pose le transport, hors de l'authentification : `authorization` est l'en-tête d'une authentification.
TRANSPORT = engine_fake.TRANSPORT_HEADERS - {"authorization"}


def described(case: dict) -> dict:
    """Une description d'une fonction sans argument, au bloc `auth` et `credential` du cas."""
    connector = {"name": "auth_cases", "label": "Auth cases", "base_url": BASE_URL, "auth": case["auth"],
                 "credential": case["credential"], "errors": []}
    function = {"name": "get_records", "class": "read", "call": {"method": "GET", "path": "/records"}}
    return {"connector": connector, "functions": [function]}


def signed(case: dict) -> tuple[dict, dict]:
    """Les en-têtes (noms en minuscules) et la query qu'ajoute le moteur à la requête."""
    _, vendor = engine_fake.play(described(case), "get_records", {}, credential=case["values"])
    (request,) = vendor.requests
    parts = engine_fake.sent(request, BASE_URL)
    assert parts["path"] == "/records"
    headers = {name: value for name, value in parts["headers"].items() if name not in TRANSPORT}
    return headers, dict(parts["query"])


@pytest.mark.parametrize("case", LOADED.cases, ids=[case["name"] for case in LOADED.cases])
def test_auth_case(case):
    assert signed(case) == (case["headers"], case["query"])


def test_the_cases_cover_basic_without_username():
    kinds = {(case["auth"]["kind"], "username" in case["auth"]) for case in LOADED.cases}
    assert {("basic", True), ("basic", False), ("bearer", False), ("api_key", False)} <= kinds
    assert {case["auth"].get("in") for case in LOADED.cases if case["auth"]["kind"] == "api_key"} == {"header", "query"}


def _write(tmp_path: pathlib.Path, mutate) -> pathlib.Path:
    data = yaml.safe_load(auth_cases.CASES.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "auth.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _case(data, name):
    return next(case for case in data["cases"] if case["name"] == name)


@pytest.mark.parametrize("mutate, message", [
    (lambda d: _case(d, "basic_without_username")["auth"].update(username="api_id"),
     "auth.username names 'api_id', which is not a credential field"),
    (lambda d: _case(d, "basic_with_username")["auth"].pop("password"), "does not match the description schema"),
    (lambda d: _case(d, "bearer")["values"].update(other="x"), "values give exactly one value to each credential field"),
    (lambda d: _case(d, "bearer")["headers"].update({"Authorization": "x"}), "does not match the auth cases form"),
    (lambda d: _case(d, "bearer").update(auth={"kind": "none"}), "does not match the auth cases form"),
], ids=["username_not_a_field", "basic_without_password", "value_without_field", "header_name_not_lowercase", "kind_not_signed"])
def test_a_case_is_checked_like_a_description(tmp_path, mutate, message):
    with pytest.raises(DescriptionError, match=message):
        auth_cases.load(_write(tmp_path, mutate))
