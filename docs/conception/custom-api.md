# Custom API : la définition d'un outil par une organisation

- **Statut** : validé avec le mainteneur le 19/11/2026
- **Dernière révision** : 2026-11-19

## Résumé

Une organisation décrit un outil que le catalogue n'a pas, et ses agents appellent ses actions par leur nom. Une définition Custom API **est** un `connector.yaml` du [format](format-de-description.md), restreint par un profil fermé : ni format à part, ni conversion à l'enregistrement. Restent trois traductions : l'import d'un document OpenAPI, la reprise une fois des définitions d'oto 1 (`from_oto1`), et la projection de la définition dans la forme TypeScript d'oto-core, à l'exécution. Le cœur s'écrit en Python (`oto/connectors/custom_api/`) puis en TypeScript, sur les mêmes cas communs (`connectors/_cases/custom_api.yaml`), selon la règle de convergence de la [fabrique](fabrique.md).

## Contexte

- Oto 1 a son propre format de Custom API, « un sous-ensemble du format, aplati » : `in` au lieu de `call.query` et `call.body`, `class: read` comme seule classe, ni exemple ni table d'erreurs. Une erreur y remonte en `Target API: HTTP <statut>`.
- Une application (`api_apps`) par organisation et par outil porte la connexion (adresse, mode d'authentification) et la définition ; des clés de membre, d'équipe ou d'organisation lui sont liées. Avant les applications, la définition vivait dans la configuration de l'instance d'une carte.
- Les deux moteurs, celui de la lib et celui d'oto-core, exécutent déjà le format.

## Objectifs et non-objectifs

- Objectifs : un seul validateur, celui du format, plus quelques règles de profil ; une définition qui plaît entre au catalogue partagé par simple copie (le préfixe changé) ; un export qui se lit comme n'importe quel connecteur ; tous les problèmes d'une définition nommés d'un coup, chacun par son code.
- Non-objectifs : les appels bruts (une route libre sous l'adresse de l'outil) ; les modes `session` et `visitor` d'oto 1 ; l'import OpenAPI dans oto-core en première version ; deviner `output.items` ou la pagination.

## Conception

### Le profil

- **Admis** : `connector` avec `name`, `label`, `description`, `logo_domain`, `namespace`, `version`, `api_version`, `base_url` (https fixe, sans gabarit), `auth`, `credential` (champs tenus par le compte seulement), `modes` (`byo_user`, `byo_org`), `timeout_s`, `query_arrays`, `headers`, `rate_limit`, `probe` (sans solde) et `errors`. Dans `functions` : `class`, `act`, `effect`, `description`, `input`, `examples`, `refusals`, `checks`, `expect`, `call` (sans `files`), `pagination`, `output` (sans `file`), `timeout_s` et `confirm`.
- **Authentification** : `none`, `api_key` (en-tête ou query), `bearer`, `basic`, `oauth2_client_credentials` et `oauth2_user`.
- **Refusé**, parce que cela touche les ressources de l'hôte ou sort du REST simple : `platform` dans `modes`, `holder: host`, `account_identity`, `settings`, `base_urls`, `quota`, `cost`, `async`, `background`, `files`, `output.file`, `handwritten`, `exposure`, `scopes` d'une fonction, `oauth2_jwt_bearer`, et dans `auth` ce qui suppose des réglages ou des niveaux (`token_urls`, `access_levels`, `scope_implies`, `default_level`, `from_token`).
- **Bornes reprises d'oto 1** : 100 fonctions et 256 Ko au plus. Aucun en-tête, constant ou argument, qui authentifie ou route la requête (`Authorization`, `Cookie`, `Set-Cookie`, `Host`, `X-Api-Key`, `Api-Key`, `Proxy-*`, ni l'en-tête de la clé). Le nom et l'espace commencent par le préfixe réservé `custom_`, qu'aucun connecteur partagé ne porte (règle 24 de la fabrique).
- **Adresses** : toute adresse de la définition (`base_url`, `token_url`, `authorize_url`, l'adresse d'identité) est en https vers un hôte public. Le profil juge l'hôte tel qu'il est écrit, sans DNS : une adresse IP non publique, un nom sans point, `localhost` ou un suffixe de réseau interne (`.local`, `.internal`, `.lan`, `.home.arpa`…) sont refusés. L'adresse résolue reste à garder par l'hôte à chaque appel (« Écart avec le code »).

