"""Client Gemini : la clé voyage dans l'en-tête `x-goog-api-key`, jamais dans l'URL, et ne suit jamais une redirection.

Aucun réseau : le transport est doublé."""
from __future__ import annotations

import pytest

from oto.tools.gemini import client as mod

class _Redirige:
    """Une 3xx vers un autre hôte, dont le `Location` garde la clé en query."""
    status_code = 302
    headers = {"Location": "https://ailleurs.test/v1/x?api_key=SECRET#f"}
    content = b""
    text = ""
    ok = False

    def json(self):
        return {}

    def raise_for_status(self):
        return None


def test_une_redirection_est_refusee_jamais_suivie(monkeypatch):
    """La clé part dans un paramètre de requête que `requests` ne retire pas sur un changement
    d'hôte : une 3xx est refusée (`RedirectRefused`), jamais suivie ni retentée, et
    sa cible est réduite à schéma, hôte et chemin — jamais sa query."""
    from oto.tools.http.client import RedirectRefused
    vus = []

    def fake(url, **kw):
        vus.append(kw)
        return _Redirige()

    c = mod.GeminiClient(api_key="k")
    monkeypatch.setattr(mod.requests, "post", fake)
    with pytest.raises(RedirectRefused) as e:
        c._post(c._url("m", "generateContent"), {}, 60)
    assert vus[0]["allow_redirects"] is False
    assert e.value.location == "https://ailleurs.test/v1/x"
    assert "SECRET" not in str(e.value)
    assert len(vus) == 1


def test_tout_post_passe_par_le_transport_unique():
    """Quatre appels, un seul `requests.post` et un seul `requests.get` : la parade vit à un endroit."""
    import inspect
    assert inspect.getsource(mod).count("requests.post(") == 1
    assert inspect.getsource(mod).count("requests.get(") == 1


class _Rep:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = status < 400
        self.headers = {}
        self.text = str(body)
        self.content = b"x"
        self.url = "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent"
        self.request = None
        self.reason = ""

    def json(self):
        return self._body


def _capture(monkeypatch, rep):
    vus = []

    def fake(url, **kw):
        vus.append((url, kw))
        return rep

    monkeypatch.setattr(mod.requests, "post", fake)
    monkeypatch.setattr(mod.requests, "get", fake)
    return vus


def test_la_cle_part_dans_l_en_tete_jamais_dans_l_url(monkeypatch):
    vus = _capture(monkeypatch, _Rep(200, {"models": []}))
    mod.GeminiClient(api_key="SECRET").list_models()
    url, kw = vus[0]
    assert "SECRET" not in url and "key=" not in url
    assert kw["headers"]["x-goog-api-key"] == "SECRET"
    assert kw["allow_redirects"] is False


def test_create_image_rend_les_octets_l_usage_et_ignore_les_pensees(monkeypatch):
    import base64
    final, brouillon = b"\x89PNGfinal", b"\x89PNGthought"
    body = {
        "candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(brouillon).decode()},
             "thought": True},
            {"text": "voici"},
            {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(final).decode()}},
        ]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 1120,
                          "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}]},
        "modelVersion": "gemini-nano-banana-2.1", "responseId": "r1",
    }
    vus = _capture(monkeypatch, _Rep(200, body))
    out = mod.GeminiClient(api_key="k").create_image(
        "une pomme", "gemini-nano-banana-2.1",
        images=[{"data": b"ref", "mime_type": "image/jpeg"}], aspect_ratio="16:9", image_size="2K")
    assert out["images"] == [{"data": final, "mime_type": "image/png"}]
    assert out["text"] == "voici" and out["finish_reason"] == "STOP"
    assert out["usage_metadata"]["candidatesTokensDetails"][0]["tokenCount"] == 1120
    url, kw = vus[0]
    assert url.endswith("/models/gemini-nano-banana-2.1:generateContent")
    gen = kw["json"]["generationConfig"]
    assert gen["responseModalities"] == ["IMAGE"]
    assert gen["imageConfig"] == {"aspectRatio": "16:9", "imageSize": "2K"}
    parts = kw["json"]["contents"][0]["parts"]
    assert parts[0] == {"text": "une pomme"}
    assert parts[1]["inline_data"] == {"mime_type": "image/jpeg", "data": base64.b64encode(b"ref").decode()}


def test_create_image_sans_image_dit_pourquoi(monkeypatch):
    _capture(monkeypatch, _Rep(200, {"promptFeedback": {"blockReason": "SAFETY"}}))
    out = mod.GeminiClient(api_key="k").create_image("x", "m")
    assert out["images"] == [] and out["finish_reason"] == "SAFETY"


def test_create_image_leve_une_erreur_amont_typee(monkeypatch):
    from oto.tools.common.errors import UpstreamHTTPError
    _capture(monkeypatch, _Rep(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}))
    with pytest.raises(UpstreamHTTPError) as e:
        mod.GeminiClient(api_key="k").create_image("x", "m")
    assert e.value.status_code == 429


@pytest.mark.parametrize("bad", ["", "  ", None])
def test_create_image_refuse_un_prompt_vide(bad):
    with pytest.raises(ValueError):
        mod.GeminiClient(api_key="k").create_image(bad, "m")
