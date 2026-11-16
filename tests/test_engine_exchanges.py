"""Les échanges de jeton du moteur (`oto.connectors.engine`) : `oauth2_client_credentials` et `oauth2_jwt_bearer`.

Ce que les cas communs du compte de service (`test_jwt_bearer_cases.py`) ne disent pas : l'échange d'un client, le cache
du jeton à l'échelle du processus (indexé par l'empreinte du credential, jamais par le secret), son échéance avec marge,
le renouvellement sur 401, et la description `google_search_console` avec une clé RSA générée par le test, jamais une
vraie clé. Aucun réseau : chaque requête part vers `engine_fake.Vendor`.
"""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

import engine_fake
from engine_fake import answer, sent
from oto.connectors import descriptions, engine
from oto.connectors.engine import tokens, transport

API = "https://api.example.test/v1"
TOKEN_URL = "https://auth.example.test/oauth/token"
SECRET = "client-secret-not-real-42"


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(tokens, "TOKENS", tokens.TokenCache())
    monkeypatch.setattr(transport, "_sleep", lambda seconds: None)
    now = [1_800_000_000.0]
    monkeypatch.setattr(tokens, "_now", lambda: now[0])
    return now


def client(**auth) -> dict:
    block = {"kind": "oauth2_client_credentials", "token_url": TOKEN_URL, "token_request": "form",
             "client_auth": "body", "client_id": "client_id", "client_secret": "client_secret"}
    block.update(auth)
    connector = {"name": "exchange_cases", "label": "Exchange cases", "base_url": API, "auth": block,
                 "credential": [{"name": "client_id", "label": "Client ID", "secret": False},
                                {"name": "client_secret", "label": "Client secret", "secret": True}],
                 "errors": [{"status": 401, "code": "access_rejected", "retryable": False, "message": "Rejected."}]}
    return {"connector": connector, "functions": [{"name": "get_records", "class": "read",
                                                   "call": {"method": "GET", "path": "/records"}}]}


CREDENTIAL = {"client_id": "client-1", "client_secret": SECRET}


def call(description, *answers, credential=CREDENTIAL, settings=None):
    vendor = engine_fake.Vendor(*answers)
    result = engine.call(description, "get_records", {}, credential=credential, settings=settings,
                         session=engine_fake.session(vendor))
    return result, vendor


def token(value="tok-1", **extra):
    return answer(200, {"access_token": value, "token_type": "Bearer", **extra})


# --- oauth2_client_credentials ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("auth, content_type, body, authorization", [
    ({}, "application/x-www-form-urlencoded",
     f"grant_type=client_credentials&client_id=client-1&client_secret={SECRET}", None),
    ({"token_request": "json", "scope": "read write"}, "application/json",
     {"grant_type": "client_credentials", "client_id": "client-1", "client_secret": SECRET, "scope": "read write"}, None),
    ({"client_auth": "basic"}, "application/x-www-form-urlencoded", "grant_type=client_credentials",
     "Basic " + base64.b64encode(f"client-1:{SECRET}".encode()).decode()),
], ids=["form_body", "json_scope", "basic"])
def test_the_client_is_exchanged_as_its_auth_says(auth, content_type, body, authorization):
    result, vendor = call(client(**auth), token(), answer(200, {"ok": True}))
    exchanged, api = (sent(r) for r in vendor.requests)
    assert (exchanged["method"], exchanged["url"], exchanged["query"]) == ("POST", TOKEN_URL, [])
    assert exchanged["headers"]["content-type"] == content_type and exchanged["body"] == body
    assert exchanged["headers"].get("authorization") == authorization
    assert api["headers"]["authorization"] == "Bearer tok-1" and result == {"ok": True}


def test_a_token_is_kept_by_fingerprint_until_a_minute_before_its_expiry(clock):
    description = client()
    _, vendor = call(description, token(expires_in=3600), answer(200, {}))
    _, vendor = call(description, answer(200, {}))
    assert len(vendor.requests) == 1, "the kept token signs the second call"
    assert all(SECRET not in key and "client-1" not in key for key in tokens.TOKENS.keys())
    _, vendor = call(description, token("tok-other"), answer(200, {}), credential={**CREDENTIAL, "client_secret": "s2"})
    assert sent(vendor.requests[1])["headers"]["authorization"] == "Bearer tok-other"
    clock[0] += 3600 - 59
    _, vendor = call(description, token("tok-2"), answer(200, {}))
    assert sent(vendor.requests[1])["headers"]["authorization"] == "Bearer tok-2"