### La validation

`validate(document) -> list[Problem]` rend **tous** les problèmes, chacun avec son code, son chemin à points et son message ; une liste vide, et la définition peut s'enregistrer. Une définition refusée ne s'enregistre jamais à moitié ; l'hôte monte `version` à chaque enregistrement. Trois temps :

1. le profil, sur toute valeur : `custom_api_key_not_allowed` (la clé nommée), `custom_api_auth_not_supported`, `custom_api_base_url_not_https`, `custom_api_base_url_internal`, `custom_api_name_reserved`, `custom_api_header_authenticates`, `custom_api_too_large`, `custom_api_example_missing` ;
2. le schéma du format, chaque erreur avec son chemin (`custom_api_schema_invalid`), sauf là où le profil a déjà nommé le problème ;
3. sur un document que le schéma accepte : chaque exemple contre l'entrée de sa fonction (`custom_api_example_invalid`) ; puis, si rien n'a été trouvé, les règles du format dont le profil a besoin, chacune `custom_api_rule_broken`, son propre code dans `rule` quand elle en a un (`body_key_twice`, `strip_path_overlaps`…).

Les règles de la fabrique dont le profil a besoin (arguments, authentification, un nom côté API une valeur, contrôles, sonde, `act`, signal de refus, délai, `personal`, corps, `strip`) vivent dans la lib, `oto/connectors/rules/`, une seule copie que la fabrique applique et réexporte. `output.sample` n'est pas exigé d'une définition : c'est une garde de la fabrique pour le catalogue partagé. Le schéma voyage avec la lib (`oto/connectors/connector.schema.json`, copie que la fabrique écrit et vérifie) ; `jsonschema` devient une dépendance de la lib.

