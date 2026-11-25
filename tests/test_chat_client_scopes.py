"""Google Chat client: least-privilege scopes, checked against the real discovery
document (offline). Every request the client builds is one of four methods, and
each one is accepted by a scope in `SCOPES`; no method that edits or deletes is
ever built."""
import pytest

pytest.importorskip("googleapiclient")
from google.oauth2.credentials import Credentials  # noqa: E402
from googleapiclient.http import HttpRequest  # noqa: E402

from oto.tools.google.chat.lib import chat_client as cc  # noqa: E402

ALLOWED = {
    "chat.spaces.list",
    "chat.spaces.findDirectMessage",
    "chat.spaces.messages.list",
    "chat.spaces.messages.create",
}


@pytest.fixture
def sent(monkeypatch):
    """Capture every request instead of sending it."""
    calls = []

    def fake_execute(self, *a, **kw):
        calls.append(self)
        return {"name": "spaces/S"}

    monkeypatch.setattr(HttpRequest, "execute", fake_execute)
    return calls


@pytest.fixture
def client():
    return cc.ChatClient(credentials=Credentials(token="t"))


def test_scopes_are_the_narrow_three():
    assert cc.SCOPES == [
        "https://www.googleapis.com/auth/chat.spaces.readonly",
        "https://www.googleapis.com/auth/chat.messages.readonly",
        "https://www.googleapis.com/auth/chat.messages.create",
    ]
    assert "https://www.googleapis.com/auth/chat.messages" not in cc.SCOPES


def _method_scopes(client, method_id):
    """The scopes the discovery document accepts for `method_id`."""
    doc = client.service._rootDesc

    def walk(resources):
        for res in resources.values():
            for m in res.get("methods", {}).values():
                yield m
            yield from walk(res.get("resources", {}))

    return next(set(m.get("scopes", ())) for m in walk(doc["resources"])
                if m["id"] == method_id)


def test_every_call_is_allowed_and_covered_by_scopes(client, sent):
    client.list_spaces(max_results=1)
    client.find_dm("a@example.com")
    client.send("spaces/S", "hi")
    client.list_messages("spaces/S", max_results=1)
    ids = {r.methodId for r in sent}
    assert ids == ALLOWED
    for method_id in ids:
        assert _method_scopes(client, method_id) & set(cc.SCOPES), method_id
