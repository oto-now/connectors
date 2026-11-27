"""La description `slack` 1.1.0 : celle du client existant (`oto.tools.slack`), sans rien y changer ; depuis 1.1.0,
les deux drapeaux d'aperçu des liens de `post_message` (`unfurl_links`, `unfurl_media`).

Une fonction par méthode du client, sous son nom, avec ses noms d'arguments et les clés que le client envoie à Slack
(query d'un GET, corps JSON d'un POST). Un seul jeton, celui du bot : le format signe chaque appel d'un connecteur par un
seul champ du credential, et le client choisit l'un de ses deux jetons par appel. Écarts nommés, gardés ici :
- le jeton : chaque fonction part sur le jeton du bot, comme le client quand l'espace n'a pas de jeton d'utilisateur ;
  `as_user` n'est pas un argument, et le routage du client vers le jeton d'utilisateur (DM, auteur choisi) est figé
  ci-dessous pour qu'un changement se voie ;
- le texte de `post_message` : le client casse les nombres entre deux deux-points et découpe au-delà de 4 000
  caractères ; la description refuse ces textes (motif, longueur), sous lesquels le client envoie le texte tel quel ;
- les valeurs que le client pose quand l'appel ne les donne pas (`CLIENT_FILLS`) ne sont pas des défauts déclarés.
Les gardes ci-dessous lisent le client lui-même : un endpoint, un argument, une clé ou un code d'erreur qu'il ajoute
sans la description fait échouer le test.
"""
from __future__ import annotations

import copy
import inspect
import re
from unittest import mock

import jsonschema
import pytest
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.slack import client as slack_client
from oto.tools.slack import text as slack_text
from oto.tools.slack.client import SlackClient

SLACK = CONNECTORS / "slack" / "connector.yaml"
BOT = "xoxb-test"
USER = "xoxp-test"

# Les méthodes publiques du client qui ne sont pas une fonction de la description, et pourquoi.
LEFT_OUT = {
    "search_messages": "the user token only: search:read is a user-token scope",
    "fetch_file": "the file is read on files.slack.com with the token, another origin than base_url",
    "download_file": "the same download, written to a local path",
    "create_usergroup": "form-encoded body",
    "update_usergroup": "form-encoded body",
    "set_usergroup_users": "form-encoded body",
    "disable_usergroup": "form-encoded body",
    "enable_usergroup": "form-encoded body",
}
FORM_ENCODED = {"create_usergroup", "update_usergroup", "set_usergroup_users", "disable_usergroup", "enable_usergroup"}
# Les arguments d'une méthode que sa fonction ne prend pas, et pourquoi.
_ONE_TOKEN = "one token, the bot token: the user token is not described"
ARGUMENTS_LEFT_OUT = {name: {"as_user": _ONE_TOKEN} for name in (
    "post_message", "delete_message", "get_user_info", "list_channels", "history", "replies", "channel_info",
    "join_channel", "open_dm", "find_user_by_email", "list_usergroups", "usergroup_users")}
# Ce que le client pose quand l'appel ne le donne pas, et que la description ne déclare pas en défaut.
CLIENT_FILLS = {
    "list_channels": {"types": "public_channel"},
    "history": {"limit": 50},
    "replies": {"limit": 50},
}
# Les drapeaux en query, que le client n'envoie que vrais (le texte « true »).
QUERY_FLAGS = {
    "history": {"inclusive"},
    "replies": {"inclusive"},
    "list_usergroups": {"include_users", "include_disabled", "include_count"},
    "usergroup_users": {"include_disabled"},
}
# Ce que fait chaque fonction : une écriture mal classée est un défaut grave.
CLASSES = {
    "read": {"list_channels", "channel_info", "history", "replies", "get_user_info", "find_user_by_email", "file_info",
             "list_usergroups", "usergroup_users"},
    "write": {"join_channel", "add_reaction", "open_dm"},
    # Publié, réécrit ou supprimé sous le nom de l'application, lu par des personnes.
    "sensitive": {"post_message", "update_message", "delete_message", "post_ephemeral"},
}
# Une écriture qui ne fait que préparer : servie du côté lecture.
NOT_ACT = {"open_dm"}
# Ce qui désigne une personne : le texte d'un message, ses blocs, un identifiant d'utilisateur, un courriel.
PERSONAL = {
    "post_message": {"text", "blocks"}, "update_message": {"text", "blocks"},
    "post_ephemeral": {"user", "text", "blocks"}, "get_user_info": {"user_id"}, "open_dm": {"user"},
    "find_user_by_email": {"email"},
}
# Ce que le client enverrait sur le jeton d'utilisateur, les deux jetons posés : ce que décrit `slack_user` (décidé par
# le mainteneur le 2026-10-22), sauf `post_ephemeral`, laissé au bot (`LEFT_TO_SLACK` de `tests/test_slack_user_description.py`).
ROUTED_TO_THE_USER = {
    # Lecture d'un DM, liste des seules conversations directes, ouverture de son DM : le jeton d'utilisateur.
    "by_reading": {("history", "D0000000001"), ("replies", "D0000000001"), ("list_channels", "im"),
                   ("list_channels", "mpim"), ("list_channels", "im,mpim"), ("open_dm", None)},
    # Écritures (et la fiche d'un fichier) : le jeton de `default_as_user`, l'auteur que choisit l'appelant.
    "by_author": {"post_message", "update_message", "delete_message", "post_ephemeral", "add_reaction", "file_info"},
}

