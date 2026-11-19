"""The schema of the format, as the lib distributes it: `oto/connectors/connector.schema.json`, the copy of
`connectors/connector.schema.json` that the fabrique writes (`python -m fabrique`) and checks (`--check`)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(resources.files("oto.connectors").joinpath("connector.schema.json").read_text(encoding="utf-8"))
