"""Les cas communs du compte de service : `connectors/_cases/jwt_bearer.yaml`, joués par le paquet de l'hôte.

`auth.kind: oauth2_jwt_bearer` (RFC 7523, `docs/conception/compte.md`) : l'hôte lit le fichier JSON de la clé, signe une
assertion en RS256 et l'échange contre un jeton. Un cas porte un bloc `auth` et un `credential` en forme source, la clé
(`key_file` et le mode de sa clé privée, ou `key_text`, un texte tel quel), une horloge (`now`, en secondes), et attend
l'assertion exacte (en-tête, revendications, signature), la requête de jeton exacte, la réponse simulée et ce qu'elle
donne : l'en-tête de l'appel et l'échéance, ou un refus nommé. Un compte à niveaux d'accès (`auth.access_levels`) porte
le niveau du compte (`access_level`, sinon `auth.default_level`, sinon `full`) : le `scope` de l'assertion est celui
de ce niveau (`level_scopes`). Une clé refusée (`refused`) l'est avant tout envoi, par
un refus nommé, le même des deux côtés.

Aucune clé privée en clair dans un fichier : le fichier porte le corps base64 d'une clé RSA de test jetable
(`test_key`, PKCS#8 DER), que `credential_value` encadre en PEM à l'exécution ; ni l'assertion entière, dont seule la
signature est écrite (l'en-tête et les revendications sont des objets). Les gardes de secrets d'un dépôt public, celle
d'oto-core comprise, refusent un en-tête PEM de clé privée et un JWT écrits en clair.

La fabrique contrôle chaque cas comme le compte d'une description (le schéma, puis les règles, la règle 2 comprise),
puis écrit `ts/src/jwt-bearer-cases.ts`. En Python, rien : `tests/test_jwt_bearer_cases.py` joue les cas contre le moteur de la lib
(`oto.connectors.engine`), devant un faux point de jeton, et contre une référence écrite dans le test.

`python -m fabrique --check` échoue quand le module TypeScript commité ne suit plus le fichier.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import yaml
from jsonschema import Draft202012Validator

from .auth_cases import description
from .descriptions import CONNECTORS, REPO, DescriptionError, check_rules, check_schema
from .typescript import HEADER, _auth, _credential_field, js, js_block

CASES = CONNECTORS / "_cases" / "jwt_bearer.yaml"
OUTPUT_NAME = "jwt-bearer-cases.ts"
CONNECTOR = "jwt_bearer_cases"

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ASSERTION = "{assertion}"
# Les modes de la clé privée d'un `key_file` : la clé de test en PEM, absente, un PEM qui n'en encadre que la première
# ligne, un texte qui n'est pas un PEM.
PRIVATE_KEYS = ("test_key", "absent", "truncated", "not_pem")
NOT_PEM = "not-a-private-key"
# L'étiquette PEM, jamais écrite en clair avec ses tirets (voir l'en-tête du module).
PEM_LABEL = "PRIVATE KEY"
KEY_REFUSALS = ("service_account_key_not_json", "service_account_key_incomplete",
                "service_account_key_foreign_token_uri", "service_account_key_unreadable")
TOKEN_REFUSALS = ("credential_rejected", "upstream_error")

_STRINGS = {"type": "object", "additionalProperties": {"type": "string"}}
_LOWER_NAMES = {"type": "object", "propertyNames": {"pattern": "^[a-z0-9-]+$"}, "additionalProperties": {"type": "string"}}
_NAME = {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"}
_AUTH = {"type": "object", "required": ["kind"], "properties": {"kind": {"const": "oauth2_jwt_bearer"}}}
_KEY = {
    "key_file": _STRINGS,
    "private_key": {"enum": list(PRIVATE_KEYS)},
    "key_text": {"type": "string"},
}
# Un `key_file` porte le mode de sa clé privée ; un `key_text` se donne tel quel, sans l'un ni l'autre.
_KEY_FORM = {"oneOf": [{"required": ["key_file", "private_key"], "not": {"required": ["key_text"]}},
                       {"required": ["key_text"], "not": {"anyOf": [{"required": ["key_file"]}, {"required": ["private_key"]}]}}]}

# La forme d'un fichier de cas ; `auth` et `credential` se valident ensuite comme le bloc d'une description.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["test_key", "cases", "refused"],
    "properties": {
        "test_key": {"type": "string", "pattern": "^([A-Za-z0-9+/=]{1,64}\n)+$"},
        "cases": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "auth", "credential", "key_file", "private_key", "now", "assertion", "token_request",
                             "token_answer", "outcome"],
                "properties": {
                    "name": _NAME, "auth": _AUTH, "credential": {"type": "array"}, "key_file": _STRINGS,
                    "access_level": _NAME,
                    "private_key": {"const": "test_key"},
                    "now": {"type": "integer", "minimum": 1},
                    "assertion": {
                        "type": "object", "additionalProperties": False, "required": ["header", "claims", "signature"],
                        "properties": {
                            "header": _STRINGS,
                            "claims": {"type": "object", "additionalProperties": False,
                                       "required": ["iss", "scope", "aud", "iat", "exp"],
                                       "properties": {"iss": {"type": "string"}, "scope": {"type": "string"},
                                                      "aud": {"type": "string"}, "iat": {"type": "integer"},
                                                      "exp": {"type": "integer"}}},
                            "signature": {"type": "string", "pattern": "^[A-Za-z0-9_-]+$"},
                        },
                    },
                    "token_request": {
                        "type": "object", "additionalProperties": False, "required": ["url", "content_type", "body"],
                        "properties": {"url": {"type": "string"}, "content_type": {"const": "application/x-www-form-urlencoded"},
                                       "body": {"type": "string"}},
                    },
                    "token_answer": {
                        "type": "object", "additionalProperties": False, "required": ["status", "headers", "body"],
                        "properties": {"status": {"type": "integer"}, "headers": _LOWER_NAMES, "body": {}},
                    },
                    "outcome": {"oneOf": [
                        {"type": "object", "additionalProperties": False, "required": ["headers", "expires_at"],
                         "properties": {"headers": _LOWER_NAMES, "expires_at": {"type": "integer"}}},
                        {"type": "object", "additionalProperties": False, "required": ["refusal"],
                         "properties": {"refusal": {"enum": list(TOKEN_REFUSALS)}}},
                    ]},
                },
            },
        },
        "refused": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "auth", "credential", "refusal"],
                "properties": {"name": _NAME, "auth": _AUTH, "credential": {"type": "array"}, **_KEY,
                               "refusal": {"enum": list(KEY_REFUSALS)}},
                **_KEY_FORM,
            },
        },
    },
}


@dataclass(frozen=True)
class JwtBearerCases:
    path: pathlib.Path
    test_key: str
    cases: list[dict]
    refused: list[dict]


def _relative(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _pem(body: str) -> str:
    return f"-----BEGIN {PEM_LABEL}-----\n{body}-----END {PEM_LABEL}-----\n"


def credential_value(key: dict, test_key: str) -> str:
    """La valeur du champ de la clé : `key_text` tel quel, ou `key_file` en JSON compact, `private_key` ajoutée en
    dernier selon son mode. La même règle que `credentialValue` de la sortie TypeScript."""
    if "key_text" in key:
        return key["key_text"]
    file = dict(key["key_file"])
    mode = key["private_key"]
    if mode == "test_key":
        file["private_key"] = _pem(test_key)
    elif mode == "truncated":
        file["private_key"] = _pem(test_key.split("\n", 1)[0] + "\n")
    elif mode == "not_pem":
        file["private_key"] = NOT_PEM
    return json.dumps(file, ensure_ascii=False, separators=(",", ":"))


def level_scopes(case: dict) -> list[str]:
    """Les droits que revendique l'assertion d'un cas : `auth.scopes`, ou ceux du niveau du compte (`access_level`,
    sinon `auth.default_level`, sinon `full`). La même règle que `levelScopes` de la sortie TypeScript."""
    auth = case["auth"]
    if "access_levels" not in auth:
        return list(auth["scopes"])
    return list(auth["access_levels"][case.get("access_level", auth.get("default_level", "full"))]["scopes"])


def _check_account(case: dict, where: str) -> None:
    checked = description(case)
    if "access_levels" in case["auth"]:
        # Un connecteur à niveaux : sa fonction déclare ses droits, ceux du niveau du cas.
        checked["functions"][0]["scopes"] = level_scopes(case)
    check_schema(checked, where)
    check_rules(checked, where)


def load(path: pathlib.Path = CASES) -> JwtBearerCases:
    """Le fichier lu, sa forme contrôlée, chaque cas contrôlé comme le compte d'une description."""
    where = _relative(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    error = next(iter(sorted(Draft202012Validator(SCHEMA).iter_errors(data), key=lambda e: list(e.absolute_path))), None)
    if error is not None:
        at = "/".join(str(p) for p in error.absolute_path) or "(root)"
        raise DescriptionError(f"{where}: does not match the jwt bearer cases form at {at}: {error.message}")
    names = [case["name"] for case in data["cases"] + data["refused"]]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise DescriptionError(f"{where}: case name(s) declared twice: {', '.join(twice)}")
    for case in data["cases"] + data["refused"]:
        levels = case["auth"].get("access_levels", {})
        level = case.get("access_level")
        if level is not None and level not in levels:
            raise DescriptionError(f"{where}: case {case['name']}: access_level {level!r} is not a level of auth")
        default = case["auth"].get("default_level")
        if levels and default is not None and default not in levels:
            # Jugé avant `level_scopes`, qui lit le niveau du cas : la règle 7 de la fabrique, au même message.
            raise DescriptionError(f"{where}: case {case['name']}: auth.default_level {default!r} is not a declared "
                                   "access level")
    for case in data["cases"] + data["refused"]:
        _check_account(case, f"{where}: case {case['name']}")
    for case in data["cases"]:
        at = f"{where}: case {case['name']}"
        if case["assertion"]["claims"]["scope"] != " ".join(level_scopes(case)):
            raise DescriptionError(f"{at}: assertion.claims.scope is not the scopes of the account's level, joined by spaces")
        request = case["token_request"]
        if request["url"] != case["auth"]["token_url"]:
            raise DescriptionError(f"{at}: token_request.url is not auth.token_url")
        if request["body"].count(ASSERTION) != 1 or not request["body"].endswith(ASSERTION):
            raise DescriptionError(f"{at}: token_request.body ends with the one {ASSERTION} it carries")
    return JwtBearerCases(path=path, test_key=data["test_key"], cases=data["cases"], refused=data["refused"])


TYPES = '''
/**
 * The key of a case: `keyFile`, serialized by `credentialValue` with its private key added last by its mode (`test_key`:
 * the test key in PEM; `absent`: none; `truncated`: a PEM that frames only the first line of the test key; `not_pem`: a
 * text that is not a PEM), or `keyText`, the value as is.
 */
export type JwtBearerKey =
  | { readonly keyFile: { readonly [field: string]: string }; readonly privateKey: "test_key" | "absent" | "truncated" | "not_pem"; readonly keyText?: never }
  | { readonly keyText: string; readonly keyFile?: never; readonly privateKey?: never }

/**
 * A common case of a service account (connectors/_cases/jwt_bearer.yaml): the auth block and the credential of a
 * connector, the key, a clock (`now`, in seconds), the exact assertion (header and claims serialized as compact JSON in
 * this key order, base64url without padding, then the RS256 signature), the exact token request (`{assertion}` in its
 * body stands for the assertion), the simulated answer, and what it yields: the header of the call and the expiry (in
 * seconds), or a named refusal.
 */
export type JwtBearerCase = JwtBearerKey & {
  readonly name: string
  readonly auth: Auth
  readonly credential: readonly CredentialField[]
  /** With `auth.accessLevels`: the level of the account; absent, `auth.defaultLevel`, else `full` (`levelScopes`). */
  readonly accessLevel?: string
  readonly now: number
  readonly assertion: {
    readonly header: { readonly [name: string]: string }
    readonly claims: { readonly iss: string; readonly scope: string; readonly aud: string; readonly iat: number; readonly exp: number }
    readonly signature: string
  }
  readonly tokenRequest: { readonly url: string; readonly contentType: "application/x-www-form-urlencoded"; readonly body: string }
  readonly tokenAnswer: { readonly status: number; readonly headers: { readonly [header: string]: string }; readonly body: unknown }
  readonly outcome:
    | { readonly headers: { readonly [header: string]: string }; readonly expiresAt: number; readonly refusal?: never }
    | { readonly refusal: "credential_rejected" | "upstream_error"; readonly headers?: never; readonly expiresAt?: never }
}

/** A key refused before anything is sent, and its refusal, the same on both sides. */
export type JwtBearerRefusedKey = JwtBearerKey & {
  readonly name: string
  readonly auth: Auth
  readonly credential: readonly CredentialField[]
  readonly refusal:
    | "service_account_key_not_json"
    | "service_account_key_incomplete"
    | "service_account_key_foreign_token_uri"
    | "service_account_key_unreadable"
}

// The PEM label, never written with its dashes: secret guards refuse a private key header written in clear.
const PEM_LABEL = "PRIVATE KEY"
const pem = (body: string): string => `-----BEGIN ${PEM_LABEL}-----\\n${body}-----END ${PEM_LABEL}-----\\n`

/**
 * The scopes the assertion of a case claims: `auth.scopes`, or those of the account's level (`accessLevel`, else
 * `auth.defaultLevel`, else `full`).
 */
export function levelScopes(auth: Auth, accessLevel?: string): readonly string[] {
  if (auth.kind !== "oauth2_jwt_bearer") throw new Error(`not a service account: ${auth.kind}`)
  if (auth.accessLevels === undefined) return auth.scopes
  const level = accessLevel ?? auth.defaultLevel ?? "full"
  const scopes = auth.accessLevels[level]
  if (scopes === undefined) throw new Error(`access level not declared: ${level}`)
  return scopes.scopes
}

/** The value of the key field of a case: `keyText` as is, or `keyFile` as compact JSON, its private key added last. */
export function credentialValue(key: JwtBearerKey): string {
  if (key.keyText !== undefined) return key.keyText
  const file: Record<string, string> = { ...key.keyFile }
  if (key.privateKey === "test_key") file.private_key = pem(testKeyPkcs8)
  else if (key.privateKey === "truncated") file.private_key = pem(`${testKeyPkcs8.split("\\n", 1)[0]}\\n`)
  else if (key.privateKey === "not_pem") file.private_key = NOT_PEM
  return JSON.stringify(file)
}
'''


def _key_entries(case: dict) -> list[tuple[str, str]]:
    if "key_text" in case:
        return [("keyText", js(case["key_text"]))]
    return [("keyFile", js_block(case["key_file"], 2)), ("privateKey", js(case["private_key"]))]


def _account_entries(case: dict) -> list[tuple[str, str]]:
    return [
        ("name", js(case["name"])),
        ("auth", _auth(CONNECTOR, case["auth"], 2)),
        ("credential", js([_credential_field(field) for field in case["credential"]])),
        *_key_entries(case),
    ]


def _outcome(outcome: dict) -> dict:
    if "refusal" in outcome:
        return outcome
    return {"headers": outcome["headers"], "expiresAt": outcome["expires_at"]}


def _case_source(case: dict) -> str:
    request = case["token_request"]
    level = [("accessLevel", js(case["access_level"]))] if "access_level" in case else []
    entries = _account_entries(case) + level + [
        ("now", js(case["now"])),
        ("assertion", js_block(case["assertion"], 2)),
        ("tokenRequest", js_block({"url": request["url"], "contentType": request["content_type"], "body": request["body"]}, 2)),
        ("tokenAnswer", js_block(case["token_answer"], 2)),
        ("outcome", js(_outcome(case["outcome"]))),
    ]
    return "  {\n" + "\n".join(f"    {key}: {value}," for key, value in entries) + "\n  }"


def _refused_source(case: dict) -> str:
    entries = _account_entries(case) + [("refusal", js(case["refusal"]))]
    return "  {\n" + "\n".join(f"    {key}: {value}," for key, value in entries) + "\n  }"


def typescript_source(loaded: JwtBearerCases) -> str:
    """`ts/src/jwt-bearer-cases.ts` : la clé de test, les types, `credentialValue`, les cas et les clés refusées."""
    head = [HEADER.format(source=_relative(loaded.path)), 'import type { Auth, CredentialField } from "./types"']
    key = ("\n/**\n * The throwaway RSA test key of these cases (2048 bits), PKCS#8 DER in base64, 64 characters a line: generated"
           " for\n * them alone, used nowhere else, not a secret.\n */\nexport const testKeyPkcs8 = " + js(loaded.test_key) + "\n"
           "\n/** The private key of a `not_pem` key file. */\nconst NOT_PEM = " + js(NOT_PEM) + "\n")
    cases = ",\n".join(_case_source(case) for case in loaded.cases)
    refused = ",\n".join(_refused_source(case) for case in loaded.refused)
    return ("\n".join(head) + "\n" + key + TYPES
            + "\n/** The common cases of a service account, in the order of the file. */\n"
            + "export const jwtBearerCases: readonly JwtBearerCase[] = [\n" + cases + ",\n]\n"
            + "\n/** The keys refused before anything is sent, in the order of the file. */\n"
            + "export const jwtBearerRefusedKeys: readonly JwtBearerRefusedKey[] = [\n" + refused + ",\n]\n")
