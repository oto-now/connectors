"""La description `allo` 1.1.0 : aucune adresse d'enregistrement ne sort vers l'agent.

Les lectures d'un appel (`list_conversations` sous `last_item`, `search_conversation_items`, `get_conversation_item`,
`get_conversation_items`) retirent par `output.strip` `recording_url`, `recording_download_url` (signée, 48 heures) et
son échéance ; l'audio se rend en fichier par `get_call_recording` (`output.file`, lien signé de 60 minutes téléchargé
sans authentification), qui remplace `get_recording_link`. Même règle que `aircall` 1.1.0
(docs/conception/sortie.md, docs/conception/fichiers.md).
"""
from __future__ import annotations

import copy

import jsonschema
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module, function_source
from oto.connectors.declared import DECLARED
from test_strip_cases import shaped

ALLO = CONNECTORS / "allo" / "connector.yaml"
ADDRESS_FIELDS = ["recording_url", "recording_download_url", "recording_download_expires_at"]
CALL_READS = {"list_conversations": "data.last_item", "search_conversation_items": "data",
              "get_conversation_item": "data", "get_conversation_items": "data"}
FILE = {"from": "url", "path": "data.url", "auth": "none", "types": {"mp3": ["audio/mpeg", "audio/mp3"]},
        "name": {"template": "call-{item_id}-recording.mp3"}, "max_bytes": 52_428_800}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(ALLO.read_text(encoding="utf-8")))


def _fn(description: dict, name: str) -> dict:
    return next(f for f in description["functions"] if f["name"] == name)


def _addresses(value) -> list[str]:
    if isinstance(value, dict):
        return [a for v in value.values() for a in _addresses(v)]
    if isinstance(value, list):
        return [a for v in value for a in _addresses(v)]
    return [value] if isinstance(value, str) and "://" in value else []


def test_the_description_is_version_1_2_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.2.1"
    check(description, ALLO)


def test_the_signed_link_is_no_function_any_more():
    names = {f["name"] for f in _description()["functions"]}
    assert "get_recording_link" not in names and "get_call_recording" in names


def test_each_call_read_strips_every_address_field_and_keeps_the_whole_answer():
    description = _description()
    for name, prefix in CALL_READS.items():
        output = _fn(description, name)["output"]
        assert "items" not in output, f"{name}: the whole answer is kept (pagination included)"
        assert output["strip"] == [f"{prefix}.{field}" for field in ADDRESS_FIELDS], name
        assert "recording url" not in _fn(description, name)["description"], name


def test_no_address_reaches_the_agent_from_the_sample_and_pagination_stays():
    description = _description()
    for name in CALL_READS:
        output = _fn(description, name)["output"]
        sample = output["sample"]
        assert _addresses(sample), f"{name}: the sample carries the addresses it proves stripped"
        assert _addresses(shaped(sample, output)) == [], name
    for name in ("list_conversations", "search_conversation_items"):
        output = _fn(description, name)["output"]
        assert "pagination" in shaped(output["sample"], output), name


def test_no_audio_address_reaches_the_agent_from_the_voices_1_2_0():
    """1.2.0: `demo_url`, the address of a voice's audio sample, is stripped like a call's recording addresses."""
    output = _fn(_description(), "list_voices")["output"]
    assert output["strip"] == ["data.demo_url"]
    assert _addresses(output["sample"]), "the sample carries the address it proves stripped"
    assert _addresses(shaped(output["sample"], output)) == []
    assert shaped(output["sample"], output)["data"][0]["id"] == "maya"


WEBHOOK_ANSWERS = ("list_webhooks", "get_webhook", "create_webhook", "update_webhook")


def test_no_webhook_answer_gives_the_signing_secret_1_2_0():
    """1.2.0, decided by the maintainer on 2026-10-23: a secret never reaches the agent; a webhook's signing secret is read
    in Allo, and no description promises it."""
    description = _description()
    for name in WEBHOOK_ANSWERS:
        function = _fn(description, name)
        output = function["output"]
        assert output["strip"] == ["data.signing_secret"], name
        assert "signing_secret" in str(output["sample"]), f"{name}: the sample carries the secret it proves stripped"
        assert "signing_secret" not in str(shaped(output["sample"], output)), name
        assert "signing_secret" not in function["description"] and "read in Allo" in function["description"], name


