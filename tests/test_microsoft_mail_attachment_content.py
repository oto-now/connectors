"""MailClient.get_attachment_content — le contenu d'une pièce jointe lu en flux :
borné par `max_bytes` (refus d'avance sur la taille déclarée, coupure en cours de
lecture), réponse toujours fermée, session donnée seule utilisée, pièce non-fichier
refusée, refus amont réduit au code et au message de Graph."""
import json

import pytest
import requests

from oto.tools.common import UpstreamHTTPError
from oto.tools.microsoft import (AttachmentContent, AttachmentTooLarge, MailboxAccessError,
                                 MailClient, UnsupportedAttachment)

G = "https://graph.microsoft.com/v1.0"
FILE = "#microsoft.graph.fileAttachment"


class _Stream:
    """A streamed answer: chunks served one by one, `closed` once closed."""

    def __init__(self, status_code=200, chunks=(), headers=None, payload=None):
        self.status_code = status_code
        self._chunks = list(chunks)
        self.headers = headers or {}
        self._payload = payload
        self.read = 0
        self.closed = False
        self.url = ""
        self.request = None

    def iter_content(self, chunk_size=1):
        for chunk in self._chunks:
            self.read += 1
            yield chunk

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    @property
    def content(self):
        if self._payload is not None:
            return json.dumps(self._payload).encode()
        return b"".join(self._chunks)

    @property
    def text(self):
        return b"".join(self._chunks).decode(errors="replace")

    def close(self):
        self.closed = True


def _meta(**fields):
    return _Stream(payload={"@odata.type": FILE, "name": "scan.pdf",
                            "contentType": "application/pdf", "size": 10, **fields})


class _Recording(requests.Session):
    """The session handed to the client: records each call, serves `responses`."""

    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)
        self.seen = []

    def request(self, method, url, **kwargs):
        self.seen.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def no_default_session(monkeypatch):
    """Only the injected session may go out: the class-level verb fails."""
    def refuse(self, *a, **k):
        raise AssertionError("a request went out through another session")
    monkeypatch.setattr(requests.Session, "request", refuse)


def _client(*responses):
    session = _Recording(responses)
    return MailClient("AT-personne", session=session), session


def test_octets_type_et_nom_par_la_session_donnee():
    body = _Stream(chunks=[b"%PDF-", b"1.7"], headers={"Content-Type": "application/pdf"})
    client, session = _client(_meta(), body)
    got = client.get_attachment_content("AAMk=", "AAMk/a=", max_bytes=100)
    assert got == AttachmentContent(name="scan.pdf", content_type="application/pdf",
                                    content=b"%PDF-1.7")
    meta_call, value_call = session.seen
    assert meta_call["url"] == f"{G}/me/messages/AAMk%3D/attachments/AAMk%2Fa%3D"
    assert meta_call["params"] == {"$select": "name,contentType,size"}
    assert value_call["url"] == f"{G}/me/messages/AAMk%3D/attachments/AAMk%2Fa%3D/$value"
    assert value_call["stream"] is True
    for call in session.seen:
        assert call["headers"]["Authorization"] == "Bearer AT-personne"
        assert call["headers"]["Prefer"] == 'IdType="ImmutableId"'
    # Le jeton voyage avec chaque requête, jamais posé sur la session donnée.
    assert "Authorization" not in session.headers
    assert body.closed


def test_type_de_la_piece_a_defaut_de_content_type_servi():
    client, _ = _client(_meta(), _Stream(chunks=[b"x"]))
    got = client.get_attachment_content("m", "a", max_bytes=10)
    assert got.content_type == "application/pdf"


def test_coupure_des_que_le_total_depasse_max_bytes():
    body = _Stream(chunks=[b"a" * 4, b"b" * 4, b"c" * 4, b"d" * 4])
    client, _ = _client(_meta(size=1), body)
    with pytest.raises(AttachmentTooLarge) as e:
        client.get_attachment_content("m", "a", max_bytes=10)
    assert e.value.max_bytes == 10 and e.value.size is None
    assert body.read == 3  # coupé au troisième morceau, le quatrième jamais lu
    assert body.closed


def test_content_length_au_dela_refuse_avant_lecture():
    body = _Stream(chunks=[b"a"], headers={"Content-Length": "11"})
    client, _ = _client(_meta(size=1), body)
    with pytest.raises(AttachmentTooLarge) as e:
        client.get_attachment_content("m", "a", max_bytes=10)
    assert e.value.size == 11 and body.read == 0 and body.closed


