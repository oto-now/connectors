"""The `visitor` auth mode of the http node and its raw bodies, against a fake local site.

The site has no account: a GET of its entry page redirects to a page that sets the
visitor's cookie, and its service answers a raw text body (not JSON) only with that
cookie. Nothing here names a real site."""
from __future__ import annotations

import http.server
import json
import threading
from urllib.parse import urlsplit

import pytest
import requests

from oto.tools.http import (
    HttpConnectorClient,
    SessionHopRefused,
    SessionRejected,
    VisitorRefused,
)
from oto.tools.http.session import SessionConfig


class Etat:
    def __init__(self):
        self.visites: list[str] = []
        self.appels: list[tuple[str, str, bytes]] = []
        self.jetons: set[str] = set()
        self.emis = 0
        self.sans_cookie = False
        self.refuse_toujours = False
        self.suite = ""              # where the entry page redirects


def _site(etat: Etat):
    class H(http.server.BaseHTTPRequestHandler):
        def _repondre(self, code, data: bytes, ctype, extra=()):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _cookie(self):
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "VISIT":
                    return v
            return None

        def do_GET(self):
            etat.visites.append(self.path)
            p = urlsplit(self.path)
            if p.path == "/":
                self.send_response(302)
                self.send_header("Location", etat.suite or "/accueil")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if p.path == "/accueil":
                extra = ()
                if not etat.sans_cookie:
                    etat.emis += 1
                    jeton = f"v{etat.emis}"
                    etat.jetons.add(jeton)
                    extra = (("Set-Cookie", f"VISIT={jeton}; Path=/"),)
                return self._repondre(200, b"<html>accueil</html>", "text/html", extra)
            self._repondre(404, b"", "text/plain")

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            etat.appels.append((self.path, self.headers.get("Content-Type") or "", body))
            if self.path == "/public/departs":
                return self._repondre(200, json.dumps({"departs": [{"ligne": "A"}]}).encode(),
                                      "application/json")
            if etat.refuse_toujours or self._cookie() not in etat.jetons:
                return self._repondre(401, b"", "text/plain")
            if self.path == "/service/rpc":
                return self._repondre(200, "//OK[1,\"é\"]".encode(),
                                      "text/plain; charset=utf-8")
            if self.path == "/service/json":
                return self._repondre(200, b'{"ok": true}', "application/json")
            self._repondre(404, b"", "text/plain")

        def log_message(self, *a):
            pass

    return H


@pytest.fixture
def banc():
    etat = Etat()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _site(etat))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield etat, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


VISITE = {"login": {"path": "/", "method": "GET", "query": {"lang": "fr"}}}
RPC = "text/x-gwt-rpc; charset=utf-8"


def _client(base, *, session=VISITE, jar=None, gardes=None, jars=None):
    gardes = [] if gardes is None else gardes
    jars = [] if jars is None else jars
    client = HttpConnectorClient(base, "visitor", {"session": json.dumps(session)},
                                 guard=gardes.append, jar=jar, on_jar=jars.append)
    return client, gardes, jars


def test_la_visite_pose_le_cookie_puis_un_corps_brut_rend_le_texte(banc):
    etat, base = banc
    client, gardes, jars = _client(base)

    rendu = client.request_raw("POST", "/service/rpc", data="7|0|4|é|", content_type=RPC,
                               max_bytes=1000)

    assert rendu == {"status": 200, "content_type": "text/plain; charset=utf-8",
                     "text": '//OK[1,"é"]'}
    assert etat.visites == ["/?lang=fr", "/accueil"]
    assert [urlsplit(u).path for u in gardes] == ["/", "/accueil"]
    assert etat.appels == [("/service/rpc", RPC, "7|0|4|é|".encode())]
    assert [c["name"] for c in json.loads(jars[-1])] == ["VISIT"]


def test_le_mode_visiteur_ecrit_aussi_en_json(banc):
    _, base = banc
    client, _, _ = _client(base)
    assert client.post("/service/json", json={"q": 1}) == {"ok": True}


def test_un_jar_rendu_par_l_hote_evite_la_visite(banc):
    etat, base = banc
    premier, _, jars = _client(base)
    premier.request("POST", "/service/json", json={})
    etat.visites.clear()
    client, _, _ = _client(base, jar=jars[-1])
    assert client.request("POST", "/service/json", json={}) == {"ok": True}
    assert etat.visites == []


def test_un_401_relance_une_visite_et_un_seul_rejeu(banc):
    etat, base = banc
    client, _, _ = _client(base)
    client.request("POST", "/service/json", json={})
    etat.jetons.clear()
    etat.visites.clear()
    assert client.request("POST", "/service/json", json={}) == {"ok": True}
    assert etat.visites == ["/?lang=fr", "/accueil"]


def test_refuse_encore_apres_la_visite_est_visitor_refused(banc):
    etat, base = banc
    client, _, _ = _client(base)
    etat.refuse_toujours = True
    with pytest.raises(VisitorRefused) as e:
        client.request("POST", "/service/json", json={})
    assert e.value.code == "visitor_refused"
    assert not isinstance(e.value, SessionRejected)