def test_only_the_answers_that_carry_an_audio_address_or_a_secret_strip_1_2_0():
    """Every answer of the team API that carries an audio address (OpenAPI read on 2026-10-22: a call's recording
    addresses, a voice's sample) or a secret (a webhook's signing secret) is stripped, and only those: a strip
    removed or added elsewhere fails here."""
    stripping = {f["name"] for f in _description()["functions"] if "strip" in f.get("output", {})}
    assert stripping == set(CALL_READS) | {"list_voices"} | set(WEBHOOK_ANSWERS)


def test_the_recording_is_a_file_from_the_signed_link():
    function = _fn(_description(), "get_call_recording")
    assert function["class"] == "read"
    assert function["input"]["required"] == ["item_id"]
    assert function["call"] == {"method": "GET", "path": "/v2/api/conversations/items/{item_id}/recording"}
    assert function["timeout_s"] == 120
    assert function["output"] == {"file": FILE}
    assert [(r["code"], r["when"], (r.get("body") or {}).get("equals")) for r in function["refusals"]] == [
        ("recording_not_found", 404, ["RECORDING_NOT_FOUND"]),
        ("call_not_found", 404, None),
        ("recording_access_denied", 403, ["RECORDING_ACCESS_DENIED"]),
        ("recording_download_restricted", 403, ["RECORDING_DOWNLOAD_RESTRICTED"]),
    ]


def test_send_sms_takes_exactly_one_sender_and_costs_a_request():
    function = _fn(_description(), "send_sms")
    schema = function["input"]
    valid = {"to": "+33199000001", "message": "Bonjour"}
    jsonschema.validate(valid | {"sender_id": "MyCompany"}, schema)
    jsonschema.validate(valid | {"from_number": "+33199000000"}, schema)
    for wrong in (valid, valid | {"sender_id": "MyCompany", "from_number": "+33199000000"},
                  valid | {"sender_id": "MyCompany", "message": "x" * 1001}):
        try:
            jsonschema.validate(wrong, schema)
        except jsonschema.ValidationError:
            continue
        raise AssertionError(f"accepted: {sorted(wrong)}")
    assert function["cost"] == {"per": "request"}


def test_no_error_message_names_a_function():
    description = _description()
    names = {f["name"] for f in description["functions"]}
    for error in description["connector"]["errors"]:
        assert not any(name in error["message"] for name in names), error["code"]


def test_the_typescript_output_carries_the_paths_and_the_file():
    checked = check(_description(), ALLO)
    module = connector_module(checked)
    assert "example.test" not in module.source and "sample" not in module.source
    functions = {f["name"]: f for f in checked.data["functions"]}
    for name, prefix in CALL_READS.items():
        strip = ", ".join(f'"{prefix}.{field}"' for field in ADDRESS_FIELDS)
        assert f"output: {{ strip: [{strip}] }}" in function_source("allo", functions[name])
    source = function_source("allo", functions["get_call_recording"])
    assert ('  output: { file: { from: "url", path: "data.url", auth: "none", '
            'types: {"mp3": ["audio/mpeg", "audio/mp3"]}, name: { template: "call-{item_id}-recording.mp3" }, '
            'maxBytes: 52428800 } },\n') in source
    assert "timeoutMs: 120000" in source


def test_the_python_table_declares_the_file_with_its_timeout():
    table = python.declared([check(_description(), ALLO)])["allo"]
    assert table["get_call_recording"] == {"file": FILE, "timeout_s": 120}
    assert table["send_sms"] == {"cost": {"per": "request"}}
    assert DECLARED["allo"] == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"


def test_what_destroys_or_rewires_a_live_line_is_sensitive_1_2_0():
    """1.2.0: every delete, publishing a call flow and switching the receptionist are confirmed before running;
    the removal of queue entries stays a write, like `remove_queue_numbers`."""
    functions = {f["name"]: f for f in _description()["functions"]}
    sensitive = {name for name, f in functions.items() if f["class"] == "sensitive"}
    deletes = {name for name, f in functions.items() if f["call"]["method"] == "DELETE"}
    assert deletes - sensitive == {"remove_item_tag", "remove_queue_numbers", "remove_numbers_from_queue"}
    assert sensitive == (deletes - {"remove_item_tag", "remove_queue_numbers", "remove_numbers_from_queue"}) | {
        "send_sms", "publish_call_flow", "set_receptionist_status"}
    for name in sensitive:
        assert functions[name]["confirm"]["summary"], name
    assert "irreversible" in functions["delete_company_attribute"]["confirm"]["summary"]
    assert "restores" in functions["delete_person_attribute"]["confirm"]["summary"]
