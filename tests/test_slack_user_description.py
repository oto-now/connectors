"""La description `slack_user` 1.1.0 : le client existant (`oto.tools.slack`) sur le jeton d'utilisateur, sans rien y changer ;
depuis 1.1.0, les deux drapeaux d'aperçu des liens de `post_message` (`unfurl_links`, `unfurl_media`).

Un second connecteur plutôt qu'un second jeton dans le format (décidé par le mainteneur le 2026-10-22) : `slack` décrit le
jeton du bot, `slack_user` celui de la personne. Le client garde les deux et choisit l'un par appel ; chaque fonction
est décrite du côté du jeton que le client prend quand l'espace porte les deux :
- par l'appel : la recherche, la lecture d'un DM (`D…`), la liste des seules conversations directes, `open_dm` ;
- par l'auteur que choisit l'appelant (`default_as_user`) : publier, réécrire, supprimer, une réaction, un fichier.
Le reste part sur le bot ; le client n'y prend le jeton d'utilisateur qu'en repli, quand c'est le seul posé : c'est
l'écart nommé (`LEFT_TO_SLACK`), figé ici. Les deux descriptions disent la même requête pour une même méthode, la même
table d'erreurs et le même motif du texte. Les gardes ci-dessous lisent le client lui-même.
"""
from __future__ import annotations

import copy
import inspect
from unittest import mock

import jsonschema
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.slack import client as slack_client
from oto.tools.slack import text as slack_text
from oto.tools.slack.client import SlackClient

SLACK_USER = CONNECTORS / "slack_user" / "connector.yaml"
SLACK = CONNECTORS / "slack" / "connector.yaml"
BOT = "xoxb-test"
USER = "xoxp-test"

# Les méthodes publiques que ni l'un ni l'autre côté ne décrit (motifs : `LEFT_OUT` de `tests/test_slack_description.py`).
LEFT_OUT = {"fetch_file", "download_file", "create_usergroup", "update_usergroup", "set_usergroup_users",
            "disable_usergroup", "enable_usergroup"}
# Décrites du côté du bot seulement, et pourquoi.
LEFT_TO_SLACK = {
    "channel_info": "the client reads it on the bot token when both are set",
    "get_user_info": "the client reads it on the bot token when both are set",
    "find_user_by_email": "the client reads it on the bot token when both are set",
    "join_channel": "the client joins on the bot token when both are set: joining is the app's act",
    "list_usergroups": "the client reads it on the bot token when both are set",
    "usergroup_users": "the client reads it on the bot token when both are set",
    "post_ephemeral": "an ephemeral message is an app's notice; oto 1 does not send one",
}
_ONE_TOKEN = "one token, the user token: the bot token is connectors/slack"
ARGUMENTS_LEFT_OUT = {name: {"as_user": _ONE_TOKEN} for name in (
    "post_message", "delete_message", "list_channels", "history", "replies", "open_dm")}
# Ce que le client pose quand l'appel ne le donne pas, et que la description ne déclare pas en défaut.
CLIENT_FILLS = {"history": {"limit": 50}, "replies": {"limit": 50}, "search_messages": {"count": 20}}
QUERY_FLAGS = {"history": {"inclusive"}, "replies": {"inclusive"}}
# Sur le jeton d'utilisateur par l'appel, quel que soit l'auteur ; par l'auteur choisi.
BY_THE_CALL = {"search_messages", "list_channels", "history", "replies", "open_dm"}
BY_THE_AUTHOR = {"post_message", "update_message", "delete_message", "add_reaction", "file_info"}
CLASSES = {
    "read": {"search_messages", "list_channels", "history", "replies", "file_info"},
    "write": {"open_dm", "add_reaction"},
    # Publié, réécrit ou supprimé sous le nom de la personne.
    "sensitive": {"post_message", "update_message", "delete_message"},
}
NOT_ACT = {"open_dm"}
PERSONAL = {
    "search_messages": {"query"}, "open_dm": {"user"}, "post_message": {"text", "blocks"},
    "update_message": {"text", "blocks"},
}

