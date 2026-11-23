# Le profil hôte : les fonctions d'un hôte décrites en OpenAPI

- **Statut** : validé par Alexis le 22/11/2026 (contrat et jeton) ; forme de la description proposée
- **Dernière révision** : 2026-11-23

## Résumé

Un hôte expose ses propres fonctions métier dans une API REST décrite en OpenAPI, et veut qu'un assistant les trouve et les appelle à côté de celles d'oto, sous le jeton de la personne. Le profil hôte lit ce document par l'import OpenAPI du module Custom API, et en tire une définition : un document du [format](format-de-description.md) par espace de noms, plus les métadonnées de l'hôte (libellés, familles, rôles). Il est strict : un document qui ne passe pas ne donne rien, et chacun de ses problèmes est nommé. La lib décrit ; l'hôte qui exécute (oto-core, puis le backend d'oto 1) obtient le jeton, appelle et relaie la confirmation. Python (`oto/connectors/custom_api/host.py`) et TypeScript (`ts/src/custom-api/host.ts`) jouent les mêmes cas communs, `connectors/_cases/host.yaml`.

## Contexte

- Une fonction d'hôte n'entre aujourd'hui au catalogue d'oto-core qu'écrite en TypeScript dans le même processus. Un hôte qui a son propre serveur n'a aucun chemin.
- Le module [Custom API](custom-api.md) importe déjà un document OpenAPI, mais pour un outil tiers : il passe les noms en snake_case, impose le préfixe `custom_`, déduit la classe du verbe et propose des candidats à confirmer.
- Le catalogue d'oto-core nomme une fonction `espace.verbe`, l'espace étant son connecteur.

## Objectifs et non-objectifs

- Objectifs : un seul format, celui des connecteurs, exécuté par le même moteur ; le nom de la fonction gardé tel que l'hôte l'écrit ; la classe, les exemples et les libellés dits par l'hôte, jamais déduits ; tous les problèmes d'un document nommés d'un coup ; le même comportement en Python et en TypeScript.
- Non-objectifs dans la lib : obtenir le jeton, garder un jeton de rafraîchissement, lire `/me/functions`, construire l'index, relayer la confirmation, journaliser. Tout cela relève de l'hôte qui exécute.

## Conception

### Le contrat attendu du document

- OpenAPI 3.1 (3.0 accepté) ; Swagger 2.0 est refusé (`host_openapi_version`). `servers` absent ou un seul serveur relatif (`/api/v2`), sans variables : l'adresse est un réglage de l'organisation, le document le même pour toutes.
- Par opération : `operationId` au motif `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`, 64 caractères au plus ; `summary` de 1 à 90 caractères, la ligne d'index ; `description` de 1 à 1 000 caractères, le contrat que lit l'assistant ; paramètres de chemin et de query, et un corps JSON objet fermé (`additionalProperties: false`).
- Extensions lues, toutes en `x-oto-*` ; une clé `x-oto-*` inconnue est refusée, les autres `x-*` sont ignorées. `x-oto-class` (obligatoire : `read`, `write` ou `sensitive` ; `GET` exige `read`, les autres verbes `write` ou `sensitive`), `x-oto-effect` (`send` ou `destroy`, fonction sensible seulement), `x-oto-examples` (obligatoire : une liste d'arguments, chemin, query et corps fusionnés, sous les noms de l'API), `x-oto-roles` (obligatoire, des rôles de la racine), `x-oto-label` (obligatoire), `x-oto-family` (obligatoire, une famille de la racine), `x-oto-output` (`items`, `strip`, `projection`).
- À la racine, `x-oto` : `functions` (le chemin de `/me/functions`), `issuer` (l'émetteur des jetons, en https vers un hôte public, tel que ses jetons le nomment), `resource` (l'indicateur de ressource de l'API chez l'émetteur, une URI absolue), `roles` (des identifiants vers des textes) et `families` (des identifiants vers un libellé, ou vers `{ label, scope }`). `scope` est le scope OAuth (RFC 6749 §3.3 : des jetons d'ASCII imprimable sauf `"` et `\`, séparés d'une espace) que l'échange et le consentement demandent pour les fonctions de la famille ; facultatif, jamais déduit : une famille écrite par son seul libellé n'en a pas, et la forme objet l'exige. Plusieurs familles peuvent partager un scope. `resource` est un ajout à la note de conception d'oto-core : l'échange de jeton et le consentement le demandent tous deux.
- Une fonction sensible déclare `confirm`, un paramètre de query booléen, et une réponse `202`. `confirm` est réservé au protocole des deux temps : jamais un argument. Tout autre nom qui, normalisé comme un argument, vaut `confirm` est refusé, quelle que soit sa place (chemin, query, en-tête, cookie, propriété du corps) et sa casse (`Confirm`, `CONFIRM`) : l'assistant ne doit jamais pouvoir sauter le premier temps. Le paramètre est refusé aussi sur une fonction qui n'est pas sensible.

