"""Auth jefacture.com — verrouille le contrat du serveur d'autorisation Banqup (région France).

Cible ce qui pourrait dériver en silence : les secrets dans le corps et jamais
dans l'URL, aucune redirection suivie, la rotation du refresh token (le nouveau
remplace l'ancien, l'ancien reste s'il n'en vient pas), le classement
« autorisation morte » contre « configuration fausse », et la cohérence des
adresses avec la description.
"""
from __future__ import annotations

import pathlib
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from oto.tools.common.credentials import MissingCredential
from oto.tools.http.client import RedirectRefused
from oto.tools.jefacture import auth as ja

DESCRIPTION = (pathlib.Path(__file__).resolve().parent.parent
               / "connectors" / "jefacture" / "connector.yaml")
OAUTH = "https://api.btx.fr.banqup.com/oauth2"
CID, SECRET, REDIRECT = "cid_test", "secret_test", "https://mcp.example.test/api/jefacture/oauth/callback"


class _Resp:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Calls(list):
    """The token requests sent, and `replies`: the answers to give, in order."""

    def __init__(self):
        super().__init__()
        self.replies = []


@pytest.fixture()
def token_calls(monkeypatch):
    calls = _Calls()
    replies = calls.replies

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if replies:
            return replies.pop(0)
        return _Resp(200, {"access_token": "AT", "refresh_token": "RT2",
                           "expires_in": 300, "token_type": "Bearer"})

    monkeypatch.setattr(ja.requests, "post", fake_post)
    return calls


def test_authorize_url():
    parts = urlsplit(ja.authorize_url(CID, REDIRECT, "etat-signe"))
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{OAUTH}/auth"
    assert {k: v[0] for k, v in parse_qs(parts.query).items()} == {
        "client_id": CID, "response_type": "code", "redirect_uri": REDIRECT,
        "scope": "openid", "state": "etat-signe"}


def test_exchange_code_sends_secrets_in_the_body(token_calls):
    grant = ja.exchange_code(CID, SECRET, "le-code", REDIRECT)
    (call,) = token_calls
    assert call["url"] == f"{OAUTH}/token"
    assert call["data"] == {"grant_type": "authorization_code", "client_id": CID,
                            "client_secret": SECRET, "code": "le-code",
                            "redirect_uri": REDIRECT}
    assert "params" not in call
    assert call["allow_redirects"] is False
    assert grant == ja.Grant("AT", "RT2", 300, "")


def test_refresh_rotates_on_the_refresh_endpoint(token_calls):
    grant = ja.refresh(CID, SECRET, "RT1")
    (call,) = token_calls
    assert call["url"] == f"{OAUTH}/refresh"
    assert call["data"] == {"grant_type": "refresh_token", "client_id": CID,
                            "client_secret": SECRET, "refresh_token": "RT1"}
    assert grant.refresh_token == "RT2"


def test_refresh_keeps_the_old_token_when_none_comes_back(token_calls):
    token_calls.replies.append(_Resp(200, {"access_token": "AT"}))
    grant = ja.refresh(CID, SECRET, "RT1")
    assert grant.refresh_token == "RT1"
    assert grant.expires_in == ja.DEFAULT_EXPIRES_IN


def test_client_credentials(token_calls):
    grant = ja.client_credentials_token(CID, SECRET)
    (call,) = token_calls
    assert call["url"] == f"{OAUTH}/client_credential/gettoken"
    assert call["data"] == {"grant_type": "client_credentials", "client_id": CID,
                            "client_secret": SECRET}
    assert grant.access_token == "AT"


def test_dead_grant_is_told_apart(token_calls):
    token_calls.replies.append(_Resp(400, {"error": "invalid_grant",
                                           "error_description": "Token is not active"}))
    with pytest.raises(ja.JefactureGrantExpired) as exc:
        ja.refresh(CID, SECRET, "RT1")
    assert exc.value.status_code == 400
    assert exc.value.code == "invalid_grant"


def test_bad_configuration_is_not_a_dead_grant(token_calls):
    token_calls.replies.append(_Resp(401, {"error": "invalid_client"}))
    with pytest.raises(ja.JefactureAuthError) as exc:
        ja.exchange_code(CID, "faux", "c", REDIRECT)
    assert not isinstance(exc.value, ja.JefactureGrantExpired)
    assert "faux" not in str(exc.value)


def test_answer_without_token_is_refused(token_calls):
    token_calls.replies.append(_Resp(200, None))
    with pytest.raises(ja.JefactureAuthError, match="without a token"):
        ja.client_credentials_token(CID, SECRET)


def test_redirect_is_refused(token_calls):
    token_calls.replies.append(_Resp(307, None, {"Location": "https://elsewhere.example.test/"}))
    with pytest.raises(RedirectRefused):
        ja.refresh(CID, SECRET, "RT1")


@pytest.mark.parametrize("call, name", [
    (lambda: ja.authorize_url("", REDIRECT, "s"), "JEFACTURE_CLIENT_ID"),
    (lambda: ja.exchange_code(CID, "", "c", REDIRECT), "JEFACTURE_CLIENT_SECRET"),
    (lambda: ja.refresh(CID, SECRET, ""), "JEFACTURE_REFRESH_TOKEN"),
])
def test_missing_secret_is_named_never_read_from_env(monkeypatch, call, name):
    for var in ("JEFACTURE_CLIENT_ID", "JEFACTURE_CLIENT_SECRET", "JEFACTURE_REFRESH_TOKEN"):
        monkeypatch.setenv(var, "from-env")
    with pytest.raises(MissingCredential) as exc:
        call()
    assert name in str(exc.value)


def test_addresses_match_the_description():
    auth = yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))["connector"]["auth"]
    assert auth["authorize_url"] == ja.AUTHORIZE_URL
    assert auth["token_url"] == ja.TOKEN_URL
    assert tuple(auth["scopes"]) == ja.SCOPES
    assert auth["rotates"] is True


def test_chaque_demande_de_jeton_porte_un_user_agent_explicite(token_calls):
    """Le pare-feu de la plateforme répond 403 (HTML) à `/oauth2/token` quand le
    User-Agent est celui de `requests` par défaut : chaque demande porte le nôtre."""
    ja.exchange_code(CID, SECRET, "code", REDIRECT)
    ja.refresh(CID, SECRET, "RT")
    ja.client_credentials_token(CID, SECRET)
    assert len(token_calls) == 3
    for call in token_calls:
        assert call["headers"]["User-Agent"] == ja.USER_AGENT
        assert not call["headers"]["User-Agent"].startswith("python-requests")