def test_expires_in_default_bounds_a_token_without_expires_in(clock):
    description = client(expires_in_default=600)
    call(description, token(), answer(200, {}))
    (kept,) = [tokens.TOKENS.get(key) for key in tokens.TOKENS.keys()]
    assert kept.expires_at == clock[0] + 600
    call(client(), token("tok-text", expires_in="120"), answer(200, {}), credential={**CREDENTIAL, "client_id": "c2"})
    assert sorted(t.expires_at for t in map(tokens.TOKENS.get, tokens.TOKENS.keys())) == [clock[0] + 120, clock[0] + 600]


def test_a_401_is_replayed_once_on_a_fresh_token():
    result, vendor = call(client(), token("tok-1"), answer(401), token("tok-2"), answer(200, {"ok": 1}))
    assert result == {"ok": 1}
    assert [sent(r)["headers"].get("authorization") for r in vendor.requests[1::2]] == ["Bearer tok-1", "Bearer tok-2"]
    with pytest.raises(engine.Refused) as raised:
        call(client(), token("tok-3"), answer(401), token("tok-4"), answer(401), credential={**CREDENTIAL, "client_id": "c2"})
    assert (raised.value.code, raised.value.status_code) == ("access_rejected", 401)


@pytest.mark.parametrize("status, message", [(401, "check its client id and secret"), (500, r"unexpected status \(500\)")])
def test_a_refusal_of_the_token_endpoint_names_no_secret(status, message):
    with pytest.raises(engine.TokenRefused, match=message) as raised:
        call(client(), answer(status, {"error": "invalid_client", "error_description": f"bad {SECRET}"}))
    assert SECRET not in str(raised.value) and raised.value.error == "invalid_client"
    assert not raised.value.credential_rejected


def test_a_token_answer_without_its_token_is_unreadable():
    with pytest.raises(engine.UnreadableAnswer, match="token answer"):
        call(client(), answer(200, {"token_type": "Bearer"}))


def test_a_token_address_by_setting():
    description = client(token_urls={"setting": "region", "values": {"us": "https://us.example.test/token",
                                                                     "eu": "https://eu.example.test/token"}})
    del description["connector"]["auth"]["token_url"]
    description["connector"]["settings"] = [{"name": "region", "label": "Region", "type": "choice",
                                             "choices": ["us", "eu"], "default": "us"}]
    _, vendor = call(description, token(), answer(200, {}), settings={"region": "eu"})
    assert sent(vendor.requests[0])["url"] == "https://eu.example.test/token"


def test_sellsy_is_exchanged_then_called():
    description = descriptions.load("sellsy")
    fn = next(f for f in description["functions"] if f["name"] == "list_estimates")
    vendor = engine_fake.Vendor(token("tok-s"), answer(200, {"data": [], "pagination": {"offset": None}}))
    engine.call(description, fn["name"], fn["examples"][0]["input"],
                credential={"client_id": "client-1", "client_secret": SECRET}, session=engine_fake.session(vendor))
    exchanged, api = (sent(r) for r in vendor.requests)
    assert exchanged["url"] == description["connector"]["auth"]["token_url"]
    assert exchanged["body"] == {"grant_type": "client_credentials", "client_id": "client-1", "client_secret": SECRET}
    assert api["headers"]["authorization"] == "Bearer tok-s"


# --- oauth2_jwt_bearer -----------------------------------------------------------------------------------------------

def _pem(private_key) -> str:
    return private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode("ascii")


