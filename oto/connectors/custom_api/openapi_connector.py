"""The `connector` block an OpenAPI document proposes: name, label, address, authentication and the standard error
table, each value said as deduced, each value it cannot propose named as a problem (`openapi.from_openapi`).

- `base_url`: the first server, when it is absolute and in https (Swagger 2.0: `https` among `schemes`, `host` and
  `basePath`); a server with variables is not proposed (`openapi_server_variables`: a definition has no settings);
- `auth`: the scheme the document requires first (`security`), else the first it declares: `http bearer` gives
  `bearer`, `apiKey` gives `api_key` (header or query), `http basic` gives `basic`, the `clientCredentials` flow gives
  `oauth2_client_credentials`, the `authorizationCode` flow gives `oauth2_user`, whose identity stays to choose;
  `openIdConnect` is not followed (`openapi_openid_connect`), a key in a cookie is not sent
  (`openapi_cookie_parameter`).
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit

from . import problems as p
from .errors import standard_errors
from .naming import identifier
from .problems import Problem
from .profile import PREFIX, internal_host

SERVER_VARIABLES = "openapi_server_variables"
OPENID_CONNECT = "openapi_openid_connect"
COOKIE_PARAMETER = "openapi_cookie_parameter"
TOKEN = [{"name": "token", "label": "API key", "secret": True}]
BASIC = [
    {"name": "username", "label": "Username", "secret": False},
    {"name": "password", "label": "Password", "secret": True},
]
CLIENT = [
    {"name": "client_id", "label": "Client ID", "secret": False},
    {"name": "client_secret", "label": "Client secret", "secret": True},
]


def _https(url: Any, at: str, problems: list[Problem]) -> Optional[str]:
    """`url` without its trailing slash when it is an absolute https address towards a public host; a problem
    otherwise."""
    if not isinstance(url, str) or not url:
        return None
    if "{" in url:
        problems.append(
            Problem(
                SERVER_VARIABLES, at, f"{url!r} has variables, which a definition has not: write the address yourself"
            )
        )
        return None
    parts = urlsplit(url)
    if parts.scheme != "https":
        problems.append(
            Problem(p.BASE_URL_NOT_HTTPS, at, f"{url!r} is not an absolute https address: write it yourself")
        )
        return None
    if not parts.hostname or internal_host(parts.hostname):
        problems.append(Problem(p.BASE_URL_INTERNAL, at, f"{url!r} does not name a public host"))
        return None
    return url.rstrip("/")


def _server(document: dict, problems: list[Problem]) -> Optional[str]:
    if "swagger" in document:
        schemes = document.get("schemes") or ["https"]
        if not document.get("host") or "https" not in schemes:
            problems.append(
                Problem(
                    p.BASE_URL_NOT_HTTPS,
                    "connector.base_url",
                    "the document names no https host: write the address yourself",
                )
            )
            return None
        return _https(f"https://{document['host']}{document.get('basePath') or ''}", "connector.base_url", problems)
    servers = document.get("servers")
    first = servers[0] if isinstance(servers, list) and servers and isinstance(servers[0], dict) else {}
    if not first.get("url"):
        problems.append(
            Problem(
                p.BASE_URL_NOT_HTTPS, "connector.base_url", "the document names no server: write the address yourself"
            )
        )
        return None
    return _https(first["url"], "connector.base_url", problems)


def _schemes(document: dict) -> dict:
    if "swagger" in document:
        return document.get("securityDefinitions") or {}
    return (document.get("components") or {}).get("securitySchemes") or {}


def _chosen(document: dict) -> Optional[tuple[str, dict]]:
    schemes = {name: s for name, s in _schemes(document).items() if isinstance(s, dict)}
    for requirement in document.get("security") or []:
        for name in requirement if isinstance(requirement, dict) else []:
            if name in schemes:
                return name, schemes[name]
    return next(iter(schemes.items()), None)


def _oauth2(scheme: dict, swagger: bool, problems: list[Problem]) -> tuple[Optional[dict], list]:
    if swagger:
        flows = {"application": "clientCredentials", "accessCode": "authorizationCode"}
        flow_name = flows.get(scheme.get("flow"))
        flow = scheme if flow_name else {}
    else:
        all_flows = scheme.get("flows") or {}
        flow_name = next((f for f in ("clientCredentials", "authorizationCode") if f in all_flows), None)
        flow = all_flows.get(flow_name) or {}
    scopes = list((flow.get("scopes") or {}).keys())
    if flow_name == "clientCredentials":
        token_url = _https(flow.get("tokenUrl"), "connector.auth.token_url", problems)
        auth = {
            "kind": "oauth2_client_credentials",
            "token_url": token_url,
            "token_request": "form",
            "client_auth": "body",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }
        if scopes:
            auth["scope"] = " ".join(scopes)
        return auth, CLIENT
    if flow_name == "authorizationCode":
        auth = {
            "kind": "oauth2_user",
            "authorize_url": _https(flow.get("authorizationUrl"), "connector.auth.authorize_url", problems),
            "token_url": _https(flow.get("tokenUrl"), "connector.auth.token_url", problems),
            "client_auth": "body",
            "refresh": "refresh_token",
            "rotates": False,
        }
        if scopes:
            auth["scopes"] = scopes
        problems.append(
            Problem(
                p.SCHEMA_INVALID,
                "connector.auth.identity",
                "who consented is read after the consent: choose the function or address that names them",
            )
        )
        return auth, []
    problems.append(
        Problem(
            p.AUTH_NOT_SUPPORTED,
            "connector.auth",
            "the document's OAuth flows are neither clientCredentials nor authorizationCode",
        )
    )
    return None, []


def _auth(document: dict, problems: list[Problem], deduced: list[dict]) -> tuple[dict, list]:
    chosen = _chosen(document)
    if chosen is None:
        deduced.append({"path": "connector.auth", "value": "none", "why": "the document declares no security scheme"})
        return {"kind": "none"}, []
    name, scheme = chosen
    kind = scheme.get("type")
    swagger = "swagger" in document
    auth: Optional[dict] = None
    credential: list = []
    if kind == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
        auth, credential = {"kind": "bearer", "token": "token"}, TOKEN
    elif (kind == "http" and str(scheme.get("scheme", "")).lower() == "basic") or kind == "basic":
        auth, credential = {"kind": "basic", "username": "username", "password": "password"}, BASIC
    elif kind == "apiKey" and scheme.get("in") in ("header", "query"):
        auth, credential = {"kind": "api_key", "in": scheme["in"], "name": scheme.get("name"), "key": "token"}, TOKEN
    elif kind == "apiKey":
        problems.append(Problem(COOKIE_PARAMETER, "connector.auth", f"the scheme {name!r} sends its key in a cookie"))
    elif kind == "oauth2":
        auth, credential = _oauth2(scheme, swagger, problems)
    elif kind == "openIdConnect":
        problems.append(
            Problem(
                OPENID_CONNECT,
                "connector.auth",
                f"the scheme {name!r} is OpenID Connect discovery, which is not followed: describe its "
                "OAuth flow yourself",
            )
        )
    else:
        problems.append(
            Problem(p.AUTH_NOT_SUPPORTED, "connector.auth", f"the scheme {name!r} ({kind}) is not supported")
        )
    if auth is None:
        return {"kind": "none"}, []
    auth = {k: v for k, v in auth.items() if v is not None}
    deduced.append({"path": "connector.auth", "value": auth["kind"], "why": f"from the security scheme {name!r}"})
    return auth, credential


def propose(document: dict) -> tuple[dict, list[dict], list[Problem]]:
    """`(connector, deduced, problems)`: the block the document proposes, never saved as such."""
    problems: list[Problem] = []
    deduced: list[dict] = []
    info = document.get("info") if isinstance(document.get("info"), dict) else {}
    label = str(info.get("title") or "Custom API").strip()[:80]
    name = PREFIX + identifier(label, "api")
    connector: dict = {
        "name": name,
        "label": label,
        "namespace": name,
        "version": "1.0.0",
        "api_version": str(info.get("version") or "unspecified"),
    }
    deduced.append({"path": "connector.name", "value": name, "why": f"the reserved prefix and the title {label!r}"})
    base_url = _server(document, problems)
    if base_url:
        connector["base_url"] = base_url
        deduced.append({"path": "connector.base_url", "value": base_url, "why": "the document's server"})
    auth, credential = _auth(document, problems, deduced)
    connector["auth"] = auth
    if credential:
        connector["credential"] = credential
    connector["modes"] = ["byo_user"] if auth["kind"] == "oauth2_user" else ["byo_user", "byo_org"]
    connector["errors"] = standard_errors(label, auth["kind"])
    deduced.append(
        {
            "path": "connector.errors",
            "value": [e["code"] for e in connector["errors"]],
            "why": "the standard error table",
        }
    )
    return connector, deduced, problems
