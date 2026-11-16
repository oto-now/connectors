"""The `session` auth mode of the http node, against two fake local servers.

The API server takes the login (POST, JSON), sets the session cookie on a code
redirect and serves the API with that cookie; the connection server, on another host
name, sends the login on to the code redirect. Nothing here names a real service."""
from __future__ import annotations

import http.server
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from oto.tools.http import (
    HttpConnectorClient,
    RedirectRefused,
    SessionHopRefused,
    SessionRejected,
)
from oto.tools.http.session import SessionConfig, SessionLogin

PASSWORD = "mot-de-passe-de-test"


class Etat:
    def __init__(self):
        self.logins: list[dict] = []
        self.connexions: list[str] = []
        self.lectures = 0
        self.jetons: set[str] = set()
        self.emis = 0
        self.expire = "401"          # 401, or "login" (a redirect to the login page)
        self.refuse_toujours = False
        self.cible_connexion = ""    # where the connection server redirects
        self.auth_url = ""


def _serveur(handler_cls, hote="127.0.0.1"):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://{hote}:{srv.server_address[1]}"


def _api(etat: Etat):
    class H(http.server.BaseHTTPRequestHandler):
        def _json(self, code, body, extra=()):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _redirect(self, location, extra=()):
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path != "/api/auth/login_check":
                return self._json(405, {"error": "post refused"})
            etat.logins.append(body)
            if body.get("password") != PASSWORD:
                return self._json(401, {"message": "Invalid credentials."})
            self._json(200, {"auth_url": etat.auth_url, "refresh_token": "r"})

        def do_GET(self):
            p = urlsplit(self.path)
            if p.path == "/ent/login_check":
                if parse_qs(p.query).get("code") != ["c0de"]:
                    return self._json(403, {"error": "bad code"})
                etat.emis += 1
                jeton = f"jwt-{etat.emis}"
                etat.jetons.add(jeton)
                return self._redirect("/app/", [("Set-Cookie", f"BEARER={jeton}; Path=/; HttpOnly")])
            if p.path == "/app/":
                return self._json(200, {"page": "app"})
            if p.path == "/api/items":
                etat.lectures += 1
                cookie = self.headers.get("Cookie") or ""
                jeton = cookie.split("BEARER=", 1)[1].split(";")[0] if "BEARER=" in cookie else ""
                if jeton in etat.jetons and not etat.refuse_toujours:
                    return self._json(200, {"items": [1, 2]})
                if etat.expire == "login":
                    return self._redirect("/login")
                return self._json(401, {"message": "Expired JWT Token"})
            if p.path == "/ailleurs":
                return self._redirect("https://example.org/x")
            self._json(404, {})

        do_HEAD = do_GET

        def log_message(self, *a):
            pass

    return H


def _connexion(etat: Etat):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            etat.connexions.append(self.path)
            p = urlsplit(self.path)
            if p.path != "/connect" or parse_qs(p.query).get("_xhr_call") != ["1"]:
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(302)
            self.send_header("Location", etat.cible_connexion)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    return H


@pytest.fixture
def banc():
    etat = Etat()
    api, base = _serveur(_api(etat))
    cx, cx_base = _serveur(_connexion(etat), hote="localhost")
    etat.auth_url = f"{cx_base}/connect?ticket=t1"
    etat.cible_connexion = f"{base}/ent/login_check?code=c0de"
    yield etat, base
    api.shutdown()
    cx.shutdown()


SESSION = {
    "login": {"path": "/api/auth/login_check", "constants": {"remember_me": True}},
    "follow": {"field": "auth_url", "query": {"_xhr_call": "1"}},
    "hosts": ["localhost"],
    "login_pages": ["/login", "/api/auth/login_check"],
}


def _fields(password=PASSWORD, session=SESSION):
    return {"username": "agent@example.org", "password": password, "session": json.dumps(session)}


def _client(base, *, password=PASSWORD, session=SESSION, jar=None, garde=None, jars=None):
    vus: list[str] = [] if garde is None else garde
    gardes = vus.append if isinstance(vus, list) else vus
    sauves = [] if jars is None else jars
    return HttpConnectorClient(base, "session", _fields(password, session), guard=gardes,
                               jar=jar, on_jar=sauves.append), vus, sauves


def test_le_login_suit_l_url_rendue_puis_les_redirections_et_lit_avec_le_cookie(banc):
    etat, base = banc
    client, gardes, jars = _client(base)

    assert client.get("/api/items") == {"items": [1, 2]}

    assert etat.logins == [{"username": "agent@example.org", "password": PASSWORD, "remember_me": True}]
    assert etat.connexions == ["/connect?ticket=t1&_xhr_call=1"]
    port = base.rsplit(":", 1)[1]
    assert [urlsplit(u).path for u in gardes] == ["/api/auth/login_check", "/connect",
                                                 "/ent/login_check", "/app/"]
    assert urlsplit(gardes[1]).hostname == "localhost" and urlsplit(gardes[2]).port == int(port)
    stocke = json.loads(jars[-1])
    assert [c["name"] for c in stocke] == ["BEARER"]
    assert PASSWORD not in jars[-1]


def test_un_jar_rendu_par_l_hote_evite_le_login(banc):
    etat, base = banc
    _, _, jars = _client(base)
    premier, _, _ = _client(base, jars=jars)
    premier.get("/api/items")
    assert len(etat.logins) == 1

    second, _, _ = _client(base, jar=jars[-1])
    assert second.get("/api/items") == {"items": [1, 2]}
    assert len(etat.logins) == 1


