"""La description `mistral` 1.0.1 : une méthode du client existant (`MistralClient.transcribe`), sans rien y changer.

Une fonction, `transcribe` : `POST /audio/transcriptions` en multipart, l'audio en partie `file`, et les parties texte
que le client envoie avec `diarize=True`, en constantes (`model`, `timestamp_granularities`, `diarize`) ; `language` et
`context_bias` quand ils sont donnés. Fonction longue (`background`), délai de 300 s, réponse brute de Mistral rendue
en fichier JSON (`from: json`), coût en secondes d'audio réglé à `usage.prompt_audio_seconds`, clé de l'hôte. Depuis
la 1.0.1, le texte servi dit que l'audio est un fichier en transit ou un fichier joint à une page que lit l'appelant.

Deux écarts nommés avec le client, gardés ici :
1. le client normalise le vocabulaire (`context_bias_terms`) ; la description n'en prend que la forme normalisée,
   l'agent découpe ;
2. le client normalise la réponse (`normalize_transcription`) ; la description rend la réponse brute.

Les gardes lisent le client lui-même ; `test_each_mutation_of_the_description_is_caught` éprouve la concordance par
des descriptions mutées, chacune refusée.
"""
from __future__ import annotations

import copy
import inspect
import json
import re

import jsonschema
import pytest
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.mistral import client as mistral_client
from oto.tools.mistral import transcription as mistral_transcription
from oto.tools.mistral.client import MistralClient

MISTRAL = CONNECTORS / "mistral" / "connector.yaml"
FILE_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"

# Les méthodes publiques du client que la description ne porte pas, et pourquoi.
LEFT_OUT = {
    "chat": "model inference: a model and messages chosen per call, not a connector function",
    "complete": "model inference, through chat",
    "complete_json": "model inference, through chat, its answer parsed locally",
    "chat_with_tools": "model inference with tool definitions: the agent loop belongs to the caller",
    "list_models": "an authentication probe of the client; the key is the host's, no account has a key to probe",
}
# Les arguments de `transcribe` que la fonction ne prend pas, et ce qui en tient lieu.
ARGUMENTS_LEFT_OUT = {
    "audio": "the bytes: the part file carries a transit file of the caller",
    "filename": "the name of the transit file",
    "mime": "the type of the transit file",
    "timestamps": "implied by diarize, a constant: timestamp_granularities segment",
    "diarize": "a constant, true",
    "model": "a constant, the client's DEFAULT_TRANSCRIPTION_MODEL",
    "timeout": "the function's timeout_s, the client's read timeout",
}
# Une réponse inventée de Mistral, de la forme que lit `normalize_transcription`.
ANSWER = {
    "model": "voxtral-mini-2602",
    "text": "Bonjour. On regarde le devis.",
    "language": "fr",
    "segments": [
        {"type": "transcription_segment", "text": " Bonjour. ", "start": 0.0, "end": 1.2, "speaker_id": "speaker_0"},
        {"type": "transcription_segment", "text": "On regarde le devis.", "start": 1.4, "end": 3.0,
         "speaker_id": "speaker_1"},
    ],
    "usage": {"prompt_audio_seconds": 3, "prompt_tokens": 10, "total_tokens": 20, "completion_tokens": 10},
}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(MISTRAL.read_text(encoding="utf-8")))


def _transcribe(description: dict) -> dict:
    (function,) = description["functions"]
    return function


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(MistralClient, inspect.isfunction) if not n.startswith("_")}


def _as_text(value) -> str:
    """Une partie texte telle que l'hôte l'écrit : texte tel quel, nombre et booléen écrits, le reste en JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value)


class _Answer:
    status_code = 200
    ok = True
    text = json.dumps(ANSWER)

    def json(self):
        return copy.deepcopy(ANSWER)


def _sent_by_the_client(monkeypatch, arguments: dict) -> dict:
    """La requête que `transcribe` envoie, avec `diarize=True` et sans réseau ; et ce qu'elle rend."""
    sent = []

    def post(url, headers=None, data=None, files=None, timeout=None, **kwargs):
        sent.append({"url": url, "headers": headers, "data": data, "files": files, "timeout": timeout})
        return _Answer()

    monkeypatch.setattr(mistral_client.requests, "post", post)
    given = {a: arguments[a] for a in ("language", "context_bias") if a in arguments}
    returned = MistralClient(api_key="not-a-key").transcribe(b"RIFF....WAVE", "call.wav", diarize=True, **given)
    assert len(sent) == 1
    return {**sent[0], "returned": returned}