TS = "1767225600.000100"
VALUES = {
    "types": "public_channel,private_channel", "channel": "C0000000001", "limit": 20, "cursor": "dXNlcjpVMEc5V0ZYTlo=",
    "oldest": "1767225600", "latest": "1767312000.000200", "inclusive": True, "thread_ts": TS, "ts": TS,
    "text": "The weekly report is ready.", "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*Ready*"}}],
    "user": "U0000000001", "user_id": "U0000000001", "name": "white_check_mark", "email": "marie@example.test",
    "file_id": "F0000000001", "include_users": True, "include_disabled": True, "include_count": True,
    "usergroup": "S0000000001", "unfurl_links": False, "unfurl_media": False,
}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(SLACK.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(SlackClient, inspect.isfunction) if not n.startswith("_")}


def _parameters(name: str) -> dict[str, inspect.Parameter]:
    return {p: v for p, v in inspect.signature(getattr(SlackClient, name)).parameters.items() if p != "self"}


class _Answer:
    status_code = 200

    def __init__(self, body: dict | None = None):
        self._body = body if body is not None else {
            "ok": True, "ts": TS, "channel": {"id": "D0000000001"}, "channels": [], "messages": [], "usergroups": [],
            "users": [], "file": {"id": "F0000000001"}}

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _requests_of_the_client(name: str, arguments: dict, client: SlackClient | None = None,
                            answers: list | None = None) -> list[dict]:
    """Ce que la méthode du client envoie, sans réseau : méthode, adresse, query, corps JSON, formulaire, en-têtes."""
    sent = []
    answers = list(answers or [])

    def request(method, url, **kwargs):
        sent.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {}),
                     "json": kwargs.get("json"), "data": kwargs.get("data"), "headers": kwargs.get("headers") or {}})
        return answers.pop(0) if answers else _Answer()

    with mock.patch.object(slack_client.requests, "request", request):
        getattr(client or SlackClient(bot_token=BOT), name)(**arguments)
    return sent


def _sent_by_the_client(name: str, arguments: dict) -> dict:
    sent = _requests_of_the_client(name, arguments)
    assert len(sent) == 1, name
    return sent[0]


def _in_query(value) -> str:
    """Une valeur en query comme l'écrit l'hôte : booléen en minuscules, nombre en texte."""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _described(function: dict, arguments: dict) -> tuple:
    """La requête que dit la description : méthode, adresse, query, corps JSON."""
    call = function["call"]
    query = {api: _in_query(arguments[a]) for api, a in call.get("query", {}).items() if a in arguments}
    body = None
    if "body" in call:
        body = {api: arguments[a] for api, a in call["body"].items() if a in arguments}
    return call["method"], SlackClient.BASE_URL + call["path"], query, body


def _client_view(name: str, sent: dict, arguments: dict) -> tuple:
    """La requête du client, moins ce qu'il pose quand l'appel ne le donne pas (`CLIENT_FILLS`, vérifié ici)."""
    params = {k: _in_query(v) for k, v in sent["params"].items()}
    for argument, value in CLIENT_FILLS.get(name, {}).items():
        if argument not in arguments:
            assert params.pop(argument) == _in_query(value), (name, argument)
    assert sent["data"] is None, name
    return sent["method"], sent["url"], params, sent["json"]


def _values(function: dict) -> dict:
    return {argument: VALUES[argument] for argument in function["input"]["properties"]}


def _valid(schema: dict, value) -> bool:
    return jsonschema.Draft202012Validator(schema).is_valid(value)


def test_the_description_is_version_1_1_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.1.1"
    check(description, SLACK)