def test_exactement_max_bytes_accepte():
    client, _ = _client(_meta(), _Stream(chunks=[b"a" * 10]))
    assert client.get_attachment_content("m", "a", max_bytes=10).content == b"a" * 10


def test_taille_declaree_au_dela_refusee_d_avance():
    meta = _meta(size=5_000_000)
    client, session = _client(meta)
    with pytest.raises(AttachmentTooLarge) as e:
        client.get_attachment_content("m", "a", max_bytes=1_000_000)
    assert e.value.size == 5_000_000 and e.value.name == "scan.pdf"
    assert len(session.seen) == 1  # le contenu n'est jamais demandé
    assert meta.closed


@pytest.mark.parametrize("kind", ["#microsoft.graph.itemAttachment",
                                  "#microsoft.graph.referenceAttachment", None])
def test_piece_non_fichier_refusee(kind):
    client, session = _client(_meta(**{"@odata.type": kind}))
    with pytest.raises(UnsupportedAttachment) as e:
        client.get_attachment_content("m", "a", max_bytes=10)
    if kind:
        assert e.value.kind == kind
    assert len(session.seen) == 1


@pytest.mark.parametrize("max_bytes", [0, -1, None, 1.5, True, "10"])
def test_max_bytes_entier_positif(max_bytes):
    client, session = _client()
    with pytest.raises(ValueError, match="max_bytes"):
        client.get_attachment_content("m", "a", max_bytes=max_bytes)
    assert session.seen == []


def test_max_bytes_obligatoire():
    client, _ = _client()
    with pytest.raises(TypeError):
        client.get_attachment_content("m", "a")


@pytest.mark.parametrize("ids", [("", "a"), ("m", ""), (None, "a"), ("m", None)])
def test_identifiants_requis(ids):
    client, session = _client()
    with pytest.raises(ValueError):
        client.get_attachment_content(*ids, max_bytes=10)
    assert session.seen == []


def _refusal(status, token_echo=True):
    body = {"error": {"code": "ErrorItemNotFound", "message": "not found",
                      "innerError": {"echo": "Bearer AT-personne" if token_echo else ""}}}
    return _Stream(status_code=status, chunks=[json.dumps(body).encode()])


def test_refus_amont_sur_le_contenu_sans_corps_brut_et_ferme():
    refusal = _refusal(404)
    client, _ = _client(_meta(), refusal)
    with pytest.raises(UpstreamHTTPError) as e:
        client.get_attachment_content("m", "a", max_bytes=10)
    assert e.value.status_code == 404
    assert e.value.body == {"error": {"code": "ErrorItemNotFound", "message": "not found"}}
    assert "AT-personne" not in str(e.value) and "innerError" not in str(e.value)
    assert refusal.closed


def test_refus_amont_sur_les_metadonnees_sans_corps_brut_et_ferme():
    refusal = _refusal(403)
    client, session = _client(refusal)
    with pytest.raises(UpstreamHTTPError) as e:
        client.get_attachment_content("m", "a", max_bytes=10)
    assert e.value.status_code == 403 and "AT-personne" not in str(e.value)
    assert refusal.closed and len(session.seen) == 1


def test_refus_amont_illisible_sans_corps():
    refusal = _Stream(status_code=502, chunks=[b"<html>gateway Bearer AT-personne</html>"])
    client, _ = _client(_meta(), refusal)
    with pytest.raises(UpstreamHTTPError) as e:
        client.get_attachment_content("m", "a", max_bytes=10)
    assert e.value.body is None and "AT-personne" not in str(e.value)


def test_boite_partagee():
    client, session = _client(_meta(), _Stream(chunks=[b"x"]))
    client.get_attachment_content("m", "a", max_bytes=10, mailbox="compta@acme.fr")
    assert session.seen[1]["url"] == f"{G}/users/compta@acme.fr/messages/m/attachments/a/$value"


def test_refus_sur_une_boite_partagee_nomme_la_boite():
    client, _ = _client(_refusal(403))
    with pytest.raises(MailboxAccessError) as e:
        client.get_attachment_content("m", "a", max_bytes=10, mailbox="compta@acme.fr")
    assert e.value.mailbox == "compta@acme.fr" and e.value.graph_code == "ErrorItemNotFound"
    assert "AT-personne" not in str(e.value)


def test_session_donnee_sert_tous_les_appels_du_client():
    client, session = _client(_Stream(payload={"value": [{"id": "m1"}]}))
    assert client.search_messages(top=1) == [{"id": "m1"}]
    assert session.seen[0]["headers"]["Authorization"] == "Bearer AT-personne"