def _described(description: dict, arguments: dict) -> dict:
    """La requête que dit la description : adresse, parties texte, parties fichier, délai."""
    connector, function = description["connector"], _transcribe(description)
    call = function["call"]
    texts = [(name, _as_text(value)) for name, value in call.get("constants", {}).get("body", {}).items()]
    texts += [(name, _as_text(arguments[a])) for name, a in call.get("body", {}).items() if a in arguments]
    return {
        "method": call["method"],
        "url": connector["base_url"] + call["path"],
        "texts": sorted(texts),
        "files": sorted(call.get("files", {})),
        "timeout": function.get("timeout_s", connector.get("timeout_s", 30)),
    }


def _arguments(description: dict) -> list[dict]:
    function = _transcribe(description)
    every = {"file": FILE_ID, "language": "fr", "context_bias": "Acme,Durand,toiture,devis"}
    return [every, *[example["input"] for example in function["examples"]]]


def _concordance_problems(monkeypatch, description: dict) -> list[str]:
    """Ce en quoi la description s'écarte du client, hors des deux écarts nommés ; vide : elles concordent."""
    problems = []
    function = _transcribe(description)
    connector = description["connector"]
    if function["name"] != "transcribe" or set(_public_methods()) - {function["name"]} != set(LEFT_OUT):
        problems.append("the function is not the client's method of the same name")
    for arguments in _arguments(description):
        try:
            jsonschema.validate(arguments, function["input"])
        except jsonschema.ValidationError as error:
            problems.append(f"an example is refused by the input: {error.message}")
            continue
        sent = _sent_by_the_client(monkeypatch, arguments)
        described = _described(description, arguments)
        if function["call"]["method"] != "POST" or described["url"] != sent["url"]:
            problems.append(f"endpoint {described['method']} {described['url']} is not {sent['url']}")
        if described["texts"] != sorted(sent["data"]):
            problems.append(f"text parts {described['texts']} are not {sorted(sent['data'])}")
        if described["files"] != sorted(sent["files"]):
            problems.append(f"file parts {described['files']} are not {sorted(sent['files'])}")
        if described["timeout"] != sent["timeout"][1]:
            problems.append(f"timeout {described['timeout']} is not the client's {sent['timeout'][1]}")
    if connector["auth"] != {"kind": "bearer", "token": "api_key"} or connector["base_url"] != MistralClient.BASE_URL:
        problems.append("the authentication or the base address is not the client's")
    if function.get("background") is not True:
        problems.append("the call is not run in the background")
    pattern = function["input"]["properties"]["context_bias"].get("pattern", "")
    bounds = re.fullmatch(r"\^\[\^\\s,\]\{(\d+),\}\(,\[\^\\s,\]\{(\d+),\}\)\{0,(\d+)\}\$", pattern)
    if not bounds or {int(bounds[1]), int(bounds[2])} != {mistral_transcription.MIN_TERM_LENGTH} \
            or int(bounds[3]) + 1 != mistral_transcription.MAX_CONTEXT_BIAS_TERMS:
        problems.append(f"the vocabulary pattern {pattern!r} is not the client's normalized form")
    if function["output"] != {"file": {"from": "json", "name": {"template": "transcription.json"}}}:
        problems.append("the answer is not Mistral's raw answer as a JSON file")
    settle = function["cost"].get("settle", {}).get("path", "")
    if _at(ANSWER, settle) != mistral_transcription.normalize_transcription(ANSWER)["duration_s"]:
        problems.append(f"the cost is not settled at the billed seconds the client reads ({settle!r})")
    return problems


def _at(value, path: str):
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def test_the_description_is_version_1_0_2_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.2"
    check(description, MISTRAL)