def test_each_method_of_the_client_is_a_function_or_named_with_its_reason():
    assert _public_methods() - set(_functions()) == set(LEFT_OUT)
    assert not set(LEFT_OUT) & set(_functions())
    assert set(_functions()) <= _public_methods()


def test_the_connector_says_what_the_client_does():
    """Le jeton du bot en jeton porteur ; délai de 60 s ; aucun rythme déclaré (Slack limite par méthode)."""
    connector = _description()["connector"]
    assert connector["base_url"] == SlackClient.BASE_URL
    assert connector["auth"] == {"kind": "bearer", "token": "bot_token"}
    assert connector["credential"] == [{"name": "bot_token", "label": "Bot token (xoxb-)", "secret": True}]
    assert _sent_by_the_client("list_channels", {})["headers"] == {"Authorization": f"Bearer {BOT}"}
    assert slack_client._HTTP_TIMEOUT == (10, 60) and connector["timeout_s"] == 60
    assert "rate_limit" not in connector
    assert "platform" not in connector["modes"], "each workspace installs its own app"
    assert connector["probe"] == {"function": "list_channels"}


def test_each_function_takes_the_arguments_of_its_client_method():
    for name, function in _functions().items():
        properties = set(function["input"]["properties"])
        named = set(_parameters(name))
        assert named - properties == set(ARGUMENTS_LEFT_OUT.get(name, {})), name
        assert properties <= named, name
    assert set(ARGUMENTS_LEFT_OUT) <= set(_functions())
    for name in ARGUMENTS_LEFT_OUT:
        assert set(ARGUMENTS_LEFT_OUT[name]) <= set(_parameters(name)), name


def test_each_argument_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        arguments = _values(function)
        jsonschema.validate(arguments, function["input"])
        sent = _sent_by_the_client(name, arguments)
        assert _client_view(name, sent, arguments) == _described(function, arguments), name


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent = _sent_by_the_client(name, example["input"])
            assert _client_view(name, sent, example["input"]) == _described(function, example["input"]), (
                name, example["title"])


def test_reads_go_in_the_query_of_a_get_writes_in_a_json_body():
    """Le format envoie un corps en JSON : chaque écriture décrite part en JSON chez le client ; les cinq écritures des
    groupes partent en formulaire, d'où leur place hors du fichier."""
    for name, function in _functions().items():
        sent = _sent_by_the_client(name, _values(function))
        if function["call"]["method"] == "GET":
            assert "body" not in function["call"] and sent["json"] is None, name
        else:
            assert "query" not in function["call"] and sent["params"] == {} and sent["json"], name
    forms = {"create_usergroup": {"name": "Support"}, "update_usergroup": {"usergroup": "S0000000001", "name": "Help"},
             "set_usergroup_users": {"usergroup": "S0000000001", "users": ["U0000000001"]},
             "disable_usergroup": {"usergroup": "S0000000001"}, "enable_usergroup": {"usergroup": "S0000000001"}}
    assert set(forms) == FORM_ENCODED <= set(LEFT_OUT)
    for name, arguments in forms.items():
        sent = _sent_by_the_client(name, arguments)
        assert sent["method"] == "POST" and sent["json"] is None and sent["data"], name


def test_what_the_client_fills_is_never_a_declared_default_nor_required():
    functions = _functions()
    for name, filled in CLIENT_FILLS.items():
        for argument, value in filled.items():
            assert _parameters(name)[argument].default == value, (name, argument)
            assert "default" not in functions[name]["input"]["properties"][argument], (name, argument)
            assert argument not in functions[name]["input"].get("required", []), (name, argument)


def test_the_query_flags_go_only_true_as_in_the_client():
    functions = _functions()
    flags = {(n, a) for n, f in functions.items() for a, s in f["input"]["properties"].items() if s.get("const") is True}
    assert flags == {(n, a) for n, arguments in QUERY_FLAGS.items() for a in arguments}
    for name, arguments in QUERY_FLAGS.items():
        required = {a: VALUES[a] for a in functions[name]["input"].get("required", [])}
        for argument in arguments:
            assert not _valid(functions[name]["input"], {**required, argument: False}), (name, argument)
            api = next(k for k, v in functions[name]["call"]["query"].items() if v == argument)
            assert _sent_by_the_client(name, {**required, argument: True})["params"][api] == "true", (name, argument)
            assert api not in _sent_by_the_client(name, {**required, argument: False})["params"], (name, argument)