def test_une_visite_sans_cookie_est_refusee_par_son_nom(banc):
    etat, base = banc
    etat.sans_cookie = True
    with pytest.raises(VisitorRefused, match="no session cookie"):
        _client(base)[0].request("POST", "/service/json", json={})


def test_un_saut_hors_des_hotes_est_refuse_avant_de_partir(banc):
    etat, base = banc
    etat.suite = "http://ailleurs.example.net/accueil"
    with pytest.raises(SessionHopRefused):
        _client(base)[0].request("POST", "/service/json", json={})


def test_la_garde_de_l_hote_arrete_la_visite_avant_l_envoi(banc):
    etat, base = banc

    def garde(url):
        raise PermissionError("egress refused")

    client = HttpConnectorClient(base, "visitor", {"session": json.dumps(VISITE)}, guard=garde)
    with pytest.raises(PermissionError):
        client.request("POST", "/service/json", json={})
    assert etat.visites == []


def test_la_methode_de_visite_est_explicite_et_les_cles_fermees():
    with pytest.raises(ValueError, match="explicit method"):
        SessionConfig({"login": {"path": "/"}}, visitor=True)
    with pytest.raises(ValueError, match="explicit method"):
        SessionConfig({"login": {"path": "/", "method": "POST"}}, visitor=True)
    with pytest.raises(ValueError, match="unknown keys"):
        SessionConfig({"login": {"path": "/", "method": "GET", "user_field": "u"}}, visitor=True)
    with pytest.raises(ValueError, match="unknown keys"):
        SessionConfig({**VISITE, "follow": {"field": "url"}}, visitor=True)
    # The session mode keeps its closed keys: no method there.
    with pytest.raises(ValueError, match="unknown keys"):
        SessionConfig({"login": {"path": "/l", "method": "GET"}})


def test_sans_garde_ni_session_le_mode_visiteur_refuse_de_se_construire():
    with pytest.raises(ValueError, match="'visitor' requires the host's egress guard"):
        HttpConnectorClient("https://site.example.org", "visitor", {"session": json.dumps(VISITE)})
    with pytest.raises(ValueError, match="requires: session"):
        HttpConnectorClient("https://site.example.org", "visitor", {}, guard=lambda u: None)


# --- raw bodies, any mode -----------------------------------------------------------

def test_un_post_json_sans_auth_vers_une_api_publique_reste_inchange(banc):
    """Non-regression: the recorded call of a public departures API, without auth."""
    etat, base = banc
    client = HttpConnectorClient(base, "none", {})
    assert client.post("/public/departs", json={"arret": "X", "n": 3}) == {"departs": [{"ligne": "A"}]}
    chemin, ctype, corps = etat.appels[-1]
    assert (chemin, ctype) == ("/public/departs", "application/json")
    assert json.loads(corps) == {"arret": "X", "n": 3}
    assert etat.visites == []


def test_un_corps_brut_declare_son_type_et_exclut_le_json(banc):
    _, base = banc
    client = HttpConnectorClient(base, "none", {})
    with pytest.raises(ValueError, match="go together"):
        client.request("POST", "/public/departs", data="x")
    with pytest.raises(ValueError, match="go together"):
        client.request("POST", "/public/departs", content_type="text/plain")
    with pytest.raises(ValueError, match="not both"):
        client.request("POST", "/public/departs", json={}, data="x", content_type="text/plain")


def test_le_rendu_brut_est_borne_en_octets(banc):
    _, base = banc
    client, _, _ = _client(base)
    with pytest.raises(ValueError, match="> limit"):
        client.request_raw("POST", "/service/rpc", data="x", content_type=RPC, max_bytes=5)


def test_le_rendu_brut_refuse_un_charset_inconnu_et_leve_sur_erreur_amont(banc, monkeypatch):
    _, base = banc
    client = HttpConnectorClient(base, "none", {})
    with pytest.raises(requests.HTTPError):
        client.request_raw("POST", "/service/rpc", data="x", content_type=RPC, max_bytes=100)

    def send(self, request, **kwargs):
        resp = requests.Response()
        resp.status_code = 200
        resp.raw = __import__("io").BytesIO(b"abc")
        resp.headers["Content-Type"] = "text/plain; charset=pas-un-charset"
        resp.request, resp.url = request, request.url
        return resp

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    with pytest.raises(ValueError, match="unknown charset"):
        client.request_raw("GET", "/x", max_bytes=100)


def test_le_mode_session_reste_en_lecture_seule_pour_le_rendu_brut():
    client = HttpConnectorClient(
        "https://api.example.org", "session",
        {"username": "u", "password": "p", "session": json.dumps({"login": {"path": "/l"}})},
        guard=lambda u: None, jar="[]")
    with pytest.raises(ValueError, match="only reads"):
        client.request_raw("POST", "/x", data="x", content_type="text/plain", max_bytes=10)
