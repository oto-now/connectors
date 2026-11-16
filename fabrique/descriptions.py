"""Lecture et contrôle des fichiers de description.

Un fichier est lu, validé contre `connectors/connector.schema.json` (chaque exemple
contre l'entrée de sa fonction), puis contre les règles que le schéma JSON ne sait
pas dire :

1. chaque argument d'entrée va à un seul endroit de `call` (chemin, query, corps,
   en-tête ou partie fichier), chaque `{param}` du chemin et chaque valeur de `query`,
   `body`, `headers` et `files` nomme un argument déclaré, un argument du chemin est requis, aucun
   argument n'est sans place (l'argument qui forme le corps entier, règle 20, a la sienne), et le
   `request_param` d'une pagination est un argument (placé en query ou au corps, emboîté ou non) ;
2. chaque référence de `auth` (`key`, `token`, `username`, `password`, `client_id`,
   `client_secret`) nomme un champ de `credential` ; en `basic`, `username` est facultatif
   (absent, l'identifiant est vide), et nomme un champ quand il est déclaré ; en
   `oauth2_jwt_bearer`, `credential` n'a qu'un champ, secret, celui que nomme `key` (le
   fichier JSON entier de la clé du compte de service) ;
3. un nom côté API reçoit une seule valeur : une constante de query ne reprend pas un
   nom de query (au corps, la règle 22 le dit à toute profondeur), un en-tête constant (du connecteur ou de la
   fonction) ni celui de l'authentification ni un autre en-tête, à la casse près, et
   une clé passée en query (`auth.in: query`) n'est le nom d'aucun paramètre de
   query ; chaque clé de `call.encode` nomme un argument placé en query, en corps ou
   en en-tête ;
4. chaque contrôle (`checks`, `expect`) nomme un refus de sa fonction ; `equal_sums`
   porte sur un argument liste dont les éléments déclarent les deux champs ;
5. la sonde, et l'identité d'un `oauth2_user` lue par une fonction, nomment une
   fonction de lecture du connecteur, appelée par `call`, sans argument requis ; la
   fonction d'une sonde qui lit un solde (`balance`) ne déclare pas de coût (`cost`) :
   on ne paie pas pour savoir si l'on peut payer ; l'identité lue à une adresse complète exige des droits que le consentement
   demande (ses `scopes`, quand le connecteur n'a pas de niveaux ; à chaque niveau
   sinon, règle 7) ;
6. les réglages : un nom n'est déclaré qu'une fois parmi `credential`, `settings` et
   `from_token` ; le défaut d'une liste fermée en est une valeur ; un gabarit d'adresse
   (`base_url`, `token_url`, `authorize_url`) ne cite que des réglages déclarés (et,
   pour `base_url`, des valeurs de `from_token` et l'identité du compte, règle 8), une
   adresse libre en tête seulement, un autre réglage après `https://` seulement ; `base_urls` et `token_urls` portent
   sur une liste fermée et donnent une adresse à chacune de ses valeurs, et rien
   d'autre ; tout réglage déclaré est cité ;
7. les niveaux d'accès (`auth.access_levels`, voir docs/conception/niveaux-d-acces.md) :
   `scope_implies` est clos (un droit impliqué qui en implique d'autres les fait
   impliquer aussi par qui l'implique : lue en un pas ou par transitivité,
   l'implication dit la même chose) et chacune de ses clés est un droit d'un
   niveau ; `full` couvre chaque droit d'un niveau restreint et chaque droit exigé
   par une fonction (`full` ne filtre rien) ; l'identité du consentement et la
   sonde sont servies à chaque niveau ; `default_level` (compte de service) nomme
   un niveau déclaré ;
8. l'identité d'un compte rattaché (`account_identity`, l'identifiant du compte chez
   un tiers qui héberge les comptes de ses utilisateurs) : son nom n'est ni celui
   d'un champ de `credential`, ni d'un réglage, ni d'une valeur de `from_token` ;
   elle est citée par `base_url`, ou par chaque adresse de `base_urls`, après l'hôte
   seulement, et par aucune autre adresse ; aucun argument d'entrée, aucun nom côté
   API de `call` (paramètre du chemin, de query, de corps, en-tête), aucune constante
   ni aucun en-tête constant ne porte son nom : l'agent ne la fournit jamais ;
9. `act` suit la classe : jamais `false` sur une fonction `sensitive`, jamais `true`
   sur une fonction `read` ; absent, il vaut `false` pour `read`, `true` sinon ; `effect`
   (`send` ou `destroy`, ce que fait une fonction sensible une fois confirmée) n'est dit
   que d'une fonction `sensitive` ;
10. le signal de refus du service (une entrée de `errors` ou un refus qui porte
   `marks_account`, une condition sur le corps `body`) : un refus qui porte l'un ou
   l'autre a un statut pour `when`, jamais une condition en texte ; un statut 2xx
   exige une condition sur le corps (sinon chaque succès serait un refus) ; un 403
   sans condition sur le corps ne marque jamais `credential_rejected` (un droit
   manquant n'est pas une clé refusée), ni un 429 sans condition `quota_exhausted`
   (un débit dépassé n'est pas un crédit épuisé) ; une entrée qui marque le compte
   n'est pas rejouable ; dans une même table, une entrée à condition ne vient pas
   après une entrée sans condition qui partage un de ses statuts (elle ne serait
   jamais lue) ;
11. le délai propre à une fonction (`timeout_s`) remplace celui du connecteur pour
   chacun de ses appels, plus court ou plus long ; il n'est jamais égal au délai du
   connecteur (déclaré, ou 30 s par défaut) : il ne dirait rien ;
12. le coût (`cost`) : `items` et `times` (`per: item`) nomment deux arguments
   distincts de l'entrée, des listes (`type: array`) requises d'au moins un élément
   (`minItems` d'un ou plus), de sorte que le pire cas se calcule toujours, jamais
   nul ni deviné ; une fonction paginée ne coûte ni à l'élément ni à la quantité
   relue (`settle`) : chaque page est une requête, et une quantité lue dans une seule
   réponse ne dirait pas celle de l'appel ;
13. le travail asynchrone (`async`) : `collect` nomme une autre fonction de lecture du
   connecteur, appelée par `call`, qui n'est pas elle-même asynchrone ; `job_id.to`
   en est le seul argument requis (la relève se fait sans rien d'autre que
   l'identifiant du travail) ; quand le coût se règle (`settle`), la quantité se lit
   dans les réponses de la relève : elle n'est pas paginée et ne déclare pas de
   `settle` à elle ; le délai de relève (`within_s`, exigé par le schéma dans ce cas,
   sauf `no_deadline` quand la référence de l'éditeur dit qu'un travail n'a pas
   d'échéance) est plus long que le délai de la soumission, quand il est déclaré ;
14. les fichiers (`call.files`, une partie d'un corps multipart par argument qui
   porte l'identifiant d'un fichier en transit de l'appelant) : la méthode envoie un
   corps (`POST`, `PUT`, `PATCH`) ; la fonction n'est pas paginée ; chaque argument
   fichier est un texte (`type: string`), jamais encodé ; un nom de partie fichier
   n'est ni un nom de `body` ni une constante de corps (une partie, une valeur) ;
15. la donnée personnelle (`personal`) : dans l'entrée, sur une propriété seulement,
   jamais à la racine, et toujours `true` ; jamais dans le schéma de la sortie, où elle
   ne voudrait rien dire. L'hôte ne journalise pas sa valeur ;
16. le poids d'une valeur (`cost.weights`, à la place de `times`) : `field` nomme un
   champ de chaque élément de `items`, requis dans l'élément, une liste d'au moins un
   élément aux valeurs uniques, dont les valeurs admises (`enum`) sont exactement les
   clés de la table de poids : une valeur sans poids est refusée par le schéma
   d'entrée, avant l'appel, jamais comptée pour rien, et un poids ne vaut que pour une
   valeur admise ;
17. le fichier rendu (`output.file`) : il exclut `items`, `strip`, `projection` et
   `schema` (`file_output_not_alone` : un fichier est toute la sortie), une pagination, `async` et le rôle de
   relève d'un travail asynchrone ; `path` est exigé avec `url`, `base64` et `base64url`, refusé avec
   `body`, `name.path` aussi (un corps binaire n'a pas de JSON) ; `auth` ne vaut qu'avec
   `from: url` (`file_auth_not_url`), `name.header` qu'avec `body` ou `url` ; le gabarit du nom ne cite que des
   arguments requis de l'entrée et finit par une extension déclarée ; aucun type
   déclaré n'est un type qu'un navigateur rend en page ou exécute (HTML, SVG, XML,
   JavaScript) ; `expect` est refusé avec `from: body`. `from: json` (la réponse JSON, ou sa branche au chemin
   facultatif `path`, écrite en JSON) a son type, `application/json` : `types` est refusé (`file_json_types`) ; son
   nom est le gabarit seul, qui finit par `.json` (`file_json_name`) ; `max_bytes` va jusqu'à 4 Mo, 4 194 304
   octets, le défaut (`file_json_max_bytes`). Les codes sont ceux des cas communs (`connectors/_cases/file.yaml`),
   jugés avant les autres refus de la règle, dans cet ordre. La forme (extensions, types MIME sans paramètre,
   `max_bytes` de 1 octet à 50 Mo, `types` exigé hors `from: json`) est celle du schéma. Une fonction longue
   (`background`, règle 21) rend un fichier comme un appel synchrone : le fichier vient du même appel ;
18. retirer de la sortie (`output.strip`) : chaque entrée est un chemin, des clés non vides séparées par des points
   (`strip_path_malformed`), déclaré une fois (`strip_path_twice`), jamais sous un autre chemin de la liste, qui le
   retire déjà en entier (`strip_path_overlaps`). Le schéma dit déjà la forme et le doublon ; la règle les redit
   avec leur code, le même que celui des cas communs (`connectors/_cases/strip.yaml`) ;
19. l'exemple de réponse (`output.sample`), une garde de la fabrique seulement : exigé dès qu'un chemin de `strip`
   porte un point (`strip_path_unsampled`), refusé sinon (`strip_sample_unused`) ; il ne contient que des valeurs
   inventées, aucune adresse (`scheme://hôte`, courriel) hors des domaines réservés aux tests — `.test`,
   `.example`, `.invalid`, `localhost`, `example.com`, `.net`, `.org` — ni identifiants dans une adresse
   (`strip_sample_foreign_address`, le message dit où, jamais la valeur) ; chaque chemin de `strip` y désigne une
   clé, dans au moins un élément, relatif à `items` et à travers les listes comme à l'exécution
   (`strip_path_absent`). L'exemple ne sort ni en TypeScript ni en Python : ni contrat de réponse, ni validation à
   l'exécution, ni documentation, ni source de types ;
20. le corps entier (`call.body` qui nomme un argument au lieu d'une table) : l'argument est déclaré
   (`body_argument_unknown`) ; c'est un objet strict, `type: object`, `additionalProperties: false` ou, à sa racine
   seulement, des clés libres toutes donnée personnelle, exactement `{type: string, personal: true}` (un texte) ou
   `{type: [string, number, boolean], personal: true}` (une valeur simple), des `properties` déclarées, au moins une,
   sans `patternProperties` (`body_argument_not_strict`) : jamais un sac libre ;
   il est requis (`body_argument_optional`) ; la méthode envoie un corps, jamais `GET` (`body_argument_method`) ;
   il ne va nulle part ailleurs, ni chemin, ni query, ni en-tête, ni partie fichier (`body_argument_elsewhere`) ;
   `encode` ne le nomme pas (`body_argument_encoded`) ; rien d'autre n'entre au corps, ni constante de corps ni
   partie fichier (`body_argument_not_alone`) ; la fonction n'est pas paginée (`body_argument_paginated`). Ce que
   l'hôte envoie est l'objet validé, tel quel, en JSON. Les codes sont ceux des cas communs
   (`connectors/_cases/body.yaml`) ;
21. la fonction longue (`background: true`, que l'hôte exécute en tâche de fond et dont il rend aussitôt un identifiant
   de travail) : elle déclare son propre délai, `timeout_s`, celui de l'appel lui-même, file non comprise
   (`background_no_timeout` ; la règle 11 vaut telle quelle) ; elle n'est pas asynchrone (`background_async` : un
   travail que tient le tiers a sa relève) ; elle n'est la relève d'aucun travail asynchrone (`background_collect`) ;
   elle n'est pas paginée (`background_paginated`). Les codes sont ceux des cas communs
   (`connectors/_cases/background.yaml`), jugés dans cet ordre ; `class`, `act`, `confirm`, `cost` et `output.file` ne
   changent pas ;
22. le corps emboîté (`call.body` en table dont une valeur est elle-même une table, décidé par le mainteneur le 2026-10-26) :
   une table emboîtée porte au moins une entrée (`body_nested_empty`) ; aucun chemin n'est à la fois constant
   (`constants.body`) et argument, ni l'un sous l'autre : la table et la constante ne se rejoignent que par des objets
   des deux côtés (`body_key_twice`, au premier niveau comme au-dessous) ; un corps multipart (`files`) n'a pas de table
   emboîtée, ses parties sont des textes (`body_nested_multipart`). Ce que l'hôte envoie : la constante du corps, puis
   chaque argument donné à son chemin, les objets parents créés au besoin, un argument absent ne créant rien. Les codes
   sont ceux des cas communs (`connectors/_cases/nested_body.yaml`), jugés dans cet ordre.
23. le corps en liste (`call.body_list: true`, décidé par le responsable produit le 2026-11-11) : le corps construit, en
   table, emboîté ou entier, part seul élément d'une liste JSON. La fonction a un corps (une table non vide, l'argument
   du corps entier ou une constante du corps : `body_list_without_body`), une méthode qui en envoie un, jamais `GET`
   (`body_list_method`), et pas de partie fichier, dont le corps est multipart (`body_list_multipart`). Les codes sont
   ceux des cas communs (`connectors/_cases/list_body.yaml`), jugés dans cet ordre.

Un fichier fautif lève `DescriptionError`, qui nomme le fichier, la fonction et la
règle : la fabrique ne génère alors rien.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass

import yaml
from jsonschema import Draft202012Validator

REPO = pathlib.Path(__file__).resolve().parent.parent
CONNECTORS = REPO / "connectors"
SCHEMA_PATH = CONNECTORS / "connector.schema.json"

PATH_PARAM = re.compile(r"\{([^{}]*)\}")
# Le délai d'un appel quand le connecteur n'en déclare pas (le `default` du schéma).
DEFAULT_TIMEOUT_S = 30
AUTH_REFERENCES = ("key", "token", "username", "password", "client_id", "client_secret")
PLACES = ("query", "body", "headers", "files")
# Les méthodes qui n'envoient pas de corps : l'argument qui forme le corps entier n'y a pas de place (règle 20).
NO_BODY_METHODS = ("GET",)
# Les méthodes qui envoient un corps multipart.
FILE_METHODS = ("POST", "PUT", "PATCH")
# Ce qui précède l'identité d'un compte dans une adresse : le schéma et l'hôte (ou une adresse libre en tête), puis `/`.
AFTER_HOST = re.compile(r"^(https://[^/{}]+|\{[a-z][a-z0-9_]*\})/")


class DescriptionError(ValueError):
    """Une description refusée : le message dit le fichier, la fonction et la règle."""


def body_argument(call: dict) -> str | None:
    """L'argument qui forme le corps entier (`call.body` en texte, règle 20), ou `None` pour un corps en table."""
    body = call.get("body")
    return body if isinstance(body, str) else None


