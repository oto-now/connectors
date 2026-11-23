"""FOD : chaque route publique du service est décrite dans `connectors/fod/connector.yaml`, ou exclue avec son motif.

Le relevé `tests/fixtures/fod/routes-publiques.txt` vient de l'OpenAPI de FOD (`python -m fabrique.releve_fod`, qui
dit le critère « publique »). Une route s'y compare à l'appel d'une fonction par sa méthode et son gabarit de chemin,
paramètres réduits à `{}`. Une route que FOD ajoute sans description fait échouer ce test une fois le relevé
régénéré ; une route décrite que FOD ne sert plus aussi ; une exclusion qui ne nomme plus une route du relevé aussi.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from fabrique.releve_fod import RELEVE, ReleveError, gabarit, routes

DESCRIPTION = Path(__file__).parent.parent / "connectors" / "fod" / "connector.yaml"

#: Routes publiques que la description laisse dehors, et pourquoi. Un motif dit ce qui empêche de la décrire ou
#: pourquoi on ne le veut pas, jamais « à faire » : une route seulement pas encore décrite n'a pas sa place ici.
EXCLUSIONS = {
    "GET /api/fr/insee/siret/{}":
        "FOD holds no INSEE key: the caller passes its own in X-Sirene-Key, and the format has no per-function "
        "credential; the SIRENE stock functions read establishments without a key.",
    "GET /api/fr/insee/headquarters/{}":
        "FOD holds no INSEE key: the caller passes its own in X-Sirene-Key, and the format has no per-function "
        "credential; company_get gives the head office without a key.",
}

#: Ce qu'un motif ne peut pas dire : qu'il n'y a pas de motif.
_EMPTY_REASONS = re.compile(r"^\s*$|\b(todo|tbd|later|à faire|a faire|plus tard|pas encore)\b", re.IGNORECASE)
_LINE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) /\S*$")


def _releve() -> list[str]:
    return RELEVE.read_text(encoding="utf-8").splitlines()


def _routes() -> dict[str, str]:
    """Gabarit (`MÉTHODE /chemin/{}`) -> ligne du relevé."""
    return {_key(*line.split(" ", 1)): line for line in _releve()}


def _described() -> dict[str, list[str]]:
    """Gabarit -> fonctions de la description qui l'appellent."""
    served: dict[str, list[str]] = {}
    for function in yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))["functions"]:
        call = function.get("call")
        if call:
            served.setdefault(_key(call["method"], call["path"]), []).append(function["name"])
    return served


def _key(method: str, path: str) -> str:
    return f"{method.upper()} {gabarit(path)}"


def test_the_list_is_well_formed():
    lines = _releve()
    assert lines, "empty list"
    malformed = [line for line in lines if not _LINE.match(line)]
    assert not malformed, f"lines not of the form `METHOD /path`: {malformed}"
    assert lines == sorted(set(lines)), "lines must be sorted and unique (python -m fabrique.releve_fod)"
    assert len(_routes()) == len(lines), "two lines differ only by the names of their path parameters"


def test_every_public_route_is_described_or_excluded():
    described = _described()
    missing = sorted(line for key, line in _routes().items() if key not in described and key not in EXCLUSIONS)
    assert not missing, (
        f"FOD serves these routes and connectors/fod/connector.yaml does not describe them: {missing}. "
        "Describe each one, or add it to EXCLUSIONS with the reason it is left out.")


def test_every_described_route_is_still_served():
    routes = _routes()
    gone = sorted(f"{key} ({', '.join(names)})" for key, names in _described().items() if key not in routes)
    assert not gone, (
        f"connectors/fod/connector.yaml calls routes FOD no longer publishes: {gone}. Remove or move the functions, "
        "or regenerate the list if it is out of date (python -m fabrique.releve_fod).")


def test_every_exclusion_names_a_listed_route_left_undescribed():
    routes, described = _routes(), _described()
    unknown = sorted(key for key in EXCLUSIONS if key not in routes)
    assert not unknown, f"exclusions that match no route of the list: {unknown}"
    both = sorted(f"{key} ({', '.join(described[key])})" for key in EXCLUSIONS if key in described)
    assert not both, f"excluded routes that the description serves anyway: {both}"


def test_every_exclusion_gives_a_reason():
    unreasoned = sorted(key for key, reason in EXCLUSIONS.items() if _EMPTY_REASONS.search(reason))
    assert not unreasoned, f"exclusions without a reason (a reason says why, never 'to do'): {unreasoned}"



def test_the_list_keeps_only_the_routes_guarded_by_the_consumer_key():
    key = {"name": "authorization", "in": "header", "required": False, "schema": {"type": "string"}}
    openapi = {"paths": {
        "/api/fr/bodacc/{siren}": {"get": {"parameters": [{"name": "siren", "in": "path"}, key]}},
        "/api/fr/aides/search": {"post": {"parameters": [key]}},
        "/api/health": {"get": {}},
        "/api/admin/runs": {"get": {"parameters": [{"name": "limit", "in": "query"}]}},
    }}
    assert routes(openapi) == ["GET /api/fr/bodacc/{siren}", "POST /api/fr/aides/search"]
    with pytest.raises(ReleveError):
        routes({"paths": {"/api/health": {"get": {}}}})