TS = "1767225600.000100"
VALUES = {
    "query": "invoice in:#finance", "count": 20, "types": "im,mpim", "channel": "D0000000001", "limit": 20,
    "cursor": "dXNlcjpVMEc5V0ZYTlo=", "oldest": "1767225600", "latest": "1767312000.000200", "inclusive": True,
    "thread_ts": TS, "ts": TS, "text": "Thanks, I'll look at it.", "user": "U0000000001",
    "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*Thanks*"}}], "name": "white_check_mark",
    "file_id": "F0000000001", "unfurl_links": False, "unfurl_media": False,
}


def _load(path) -> dict:
    return copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))


def _functions(path=SLACK_USER) -> dict[str, dict]:
    return {f["name"]: f for f in _load(path)["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(SlackClient, inspect.isfunction) if not n.startswith("_")}


def _parameters(name: str) -> dict[str, inspect.Parameter]:
    return {p: v for p, v in inspect.signature(getattr(SlackClient, name)).parameters.items() if p != "self"}


class _Answer:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "ts": TS, "channel": {"id": "D0000000001"}, "channels": [], "messages": [],
                "usergroups": [], "users": [], "file": {"id": "F0000000001"}}


def _sent(name: str, arguments: dict, client: SlackClient | None = None) -> list[dict]:
    """Ce que la méthode du client envoie, sans réseau : méthode, adresse, query, corps JSON, formulaire, jeton."""
    sent = []

    def request(method, url, **kwargs):
        sent.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {}),
                     "json": kwargs.get("json"), "data": kwargs.get("data"),
                     "token": (kwargs.get("headers") or {})["Authorization"].removeprefix("Bearer ")})
        return _Answer()

    with mock.patch.object(slack_client.requests, "request", request):
        getattr(client or _user_only(), name)(**arguments)
    return sent


def _user_only() -> SlackClient:
    """Le client sur le seul jeton d'utilisateur. Sans `default_as_user=True`, il refuse une écriture (`as_user=False
    requires SLACK_BOT_TOKEN`) : l'auteur ne retombe pas sur l'autre jeton, seule une lecture le fait."""
    return SlackClient(user_token=USER, default_as_user=True)


def _one(name: str, arguments: dict, client: SlackClient | None = None) -> dict:
    sent = _sent(name, arguments, client)
    assert len(sent) == 1, name
    return sent[0]


def _token(name: str, arguments: dict, default_as_user: bool) -> str:
    return _one(name, arguments, SlackClient(bot_token=BOT, user_token=USER, default_as_user=default_as_user))["token"]