def test_the_description_says_what_the_client_sends(monkeypatch):
    assert _concordance_problems(monkeypatch, _description()) == []


def test_what_the_description_leaves_out_is_named_with_its_reason():
    assert _public_methods() - {"transcribe"} == set(LEFT_OUT)
    signature = inspect.signature(MistralClient.transcribe).parameters
    properties = set(_transcribe(_description())["input"]["properties"])
    assert {p for p in signature if p != "self"} - properties == set(ARGUMENTS_LEFT_OUT)
    assert properties - set(signature) == {"file"}, "the transit file stands for audio, filename and mime"


def test_the_constants_are_what_the_client_sends_with_diarize():
    """Le modèle daté du client, et l'horodatage par segment que la diarisation exige (sans lui, un 422)."""
    constants = _transcribe(_description())["call"]["constants"]
    assert constants == {"body": {"model": mistral_transcription.DEFAULT_TRANSCRIPTION_MODEL,
                                  "timestamp_granularities": "segment", "diarize": True}}
    source = inspect.getsource(MistralClient.transcribe)
    assert 'data.append(("timestamp_granularities", "segment"))' in source
    assert 'data.append(("diarize", "true"))' in source
    assert 'data.append(("context_bias", ",".join(termes)))' in source


def test_gap_1_the_vocabulary_is_taken_normalized_and_the_agent_splits(monkeypatch):
    """Une chaîne que le schéma admet, sans doublon, part telle quelle chez le client ; ce que le client normalise
    (espaces, termes courts, doublons, plus de 100 termes), le schéma le refuse."""
    schema = _transcribe(_description())["input"]["properties"]["context_bias"]
    admitted = ["Acme", "Acme,Durand,toiture", "C++,l'équipe,R&D,2026", ",".join(f"term{i:03d}" for i in range(100))]
    for vocabulary in admitted:
        jsonschema.validate(vocabulary, schema)
        assert mistral_transcription.context_bias_terms(vocabulary) == (vocabulary.split(","), [])
        sent = _sent_by_the_client(monkeypatch, {"context_bias": vocabulary})
        assert ("context_bias", vocabulary) in sent["data"]
    normalized_by_the_client = ["Jean Durand", "le,devis", "Acme,", ",Acme", "Acme,,Durand",
                                ",".join(f"term{i:03d}" for i in range(101)), "Acme\nDurand"]
    for vocabulary in normalized_by_the_client:
        assert not jsonschema.Draft202012Validator(schema).is_valid(vocabulary), vocabulary
        terms, dropped = mistral_transcription.context_bias_terms(vocabulary)
        assert ",".join(terms) != vocabulary or dropped, vocabulary
    # Un vocabulaire vide ne part pas chez le client ; l'agent omet l'argument.
    assert not jsonschema.Draft202012Validator(schema).is_valid("")
    assert mistral_transcription.context_bias_terms("") == ([], [])
    # Les doublons à la casse près passent le schéma, et le client en retire : l'agent les évite, la description le dit.
    jsonschema.validate("Acme,acme", schema)
    assert mistral_transcription.context_bias_terms("Acme,acme") == (["Acme"], [])
    description = _transcribe(_description())["description"]
    assert "split a name of several words into its words yourself" in description


def test_gap_2_the_answer_is_mistral_raw_answer_not_the_client_normalized_one(monkeypatch):
    returned = _sent_by_the_client(monkeypatch, {})["returned"]
    assert returned != ANSWER and "duration_s" in returned and "usage" not in returned
    assert _transcribe(_description())["output"]["file"] == {"from": "json", "name": {"template": "transcription.json"}}
    assert "path" not in _transcribe(_description())["output"]["file"], "the whole answer, not a branch"


def test_the_long_call_its_timeout_and_its_limits_are_said():
    function = _transcribe(_description())
    assert function["timeout_s"] == mistral_client.TRANSCRIPTION_TIMEOUT[1] == 300
    assert function["background"] is True
    assert "timeout_s" not in _description()["connector"], "the connector keeps the default; the function its own"
    for limit in ("Up to 3 hours of audio per request", "within 300 seconds", "measured up to 2 h 35 of audio"):
        assert limit in function["description"], limit
    assert "accepts up to 3 h per request" in inspect.getsource(mistral_client)
    assert "up to 3 h of audio per" in mistral_transcription.__doc__.replace("\n", " ")