def test_every_function_goes_on_the_bot_token_and_the_users_routing_is_the_named_gap():
    """Le jeton du bot seul : chaque fonction décrite part sur lui, comme la décrit le fichier. Les deux jetons posés, le
    client en envoie certaines sur le jeton d'utilisateur, par l'appel (lecture d'un DM) ou par l'auteur
    (`default_as_user`) : c'est ce que décrit `slack_user`, figé ici ; ici, le bot n'y va qu'en repli."""
    functions = _functions()
    for name, function in functions.items():
        sent = _sent_by_the_client(name, _values(function))
        assert sent["headers"]["Authorization"] == f"Bearer {BOT}", name

    def token(name: str, arguments: dict, default_as_user: bool) -> str:
        client = SlackClient(bot_token=BOT, user_token=USER, default_as_user=default_as_user)
        sent = _requests_of_the_client(name, arguments, client)
        return sent[0]["headers"]["Authorization"].removeprefix("Bearer ")

    by_reading = set()
    for name, function in functions.items():
        for channel in ("C0000000001", "G0000000001", "D0000000001"):
            arguments = {**_values(function), **({"channel": channel} if "channel" in function["input"]["properties"] else {})}
            if token(name, arguments, False) == USER:
                by_reading.add((name, channel if "channel" in arguments else None))
    for types in ("public_channel", "private_channel", "im", "mpim", "im,mpim", "public_channel,im"):
        if token("list_channels", {"types": types}, False) == USER:
            by_reading.add(("list_channels", types))
    assert by_reading == ROUTED_TO_THE_USER["by_reading"]
    by_author = {name for name, function in functions.items()
                 if token(name, _values(function), True) == USER and token(name, _values(function), False) == BOT}
    assert by_author == ROUTED_TO_THE_USER["by_author"]
    with pytest.raises(ValueError):
        _requests_of_the_client("search_messages", {"query": "release"})


def test_the_text_of_post_message_is_sent_as_given_under_its_two_bounds():
    """Le client casse un nombre entre deux deux-points et découpe au-delà de 4 000 caractères. Le schéma refuse ces
    textes ; tout texte qu'il admet, le client l'envoie tel quel, en une requête."""
    text_schema = _functions()["post_message"]["input"]["properties"]["text"]
    assert text_schema["maxLength"] == slack_text.MAX_TEXT_LEN == 4000
    corpus = [
        "The weekly report is ready.", "20:02 to 20:51: one voice asked", "at 12:30:00", "Kick-off at 9:05",
        ":100: done", "counter :1234: ok", ":smile: and :+1:", "ratio 3:2", "a:1:", "x :0: y", "::12::",
        "10:30 - 11:00", "line one\nat 20:51:\nline three", "no colon here", ":12 and 34:", "trailing 7:",
    ]
    refused = set()
    for text in corpus:
        valid = _valid(text_schema, text)
        if valid:
            assert slack_text.escape_false_emoji_shortcodes(text) == text, text
            sent = _requests_of_the_client("post_message", {"channel": "C0000000001", "text": text})
            assert len(sent) == 1 and sent[0]["json"]["text"] == text, text
        else:
            refused.add(text)
            assert slack_text.escape_false_emoji_shortcodes(text) != text, text
    assert refused == {"20:02 to 20:51: one voice asked", "at 12:30:00", "a:1:", "x :0: y", "::12::",
                       "line one\nat 20:51:\nline three"}
    longest = "word " * 799 + "last."
    assert len(longest) == 4000 and _valid(text_schema, longest)
    assert len(_requests_of_the_client("post_message", {"channel": "C0000000001", "text": longest})) == 1
    assert not _valid(text_schema, longest + "!")
    assert len(_requests_of_the_client("post_message", {"channel": "C0000000001", "text": longest + " more"})) == 2


def test_update_and_ephemeral_send_their_text_as_given_as_the_client_does():
    functions = _functions()
    for name in ("update_message", "post_ephemeral"):
        text = functions[name]["input"]["properties"]["text"]
        assert "pattern" not in text and "maxLength" not in text, name
        arguments = {**_values(functions[name]), "text": "at 20:51: sharp"}
        assert _sent_by_the_client(name, arguments)["json"]["text"] == "at 20:51: sharp", name