def test_un_401_relance_un_login_complet_et_un_seul_rejeu(banc):
    etat, base = banc
    client, _, jars = _client(base)
    client.get("/api/items")
    etat.jetons.clear()                       # the JWT expired

    assert client.get("/api/items") == {"items": [1, 2]}
    assert len(etat.logins) == 2
    assert json.loads(jars[-1])[0]["value"] == "jwt-2"


def test_une_redirection_vers_le_login_relance_le_login(banc):
    etat, base = banc
    etat.expire = "login"
    client, _, _ = _client(base)
    client.get("/api/items")
    etat.jetons.clear()

    assert client.get("/api/items") == {"items": [1, 2]}
    assert len(etat.logins) == 2


def test_refuse_encore_apres_le_login_credential_rejected_sans_second_rejeu(banc):
    etat, base = banc
    client, _, _ = _client(base)
    client.get("/api/items")
    etat.refuse_toujours = True
    lectures = etat.lectures

    with pytest.raises(SessionRejected) as e:
        client.get("/api/items")
    assert e.value.code == "credential_rejected"
    assert etat.lectures == lectures + 2      # the call, then one replay
    assert len(etat.logins) == 2


def test_un_mot_de_passe_refuse_est_credential_rejected_et_ne_fuit_pas(banc):
    etat, base = banc
    client, _, _ = _client(base, password="faux-mot-de-passe")
    with pytest.raises(SessionRejected) as e:
        client.get("/api/items")
    assert e.value.code == "credential_rejected"
    assert "faux-mot-de-passe" not in str(e.value) and "agent@example.org" not in str(e.value)
    assert etat.connexions == []


def test_un_saut_vers_un_hote_non_declare_est_refuse_avant_de_partir(banc):
    etat, base = banc
    sans_hote = {k: v for k, v in SESSION.items() if k != "hosts"}
    client, gardes, _ = _client(base, session=sans_hote)
    with pytest.raises(SessionHopRefused):
        client.get("/api/items")
    assert etat.connexions == []
    assert all(urlsplit(u).hostname == "127.0.0.1" for u in gardes)


def test_la_garde_de_l_hote_arrete_la_chaine_avant_l_envoi(banc):
    etat, base = banc

    def garde(url):
        if urlsplit(url).hostname == "localhost":
            raise PermissionError("egress refused")

    client, _, _ = _client(base, garde=garde)
    with pytest.raises(PermissionError):
        client.get("/api/items")
    assert len(etat.logins) == 1 and etat.connexions == []


def test_trop_de_redirections_leve(banc):
    etat, base = banc
    etat.cible_connexion = etat.auth_url.replace("ticket=t1", "ticket=t2&_xhr_call=1")
    client, _, _ = _client(base)
    with pytest.raises(ValueError, match="redirects"):
        client.get("/api/items")


def test_le_mode_session_ne_fait_que_lire(banc):
    _, base = banc
    client, _, _ = _client(base)
    with pytest.raises(ValueError, match="only reads"):
        client.post("/api/items", json={})
    for method in ("PUT", "PATCH", "DELETE"):
        with pytest.raises(ValueError, match="only reads"):
            client.request(method, "/api/items")
    assert client.request("HEAD", "/api/items") == {}


def test_une_autre_redirection_de_l_api_reste_refusee(banc):
    _, base = banc
    client, _, _ = _client(base)
    with pytest.raises(RedirectRefused):
        client.get("/ailleurs")


def test_sans_garde_le_mode_session_refuse_de_se_construire(banc):
    _, base = banc
    with pytest.raises(ValueError, match="egress guard"):
        HttpConnectorClient(base, "session", _fields())


def test_une_cle_inconnue_ou_un_chemin_manquant_leve():
    with pytest.raises(ValueError, match="unknown keys"):
        SessionConfig({**SESSION, "retry": 3})
    with pytest.raises(ValueError, match="login.path"):
        SessionConfig({"login": {}})
    with pytest.raises(ValueError, match="is the login or the password field"):
        SessionConfig({"login": {"path": "/l", "constants": {"password": "x"}}})
    with pytest.raises(ValueError, match="requires: session"):
        HttpConnectorClient("https://api.example.org", "session",
                            {"username": "u", "password": "p"}, guard=lambda u: None)


def test_le_domaine_declare_borne_les_sauts():
    auth = SessionLogin("https://api.example.org", "u", "p",
                        SessionConfig({"login": {"path": "/l"}, "domain": "example.org"}),
                        guard=lambda u: None)
    auth._allowed("https://connect.example.org/x")
    auth._allowed("https://example.org/x")
    for ailleurs in ("https://evil-example.org/x", "https://example.org.evil.net/x",
                     "ftp://connect.example.org/x"):
        with pytest.raises(SessionHopRefused):
            auth._allowed(ailleurs)
    with pytest.raises(ValueError, match="not under it"):
        SessionLogin("https://api.example.net", "u", "p",
                     SessionConfig({"login": {"path": "/l"}, "domain": "example.org"}),
                     guard=lambda u: None)


def test_les_autres_modes_gardent_leur_comportement(banc):
    _, base = banc
    client = HttpConnectorClient(base, "none", {})
    with pytest.raises(requests.HTTPError):
        client.get("/api/items")