### La définition rendue

`from_host_openapi(document, *, reserved)` (Python) et `fromHostOpenApi(document, { compile, reserved, yaml? })` (TypeScript) rendent `{ definition, problems }`. `reserved` : les espaces que l'appelant sert déjà, aucun n'étant repris par l'hôte. La définition :

```yaml
host: { functions: /me/functions, roles: { admin: Administrator, … }, families: { project: { label: Projects, scope: "projects:read" }, share: Shares } }
functions:                        # les métadonnées de l'hôte, dans l'ordre du document
  project.list: { label: See my projects, family: project, roles: [admin, coach, member], summary: …, scope: "projects:read" }
  share.create: { label: Share a project outside, family: share, roles: [admin, coach], summary: …, scope: null }
connectors:                       # un document du format par espace de noms, dans l'ordre d'apparition
  - connector:
      name: project               # l'espace, avant le point
      label: Host API             # info.title
      namespace: project
      version: 1.0.0
      api_version: 2.4.0          # info.version
      base_url: "{address}/api/v2"
      settings: [{ name: address, label: Address of the host, type: url }]
      auth: { kind: host_delegated, issuer: "https://auth.example.test/oidc", resource: "https://api.example.test" }
      modes: [byo_user]
      errors: [ … ]               # la table standard, un 401 qui ne marque aucun compte
    functions:
      - { name: list, class: read, description: …, input: …, examples: [{ title: Example 1, input: {} }], call: …, output: … }
```

Le nom servi d'une fonction est `<connecteur>.<nom>`, donc l'`operationId` tel quel. Les arguments passent en snake_case comme dans l'import (le nom de l'API reste dans `call`), et les clés de `x-oto-examples` les suivent. Une fonction sensible porte `confirm: { by: host }`. `host.families` reste tel qu'écrit ; chaque fonction de `functions` porte le `scope` de sa famille (`null` pour une famille sans scope), pour que l'hôte qui exécute le lise sans recalcul.

### Le jeton : `host_delegated`

`auth: { kind: host_delegated, issuer, resource }`, un huitième mode du [compte](compte.md), sans `credential`. Il dit : le jeton vient de l'hôte qui exécute, jamais d'un compte ; pour chaque appel, l'hôte obtient de `issuer` un jeton de la personne dont l'audience est `resource`, avec le `scope` de la fonction s'il en a un, et l'envoie en `Authorization: Bearer`. Deux voies, sans repli silencieux entre elles :