def test_the_cost_is_seconds_of_audio_worst_case_three_hours_settled_at_the_billed_seconds():
    cost = _transcribe(_description())["cost"]
    assert cost == {"per": "request", "quantity": 3 * 3600, "unit": "seconds",
                    "settle": {"path": "usage.prompt_audio_seconds"}}
    assert _at(ANSWER, cost["settle"]["path"]) == mistral_transcription.normalize_transcription(ANSWER)["duration_s"]


def test_the_key_is_the_host_and_its_refusals_mark_the_account():
    connector = _description()["connector"]
    assert connector["credential"] == [
        {"name": "api_key", "label": "Mistral API key", "secret": True, "holder": "host"}]
    assert connector["modes"] == ["platform"] and "account_identity" not in connector and "probe" not in connector
    marks = {e["status"]: e["marks_account"] for e in connector["errors"] if "marks_account" in e}
    assert marks == {401: "credential_rejected", 402: "quota_exhausted"}
    retried = {e["code"] for e in connector["errors"] if e["retryable"]}
    assert retried == {"rate_limited", "upstream_unavailable"}
    assert not any("body" in e for e in connector["errors"])


def test_the_function_is_a_read_and_only_the_vocabulary_is_personal():
    function = _transcribe(_description())
    assert function["class"] == "read" and "act" not in function and "confirm" not in function
    marked = {n for n, p in function["input"]["properties"].items() if p.get("personal")}
    assert marked == {"context_bias"}, "names of people; the file id designates no one"


MUTATIONS = {
    "another path": lambda d: _transcribe(d)["call"].update(path="/audio/transcription"),
    "another model": lambda d: _transcribe(d)["call"]["constants"]["body"].update(model="voxtral-mini-latest"),
    "no timestamp granularity": lambda d: _transcribe(d)["call"]["constants"]["body"].pop("timestamp_granularities"),
    "diarize false": lambda d: _transcribe(d)["call"]["constants"]["body"].update(diarize=False),
    "diarize as a word": lambda d: _transcribe(d)["call"]["constants"]["body"].update(diarize="True"),
    "language renamed": lambda d: _transcribe(d)["call"].update(body={"lang": "language",
                                                                       "context_bias": "context_bias"}),
    "file part renamed": lambda d: _transcribe(d)["call"].update(files={"audio": "file"}),
    "another timeout": lambda d: _transcribe(d).update(timeout_s=120),
    "not in the background": lambda d: _transcribe(d).pop("background"),
    "terms of two characters": lambda d: _transcribe(d)["input"]["properties"]["context_bias"].update(
        pattern="^[^\\s,]{2,}(,[^\\s,]{2,}){0,99}$"),
    "two hundred terms": lambda d: _transcribe(d)["input"]["properties"]["context_bias"].update(
        pattern="^[^\\s,]{3,}(,[^\\s,]{3,}){0,199}$"),
    "a branch of the answer": lambda d: _transcribe(d)["output"]["file"].update(path="segments"),
    "settled at the tokens": lambda d: _transcribe(d)["cost"]["settle"].update(path="usage.total_tokens"),
    "another host": lambda d: d["connector"].update(base_url="https://api.mistral.example/v1"),
}


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_each_mutation_of_the_description_is_caught(monkeypatch, mutation):
    description = _description()
    MUTATIONS[mutation](description)
    assert _concordance_problems(monkeypatch, description), mutation


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), MISTRAL)
    module = connector_module(checked)
    assert 'name: "mistral.transcribe",' in module.source
    assert "background: true," in module.source and "timeoutMs: 300000," in module.source
    assert '"holder": "host"' in module.source
    table = python.declared([checked])["mistral"]
    assert set(table) == {"transcribe"}
    assert DECLARED.get("mistral") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
