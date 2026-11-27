"""La description `aircall` 1.1.0 : aucune adresse d'enregistrement ne sort vers l'agent.

Les lectures d'un appel (`list_calls`, `search_calls`, `get_call`) retirent par `output.strip` les adresses signées de
l'enregistrement et de la messagerie vocale, leurs adresses courtes et la page `asset` ; l'audio se rend en fichier par
`get_call_recording` et `get_call_voicemail` (`output.file`, adresse signée téléchargée sans authentification).
Décidé par le mainteneur le 2026-10-21 (docs/conception/sortie.md, docs/conception/fichiers.md).
"""
from __future__ import annotations

import copy

import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module, function_source
from oto.connectors.declared import DECLARED
from test_strip_cases import shaped

AIRCALL = CONNECTORS / "aircall" / "connector.yaml"
ADDRESS_FIELDS = ["recording", "recording_short_url", "voicemail", "voicemail_short_url", "asset"]
CALL_READS = {"list_calls": "calls", "search_calls": "calls", "get_call": "call"}
RENDERED = {"get_call_recording": "recording", "get_call_voicemail": "voicemail"}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(AIRCALL.read_text(encoding="utf-8")))


def _fn(description: dict, name: str) -> dict:
    return next(f for f in description["functions"] if f["name"] == name)


def _addresses(value) -> list[str]:
    if isinstance(value, dict):
        return [a for v in value.values() for a in _addresses(v)]
    if isinstance(value, list):
        return [a for v in value for a in _addresses(v)]
    return [value] if isinstance(value, str) and "://" in value else []


def test_the_description_is_version_1_1_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.1.1"
    check(description, AIRCALL)


def test_no_function_takes_short_urls_any_more():
    for function in _description()["functions"]:
        assert "fetch_short_urls" not in function["input"].get("properties", {}), function["name"]
        assert "fetch_short_urls" not in (function.get("call") or {}).get("query", {}), function["name"]


def test_each_call_read_strips_every_address_field_and_keeps_the_whole_answer():
    description = _description()
    for name, wrapper in CALL_READS.items():
        output = _fn(description, name)["output"]
        assert "items" not in output, f"{name}: the whole answer is kept (meta included)"
        assert output["strip"] == [f"{wrapper}.{field}" for field in ADDRESS_FIELDS], name


def test_no_address_reaches_the_agent_from_the_sample_and_meta_stays():
    description = _description()
    for name in CALL_READS:
        output = _fn(description, name)["output"]
        sample = output["sample"]
        assert _addresses(sample), f"{name}: the sample carries the addresses it proves stripped"
        served = shaped(sample, output)
        assert _addresses(served) == [], name
    listed = _fn(description, "list_calls")["output"]
    assert "meta" in shaped(listed["sample"], listed)


def test_the_two_audio_functions_render_a_file_from_the_signed_address():
    description = _description()
    for name, field in RENDERED.items():
        function = _fn(description, name)
        assert function["class"] == "read"
        assert function["input"]["required"] == ["call_id"]
        assert function["call"] == {"method": "GET", "path": "/v1/calls/{call_id}"}
        assert function["timeout_s"] == 120
        assert {r["code"]: r["when"] for r in function["refusals"]} == {"call_not_found": 404}
        file = function["output"]["file"]
        assert file["from"] == "url" and file["path"] == f"call.{field}" and file["auth"] == "none"
        assert file["types"] == {"mp3": ["audio/mpeg", "audio/mp3"]}
        assert file["name"] == {"template": f"call-{{call_id}}-{field}.mp3"}
        assert file["max_bytes"] == 52_428_800


def test_the_typescript_output_carries_the_paths_and_the_files():
    checked = check(_description(), AIRCALL)
    module = connector_module(checked)
    assert "example.test" not in module.source and "sample" not in module.source
    functions = {f["name"]: f for f in checked.data["functions"]}
    for name, wrapper in CALL_READS.items():
        strip = ", ".join(f'"{wrapper}.{field}"' for field in ADDRESS_FIELDS)
        assert f"output: {{ strip: [{strip}] }}" in function_source("aircall", functions[name])
    for name, field in RENDERED.items():
        source = function_source("aircall", functions[name])
        assert (f'  output: {{ file: {{ from: "url", path: "call.{field}", auth: "none", '
                f'types: {{"mp3": ["audio/mpeg", "audio/mp3"]}}, name: {{ template: "call-{{call_id}}-{field}.mp3" }}, '
                f'maxBytes: 52428800 }} }},\n') in source
        assert "timeoutMs: 120000" in source


def test_the_python_table_declares_the_two_files_with_their_timeout():
    table = python.declared([check(_description(), AIRCALL)])["aircall"]
    for name, field in RENDERED.items():
        expected = {
            "cost": {"per": "request"},
            "file": {"from": "url", "path": f"call.{field}", "auth": "none", "types": {"mp3": ["audio/mpeg", "audio/mp3"]},
                     "name": {"template": f"call-{{call_id}}-{field}.mp3"}, "max_bytes": 52_428_800},
            "timeout_s": 120,
        }
        assert table[name] == expected
        assert DECLARED["aircall"][name] == expected, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
