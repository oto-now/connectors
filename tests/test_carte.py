"""La carte du parc (`docs/carte-du-parc.md`) : à jour des descriptions, et la règle de type, une branche par cas."""
from __future__ import annotations

import pytest

from fabrique import carte
from fabrique.build import generate


def test_committed_carte_is_current():
    assert carte.OUTPUT.read_text(encoding="utf-8") == generate().carte, \
        f"{carte.RELATIVE} is out of date: run `python -m fabrique`"


def test_carte_has_one_row_per_connector():
    result = generate()
    rows = [line for line in result.carte.splitlines() if line.startswith("| `")]
    names = [row.split("`")[1] for row in rows]
    assert names == sorted(m.connector for m in result.modules)


KEY_FIELD = {"name": "api_key", "label": "API key", "secret": True}


@pytest.mark.parametrize("connector, expected", [
    ({"auth": {"kind": "oauth2_user"}}, carte.PERSON_OAUTH),
    ({"auth": {"kind": "oauth2_user"}, "credential": [{**KEY_FIELD, "holder": "host"}]}, carte.PERSON_OAUTH),
    ({"auth": {"kind": "oauth2_client_credentials"}, "credential": [KEY_FIELD]}, carte.CLIENT_APPLICATION),
    ({"auth": {"kind": "oauth2_jwt_bearer"}, "credential": [KEY_FIELD]}, carte.SERVICE_ACCOUNT),
    ({"auth": {"kind": "oauth2_jwt_bearer"}, "credential": [{**KEY_FIELD, "holder": "host"}]}, carte.SERVICE_ACCOUNT),
    ({"auth": {"kind": "bearer"}, "credential": [{**KEY_FIELD, "holder": "host"}]}, carte.HOST_KEY),
    ({"auth": {"kind": "none"}}, carte.NO_KEY),
    ({"auth": {"kind": "api_key"}, "credential": [KEY_FIELD]}, carte.KEY),
    ({"auth": {"kind": "bearer"}, "credential": [{**KEY_FIELD, "holder": "account"}]}, carte.KEY),
    ({"auth": {"kind": "basic"}, "credential": [KEY_FIELD]}, carte.KEY),
])
def test_kind_rule(connector, expected):
    assert carte.kind(connector) == expected