def test_each_error_code_of_the_client_has_its_entry_under_a_200():
    """Slack refuse sous un 200, `{ok: false, error}` : chaque code que nomme le client a son entrée, groupée comme le
    client la groupe (son statut) ; les codes d'authentification marquent le compte, les codes de débit se rejouent ;
    tout autre `ok: false` est le dernier refus du 200."""
    errors = _description()["connector"]["errors"]
    under_200 = [e for e in errors if e["status"] == 200]
    assert under_200 == errors[:len(under_200)]
    *coded, last = under_200
    assert last["body"] == {"path": "ok", "equals": [False]} and last["code"] == "invalid_request"
    seen = {}
    for entry in coded:
        assert entry["body"]["path"] == "error" and set(entry["body"]) == {"path", "equals"}, entry["code"]
        # Un incident de Slack (5xx) est une seule famille, quel que soit son statut.
        statuses = {min(slack_client._SLACK_ERROR_STATUS[code], 500) for code in entry["body"]["equals"]}
        assert len(statuses) == 1, entry["code"]
        status = statuses.pop()
        for code in entry["body"]["equals"]:
            assert code not in seen, code
            seen[code] = entry
        assert (entry.get("marks_account") == "credential_rejected") == (status == 401), entry["code"]
        assert entry["retryable"] == (status == 429), entry["code"]
        if status == 500:
            assert entry["code"] == "upstream_unavailable"
    assert set(seen) == set(slack_client._SLACK_ERROR_STATUS)
    assert not [e for e in errors if "marks_account" in e and e["status"] != 200]
    http = {(e["status"] if isinstance(e["status"], int) else tuple(e["status"])): e for e in errors[len(under_200):]}
    assert http[429]["retryable"] is True and http[429]["code"] == "rate_limited"
    assert http[(500, 502, 503, 504)]["retryable"] is False and http[(500, 502, 503, 504)]["code"] == "upstream_unavailable"
    assert set(http) == {429, (500, 502, 503, 504)}


def test_reads_and_writes_are_classified_and_every_act_is_served_on_the_act_side():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        assert (function["call"]["method"] == "GET") == (function["class"] == "read"), name
        assert ("confirm" in function) == (function["class"] == "sensitive"), name
        expected_act = function["class"] != "read" and name not in NOT_ACT
        assert function.get("act", function["class"] != "read") == expected_act, name
    assert {n for n in functions if n.startswith("delete")} <= CLASSES["sensitive"]


def test_only_history_is_paginated_and_each_call_costs_one_request():
    """`conversations.replies` répète le parent du fil en tête de chaque page : la suivre rendrait le parent une fois par
    page, d'où l'absence de pagination ; l'agent repasse le curseur."""
    functions = _functions()
    for name, function in functions.items():
        assert function["cost"] == {"per": "request"}, name
    paginated = {n for n, f in functions.items() if "pagination" in f}
    assert paginated == {"history"}
    assert functions["history"]["pagination"] == {
        "kind": "cursor", "request_param": "cursor", "next": "response_metadata.next_cursor", "more": "has_more",
        "max_pages": 10}
    assert functions["history"]["output"] == {"items": "messages"}
    assert "cursor" in functions["replies"]["input"]["properties"] and "output" not in functions["replies"]
    assert "cursor" not in functions["list_channels"]["input"]["properties"], "the client reads one page"


def test_what_designates_a_person_is_personal():
    for name, function in _functions().items():
        personal = {a for a, s in function["input"]["properties"].items() if s.get("personal") is True}
        assert personal == PERSONAL.get(name, set()), name


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), SLACK)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "slack.{name}",' in module.source, name
    for name in LEFT_OUT:
        assert f'"slack.{name}"' not in module.source, name
    table = python.declared([checked])["slack"]
    assert set(table) == set(_functions())
    assert DECLARED.get("slack") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"


def test_the_bot_token_is_the_only_secret_and_no_function_names_a_token():
    """Le compte ne porte que le jeton du bot ; aucune fonction ne prend un jeton ni un auteur en argument."""
    for name, function in _functions().items():
        assert not {"as_user", "token", "author"} & set(function["input"]["properties"]), name
        assert not re.search(r"xox[bp]-", str(function["examples"])), name


def test_what_the_client_sends_on_the_user_token_is_described_by_slack_user():
    """Le côté de la personne, `slack_user`, porte ce que le client envoie sur son jeton, plus la recherche, que le
    client n'envoie que là ; `post_ephemeral` reste au bot."""
    user_side = {f["name"] for f in yaml.safe_load((CONNECTORS / "slack_user" / "connector.yaml").read_text(
        encoding="utf-8"))["functions"]}
    routed = {name for name, _ in ROUTED_TO_THE_USER["by_reading"]} | ROUTED_TO_THE_USER["by_author"]
    assert user_side == (routed - {"post_ephemeral"}) | {"search_messages"}