@pytest.fixture(scope="module")
def rsa_key():
    """Une clé RSA jetable, générée pour ce test : jamais une vraie clé."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_file(private_key, **extra) -> str:
    file = {"type": "service_account", "project_id": "test-project-not-real", "private_key_id": "kid-not-real",
            "client_email": "reader@test-project-not-real.example.test", "token_uri": "https://oauth2.googleapis.com/token",
            "private_key": _pem(private_key)}
    file.update(extra)
    return json.dumps(file)


def _decoded(segment: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def test_search_console_signs_its_assertion_and_calls_with_the_token(rsa_key, clock):
    description = descriptions.load("google_search_console")
    vendor = engine_fake.Vendor(token("ya29.not-real", expires_in=3599), answer(200, {"siteEntry": [{"siteUrl": "x"}]}))
    pages = engine.execute(description, "list_sites", {}, credential={"service_account_key": _key_file(rsa_key)},
                           session=engine_fake.session(vendor))
    assert pages.answers == [{"siteEntry": [{"siteUrl": "x"}]}]
    exchanged, api = vendor.requests
    assert sent(exchanged)["url"] == "https://oauth2.googleapis.com/token"
    form = dict(pair.split("=", 1) for pair in exchanged.body.decode("ascii").split("&"))
    assert form["grant_type"] == "urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer"
    header, claims, signature = form["assertion"].split(".")
    assert _decoded(header) == {"alg": "RS256", "typ": "JWT", "kid": "kid-not-real"}
    at = int(clock[0])
    assert _decoded(claims) == {"iss": "reader@test-project-not-real.example.test",
                                "scope": "https://www.googleapis.com/auth/webmasters.readonly",
                                "aud": "https://oauth2.googleapis.com/token", "iat": at, "exp": at + 3600}
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    rsa_key.public_key().verify(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)),
                                f"{header}.{claims}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    parts = sent(api, "https://searchconsole.googleapis.com")
    assert parts["path"] == "/webmasters/v3/sites" and parts["headers"]["authorization"] == "Bearer ya29.not-real"


def test_a_key_of_another_type_is_unreadable_and_nothing_is_sent():
    other = ec.generate_private_key(ec.SECP256R1())
    vendor = engine_fake.Vendor()
    with pytest.raises(engine.Refused) as raised:
        engine.call(descriptions.load("google_search_console"), "list_sites", {},
                    credential={"service_account_key": _key_file(other)}, session=engine_fake.session(vendor))
    assert raised.value.code == "service_account_key_unreadable" and vendor.requests == []
    assert "BEGIN" not in str(raised.value)


def test_a_401_of_the_api_replays_on_a_fresh_assertion(rsa_key):
    description = descriptions.load("google_search_console")
    vendor = engine_fake.Vendor(token("t1"), answer(401), token("t2"), answer(200, {"siteEntry": []}))
    engine.execute(description, "list_sites", {}, credential={"service_account_key": _key_file(rsa_key)},
                   session=engine_fake.session(vendor))
    assert [sent(r)["headers"].get("authorization") for r in vendor.requests[1::2]] == ["Bearer t1", "Bearer t2"]


def test_invalid_grant_rejects_the_credential(rsa_key):
    vendor = engine_fake.Vendor(answer(400, {"error": "invalid_grant", "error_description": "Invalid JWT Signature."}))
    with pytest.raises(engine.TokenRefused) as raised:
        engine.call(descriptions.load("google_search_console"), "list_sites", {},
                    credential={"service_account_key": _key_file(rsa_key)}, session=engine_fake.session(vendor))
    assert raised.value.credential_rejected and raised.value.marks_account == "credential_rejected"


# --- niveaux d'accès d'un compte de service (docs/conception/niveaux-d-acces.md) ---------------------------------------

READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
WEBMASTERS = "https://www.googleapis.com/auth/webmasters"
SITEMAP = {"site_url": "sc-domain:example.com", "feedpath": "https://www.example.com/sitemap.xml"}


def _claimed(request) -> str:
    form = dict(pair.split("=", 1) for pair in request.body.decode("ascii").split("&"))
    return _decoded(form["assertion"].split(".")[1])["scope"]


@pytest.mark.parametrize("level, scope", [(None, READONLY), ("read", READONLY), ("full", WEBMASTERS)],
                         ids=["default_level", "read", "full"])
def test_search_console_claims_the_scopes_of_the_account_level(rsa_key, level, scope):
    """Sans niveau donné, le niveau par défaut de la description (`read`) ; la lecture est servie aux deux niveaux."""
    vendor = engine_fake.Vendor(token(), answer(200, {"siteEntry": []}))
    engine.execute(descriptions.load("google_search_console"), "list_sites", {},
                   credential={"service_account_key": _key_file(rsa_key)}, session=engine_fake.session(vendor),
                   access_level=level)
    assert _claimed(vendor.requests[0]) == scope


@pytest.mark.parametrize("level", [None, "read"], ids=["default_level", "read"])
def test_add_sitemap_is_refused_below_full_and_nothing_is_sent(rsa_key, level):
    vendor = engine_fake.Vendor()
    with pytest.raises(engine.Refused) as raised:
        engine.call(descriptions.load("google_search_console"), "add_sitemap", SITEMAP,
                    credential={"service_account_key": _key_file(rsa_key)}, session=engine_fake.session(vendor),
                    access_level=level)
    assert (raised.value.code, raised.value.status_code) == ("access_level_denied", None)
    assert "'read'" in str(raised.value) and "add_sitemap" in str(raised.value)
    assert vendor.requests == []


def test_add_sitemap_at_full_puts_the_sitemap_with_a_write_token(rsa_key):
    vendor = engine_fake.Vendor(token("ya29.write-not-real"), answer(204))
    result = engine.call(descriptions.load("google_search_console"), "add_sitemap", SITEMAP,
                         credential={"service_account_key": _key_file(rsa_key)}, session=engine_fake.session(vendor),
                         access_level="full")
    assert result == {}
    exchanged, api = vendor.requests
    assert _claimed(exchanged) == WEBMASTERS
    parts = sent(api, "https://searchconsole.googleapis.com")
    assert (parts["method"], parts["path"]) == (
        "PUT", "/webmasters/v3/sites/sc-domain%3Aexample.com/sitemaps/https%3A%2F%2Fwww.example.com%2Fsitemap.xml")
    assert parts["headers"]["authorization"] == "Bearer ya29.write-not-real"


def test_add_sitemap_by_a_restricted_user_names_the_permission(rsa_key):
    refused = answer(403, {"error": {"code": 403, "message": "User does not have sufficient permission for site "
                                     "'sc-domain:example.com'. See also: https://support.google.com/webmasters/answer/2451999.",
                                     "errors": [{"reason": "forbidden"}]}})
    vendor = engine_fake.Vendor(token(), refused)
    with pytest.raises(engine.Refused) as raised:
        engine.call(descriptions.load("google_search_console"), "add_sitemap", SITEMAP,
                    credential={"service_account_key": _key_file(rsa_key)}, session=engine_fake.session(vendor),
                    access_level="full")
    assert raised.value.code == "owner_or_full_required"


def test_a_token_is_kept_per_level(rsa_key):
    """Le jeton gardé est indexé par ses droits : un compte passé à `full` signe une nouvelle assertion."""
    description = descriptions.load("google_search_console")
    credential = {"service_account_key": _key_file(rsa_key)}
    vendor = engine_fake.Vendor(token("t-read"), answer(200, {"siteEntry": []}), token("t-full"), answer(200, {"siteEntry": []}))
    for level in ("read", "full"):
        engine.execute(description, "list_sites", {}, credential=credential, session=engine_fake.session(vendor),
                       access_level=level)
    assert [_claimed(vendor.requests[i]) for i in (0, 2)] == [READONLY, WEBMASTERS]


def test_an_undeclared_level_raises_and_nothing_is_sent(rsa_key):
    vendor = engine_fake.Vendor()
    with pytest.raises(engine.UnknownAccessLevel):
        engine.call(descriptions.load("google_search_console"), "list_sites", {},
                    credential={"service_account_key": _key_file(rsa_key)}, session=engine_fake.session(vendor),
                    access_level="admin")
    assert vendor.requests == []


def test_a_connector_without_levels_takes_full_or_none_only():
    """Un connecteur sans niveaux n'a que `full` : aucun niveau, ou `full`, ne change rien ; un autre lève."""
    vendor = engine_fake.Vendor(token(), answer(200, {"ok": True}), answer(200, {"ok": True}))
    for level in (None, "full"):
        assert engine.call(client(), "get_records", {}, credential=CREDENTIAL, session=engine_fake.session(vendor),
                           access_level=level) == {"ok": True}
    assert len(vendor.requests) == 3, "one token, kept for both calls: the same scope"
    with pytest.raises(engine.UnknownAccessLevel):
        engine.call(client(), "get_records", {}, credential=CREDENTIAL,
                    session=engine_fake.session(engine_fake.Vendor()), access_level="read")