1. client first-party : échange de jeton (RFC 8693) du jeton reçu, `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, `subject_token`, `resource`, l'hôte authentifié comme application M2M ;
2. client third-party : le consentement de la personne (code d'autorisation), dont l'hôte garde le jeton de rafraîchissement, le mécanisme d'`oauth2_user`.

Un appel qui ne passe par aucune des deux est refusé, avec une erreur explicite. Les points d'accès sont ceux que l'émetteur publie (ses métadonnées), et les clients de l'hôte chez l'émetteur sont les siens, jamais dans la description, comme l'application OAuth d'un `oauth2_user`.

### Les problèmes

Trois temps, comme le profil Custom API : le document et ses extensions ; puis, sur un document sans problème, chaque connecteur construit passe le schéma du format, ses exemples, puis les règles du format. Chaque problème dit sa place dans le document OpenAPI (`paths./shares.post.x-oto-class`).

- Lecture et import : `openapi_not_a_document`, `openapi_too_large`, `openapi_method_not_supported`, `openapi_body_not_json`, `openapi_body_not_object`, `openapi_cookie_parameter`, `openapi_ref_external`, `openapi_ref_too_deep`, `openapi_ref_missing`, `openapi_too_many_operations`, `openapi_server_variables`, `custom_api_argument_name_collision`.
- Profil : `host_openapi_version`, `host_server_not_relative`, `host_root_invalid`, `host_extension_unknown`, `host_extension_missing`, `host_extension_invalid`, `host_operation_id_invalid`, `host_name_collision`, `host_namespace_reserved`, `host_namespace_act` (un espace en `_act`, le côté qui agit), `host_summary_invalid`, `host_description_invalid`, `host_class_mismatch`, `host_input_not_closed`, `host_example_missing`, `host_confirm_reserved`, `host_confirm_missing`, `host_scope_invalid` (le scope d'une famille absent de la forme objet, ou hors de la RFC 6749 §3.3, à `x-oto.families.<famille>.scope`). Une famille ni texte ni objet, ou au libellé vide, est un `host_root_invalid` à sa place (`x-oto.families.<famille>`, `….label`) ; une clé inconnue d'une famille, un `host_extension_unknown`.
- Format : `host_schema_invalid`, `host_example_invalid`, `host_rule_broken` (la règle dans `rule`, `strip_path_overlaps`…).

## Décisions et alternatives écartées

- **`host_delegated` décrit les deux voies d'un coup** (proposé le 22/11/2026) : la voie se choisit à l'appel, selon le client qui appelle, que seule l'exécution connaît. Deux modes, ou `oauth2_user` plus un mode d'échange, auraient fait choisir la voie dans le document, identique pour toutes les organisations. `issuer` y est en plus de `resource` : les deux voies passent par lui, et l'hôte peut vérifier qu'il est le sien ou un émetteur qu'il connaît.
- **La transmission du jeton reçu, telle quelle** : écartée par Alexis le 22/11/2026. Le jeton n'est jamais transmis ; l'audience est toujours l'API de l'hôte. L'impersonation par l'API d'administration de l'émetteur et une assertion signée par oto, que l'hôte vérifierait hors de l'émetteur, sont écartées aussi.
- **Un document du format par espace de noms** (proposé) : le nom `espace.verbe` est exactement `<connecteur>.<fonction>` ; un seul connecteur aurait réécrit les noms.
- **`confirm: { by: host }` plutôt qu'un gabarit** (proposé) : le récapitulatif vient de l'hôte, qui sait le rédiger ; un gabarit inventé aurait menti à la personne.
- **Libellés, familles, rôles et `summary` hors du format** (proposé) : ce sont des données de présentation de l'hôte, sans sens pour un connecteur partagé ; ils vivent à côté des documents, dans `functions` de la définition. Les ajouter au format reste possible le jour où un autre consommateur s'en sert.
- **Le scope sur la famille, en forme objet facultative** (proposé le 23/11/2026) : une famille est son libellé, comme avant, ou `{ label, scope }`. Les documents déjà écrits restent valides, et le scope suit l'unité que la personne voit (la famille) plutôt que chaque opération. Une table `scopes` à part, à côté de `families`, aurait gardé les familles intactes mais dédoublé leurs clés ; une extension `x-oto-scope` par opération aurait laissé deux fonctions d'une même famille demander des droits différents. La forme objet exige son scope : une famille sans scope s'écrit par son seul libellé, et rien ne se devine.
- **La classe déclarée, non déduite** (Alexis, 22/11/2026) : seule la déclaration distingue `write` de `sensitive` ; le verbe ne sert qu'au contrôle de cohérence.
- **Les formes de l'hôte refusées hors du profil** : `host_delegated` et `confirm.by` sont refusés par le profil Custom API et, pour un connecteur partagé, par la règle 26 de la [fabrique](fabrique.md).

## Sécurité et confidentialité

- La description ne porte aucun secret : ni jeton, ni identifiant ou secret de client.
- Le jeton est propre à l'API de l'hôte (audience `resource`) : un jeton reçu par oto ne part jamais tel quel ailleurs.
- L'adresse de l'hôte est un réglage `url` : https seulement, gardée à chaque appel par l'hôte qui exécute, aucune redirection suivie.
- Un document OpenAPI est une donnée : références locales seulement, profondeur et taille bornées. L'émetteur est jugé tel qu'écrit, sans DNS.

## Écart avec le code

- Écrit : l'import OpenAPI en TypeScript (`fromOpenApi`, `standardErrors`), le profil en Python et en TypeScript, `host_delegated` et `confirm.by: host` dans le schéma, les types TypeScript et `toDefinition`, la règle 26, les cas communs et leurs tests (`tests/test_host_cases.py`, `ts/test/host.test.ts`).
- Le moteur Python de la lib n'exécute pas `host_delegated` : il le refuse (`Unsupported`). L'exécution (échange, consentement, rafraîchissement, `/me/functions`, confirmation relayée, `problem+json`) reste à écrire dans oto-core, puis dans le backend d'oto 1.
- La lecture d'un document par son adresse (`read_url`) n'existe qu'en Python ; l'hôte TypeScript lit le document avec sa propre garde d'adresse.
- Les messages des problèmes de schéma diffèrent entre Python et TypeScript (les codes et les places sont les mêmes) ; un `info.version` écrit en nombre décimal (`1.0`) se lit `1.0` en Python et `1` en TypeScript.

## Questions ouvertes

- Une fonction sans scope (famille écrite par son libellé) : l'hôte qui exécute demande l'audience seule. Faut-il, pour un hôte qui filtre par droits, exiger un scope sur chaque famille ?
- `modes: [byo_user]` est porté parce que le schéma l'exige avec `auth` ; il ne sert qu'à oto 1.
- 100 opérations au plus, la borne de l'import.

## Historique

- 2026-11-23 : scope OAuth par famille (`{ label, scope }`, RFC 6749 §3.3), porté par chaque fonction de la définition, code `host_scope_invalid` — demandé pour l'échange et le consentement, forme proposée.
- 2026-11-22 : profil hôte, import OpenAPI porté en TypeScript, mode `host_delegated` (échange RFC 8693 ou consentement, sans repli), `confirm.by: host`, règle 26 — contrat et jeton décidés par Alexis, forme de la description proposée (source : la conception des fonctions d'un hôte, dans oto-core).