def _in_query(value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _described(function: dict, arguments: dict) -> tuple:
    call = function["call"]
    query = {api: _in_query(arguments[a]) for api, a in call.get("query", {}).items() if a in arguments}
    body = {api: arguments[a] for api, a in call["body"].items() if a in arguments} if "body" in call else None
    return call["method"], SlackClient.BASE_URL + call["path"], query, body


def _client_view(name: str, sent: dict, arguments: dict) -> tuple:
    params = {k: _in_query(v) for k, v in sent["params"].items()}
    for argument, value in CLIENT_FILLS.get(name, {}).items():
        if argument not in arguments:
            assert params.pop(argument) == _in_query(value), (name, argument)
    assert sent["data"] is None, name
    return sent["method"], sent["url"], params, sent["json"]


def _values(function: dict) -> dict:
    return {argument: VALUES[argument] for argument in function["input"]["properties"]}


def _calls(function: dict) -> list[dict]:
    """Un appel qui donne chaque argument, et chaque exemple."""
    return [_values(function), *(e["input"] for e in function["examples"])]


def _valid(schema: dict, value) -> bool:
    return jsonschema.Draft202012Validator(schema).is_valid(value)


def test_the_description_is_version_1_1_1_and_passes_the_fabrique():
    description = _load(SLACK_USER)
    assert description["connector"]["version"] == "1.1.1"
    check(description, SLACK_USER)


def test_each_method_of_the_client_is_on_one_side_or_named_with_its_reason():
    functions, bot_side = set(_functions()), set(_functions(SLACK))
    assert functions <= _public_methods()
    assert _public_methods() - functions == LEFT_OUT | set(LEFT_TO_SLACK)
    assert set(LEFT_TO_SLACK) <= bot_side and not LEFT_OUT & (functions | bot_side)
    assert "search_messages" not in bot_side


def test_the_connector_says_what_the_client_does():
    connector = _load(SLACK_USER)["connector"]
    assert connector["base_url"] == SlackClient.BASE_URL
    assert connector["auth"] == {"kind": "bearer", "token": "user_token"}
    assert connector["credential"] == [{"name": "user_token", "label": "User token (xoxp-)", "secret": True}]
    assert _one("search_messages", {"query": "x"})["token"] == USER
    assert slack_client._HTTP_TIMEOUT == (10, 60) and connector["timeout_s"] == 60
    assert connector["modes"] == ["byo_user"], "a user token speaks for one person"
    assert "rate_limit" not in connector
    assert "probe" not in connector, "every function needs an argument"
    assert all(f["input"].get("required") for f in _functions().values())


def test_each_function_takes_the_arguments_of_its_client_method():
    for name, function in _functions().items():
        properties = set(function["input"]["properties"])
        assert set(_parameters(name)) - properties == set(ARGUMENTS_LEFT_OUT.get(name, {})), name
        assert properties <= set(_parameters(name)), name


def test_each_call_and_each_example_sends_what_the_client_sends():
    for name, function in _functions().items():
        for arguments in _calls(function):
            jsonschema.validate(arguments, function["input"])
            assert _client_view(name, _one(name, arguments), arguments) == _described(function, arguments), name


def test_reads_go_in_the_query_of_a_get_writes_in_a_json_body():
    for name, function in _functions().items():
        sent = _one(name, _values(function))
        if function["call"]["method"] == "GET":
            assert "body" not in function["call"] and sent["json"] is None, name
        else:
            assert "query" not in function["call"] and sent["params"] == {} and sent["json"], name


def test_what_the_client_fills_is_never_a_declared_default_nor_required():
    functions = _functions()
    for name, filled in CLIENT_FILLS.items():
        for argument, value in filled.items():
            assert _parameters(name)[argument].default == value, (name, argument)
            assert "default" not in functions[name]["input"]["properties"][argument], (name, argument)
            assert argument not in functions[name]["input"]["required"], (name, argument)
    # Sans `types`, le client pose public_channel, qu'il lit sur le bot : ici, `types` est exigé.
    assert _parameters("list_channels")["types"].default == "public_channel"
    assert functions["list_channels"]["input"]["required"] == ["types"]


def test_the_query_flags_go_only_true_as_in_the_client():
    functions = _functions()
    flags = {(n, a) for n, f in functions.items() for a, s in f["input"]["properties"].items() if s.get("const") is True}
    assert flags == {(n, a) for n, arguments in QUERY_FLAGS.items() for a in arguments}
    for name, arguments in QUERY_FLAGS.items():
        for argument in arguments:
            assert _one(name, {"channel": "D0000000001", "thread_ts": TS, argument: True} if name == "replies"
                        else {"channel": "D0000000001", argument: True})["params"][argument] == "true"


def test_each_function_is_on_the_token_the_client_takes_when_both_are_set():
    """Les deux jetons posés : une fonction de l'appel part sur la personne quel que soit l'auteur, une fonction de
    l'auteur selon `default_as_user` ; ce que le client lit sur le bot, le schéma le refuse ici."""
    functions = _functions()
    assert set(functions) == BY_THE_CALL | BY_THE_AUTHOR
    for name, function in functions.items():
        for arguments in _calls(function):
            assert _token(name, arguments, True) == USER, name
            assert _token(name, arguments, False) == (USER if name in BY_THE_CALL else BOT), name
    for name in ("history", "replies"):
        schema = functions[name]["input"]
        for channel in ("C0000000001", "G0000000001"):
            arguments = {**_values(functions[name]), "channel": channel}
            assert not _valid(schema, arguments), (name, channel)
            assert _token(name, arguments, True) == BOT, (name, channel)
    for types in ("public_channel", "private_channel", "public_channel,im", "mpim,private_channel"):
        assert not _valid(functions["list_channels"]["input"], {"types": types}), types
        assert _token("list_channels", {"types": types}, True) == BOT, types


def test_what_is_left_to_slack_goes_on_the_bot_and_on_the_user_token_only_as_a_fallback():
    """Le repli, écart nommé : avec le seul jeton d'utilisateur, le client envoie aussi sur lui ce que le bot lit quand
    les deux sont posés. Ces fonctions restent du côté de `slack`."""
    bot_side = _functions(SLACK)
    for name, reason in LEFT_TO_SLACK.items():
        arguments = _values_of_slack(bot_side[name])
        if name == "post_ephemeral":
            assert _token(name, arguments, True) == USER, "the client sends it on the chosen author's token"
            continue
        assert _token(name, arguments, True) == BOT, (name, reason)
        assert _one(name, arguments, SlackClient(user_token=USER))["token"] == USER, name
    # Une écriture, elle, ne retombe pas : sans auteur choisi, le client sur le seul jeton d'utilisateur la refuse.
    for name in BY_THE_AUTHOR:
        try:
            _sent(name, _values(_functions()[name]), SlackClient(user_token=USER))
        except ValueError as error:
            assert "requires SLACK_BOT_TOKEN" in str(error), name
        else:
            raise AssertionError(f"{name}: the client writes without a chosen author")


_SLACK_VALUES = {
    "channel": "C0000000001", "user": "U0000000001", "user_id": "U0000000001", "email": "marie@example.test",
    "text": "Ready.", "usergroup": "S0000000001",
}


def _values_of_slack(function: dict) -> dict:
    return {a: _SLACK_VALUES[a] for a in function["input"].get("required", [])}


def test_both_sides_say_the_same_request_the_same_errors_and_the_same_text_bounds():
    ours, theirs = _functions(), _functions(SLACK)
    for name in set(ours) & set(theirs):
        assert ours[name]["call"] == theirs[name]["call"], name
        assert ours[name]["class"] == theirs[name]["class"] and ours[name].get("act") == theirs[name].get("act"), name
        assert ours[name].get("pagination") == theirs[name].get("pagination"), name
        assert ours[name].get("output") == theirs[name].get("output"), name
    text, bot_text = (f["post_message"]["input"]["properties"]["text"] for f in (ours, theirs))
    assert {k: v for k, v in text.items() if k != "description"} == {k: v for k, v in bot_text.items() if k != "description"}
    assert text["maxLength"] == slack_text.MAX_TEXT_LEN

    def rows(path):
        return [{k: v for k, v in e.items() if k != "message"} for e in _load(path)["connector"]["errors"]]

    assert rows(SLACK_USER) == rows(SLACK)
    marked = next(e for e in _load(SLACK_USER)["connector"]["errors"] if e.get("marks_account"))
    assert set(marked["body"]["equals"]) == {c for c, s in slack_client._SLACK_ERROR_STATUS.items() if s == 401}


def test_the_text_of_post_message_is_sent_as_given_under_its_two_bounds():
    schema = _functions()["post_message"]["input"]["properties"]["text"]
    for text in ("Thanks, I'll look at it.", "Kick-off at 9:05", ":100: done", "20:02 to 20:51: go", "at 12:30:00"):
        if _valid(schema, text):
            assert _one("post_message", {"channel": "D0000000001", "text": text})["json"]["text"] == text, text
        else:
            assert slack_text.escape_false_emoji_shortcodes(text) != text, text
    assert not _valid(schema, "x" * 4001)


def test_reads_and_writes_are_classified_and_every_act_is_served_on_the_act_side():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        assert (function["call"]["method"] == "GET") == (function["class"] == "read"), name
        assert ("confirm" in function) == (function["class"] == "sensitive"), name
        if "confirm" in function:
            assert "under the person's own name" in function["confirm"]["summary"], name
        expected_act = function["class"] != "read" and name not in NOT_ACT
        assert function.get("act", function["class"] != "read") == expected_act, name


def test_only_history_is_paginated_and_each_call_costs_one_request():
    functions = _functions()
    assert {n for n, f in functions.items() if "pagination" in f} == {"history"}
    for name, function in functions.items():
        assert function["cost"] == {"per": "request"}, name


def test_what_designates_a_person_is_personal():
    for name, function in _functions().items():
        personal = {a for a, s in function["input"]["properties"].items() if s.get("personal") is True}
        assert personal == PERSONAL.get(name, set()), name


def test_the_typescript_output_and_the_python_table():
    checked = check(_load(SLACK_USER), SLACK_USER)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "slack_user.{name}",' in module.source, name
    table = python.declared([checked])["slack_user"]
    assert set(table) == set(_functions())
    assert DECLARED.get("slack_user") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"