Le module TypeScript (`ts/src/custom-api/`, exposé sous `@otomata_tech/connectors/custom-api`) porte la même validation, `validate(document, compile)`, avec les mêmes codes, et `toDefinition(document)`, qui projette la définition dans la forme que la fabrique génère pour un connecteur partagé (`Connector`, ses fonctions en ligne) ; ce que la fabrique ne génère pas (une entrée dont la racine n'est pas un objet strict) lève `NotGenerated`. Le paquet reste sans dépendance : le validateur JSON Schema arrive en paramètre, `compile`, qui rend pour un schéma la fonction listant les erreurs d'une valeur dans la forme d'Ajv, sans juger `format` et en connaissant le mot `personal`. Le schéma voyage dans le paquet (`ts/src/connector-schema.ts`, que la fabrique écrit). Les cas communs se jouent des deux côtés, et une garde applique `toDefinition` à chaque `connectors/*/connector.yaml` et la compare au module généré.

La table d'erreurs standard (`standard_errors(label, auth_kind)`) est proposée en clair : 400 et 422, 401 (qui marque le compte, sauf sans authentification), 403, 404, 429 et 5xx rejouables.

### Exemple : un outil fictif, OAuth par personne, deux actions

```yaml
connector:
  name: custom_acme_planning
  label: Acme Planning
  description: Shifts of the teams of an organisation, read and planned.
  logo_domain: acme-planning.test
  namespace: custom_acme_planning
  version: 1.0.3                       # montée par l'hôte à chaque enregistrement
  api_version: v2
  base_url: https://api.acme-planning.test/v2
  auth:
    kind: oauth2_user
    authorize_url: https://auth.acme-planning.test/oauth/authorize
    token_url: https://auth.acme-planning.test/oauth/token
    client_auth: body
    scopes: [shifts.read, shifts.write, openid, email]
    pkce: S256
    refresh: refresh_token
    rotates: true
    identity: { url: https://auth.acme-planning.test/userinfo, path: email, scopes: [openid, email] }
  modes: [byo_user]
  errors:
    - { status: 401, code: authorization_expired, retryable: false, marks_account: credential_rejected,
        message: "Acme Planning no longer accepts this authorization: the person must connect again." }
functions:
  - name: list_shifts
    class: read
    description: Shifts of one team between two dates.
    input:
      type: object
      additionalProperties: false
      required: [team_id, from]
      properties:
        team_id: { type: string, minLength: 1 }
        from: { type: string, pattern: "^[0-9]{4}-[0-9]{2}-[0-9]{2}$" }
        cursor: { type: string }
    examples: [{ title: A team's week, input: { team_id: tm_42, from: "2026-11-16" } }]
    call: { method: GET, path: "/teams/{team_id}/shifts", query: { start_date: from, cursor: cursor } }
    pagination: { kind: cursor, request_param: cursor, next: meta.next_cursor, max_pages: 5 }
    output: { items: data }
```

Aucun secret : le `client_id` et le `client_secret` sont ceux de l'application OAuth de l'organisation chez l'outil, saisis à part et rangés au coffre. Les cas communs portent l'exemple entier, avec une action d'écriture.

### L'import OpenAPI

`from_openapi(document)` rend un connecteur proposé, des candidats et une liste `skipped` (méthode, chemin, code, raison), chaque valeur déduite dite comme telle ; rien n'est enregistré d'office. `read_url(url)` lit un document en https vers un hôte public, sans redirection, 5 Mo au plus.

- **Accepté** : OpenAPI 3.0 et 3.1, Swagger 2.0, en JSON ou en YAML ; GET, POST, PUT, PATCH, DELETE ; paramètres de chemin, de query et d'en-tête (ceux-ci dans `call.headers`) ; un corps JSON objet, `allOf` fusionné ; les `$ref` locaux sur 8 niveaux ; 100 opérations.
- **Déduit** : le nom (`operationId` en snake_case, ou la méthode et le chemin), la classe d'après le verbe (DELETE sensible, `effect: destroy`, confirmé par « `<METHOD> <path>`: irreversible. »), les exemples du document seulement s'ils valident l'entrée (`{}` sans argument requis), `base_url` tiré du premier serveur absolu en https, l'authentification tirée de `securitySchemes` (`http bearer` → `bearer`, `apiKey` → `api_key`, `http basic` → `basic`, `clientCredentials` → `oauth2_client_credentials`, `authorizationCode` → `oauth2_user`, dont l'identité reste à choisir).
- **Refusé, chaque fois nommé** : `openapi_not_a_document`, `openapi_too_large`, `openapi_ref_external`, `openapi_ref_too_deep`, `openapi_ref_missing` (jamais un objet vide à la place), `openapi_method_not_supported`, `openapi_body_not_json`, `openapi_body_not_object`, `openapi_cookie_parameter` (un cookie requis ; un cookie facultatif est écarté et dit), `openapi_name_collision` (le nom proposé est montré, à confirmer), `custom_api_argument_name_collision`, `openapi_server_variables`, `openapi_openid_connect`, `openapi_too_many_operations`. Un candidat sans exemple possible porte `custom_api_example_missing`.

### La reprise d'oto 1

`from_oto1(definition_oto1) -> (document | None, rapport)`, fonction pure. L'entrée est une application d'oto 1, sans secret : `account`, `connection` (champs non secrets) et `definition`. Le rapport dit ce qui est déduit (`deduced`), ce qui est laissé sans bloquer (`dropped`) et ce qui bloque (`blocking`, avec ses codes), sans jamais répéter un secret.

- **Modes** : `bearer` → `bearer` ; `header` et `query` → `api_key` ; `basic` → `basic` ; `oauth2` → `oauth2_client_credentials` (jeton demandé en formulaire, client dans le corps) ; `none` → `none`. Les champs du coffre (`token`, `username`, `password`, `client_id`, `client_secret`) gardent leurs noms.
- **Déduit** : le nom (`custom_` et le nom de l'application en snake_case), `version` 1.0.0, `api_version` `unspecified`, les modes, la classe d'après le verbe (`class: read` déclaré reste `read`), chaque argument renommé en snake_case (le nom côté API reste dans `call`), sa place selon la règle d'oto 1, chaque `variables` devenu une constante de `call` (dans le chemin ou dans `constants`, l'argument sortant de l'entrée), l'exemple `{}` d'une action sans argument requis, la table d'erreurs standard.
- **Repris tels quels** : le libellé, les en-têtes constants, `description` et `logo_domain` (sans blancs autour).
- **Laissé** : une `description` ou un `logo_domain` que le format refuse (vide ou trop longue, domaine mal formé), `doc_path` (les appels bruts restent dans oto 1), une variable qu'aucune action n'utilise, `retryable: true` d'un refus.
- **Bloque, sans repli** : une adresse en `http://` ou interne, les modes `session` et `visitor`, une action à argument requis sans exemple, deux arguments au même nom en snake_case, une variable qui n'est pas un segment de chemin (`custom_api_variable_invalid`), un secret dans l'export, et tout problème que le profil trouve au document construit.

Le rapport à blanc, `python -m oto.connectors.custom_api.rapport <export.json> <rapport.json>`, applique `from_oto1` à chaque application d'un export JSON fourni par l'exploitant d'oto 1 et écrit un rapport agrégé, par application et par code. Il ne se connecte à rien, n'écrit que ce rapport et n'en écrase jamais un ; la forme de l'export est dans son en-tête.

## Décisions et alternatives écartées

- **Même format, profil fermé** (décidé le 19/11/2026) : un format à part, converti, a été écarté. L'aplatissement d'oto 1 crée un troisième vocabulaire, et sa conversion dériverait à chaque forme ajoutée au format. Le prix, un format plus lourd à écrire, est absorbé par l'écran et l'import, qui proposent et font tout valider.
- **Exemples obligatoires** (19/11/2026) : un profil où ils seraient facultatifs a été écarté ; un exemple validé est la meilleure aide pour l'agent. Une action reprise sans exemple bloque son application jusqu'à ce qu'on en écrive un.
- **Les `variables` deviennent des constantes de `call`** (19/11/2026) : une forme « valeur d'organisation par défaut » remplie par l'hôte a été écartée. L'agent ne peut plus forcer une autre valeur.
- **`session` et `visitor` ne sont pas traduits** (19/11/2026) : `from_oto1` les nomme comme bloquants ; l'application reste dans oto 1 tant que le format n'a pas de mode qui les dise.
- **Les appels bruts restent dans oto 1** (19/11/2026) : oto-core n'en a pas, l'unité du format étant la fonction.
- **`connector.description` et `logo_domain` entrent au format, facultatifs** (19/11/2026) : d'abord laissés dehors tant qu'oto-core ne les lisait pas ; il les lit désormais (`description`, `logoDomain`) avec les mêmes contrôles, et un oto-core plus ancien ignore ces clés. Un bloc `custom_api` à côté a été écarté : une définition serait alors « presque » un `connector.yaml`.
- **`http://` et adresses internes refusés** (19/11/2026) : l'exception nommée par l'opérateur d'oto 1 n'est pas reprise ; le rapport à blanc nomme chaque définition concernée.
- **Préfixe réservé `custom_`** (19/11/2026) : aucune collision possible avec le catalogue ni avec les outils d'oto 1.
- **Pas d'import OpenAPI dans oto-core en première version** (19/11/2026) : l'écran accepte un document collé ou écrit par l'agent, validé par le module TypeScript ; le portage vient quand l'écran le réclame.
- **Les règles du format copiées dans le paquet** : écarté ; elles ont été déplacées de la fabrique dans la lib, que la fabrique appelle.

## Sécurité et confidentialité

- Une définition ne porte aucun secret : un en-tête qui authentifie est refusé, les champs du coffre sont nommés, jamais remplis ; un export qui porte `token`, `password` ou `client_secret` bloque son application, sans répéter la valeur.
- Aucune requête vers une adresse interne : refusée à l'écriture ; à l'appel, l'hôte garde l'adresse résolue, et le moteur ne suit aucune redirection.
- Un document OpenAPI est une donnée : références locales seulement, profondeur bornée, taille bornée, lecture par adresse gardée.
- Le rapport à blanc est en lecture seule et ne s'exécute que sur un export fourni par l'exploitant.

## Écart avec le code

- Écrit : le paquet Python (`validate`, `from_openapi`, `read_url`, `from_oto1`, `standard_errors`), le rapport à blanc, les règles dans `oto/connectors/rules/`, la garde du préfixe dans la fabrique, les cas communs et leurs tests (`tests/test_custom_api_cases.py`, `tests/test_custom_api.py`) ; le module TypeScript (`validate`, `toDefinition`), ses règles recopiées de `oto/connectors/rules/` et sa garde (`ts/test/custom-api.test.ts`).
- Le module TypeScript n'a ni import OpenAPI, ni reprise d'oto 1, ni table d'erreurs standard : ils restent en Python. Ses règles sont une seconde copie de celles de la lib, tenue par les cas communs. Les erreurs du validateur passé sont ramenées à celles de `jsonschema` (une erreur de `oneOf`/`anyOf` tient lieu de celles de ses branches) : les codes sont les mêmes, les messages du schéma diffèrent. Le poids se mesure sur le JSON écrit comme Python l'écrit, un nombre décimal à part.
- Pas encore écrit : la bascule d'oto 1 (colonne du document dans `api_apps`, reprise réelle, appel par le moteur, retrait de son format) ; dans oto-core, le stockage par organisation, l'application OAuth d'une organisation et l'écran.
- Le moteur Python exécute une définition valide, sauf `oauth2_user`. Il ne garde à chaque requête qu'une adresse venue d'un réglage ; une `base_url` fixe ne l'est pas : la garde d'egress de l'hôte (celle d'oto 1 aujourd'hui) reste exigée pour une définition d'organisation.
- Les connecteurs partagés ne portent pas encore `description` ni `logo_domain` : leur ajout est un travail à part.

## Questions ouvertes

- Les modes `session` et `visitor` : le rapport à blanc dira s'ils servent ; si oui, un mode `session` au format, d'abord dans le moteur Python, sinon leur retrait d'oto 1.
- `oauth2_jwt_bearer` : refusé tant que personne n'en a besoin.

## Historique

- 2026-11-19 : proposition relue sur le code d'oto 1, de la lib et d'oto-core ; même format restreint par un profil fermé, exemples obligatoires, variables en constantes, `session` et `visitor` bloquants, appels bruts laissés à oto 1, description et logo hors du format pour l'instant, `http://` et adresses internes refusés, préfixe `custom_` réservé, pas d'import OpenAPI dans oto-core en première version ; premier paquet Python et rapport à blanc — décidé par le mainteneur (source : lot 4 de la bascule des connecteurs d'oto 1 dans la lib).
- 2026-11-19 : module TypeScript (`validate`, `toDefinition`), validateur JSON Schema passé en paramètre, schéma distribué dans le paquet, garde de la projection sur chaque description — décidé par le mainteneur (source : lot 4 de la bascule des connecteurs d'oto 1 dans la lib).
- 2026-11-19 : `connector.description` et `logo_domain` ajoutés au format, admis par le profil et repris d'oto 1 par `from_oto1` — décidé par le mainteneur (source : lecture de ces champs par oto-core).
