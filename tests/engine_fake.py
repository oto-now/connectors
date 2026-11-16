"""Faux tiers pour le moteur (`oto.connectors.engine`) : un adaptateur `requests` monté sur une vraie `Session`.

Le moteur compose sa requête et l'envoie par la session qu'on lui passe ; le faux rejoue des réponses dans l'ordre, sans
réseau, par le vrai chemin de `requests` (préparation, en-têtes, lecture du corps en flux), et note chaque requête
envoyée. `sent` la relit en parties : méthode, chemin (encodé, sous l'adresse de base), paires de la query (décodées),
en-têtes (noms en minuscules) et corps (JSON lu, texte d'un formulaire, ou absent).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Union
from urllib.parse import parse_qsl, urlsplit

import requests
from requests.adapters import HTTPAdapter

from files_fake import raw

Answer = Union[dict, Callable[[requests.PreparedRequest], dict], BaseException]


def answer(status: int = 200, json_body: Any = None, *, text: str | None = None, headers: dict | None = None) -> dict:
    """Une réponse du tiers : un corps JSON, un texte, ou rien (`json_body` et `text` absents)."""
    spec: dict = {"status": status, "headers": headers or {}}
    if text is not None:
        spec["text"] = text
    elif json_body is not None:
        spec["json"] = json_body
    return spec


class Vendor(HTTPAdapter):
    """Rend ses réponses dans l'ordre (une réponse, une fonction de la requête, ou une exception à lever), et note
    chaque requête ; une requête de trop échoue."""

    def __init__(self, *answers: Answer):
        super().__init__()
        self.answers = list(answers)
        self.requests: list[requests.PreparedRequest] = []
        self.options: list[dict] = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        self.options.append(kwargs)
        if not self.answers:
            raise AssertionError(f"one request too many: {request.method} {request.url}")
        spec = self.answers.pop(0)
        if isinstance(spec, BaseException):
            raise spec
        if callable(spec):
            spec = spec(request)
        return self.build_response(request, raw(spec))


def session(vendor: Vendor) -> requests.Session:
    """Une session sans rien de l'environnement, montée sur le faux tiers."""
    built = requests.Session()
    built.trust_env = False
    built.mount("https://", vendor)
    built.mount("http://", vendor)
    return built


def sent(request: requests.PreparedRequest, base_url: str = "") -> dict:
    """Une requête envoyée, en parties : `method`, `url` (sans la query), `path` (sous `base_url`), `query` (paires
    décodées, dans l'ordre), `headers` (noms en minuscules) et `body` (JSON lu, texte, ou `None`)."""
    parts = urlsplit(request.url)
    url = f"{parts.scheme}://{parts.netloc}{parts.path}"
    body = request.body
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    content_type = request.headers.get("content-type", "")
    if body is not None and content_type == "application/json":
        body = json.loads(body)
    return {
        "method": request.method,
        "url": url,
        "path": url[len(base_url):] if base_url and url.startswith(base_url) else parts.path,
        "query": [list(pair) for pair in parse_qsl(parts.query, keep_blank_values=True)],
        "headers": {name.lower(): value for name, value in request.headers.items()},
        "body": body,
    }


#: Les en-têtes que pose tout envoi, hors de ce qu'une description place : le transport (`requests`), le type du corps
#: et l'acceptation du JSON par le moteur, l'authentification.
TRANSPORT_HEADERS = frozenset({"accept", "accept-encoding", "user-agent", "connection", "content-length", "content-type",
                               "authorization"})


def placed(parts: dict) -> dict:
    """Ce qu'une requête envoyée porte de la description, dans la forme des cas communs : chemin, query, en-têtes posés
    (hors transport et authentification) et corps."""
    headers = {name: value for name, value in parts["headers"].items() if name not in TRANSPORT_HEADERS}
    return {"path": parts["path"], "query": parts["query"], "headers": headers, "body": parts["body"]}


def play(description: dict, function: str, arguments: dict, *answers: Answer, credential: dict | None = None,
         settings: dict | None = None):
    """Appelle la fonction par le moteur contre le faux tiers ; rend le résultat et le faux (ses requêtes)."""
    from oto.connectors import engine

    vendor = Vendor(*(answers or (answer(200, {}),)))
    fields = {field["name"]: "test-key-not-real" for field in description["connector"].get("credential", [])}
    result = engine.call(description, function, arguments, credential=credential if credential is not None else fields,
                         settings=settings, session=session(vendor))
    return result, vendor