def body_entries(table: dict, at: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    """Les entrées d'un corps en table, à toute profondeur (règle 22) : le chemin des clés côté API et l'argument, dans
    l'ordre du fichier ; une table emboîtée vide n'en donne aucune."""
    entries: list[tuple[tuple[str, ...], str]] = []
    for key, value in table.items():
        if isinstance(value, dict):
            entries += body_entries(value, at + (key,))
        else:
            entries.append((at + (key,), value))
    return entries


def argument_table(call: dict, kind: str) -> dict:
    """La table d'une place de `call` (`query`, `body`, `headers`, `files`) : nom côté API → argument ; vide quand la place
    est absente, ou quand le corps est un argument entier (`body_argument`), qui n'a pas de nom côté API. Celle du corps
    est mise à plat (`body_entries`) : un chemin emboîté s'y nomme par ses clés jointes d'un point, pour les messages."""
    table = call.get(kind)
    if not isinstance(table, dict):
        return {}
    if kind == "body":
        return {".".join(path): argument for path, argument in body_entries(table)}
    return table


# Les refus de la règle 20, les mêmes des deux côtés (cas communs `connectors/_cases/body.yaml`), dans l'ordre où ils
# se jugent.
BODY_REFUSALS = ("body_argument_unknown", "body_argument_not_strict", "body_argument_optional", "body_argument_method",
                 "body_argument_elsewhere", "body_argument_encoded", "body_argument_not_alone", "body_argument_paginated")


# Les clés libres de l'argument du corps entier, à sa racine seulement : au lieu de `additionalProperties: false`,
# chaque clé que `properties` ne déclare pas est marquée donnée personnelle (règle 15 : l'hôte ne journalise pas sa
# valeur), de l'un des deux genres que la description choisit. Les clés déclarées gardent leur schéma.
# - le texte (décidé par le mainteneur le 2026-10-22) : `{type: string, personal: true}` ;
# - la valeur simple (décidé par le mainteneur le 2026-10-22) : un texte, un nombre ou un vrai/faux, pour des champs que le
#   tiers type (les champs personnalisés d'un CRM). `number`, pas `integer` : un champ nombre du tiers prend une
#   décimale, et JSON ne distingue pas `2` de `2.0`. Une date reste un texte ; `null`, un objet, une liste restent
#   refusés. Les trois types dans cet ordre, l'écriture unique que les deux juges comparent telle quelle.
# La forme exacte, rien d'autre : ni `true`, ni un autre type, ni un schéma sans `personal`, ni un mot de plus.
FREE_TEXT_KEYS = {"type": "string", "personal": True}
FREE_SIMPLE_KEYS = {"type": ["string", "number", "boolean"], "personal": True}


def free_keys(additional: object) -> bool:
    """`additionalProperties` qui admet des clés libres, toutes donnée personnelle : exactement `FREE_TEXT_KEYS` ou
    `FREE_SIMPLE_KEYS`, `personal` le booléen `true` (jamais `1`, que `==` confondrait)."""
    return (isinstance(additional, dict) and additional.keys() == FREE_TEXT_KEYS.keys()
            and additional["personal"] is True
            and any(additional["type"] == form["type"] for form in (FREE_TEXT_KEYS, FREE_SIMPLE_KEYS)))


def strict_object(schema: object) -> bool:
    """Un objet strict : `type: object`, `additionalProperties: false` ou des clés libres toutes donnée personnelle
    (`free_keys`), au moins une propriété déclarée, aucune clé admise par motif (`patternProperties` rouvrirait
    l'objet)."""
    if not isinstance(schema, dict):
        return False
    additional = schema.get("additionalProperties")
    return (schema.get("type") == "object" and (additional is False or free_keys(additional))
            and isinstance(schema.get("properties"), dict) and len(schema["properties"]) >= 1
            and "patternProperties" not in schema)


def body_argument_refusal(function: dict) -> tuple[str, str] | None:
    """Le premier défaut d'un corps formé par un argument entier, `(code, raison)`, ou `None` : la règle 20, hors de
    toute description, que les cas communs rejouent."""
    call = function["call"]
    argument = body_argument(call)
    if argument is None:
        return None
    properties = function["input"]["properties"]
    if argument not in properties:
        return ("body_argument_unknown", "is not an input argument")
    if not strict_object(properties[argument]):
        return ("body_argument_not_strict",
                "must be a strict object (type: object, additionalProperties: false or free keys "
                "{type: string, personal: true} or {type: [string, number, boolean], personal: true}, at least one "
                "declared property, no patternProperties): the body is what the agent may send, never a free bag")
    if argument not in function["input"].get("required", []):
        return ("body_argument_optional", "must be required: the whole body is never absent")
    if call["method"] in NO_BODY_METHODS:
        return ("body_argument_method", f"needs a method that sends a body, not {call['method']}")
    elsewhere = PATH_PARAM.findall(call["path"])
    for kind in ("query", "headers", "files"):
        elsewhere += argument_table(call, kind).values()
    if argument in elsewhere:
        return ("body_argument_elsewhere", "is the whole body, so it goes nowhere else (path, query, headers, files)")
    if argument in (call.get("encode") or {}):
        return ("body_argument_encoded", "is sent as the JSON object it is, never encoded")
    if "body" in (call.get("constants") or {}) or "files" in call:
        return ("body_argument_not_alone", "is the whole body: no body constant nor file part joins it")
    if "pagination" in function:
        return ("body_argument_paginated", "a function whose body is one argument is not paginated")
    return None


# Les refus de la règle 22, les mêmes des deux côtés (cas communs `connectors/_cases/nested_body.yaml`), dans l'ordre où
# ils se jugent.
NESTED_BODY_REFUSALS = ("body_nested_empty", "body_key_twice", "body_nested_multipart")


def _empty_table(table: dict, at: tuple[str, ...]) -> tuple[str, ...] | None:
    """Le chemin de la première table emboîtée vide, ou `None`."""
    for key, value in table.items():
        if isinstance(value, dict):
            found = (at + (key,)) if not value else _empty_table(value, at + (key,))
            if found is not None:
                return found
    return None


def _clash(table: dict, constant: object, at: tuple[str, ...]) -> tuple[str, ...] | None:
    """Le premier chemin où la table et la constante du corps se rencontrent autrement que par deux objets : un argument
    là où la constante a une valeur, ou une table emboîtée sous une constante qui n'est pas un objet ; `None` sinon."""
    if not isinstance(constant, dict):
        return None
    for key, value in table.items():
        if key not in constant:
            continue
        if not (isinstance(value, dict) and isinstance(constant[key], dict)):
            return at + (key,)
        found = _clash(value, constant[key], at + (key,))
        if found is not None:
            return found
    return None


def body_table_refusal(function: dict) -> tuple[str, str, str] | None:
    """Le premier défaut d'un corps en table, `(code, chemin, raison)`, ou `None` : la règle 22, hors de toute
    description, que les cas communs rejouent ; un corps formé d'un argument entier relève de la règle 20."""
    call = function["call"]
    table = call.get("body")
    if not isinstance(table, dict):
        return None
    empty = _empty_table(table, ())
    if empty is not None:
        return ("body_nested_empty", ".".join(empty), "is a nested table without any entry: it places nothing")
    clash = _clash(table, (call.get("constants") or {}).get("body"), ())
    if clash is not None:
        return ("body_key_twice", ".".join(clash),
                "is both a body constant and an argument, or one is under the other: they meet only through objects")
    if "files" in call:
        nested = next((path for path, _ in body_entries(table) if len(path) > 1), None)
        if nested is not None:
            return ("body_nested_multipart", ".".join(nested),
                    "is nested in a multipart body, whose parts are texts: a file part rules out a nested table")
    return None


# Les refus de la règle 23, les mêmes des deux côtés (cas communs `connectors/_cases/list_body.yaml`), dans l'ordre où
# ils se jugent.
LIST_BODY_REFUSALS = ("body_list_without_body", "body_list_method", "body_list_multipart")


def body_list_refusal(function: dict) -> tuple[str, str] | None:
    """Le premier défaut d'un corps en liste (`call.body_list`), `(code, raison)`, ou `None` : la règle 23, hors de toute
    description, que les cas communs rejouent."""
    call = function["call"]
    if call.get("body_list") is not True:
        return None
    if not (call.get("body") or "body" in (call.get("constants") or {})):
        return ("body_list_without_body", "sends the body as a list, and the function has no body to put in it")
    if call["method"] in NO_BODY_METHODS:
        return ("body_list_method", f"needs a method that sends a body, not {call['method']}")
    if "files" in call:
        return ("body_list_multipart", "a multipart body (files) is a form, never a JSON list")
    return None


# Les refus de la règle 21, les mêmes des deux côtés (cas communs `connectors/_cases/background.yaml`), dans l'ordre où
# ils se jugent.
BACKGROUND_REFUSALS = ("background_no_timeout", "background_async", "background_collect", "background_paginated")


def background_refusal(function: dict, functions: list[dict]) -> tuple[str, str] | None:
    """Le premier défaut d'une fonction longue (`background: true`), `(code, raison)`, ou `None` : la règle 21, parmi les
    fonctions de son connecteur (une relève se lit sur la fonction qui soumet), que les cas communs rejouent."""
    if function.get("background") is not True:
        return None
    if "timeout_s" not in function:
        return ("background_no_timeout", "declares its own timeout_s, the delay of the call itself, queue not included")
    if "async" in function:
        return ("background_async", "is not asynchronous: a job the vendor holds has its collect, the host holds none")
    submitter = next((f["name"] for f in functions if (f.get("async") or {}).get("collect") == function["name"]), None)
    if submitter is not None:
        return ("background_collect", f"collects the asynchronous job of {submitter!r}, which the vendor holds")
    if "pagination" in function:
        return ("background_paginated", "is not paginated: one call, one answer")
    return None


@dataclass(frozen=True)
class Description:
    path: pathlib.Path
    data: dict

    @property
    def name(self) -> str:
        return self.data["connector"]["name"]


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _relative(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def check_schema(data: dict, where: str) -> None:
    """Le schéma du format, puis chaque exemple contre l'entrée de sa fonction."""
    errors = sorted(Draft202012Validator(schema()).iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        at = "/".join(str(p) for p in first.absolute_path) or "(root)"
        raise DescriptionError(f"{where}: does not match the description schema at {at}: {first.message}")
    for function in data["functions"]:
        validator = Draft202012Validator(function["input"])
        for example in function["examples"]:
            error = next(iter(validator.iter_errors(example["input"])), None)
            if error is not None:
                raise DescriptionError(
                    f"{where}: function {function['name']}: example {example['title']!r} "
                    f"does not match its input: {error.message}"
                )


def check_arguments(function: dict, where: str) -> None:
    """Règle 1 : un argument va à un seul endroit, et tout endroit nomme un argument."""
    call = function.get("call")
    if call is None:
        return
    name = function["name"]
    declared = set(function["input"]["properties"])
    required = set(function["input"].get("required", []))
    placed: dict[str, str] = {}

    def place(argument: str, at: str) -> None:
        if argument not in declared:
            raise DescriptionError(f"{where}: function {name}: {at} names {argument!r}, which is not an input argument")
        if argument in placed:
            raise DescriptionError(
                f"{where}: function {name}: argument {argument!r} goes to two places ({placed[argument]} and {at})"
            )
        placed[argument] = at

    whole = body_argument(call)
    refusal = body_argument_refusal(function)
    if refusal is not None:
        code, reason = refusal
        raise DescriptionError(f"{where}: function {name}: call.body {whole!r}: {code}: {reason}")
    if whole is not None:
        place(whole, "the whole body")
    nested = body_table_refusal(function)
    if nested is not None:
        code, path, reason = nested
        raise DescriptionError(f"{where}: function {name}: call.body {path!r}: {code}: {reason}")
    listed = body_list_refusal(function)
    if listed is not None:
        code, reason = listed
        raise DescriptionError(f"{where}: function {name}: call.body_list: {code}: {reason}")
    for argument in PATH_PARAM.findall(call["path"]):
        place(argument, "the path")
        if argument not in required:
            raise DescriptionError(f"{where}: function {name}: path argument {argument!r} must be required")
    for kind in PLACES:
        for api_name, argument in argument_table(call, kind).items():
            place(argument, f"{kind} {api_name!r}")
    pagination = function.get("pagination")
    unplaced = declared - set(placed)
    if unplaced:
        raise DescriptionError(
            f"{where}: function {name}: argument(s) {', '.join(sorted(unplaced))} go nowhere in call"
        )
    if pagination and pagination["request_param"] not in declared:
        raise DescriptionError(
            f"{where}: function {name}: pagination request_param {pagination['request_param']!r} is not an input argument"
        )
    for argument in call.get("encode") or {}:
        if argument not in placed or placed[argument] == "the path" or placed[argument].startswith("files "):
            raise DescriptionError(
                f"{where}: function {name}: encode names {argument!r}, which is not placed in query, body or headers"
                " (a file is never encoded)"
            )


def check_auth(connector: dict, where: str) -> None:
    """Règle 2 : une référence de `auth` désigne un champ de `credential` existant ; en `oauth2_jwt_bearer`, le seul
    champ, secret."""
    auth = connector["auth"]
    fields = {field["name"] for field in connector.get("credential", [])}
    for reference in AUTH_REFERENCES:
        if reference in auth and auth[reference] not in fields:
            raise DescriptionError(
                f"{where}: auth.{reference} names {auth[reference]!r}, which is not a credential field "
                f"({', '.join(sorted(fields)) or 'none declared'})"
            )
    if auth["kind"] == "oauth2_jwt_bearer":
        others = sorted(fields - {auth["key"]})
        if others:
            raise DescriptionError(
                f"{where}: auth.kind oauth2_jwt_bearer takes a single credential field, the key file named by auth.key "
                f"({auth['key']!r}); remove {', '.join(others)}"
            )
        field = next(f for f in connector["credential"] if f["name"] == auth["key"])
        if field["secret"] is not True:
            raise DescriptionError(f"{where}: auth.key names {auth['key']!r}, which must be a secret field (secret: true)")


def _auth_header(auth: dict) -> str | None:
    if auth["kind"] == "api_key":
        return auth["name"] if auth["in"] == "header" else None
    if auth["kind"] in ("bearer", "basic", "oauth2_client_credentials", "oauth2_user", "oauth2_jwt_bearer"):
        return "Authorization"
    return None


def _auth_query(auth: dict) -> str | None:
    return auth["name"] if auth["kind"] == "api_key" and auth["in"] == "query" else None


def check_constants(connector: dict, function: dict, where: str) -> None:
    """Règle 3 : un nom côté API, une seule valeur."""
    call = function.get("call")
    if call is None:
        return
    name = function["name"]
    constants = call.get("constants") or {}
    # Au corps, la règle 22 juge la constante et la table à toute profondeur (`body_key_twice`).
    for api_name in constants.get("query") or {}:
        if api_name in argument_table(call, "query"):
            raise DescriptionError(f"{where}: function {name}: query {api_name!r} is both a constant and an argument")
    auth_query = _auth_query(connector["auth"])
    if auth_query and (auth_query in (call.get("query") or {}) or auth_query in (constants.get("query") or {})):
        raise DescriptionError(
            f"{where}: function {name}: query {auth_query!r} is set twice (the authentication and the function)"
        )
    seen: dict[str, str] = {}
    auth_header = _auth_header(connector["auth"])
    if auth_header:
        seen[auth_header.lower()] = "the authentication"
    sources = [
        ("the connector headers", connector.get("headers") or {}),
        ("the function constant headers", constants.get("headers") or {}),
        ("the function headers", call.get("headers") or {}),
    ]
    for source, headers in sources:
        for header in headers:
            if header.lower() in seen:
                raise DescriptionError(
                    f"{where}: function {name}: header {header!r} is set twice ({seen[header.lower()]} and {source})"
                )
            seen[header.lower()] = source


def act(function: dict) -> bool:
    """Ce qui engage ou détruit : `act` déclaré, sinon déduit de la classe (`read` → false, `write` et `sensitive` → true)."""
    return function.get("act", function["class"] != "read")


def check_act(function: dict, where: str) -> None:
    """Règle 9 : `act` ne contredit pas la classe, et `effect` n'est dit que d'une fonction sensible."""
    declared = function.get("act")
    if declared is False and function["class"] == "sensitive":
        raise DescriptionError(f"{where}: function {function['name']}: a sensitive function cannot declare act: false")
    if declared is True and function["class"] == "read":
        raise DescriptionError(f"{where}: function {function['name']}: a read function cannot declare act: true")
    if "effect" in function and function["class"] != "sensitive":
        raise DescriptionError(f"{where}: function {function['name']}: effect is said of a sensitive function only")


def check_timeout(connector: dict, function: dict, where: str) -> None:
    """Règle 11 : le délai d'une fonction n'est pas celui du connecteur."""
    own = function.get("timeout_s")
    if own is not None and own == connector.get("timeout_s", DEFAULT_TIMEOUT_S):
        raise DescriptionError(
            f"{where}: function {function['name']}: timeout_s {own} is the connector's timeout: declare none"
        )


def check_background(data: dict, function: dict, where: str) -> None:
    """Règle 21 : une fonction longue a son propre délai, et n'est ni asynchrone, ni une relève, ni paginée."""
    refusal = background_refusal(function, data["functions"])
    if refusal is not None:
        code, reason = refusal
        raise DescriptionError(f"{where}: function {function['name']}: background: {code}: {reason}")


def _check_cost_list(function: dict, key: str, where: str) -> None:
    """Un argument que le coût compte : une liste requise, d'au moins un élément."""
    argument = function["cost"][key]
    schema = function["input"]["properties"].get(argument)
    if schema is None:
        raise DescriptionError(f"{where}: function {function['name']}: cost.{key} names {argument!r}, which is not an input argument")
    listed = schema.get("type") == "array" and isinstance(schema.get("minItems"), int) and schema["minItems"] >= 1
    if not listed or argument not in function["input"].get("required", []):
        raise DescriptionError(
            f"{where}: function {function['name']}: cost.{key} {argument!r} must be a required list argument "
            "(type: array, minItems 1 or more), so that the worst case is never zero nor guessed"
        )


def _check_cost_weights(function: dict, where: str) -> None:
    """Règle 16, le poids d'une valeur : le champ qui porte les valeurs est, dans chaque élément de `items`, une liste requise
    d'au moins un élément, aux valeurs uniques, dont les valeurs admises sont exactement les clés de la table."""
    cost = function["cost"]
    field = cost["weights"]["field"]
    element = function["input"]["properties"][cost["items"]].get("items")
    prefix = f"{where}: function {function['name']}: cost.weights.field {field!r}"
    if not isinstance(element, dict) or not isinstance(element.get("properties"), dict) or field not in element["properties"]:
        raise DescriptionError(f"{prefix} must be a property of each element of {cost['items']!r} (an object schema)")
    schema = element["properties"][field]
    listed = (schema.get("type") == "array" and isinstance(schema.get("minItems"), int) and schema["minItems"] >= 1
              and schema.get("uniqueItems") is True)
    if not listed or field not in element.get("required", []):
        raise DescriptionError(
            f"{prefix} must be a required list of each element (type: array, minItems 1 or more, uniqueItems: true), "
            "so that each element counts at least one weight, each value once"
        )
    allowed = schema.get("items", {}).get("enum") if isinstance(schema.get("items"), dict) else None
    table = set(cost["weights"]["values"])
    if not isinstance(allowed, list) or not all(isinstance(v, str) for v in allowed) or set(allowed) != table:
        raise DescriptionError(
            f"{prefix}: its allowed values (items.enum, texts) must be exactly the keys of cost.weights.values "
            f"({', '.join(sorted(table))}), so that a value without a weight is refused before the call"
        )


def check_cost(function: dict, where: str) -> None:
    """Règle 12 : le coût à l'élément compte des listes requises et non vides, distinctes ; ni l'élément ni la quantité
    relue sur une fonction paginée. Les valeurs pesées dans chaque élément sont la règle 16."""
    cost = function.get("cost")
    if cost is None:
        return
    name = function["name"]
    if cost["per"] == "item":
        _check_cost_list(function, "items", where)
        if "weights" in cost:
            _check_cost_weights(function, where)
        if "times" in cost:
            if cost["times"] == cost["items"]:
                raise DescriptionError(f"{where}: function {name}: cost.times names the items argument {cost['items']!r} again")
            _check_cost_list(function, "times", where)
    if "pagination" in function and (cost["per"] == "item" or "settle" in cost):
        raise DescriptionError(
            f"{where}: function {name}: a paginated function costs per request or per page, without settle: "
            "each page is a request, and one response does not tell the quantity of the call"
        )


def check_async(data: dict, function: dict, where: str) -> None:
    """Règle 13 : la relève d'un travail asynchrone est une autre lecture du connecteur, appelable avec l'identifiant du
    travail seul ; une quantité réglée se lit dans ses réponses, dans le délai de relève."""
    declared = function.get("async")
    if declared is None:
        return
    name = function["name"]
    collect = next((f for f in data["functions"] if f["name"] == declared["collect"]), None)
    if collect is None or collect is function:
        raise DescriptionError(f"{where}: function {name}: async.collect names {declared['collect']!r}, which is not another function of the connector")
    if collect["class"] != "read" or "call" not in collect or "async" in collect:
        raise DescriptionError(
            f"{where}: function {name}: async.collect {collect['name']!r} must be a read function with a call, not asynchronous itself"
        )
    to = declared["job_id"]["to"]
    if collect["input"].get("required", []) != [to] or to not in collect["input"]["properties"]:
        raise DescriptionError(
            f"{where}: function {name}: async.job_id.to {to!r} must be the only required argument of {collect['name']!r}"
        )
    timeout = function.get("timeout_s", data["connector"].get("timeout_s", DEFAULT_TIMEOUT_S))
    if "within_s" in declared and declared["within_s"] <= timeout:
        raise DescriptionError(
            f"{where}: function {name}: async.within_s {declared['within_s']} must exceed the timeout of the submission ({timeout} s)"
        )
    if "settle" in function.get("cost", {}) and ("pagination" in collect or "settle" in collect.get("cost", {})):
        raise DescriptionError(
            f"{where}: function {name}: its quantity is read in the responses of {collect['name']!r}, "
            "which must be neither paginated nor settled on its own"
        )


def check_files(function: dict, where: str) -> None:
    """Règle 14 : les parties fichier d'un corps multipart, dans une requête qui envoie un corps, une fois, sans
    pagination ; l'argument d'une partie porte l'identifiant d'un fichier en transit, un texte."""
    call = function.get("call") or {}
    files = call.get("files")
    if not files:
        return
    name = function["name"]
    if call["method"] not in FILE_METHODS:
        raise DescriptionError(f"{where}: function {name}: call.files needs a method that sends a body ({', '.join(FILE_METHODS)})")
    if "pagination" in function:
        raise DescriptionError(f"{where}: function {name}: a function that sends files is not paginated: a file is sent once")
    texts = set(argument_table(call, "body")) | set((call.get("constants") or {}).get("body") or {})
    for part, argument in files.items():
        if part in texts:
            raise DescriptionError(f"{where}: function {name}: multipart part {part!r} is both a file and a body value")
        if function["input"]["properties"][argument].get("type") != "string":
            raise DescriptionError(
                f"{where}: function {name}: file argument {argument!r} must be type: string, the id of a transit file"
            )


# Les types qu'un navigateur rend en page ou exécute : jamais le type d'un fichier rendu, que l'hôte sert à une personne.
PAGE_OR_SCRIPT_TYPES = frozenset({
    "text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml", "text/xsl",
    "text/javascript", "application/javascript", "application/x-javascript", "text/ecmascript",
    "application/ecmascript", "text/jscript", "text/livescript", "text/vbscript", "application/wasm",
})
# Les sorties qu'un fichier exclut : il est toute la sortie de la fonction.
NOT_WITH_FILE = ("items", "strip", "projection", "schema")
EXTENSION_AT_END = re.compile(r"\.([a-z0-9]{1,16})$")


# Le plafond d'un fichier rendu, et son défaut : 50 Mo, celui d'un fichier chez l'hôte d'oto 2 (le `default` du schéma).
DEFAULT_MAX_BYTES = 52_428_800
# Le plafond d'un fichier `from: json`, et son défaut : 4 Mo, celui d'une réponse JSON chez l'hôte d'oto 2.
JSON_MAX_BYTES = 4_194_304
# Les refus de la règle 17 qui portent un code, les mêmes des deux côtés (cas communs `connectors/_cases/file.yaml`), dans
# l'ordre où ils se jugent.
FILE_DECLARATION_REFUSALS = ("file_output_not_alone", "file_auth_not_url", "file_json_types", "file_json_name",
                             "file_json_max_bytes")


def output_file(function: dict) -> dict | None:
    """Le fichier rendu d'une fonction, ses défauts résolus (`auth: none` d'une adresse, `max_bytes` de 50 Mo), ses clés
    dans l'ordre du format : la forme que portent les deux sorties, pour que l'hôte et la lib n'aient aucun défaut à
    connaître."""
    file = (function.get("output") or {}).get("file")
    if file is None:
        return None
    resolved: dict = {"from": file["from"]}
    if "path" in file:
        resolved["path"] = file["path"]
    if file["from"] == "url" or "auth" in file:
        resolved["auth"] = file.get("auth", "none")
    if "types" in file:
        resolved["types"] = file["types"]
    resolved["name"] = {key: file["name"][key] for key in ("header", "path", "template") if key in file["name"]}
    resolved["max_bytes"] = file.get("max_bytes", JSON_MAX_BYTES if file["from"] == "json" else DEFAULT_MAX_BYTES)
    return resolved


def page_or_script(mime: str) -> bool:
    """Un type qu'un navigateur rend en page ou exécute : HTML, SVG, tout XML, JavaScript."""
    return mime in PAGE_OR_SCRIPT_TYPES or mime.endswith("+xml") or mime.endswith("/xml")


def output_file_refusal(function: dict) -> tuple[str, str] | None:
    """Le premier refus à code d'un fichier rendu, `(code, raison)`, ou `None` : la part de la règle 17 que les cas
    communs rejouent, hors de toute description."""
    output = function.get("output") or {}
    file = output.get("file")
    if file is None:
        return None
    if any(key in output for key in NOT_WITH_FILE):
        return ("file_output_not_alone", "excludes items, strip, projection and schema: the file is the whole output")
    source = file["from"]
    if "auth" in file and source != "url":
        return ("file_auth_not_url", "auth is only for from: url: nothing else is downloaded")
    if source != "json":
        return None
    if "types" in file:
        return ("file_json_types", "types is refused with from: json, whose type is application/json")
    naming = file["name"]
    if set(naming) != {"template"} or not naming["template"].endswith(".json"):
        return ("file_json_name", "the name of a JSON file is its template alone, which ends with .json")
    if file.get("max_bytes", JSON_MAX_BYTES) > JSON_MAX_BYTES:
        return ("file_json_max_bytes", f"max_bytes is {JSON_MAX_BYTES} at most with from: json, that of a JSON answer")
    return None


def check_output_file(data: dict, function: dict, where: str) -> None:
    """Règle 17 : un fichier rendu est toute la sortie d'un seul appel ; ses octets, son nom et ses types se lisent là
    où la réponse les porte, et son nom se fabrique toujours des arguments requis."""
    file = (function.get("output") or {}).get("file")
    if file is None:
        return
    name = function["name"]
    at = f"{where}: function {name}: output.file"
    refusal = output_file_refusal(function)
    if refusal is not None:
        code, reason = refusal
        raise DescriptionError(f"{at}: {code}: {reason}")
    if "pagination" in function:
        raise DescriptionError(f"{where}: function {name}: a function that renders a file is not paginated: one call, one file")
    if "async" in function:
        raise DescriptionError(f"{where}: function {name}: a function that renders a file is not asynchronous")
    submitter = next((f["name"] for f in data["functions"] if (f.get("async") or {}).get("collect") == name), None)
    if submitter is not None:
        raise DescriptionError(
            f"{where}: function {name}: renders a file and collects the asynchronous job of {submitter!r}: a collect answers JSON"
        )
    source = file["from"]
    if source not in ("body", "json") and "path" not in file:
        raise DescriptionError(f"{at}.path is required unless from: body or json: it points at the address or the encoded bytes")
    if source == "body" and "path" in file:
        raise DescriptionError(f"{at}.path is refused with from: body, whose bytes are the whole answer")
    naming = file["name"]
    if "header" in naming and source not in ("body", "url"):
        raise DescriptionError(f"{at}.name.header is only for from: body or url: a base64 field has no headers of its own")
    if "path" in naming and source == "body":
        raise DescriptionError(f"{at}.name.path is refused with from: body, which has no JSON")
    template = naming["template"]
    required = set(function["input"].get("required", []))
    for argument in PATH_PARAM.findall(template):
        if argument not in required:
            raise DescriptionError(f"{at}.name.template cites {argument!r}, which is not a required input argument")
    if source == "json":
        return
    end = EXTENSION_AT_END.search(template)
    if end is None or end.group(1) not in file["types"]:
        declared = ", ".join(f".{extension}" for extension in file["types"])
        raise DescriptionError(f"{at}.name.template must end with a declared extension ({declared})")
    for mimes in file["types"].values():
        for mime in mimes:
            if page_or_script(mime):
                raise DescriptionError(f"{at}.types: {mime!r} is a type a browser renders as a page or runs")
    if source == "body" and "expect" in function:
        raise DescriptionError(f"{where}: function {name}: expect is refused with output.file from: body, which has no JSON")


# Les refus de la règle 18, les mêmes des deux côtés (cas communs `connectors/_cases/strip.yaml`).
STRIP_REFUSALS = ("strip_path_malformed", "strip_path_overlaps", "strip_path_twice")


def strip_refusal(paths: list[str]) -> tuple[str, str, str] | None:
    """Le premier défaut d'une liste `strip`, `(code, chemin, raison)`, ou `None` : la règle 18 hors de toute description,
    que les cas communs rejouent."""
    seen: set[str] = set()
    for path in paths:
        if any(key == "" for key in path.split(".")):
            return ("strip_path_malformed", path,
                    "a path is keys separated by dots, each non-empty (no leading, trailing or doubled dot)")
        if path in seen:
            return ("strip_path_twice", path, "declared twice")
        seen.add(path)
    for path in paths:
        above = next((other for other in paths if path.startswith(other + ".")), None)
        if above is not None:
            return ("strip_path_overlaps", path, f"lies under {above!r}, which is already removed whole")
    return None


# Les refus de la règle 19 : l'exemple de réponse, une garde de la fabrique seulement (l'hôte ne le voit jamais).
SAMPLE_REFUSALS = ("strip_path_absent", "strip_path_unsampled", "strip_sample_foreign_address", "strip_sample_unused")
# Une adresse dans un texte : `scheme://autorité`, ou un courriel ; seuls les domaines réservés aux tests y sont admis.
ADDRESS = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://([^\s/?#]*)")
MAIL = re.compile(r"[^\s@/:]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
RESERVED_TLDS = ("test", "example", "invalid", "localhost")
RESERVED_DOMAINS = ("example.com", "example.net", "example.org")


def _reserved(host: str) -> bool:
    host = host.lower().strip("[]").rstrip(".")
    return (host == "localhost" or host.rsplit(".", 1)[-1] in RESERVED_TLDS
            or any(host == domain or host.endswith("." + domain) for domain in RESERVED_DOMAINS))


def _foreign(text: str) -> bool:
    """Un texte qui nomme une adresse hors des domaines réservés, ou des identifiants dans une adresse."""
    for authority in ADDRESS.findall(text):
        if "@" in authority or not _reserved(authority.rsplit(":", 1)[0] if authority.count(":") == 1 else authority):
            return True
    return any(not _reserved(domain) for domain in MAIL.findall(text))


def _texts(value: object, at: str):
    """Chaque texte d'un JSON (clé ou valeur), avec sa place (`call.recording`, `calls.0`)."""
    if isinstance(value, dict):
        for key, inner in value.items():
            place = f"{at}.{key}" if at else str(key)
            yield place, str(key)
            yield from _texts(inner, place)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _texts(inner, f"{at}.{index}" if at else str(index))
    elif isinstance(value, str):
        yield at or "(root)", value


def _value_at(value: object, path: str) -> object:
    """La valeur au chemin pointé de `items`, un index de liste en nombre (la lecture de l'exécution)."""
    for key in path.split("."):
        if isinstance(value, list) and key.isdigit():
            value = value[int(key)] if int(key) < len(value) else None
        elif isinstance(value, dict):
            value = value.get(key)
        else:
            return None
    return value


def _designates(value: object, keys: list[str]) -> bool:
    """Le chemin atteint sa clé dans au moins un élément : une liste se traverse, comme à l'exécution."""
    if isinstance(value, list):
        return any(_designates(element, keys) for element in value)
    if not isinstance(value, dict) or keys[0] not in value:
        return False
    return len(keys) == 1 or _designates(value[keys[0]], keys[1:])


def sample_refusal(output: dict) -> tuple[str, str | None, str] | None:
    """Le premier défaut de l'exemple de réponse, `(code, chemin ou None, raison)`, ou `None` : la règle 19."""
    strip = output.get("strip") or []
    dotted = [path for path in strip if "." in path]
    if "sample" not in output:
        if dotted:
            return ("strip_path_unsampled", dotted[0],
                    "a dotted path needs output.sample, a made-up raw answer it is judged against")
        return None
    if not dotted:
        return ("strip_sample_unused", None, "a sample serves only to judge the dotted paths of strip, and none is declared")
    sample = output["sample"]
    for place, text in _texts(sample, ""):
        if _foreign(text):
            return ("strip_sample_foreign_address", None,
                    f"the text at {place!r} names an address outside the domains reserved for tests (.test, .example, "
                    ".invalid, localhost, example.com) or credentials in an address: a sample holds made-up values only")
    root = sample
    if "items" in output:
        root = _value_at(sample, output["items"])
        if not isinstance(root, list):
            return ("strip_path_absent", strip[0], f"output.items {output['items']!r} is not a list in output.sample")
    for path in strip:
        if not _designates(root, path.split(".")):
            where = "each element of items" if "items" in output else "output.sample"
            return ("strip_path_absent", path, f"designates nothing in {where}")
    return None


def output_refusal(output: dict) -> tuple[str, str | None, str] | None:
    """Le premier défaut de `strip` et de son exemple : la règle 18, puis la 19."""
    return (strip_refusal(output["strip"]) if output.get("strip") else None) or sample_refusal(output)


def check_strip(function: dict, where: str) -> None:
    """Règles 18 et 19 : chaque chemin de `strip` est bien formé, déclaré une fois, sous aucun autre, et un chemin à
    points désigne une clé de l'exemple de réponse, qui n'est là que pour lui."""
    refusal = output_refusal(function.get("output") or {})
    if refusal is not None:
        code, path, reason = refusal
        at = "output.sample" if path is None else f"output.strip {path!r}"
        raise DescriptionError(f"{where}: function {function['name']}: {at}: {code}: {reason}")


# Les mots d'un JSON Schema dont la valeur est un schéma, une liste de schémas, ou des schémas par nom : la donnée
# personnelle se cherche là seulement, jamais dans `examples`, `default`, `const` ni `enum`, et le nom d'une propriété
# (une propriété qui s'appellerait `personal`) n'est jamais lu comme un mot.
SCHEMA_KEYWORDS = ("items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames",
                   "unevaluatedItems", "unevaluatedProperties")
SCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")
SCHEMA_MAPS = ("properties", "patternProperties", "$defs", "dependentSchemas")


def _personal_marks(schema: object, at: str) -> list[tuple[str, object]]:
    """Chaque `personal` d'un schéma, avec sa place (`input.properties.email`)."""
    if not isinstance(schema, dict):
        return []
    found = [(at, schema["personal"])] if "personal" in schema else []
    for key in SCHEMA_KEYWORDS:
        found += _personal_marks(schema.get(key), f"{at}.{key}")
    for key in SCHEMA_LISTS:
        for index, child in enumerate(schema.get(key) or []):
            found += _personal_marks(child, f"{at}.{key}[{index}]")
    for key in SCHEMA_MAPS:
        for name, child in (schema.get(key) or {}).items():
            found += _personal_marks(child, f"{at}.{key}.{name}")
    return found


def check_personal(function: dict, where: str) -> None:
    """Règle 15 : `personal` marque une propriété de l'entrée, toujours `true` ; ni la racine ni la sortie."""
    name = function["name"]
    for at, value in _personal_marks(function["input"], "input"):
        if at == "input":
            raise DescriptionError(f"{where}: function {name}: personal marks a property of input, not input itself")
        if value is not True:
            raise DescriptionError(f"{where}: function {name}: {at}: personal must be true (declare it only where it applies)")
    schema = (function.get("output") or {}).get("schema")
    for at, _ in _personal_marks(schema, "output.schema"):
        raise DescriptionError(f"{where}: function {name}: {at}: personal marks an input argument, not an output field")


def _statuses(entry: dict) -> list[int]:
    status = entry["status"] if "status" in entry else entry["when"]
    return status if isinstance(status, list) else [status]


def _check_signal_table(entries: list[dict], at: str, where: str) -> None:
    """Règle 10, sur une table (`errors` du connecteur, ou les refus d'une fonction)."""
    plain: set[int] = set()
    for entry in entries:
        label = f"{at} {entry['code']!r}"
        if "status" not in entry and not isinstance(entry["when"], int):
            if "body" in entry or "marks_account" in entry:
                raise DescriptionError(
                    f"{where}: {label}: a refusal with body or marks_account needs an HTTP status in when, not a text condition"
                )
            continue
        statuses = _statuses(entry)
        body = "body" in entry
        mark = entry.get("marks_account")
        if not body and any(200 <= status < 300 for status in statuses):
            raise DescriptionError(f"{where}: {label}: a 2xx status needs a body condition, or every success would be refused")
        if mark == "credential_rejected" and not body and 403 in statuses:
            raise DescriptionError(
                f"{where}: {label}: a 403 without a body condition cannot mark credential_rejected (a missing right is not a refused key)"
            )
        if mark == "quota_exhausted" and not body and 429 in statuses:
            raise DescriptionError(
                f"{where}: {label}: a 429 without a body condition cannot mark quota_exhausted (a rate limit is not spent credits)"
            )
        if mark is not None and entry.get("retryable") is True:
            raise DescriptionError(f"{where}: {label}: an entry that marks the account is not retryable")
        shadowed = sorted(plain.intersection(statuses))
        if body and shadowed:
            raise DescriptionError(
                f"{where}: {label}: its body condition comes after an entry without one for status "
                f"{', '.join(map(str, shadowed))}, so it is never read"
            )
        if not body:
            plain.update(statuses)


def check_signals(data: dict, where: str) -> None:
    """Règle 10 : le signal de refus du service, dans la table du connecteur et dans les refus de chaque fonction."""
    _check_signal_table(data["connector"]["errors"], "errors", where)
    for function in data["functions"]:
        _check_signal_table(function.get("refusals", []), f"function {function['name']}: refusal", where)


def check_checks(function: dict, where: str) -> None:
    """Règle 4 : un contrôle nomme un refus de sa fonction, et `equal_sums` une liste qui a ses deux champs."""
    name = function["name"]
    refusals = {refusal["code"] for refusal in function.get("refusals", [])}
    for check in function.get("checks", []) + function.get("expect", []):
        if check["refusal"] not in refusals:
            raise DescriptionError(
                f"{where}: function {name}: {check['kind']} names refusal {check['refusal']!r}, which the function does not declare"
            )
        if check["kind"] == "equal_sums":
            argument = function["input"]["properties"].get(check["items"])
            item = (argument or {}).get("items") if (argument or {}).get("type") == "array" else None
            missing = [f for f in check["fields"] if f not in ((item or {}).get("properties") or {})]
            if item is None or missing:
                raise DescriptionError(
                    f"{where}: function {name}: equal_sums needs {check['items']!r} to be a list argument whose items "
                    f"declare {', '.join(check['fields'])}"
                )


def _plain_read(data: dict, role: str, name: str, where: str) -> None:
    function = next((f for f in data["functions"] if f["name"] == name), None)
    if function is None:
        raise DescriptionError(f"{where}: {role} names {name!r}, which is not a function of the connector")
    if function["class"] != "read" or "call" not in function or function["input"].get("required"):
        raise DescriptionError(
            f"{where}: {role} {name!r} must be a read function with a call and no required argument"
        )


def check_probe(data: dict, where: str) -> None:
    """Règle 5 : la sonde et l'identité d'un consentement sont des lectures par `call`, appelables sans argument ; la
    fonction d'une sonde qui lit un solde ne coûte rien."""
    probe = data["connector"].get("probe")
    if probe is not None:
        _plain_read(data, "probe", probe["function"], where)
        function = next(f for f in data["functions"] if f["name"] == probe["function"])
        if "balance" in probe and "cost" in function:
            raise DescriptionError(
                f"{where}: probe {probe['function']!r} reads a balance, so it must declare no cost: one never pays to learn whether one can pay"
            )
    auth = data["connector"]["auth"]
    identity = auth.get("identity")
    if identity is not None and "function" in identity:
        _plain_read(data, "auth.identity", identity["function"], where)
    if identity is not None and "url" in identity and "access_levels" not in auth:
        beyond = sorted(set(identity["scopes"]) - set(auth.get("scopes", [])))
        if beyond:
            raise DescriptionError(f"{where}: auth.identity needs {', '.join(beyond)}, which auth.scopes does not ask for")


def _check_template(
    template: str, at: str, settings: dict[str, dict], from_token: set[str], where: str, identity: str | None = None,
) -> set[str]:
    """Un gabarit d'adresse : chaque `{nom}` est un réglage déclaré (ou une valeur du jeton, ou l'identité du compte
    quand `identity` la nomme), à sa place."""
    cited = PATH_PARAM.findall(template)
    for position, name in enumerate(cited):
        head = position == 0 and template.startswith("{")
        if name in from_token:
            continue
        if name == identity:
            _check_identity_place(template, at, name, where)
            continue
        setting = settings.get(name)
        if setting is None:
            known = sorted(settings) + sorted(from_token)
            raise DescriptionError(
                f"{where}: {at} cites {{{name}}}, which is not a declared setting ({', '.join(known) or 'none declared'})"
            )
        if head and setting["type"] != "url":
            raise DescriptionError(f"{where}: {at} starts with {{{name}}}, which is not a url setting")
        if not head and setting["type"] == "url":
            raise DescriptionError(f"{where}: {at} cites the url setting {{{name}}} elsewhere than at its head")
    return set(cited)


def _check_identity_place(url: str, at: str, identity: str, where: str) -> None:
    if not AFTER_HOST.match(url[: url.index("{" + identity + "}")]):
        raise DescriptionError(f"{where}: {at} cites the account identity {{{identity}}} elsewhere than after the host")


def _check_urls_by_setting(urls: dict, at: str, settings: dict[str, dict], where: str, identity: str | None = None) -> set[str]:
    for url in urls["values"].values():
        for name in PATH_PARAM.findall(url):
            if name != identity:
                raise DescriptionError(f"{where}: {at}.values cites {{{name}}}, which is not the account identity")
            _check_identity_place(url, f"{at}.values", name, where)
    setting = settings.get(urls["setting"])
    if setting is None or setting["type"] != "choice":
        raise DescriptionError(f"{where}: {at}.setting names {urls['setting']!r}, which is not a choice setting")
    if set(urls["values"]) != set(setting["choices"]):
        raise DescriptionError(
            f"{where}: {at}.values must give a URL to each choice of {urls['setting']!r} "
            f"({', '.join(setting['choices'])}), and to nothing else"
        )
    return {urls["setting"]}


def check_settings(connector: dict, where: str) -> None:
    """Règle 6 : réglages déclarés une fois, cités à leur place, tous cités."""
    auth = connector["auth"]
    settings = {s["name"]: s for s in connector.get("settings", [])}
    from_token = set(auth.get("from_token", []))
    names = [f["name"] for f in connector.get("credential", [])] + [s["name"] for s in connector.get("settings", [])]
    names += list(from_token)
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise DescriptionError(f"{where}: name(s) declared twice among credential, settings and from_token: {', '.join(twice)}")
    for setting in settings.values():
        if "default" in setting and setting["default"] not in setting["choices"]:
            raise DescriptionError(f"{where}: setting {setting['name']!r}: default {setting['default']!r} is not one of its choices")
    identity = (connector.get("account_identity") or {}).get("name")
    cited: set[str] = set()
    if "base_url" in connector:
        cited |= _check_template(connector["base_url"], "base_url", settings, from_token, where, identity)
    if "base_urls" in connector:
        cited |= _check_urls_by_setting(connector["base_urls"], "base_urls", settings, where, identity)
    for key in ("token_url", "authorize_url"):
        if key in auth:
            cited |= _check_template(auth[key], f"auth.{key}", settings, set(), where)
    if "token_urls" in auth:
        cited |= _check_urls_by_setting(auth["token_urls"], "auth.token_urls", settings, where)
    unused = sorted(set(settings) - cited)
    if unused:
        raise DescriptionError(f"{where}: setting(s) {', '.join(unused)} cited by no URL")


def check_account_identity(data: dict, where: str) -> None:
    """Règle 8 : l'identité d'un compte rattaché a un nom à elle, n'est citée que par l'adresse de base, après l'hôte, et
    jamais fournie par l'agent. La place dans le gabarit se vérifie avec les réglages (règle 6)."""
    connector = data["connector"]
    declared = connector.get("account_identity")
    if declared is None:
        return
    identity = declared["name"]
    for kind, names in (
        ("a credential field", [f["name"] for f in connector.get("credential", [])]),
        ("a setting", [s["name"] for s in connector.get("settings", [])]),
        ("a value of auth.from_token", connector["auth"].get("from_token", [])),
    ):
        if identity in names:
            raise DescriptionError(f"{where}: account_identity {identity!r} is also the name of {kind}")
    if "base_url" in connector:
        urls = [("base_url", connector["base_url"])]
    else:
        urls = [(f"base_urls.values.{k}", v) for k, v in (connector.get("base_urls") or {}).get("values", {}).items()]
    if not urls:
        raise DescriptionError(f"{where}: account_identity {identity!r} needs a base_url that cites it")
    for at, url in urls:
        if "{" + identity + "}" not in url:
            raise DescriptionError(f"{where}: {at} does not cite the account identity {{{identity}}}")
    auth = connector["auth"]
    others = [(f"auth.{key}", auth[key]) for key in ("token_url", "authorize_url") if key in auth]
    others += [(f"auth.token_urls.values.{k}", v) for k, v in (auth.get("token_urls") or {}).get("values", {}).items()]
    for at, url in others:
        if identity in PATH_PARAM.findall(url):
            raise DescriptionError(f"{where}: {at} cites the account identity {{{identity}}}, which only the base URL cites")
    if identity.lower() in {header.lower() for header in connector.get("headers") or {}}:
        raise DescriptionError(f"{where}: the connector header {identity!r} bears the name of the account identity")
    for function in data["functions"]:
        name = function["name"]
        if identity in function["input"]["properties"]:
            raise DescriptionError(
                f"{where}: function {name}: input argument {identity!r} bears the name of the account identity, "
                "which only the base URL cites"
            )
        call = function.get("call")
        if call is None:
            continue
        if identity in PATH_PARAM.findall(call["path"]):
            raise DescriptionError(f"{where}: function {name}: the path cites the account identity {{{identity}}}")
        for kind in PLACES:
            mapping = argument_table(call, kind)
            if kind == "body":
                # Toute clé d'un chemin emboîté, à chaque profondeur.
                api_names = {key for path, _ in body_entries(call["body"] if isinstance(call.get("body"), dict) else {})
                             for key in path}
            else:
                api_names = {n.lower() for n in mapping} if kind == "headers" else set(mapping)
            if identity in api_names or identity in mapping.values():
                raise DescriptionError(
                    f"{where}: function {name}: {kind} parameter {identity!r} bears the name of the account identity"
                )
        for kind, constants in (call.get("constants") or {}).items():
            if identity in ({n.lower() for n in constants} if kind == "headers" else set(constants)):
                raise DescriptionError(
                    f"{where}: function {name}: constant {kind} {identity!r} bears the name of the account identity"
                )


def covered(scopes: list[str], implies: dict[str, list[str]]) -> set[str]:
    """Les droits que couvrent ceux d'un niveau : eux-mêmes et ce qu'ils impliquent (`scope_implies`, clos)."""
    return set(scopes).union(*(implies.get(scope, []) for scope in scopes))


def served(function: dict, scopes: list[str], implies: dict[str, list[str]]) -> bool:
    """Une fonction (ou une identité lue à une adresse) est servie à un niveau quand chacun des droits qu'elle exige
    est couvert par ceux du niveau."""
    return set(function["scopes"]) <= covered(scopes, implies)


def check_access_levels(data: dict, where: str) -> None:
    """Règle 7 : implication close et utile, `full` couvre tout, identité et sonde servies à chaque niveau, niveau par
    défaut déclaré."""
    auth = data["connector"]["auth"]
    levels = auth.get("access_levels")
    if levels is None:
        return
    implies = auth.get("scope_implies", {})
    granted = {scope for level in levels.values() for scope in level["scopes"]}
    for scope, implied in implies.items():
        if scope not in granted:
            raise DescriptionError(f"{where}: auth.scope_implies: {scope!r} is the scope of no access level")
        missing = sorted({deeper for scope2 in implied for deeper in implies.get(scope2, [])} - set(implied) - {scope})
        if missing:
            raise DescriptionError(
                f"{where}: auth.scope_implies: {scope!r} implies a scope that implies {', '.join(missing)}, "
                f"which {scope!r} must then list too"
            )
    default = auth.get("default_level")
    if default is not None and default not in levels:
        raise DescriptionError(f"{where}: auth.default_level {default!r} is not a declared access level")
    full = covered(levels["full"]["scopes"], implies)
    for name, level in levels.items():
        beyond = sorted(set(level["scopes"]) - full)
        if beyond:
            raise DescriptionError(
                f"{where}: access level {name!r} asks for {', '.join(beyond)}, which level 'full' does not cover"
            )
    for function in data["functions"]:
        beyond = sorted(set(function["scopes"]) - full)
        if beyond:
            raise DescriptionError(
                f"{where}: function {function['name']}: scope(s) {', '.join(beyond)} not covered by level 'full'"
            )
    # Un compte de service (`oauth2_jwt_bearer`) n'a pas d'identité de consentement : seule la sonde est jugée.
    identity = auth.get("identity")
    roles = []
    if identity is not None and "function" in identity:
        roles.append(("auth.identity", identity["function"], next(f for f in data["functions"] if f["name"] == identity["function"])))
    elif identity is not None:
        roles.append(("auth.identity", identity["url"], identity))
    if "probe" in data["connector"]:
        name = data["connector"]["probe"]["function"]
        roles.append(("probe", name, next(f for f in data["functions"] if f["name"] == name)))
    for role, name, required in roles:
        for level_name, level in levels.items():
            if not served(required, level["scopes"], implies):
                raise DescriptionError(
                    f"{where}: {role} {name!r} is not served at access level {level_name!r}: "
                    f"its scopes ({', '.join(required['scopes'])}) are not all covered"
                )


def check(data: dict, path: pathlib.Path) -> Description:
    where = _relative(path)
    if not isinstance(data, dict):
        raise DescriptionError(f"{where}: not a mapping")
    check_schema(data, where)
    if data["connector"]["name"] != path.parent.name:
        raise DescriptionError(f"{where}: connector.name {data['connector']['name']!r} is not the folder name")
    check_rules(data, where)
    return Description(path=path, data=data)


def check_rules(data: dict, where: str) -> None:
    """Les règles hors schéma, sur une description déjà validée par le schéma (`check`, et les cas du coût)."""
    check_auth(data["connector"], where)
    check_account_identity(data, where)
    check_settings(data["connector"], where)
    for function in data["functions"]:
        check_arguments(function, where)
        check_constants(data["connector"], function, where)
        check_checks(function, where)
        check_act(function, where)
        check_timeout(data["connector"], function, where)
        check_background(data, function, where)
        check_cost(function, where)
        check_async(data, function, where)
        check_files(function, where)
        check_output_file(data, function, where)
        check_strip(function, where)
        check_personal(function, where)
    names = [function["name"] for function in data["functions"]]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise DescriptionError(f"{where}: function name(s) declared twice: {', '.join(twice)}")
    check_probe(data, where)
    check_access_levels(data, where)
    check_signals(data, where)


def load(path: pathlib.Path) -> Description:
    return check(yaml.safe_load(path.read_text(encoding="utf-8")), path)


def load_all(root: pathlib.Path = CONNECTORS) -> list[Description]:
    return [load(path) for path in sorted(root.glob("*/connector.yaml"))]
