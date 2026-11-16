# Le corps d'une requête

- **Statut** : validé par le mainteneur le 29/09/2026 (issu du [format de description](format-de-description.md))
- **Dernière révision** : 2026-11-11

## Résumé

`call.body` dit le corps JSON d'une requête sous deux formes : une table qui associe des noms côté API à des arguments, emboîtée au besoin sous des clés du corps (une enveloppe, les variables d'une requête GraphQL), ou le nom d'un seul argument, un objet strict que l'agent compose et qui forme le corps entier. Les constantes de corps (`constants.body`), l'encodage d'un argument (`encode`) et les parties d'un corps multipart (`files`, [fichiers](fichiers.md)) s'y ajoutent selon des règles fixes ; `call.body_list` envoie le corps construit seul élément d'une liste JSON, pour un tiers dont le corps est un tableau nu d'une tâche. Ce que le format ne sait pas dire reste au client, écrit à la main.

## Contexte

- Beaucoup d'API prennent leurs arguments au premier niveau du corps ; d'autres sous une clé (`{"data": {…}}`, JSON:API, GraphQL), d'autres encore un objet entier (un contact, les réglages d'une campagne), parfois avec des clés que l'utilisateur crée (variables d'un lead, champs personnalisés d'un CRM).
- L'hôte d'oto 2 n'envoie que ce que donne l'appel : aucun défaut n'est rempli à sa place.

## Objectifs et non-objectifs

- Décrire, pour les deux langages et pour l'hôte, la requête exacte qui part de l'entrée validée.
- Un corps est une table ou un argument, jamais les deux.
- Hors objectif : une requête bâtie selon les arguments donnés (un champ GraphQL demandé seulement quand un argument le dit), un argument emballé dans une liste, une valeur dérivée d'un argument, un verbe choisi par un argument, un corps qui est une liste de plusieurs objets.

## Conception

### Le corps en table

`body` associe un nom côté API à un argument ; il est envoyé quelle que soit la méthode, `DELETE` compris. `constants.body` ajoute des valeurs envoyées à chaque appel, hors du schéma (`{ body: { draft: true } }`) ; `encode: { filter: json }` sérialise un argument structuré en une chaîne. Chaque argument va à un seul endroit, chaque nom côté API reçoit une seule valeur (règles 1 et 3 de la [fabrique](fabrique.md)).

### Le corps entier

Décidé par le superviseur le 2026-10-22 (règle 20 de la [fabrique](fabrique.md)) : `call.body` nomme un argument au lieu d'associer des noms côté API à des arguments, quand le tiers prend pour corps un objet entier que l'agent compose (un contact, les réglages d'une campagne, une étape de séquence). La forme est celle d'une référence d'argument partout ailleurs dans le format (`request_param`, `cost.items`, `job_id.to`) : un nom, là où une table associerait des noms. Un corps est ainsi une table ou un argument, jamais les deux : le schéma ne connaît qu'une clé `body`.

```yaml
input:
  type: object
  additionalProperties: false
  required: [campaign_id, lead]
  properties:
    campaign_id: { type: string }
    lead:
      type: object
      additionalProperties: false
      required: [email]
      properties:
        email: { type: string, personal: true }
        first_name: { type: string }
call:
  method: POST
  path: "/campaigns/{campaign_id}/leads"
  body: lead
```

- **Ce qui part** : l'objet validé contre le schéma d'entrée, tel quel, en corps JSON (`Content-Type: application/json`, comme un corps en table). Une propriété absente ne part pas, une propriété `null` part `null`, comme une entrée d'une table ; aucune clé n'est ajoutée, renommée ni retirée.
- **L'argument est un objet strict** (`body_argument_not_strict`) : `type: object` (pas `[object, "null"]`), `additionalProperties: false`, au moins une propriété déclarée, sans `patternProperties`. Ce que l'agent peut mettre au corps est dit clé par clé, jamais un sac libre que le tiers lirait à sa façon ; une propriété de l'objet reste libre de sa propre forme (une table de variables, `additionalProperties: { type: string }`).
- **Ou à clés libres en texte** (décidé par le mainteneur le 2026-10-22) : à la racine de l'argument seulement, `additionalProperties` peut valoir, au lieu de `false`, exactement `{ type: string, personal: true }`, quand le tiers prend au premier niveau du corps, à côté de ses champs connus, des clés que l'utilisateur crée (les variables d'un lead, les champs personnalisés d'un contact). Les clés déclarées gardent leur schéma ; toute autre clé est un texte, et une donnée personnelle ([format](format-de-description.md), « Donnée personnelle ») : l'hôte ne journalise pas sa valeur, par prudence, puisque rien ne dit ce qu'elle porte. Une clé libre qui n'est pas un texte (nombre, `null`, objet, liste) est refusée par le schéma d'entrée, avant tout envoi ; elle part, sinon, dans le même corps JSON que les clés déclarées, telle quelle. Toute autre forme reste `body_argument_not_strict` : `true`, un autre type (nombre, objet, `[string, "null"]`), un schéma sans `personal` ou avec `personal: false`, un mot de plus (`maxLength`, `description`), des clés libres avec `patternProperties` ou sans propriété déclarée. `maxLength` n'est pas admis : la source qui a motivé la forme (une OpenAPI qui déclare `additionalProperties: { type: string }`) ne borne pas la valeur, et une forme sans besoin serait à tenir des deux côtés ; il s'ajoutera avec la description qui en aura un. Un objet imbriqué dans l'argument garde sa forme libre, comme avant.

```yaml
contact:
  type: object
  additionalProperties: { type: string, personal: true }
  required: [email]
  properties:
    email: { type: string, personal: true }
    score: { type: integer, minimum: 0 }
```

- **Ou à clés libres à valeur simple** (décidé par le mainteneur le 2026-10-22) : au même endroit, `additionalProperties` peut valoir exactement `{ type: [string, number, boolean], personal: true }`, quand le tiers type les champs que l'utilisateur crée (les champs personnalisés d'un CRM, nombre, vrai/faux ou date). Chaque description choisit l'un des deux genres. Toute clé libre est alors un texte, un nombre ou un vrai/faux, et une donnée personnelle, quel que soit son type ; `null`, un objet ou une liste sont refusés par le schéma d'entrée, avant tout envoi ; une date est un texte. `number` et pas `integer` : un champ nombre du tiers prend une décimale, et JSON ne distingue pas `2` de `2.0`. Les trois types s'écrivent dans cet ordre : une seule écriture, que la fabrique et oto-core comparent telle quelle ; un autre ordre, un type de plus ou de moins (`integer`, `"null"`, `object`), un schéma sans `personal` ou un mot de plus (`maxLength`, toujours non admis) restent `body_argument_not_strict`.

```yaml
account:
  type: object
  additionalProperties: { type: [string, number, boolean], personal: true }
  required: [name]
  properties:
    name: { type: string }
```

- **Il est requis** (`body_argument_optional`) : le corps n'est jamais absent ; un objet vide, si le schéma l'admet, part `{}`. Il est **déclaré** dans l'entrée (`body_argument_unknown`).
- **Il ne va nulle part ailleurs** (`body_argument_elsewhere`) : ni chemin, ni query, ni en-tête, ni partie fichier ; les autres arguments gardent leurs places (chemin, query, en-têtes). `encode` ne le nomme pas (`body_argument_encoded`) : il part en objet, pas en chaîne.
- **Rien d'autre n'entre au corps** (`body_argument_not_alone`) : ni constante de corps (`constants.body`), ni partie fichier (`files`, dont le corps est multipart) ; ce que l'hôte envoie est l'objet validé, exactement.
- **La méthode envoie un corps** (`body_argument_method`) : jamais `GET` ; `DELETE` en porte un, comme un corps en table.
- **Pas de pagination** (`body_argument_paginated`) : une recherche paginée en `POST` porte son curseur dans son corps, que cette forme ne sait pas écrire sans changer l'objet de l'agent ; un curseur en query à côté d'un corps entier n'a aucun cas connu. À rouvrir avec une description qui en a besoin.
- **Ce qui ne change pas** : les exemples se valident contre l'entrée comme tout exemple (un objet hors schéma est refusé par la fabrique) ; `class`, `act` et `confirm` sont indépendants du corps (`confirm.summary` peut citer l'argument, que l'hôte écrit en JSON) ; `personal` marque une propriété de l'objet comme toute propriété. Tout argument a une place (règle 1) : celle de l'argument du corps entier est le corps.
- La fabrique refuse ce qui précède avec ces codes, dans cet ordre, les mêmes que ceux d'oto-core ; les cas communs (`connectors/_cases/body.yaml`, [fabrique](fabrique.md)) disent, sur des appels simulés, la requête envoyée, les entrées refusées avant tout envoi et les fonctions refusées.

### Le corps emboîté

Décidé par le mainteneur le 2026-10-26 (règle 22 de la [fabrique](fabrique.md)) : dans un corps en table, la valeur d'une clé est un argument ou une table de même forme, à toute profondeur, quand le tiers prend les arguments sous une clé du corps : une enveloppe (`{"data": {…}}`), les variables d'une requête GraphQL (`{"query": "…", "variables": {…}}`), une ressource typée (`{"data": {"type": …, "attributes": {…}}}`). La table s'écrit dans la forme du corps envoyé, comme `constants.body` ; une clé est une clé, jamais un chemin : un point n'y est qu'un caractère.

```yaml
# Une API GraphQL : le texte de la requête est constant, les arguments sont ses variables.
input:
  type: object
  additionalProperties: false
  required: [issue_id]
  properties:
    issue_id: { type: string }
    title: { type: string }
    team_id: { type: string }
call:
  method: POST
  path: /graphql
  body: { variables: { id: issue_id, input: { title: title, teamId: team_id } } }
  constants:
    body: { query: "mutation UpdateIssue($id: String!, $input: IssueInput!) { updateIssue(id: $id, input: $input) { ok } }" }
```

- **Ce qui part** : la constante du corps, puis chaque argument donné posé à son chemin, les objets parents créés au besoin : `{"query": "mutation …", "variables": {"id": "i-1", "input": {"title": "Spring"}}}`. Un argument absent ne part pas et ne crée aucun parent (sans `title` ni `team_id`, `variables` ne porte que `id`) ; un `null` part `null` ; `encode` s'applique à l'argument avant qu'il soit posé, comme au premier niveau.
- **Constante et table ne se rejoignent que par des objets** (`body_key_twice`) : sous un même parent, elles portent des clés distinctes, sauf une clé qui est un objet des deux côtés, jugée alors au niveau en dessous. Refusés : un argument sur une clé constante (`data.format` constant et argument), un argument à la place d'un objet constant (`data` argument, `data.format` constant), une table sous une constante qui n'est pas un objet. La règle vaut au premier niveau : `draft` à la fois constant et argument est refusé avec ce code.
- **Une table emboîtée porte au moins une entrée** (`body_nested_empty`) : vide, elle ne poserait rien. Une table de premier niveau vide reste l'absence de corps en table.
- **Pas de table emboîtée dans un corps multipart** (`body_nested_multipart`) : ses parties sont des textes, une par clé de premier niveau.
- **La pagination sous une clé emboîtée** : `request_param` nomme un argument, où qu'il soit placé ; chaque page se compose comme la première, le curseur à la place de l'argument. `next`, `more` et `total` sont des chemins pointés de la réponse : `body: { variables: { first: first, after: after } }`, `request_param: after`, `next: data.issues.pageInfo.endCursor`, `more: data.issues.pageInfo.hasNextPage`.
- **Ce qu'elle ne dit pas**, qui reste au client : une requête GraphQL bâtie selon les arguments donnés (un champ demandé seulement quand un argument le dit) ; un argument emballé dans une liste (`filterGroups: [{filters}]`) ; une valeur dérivée d'un argument (un identifiant tiré d'un nom, une échéance complétée) ; un verbe choisi par un argument (`PATCH` ou `PUT`).
- **Sortie TypeScript** : la table telle quelle (`RequestSpec.body`, de type `BodyMap`). Un paquet d'hôte qui n'attend qu'une table plate la refuse à la déclaration (une valeur qui n'est pas un nom d'argument : « request places [object Object], which the schema does not declare »), au lieu d'envoyer un corps faux.
- La fabrique refuse ce qui précède avec ces codes, dans cet ordre, les mêmes que ceux d'oto-core ; les cas communs (`connectors/_cases/nested_body.yaml`, [fabrique](fabrique.md)) disent, sur des appels simulés, la requête envoyée (pagination comprise), les entrées refusées avant tout envoi et les fonctions refusées.

### Le corps en liste

Décidé par le responsable produit le 2026-11-11 (règle 23 de la [fabrique](fabrique.md)) : `call.body_list: true` dit que le corps construit part seul élément d'une liste JSON, quand le tiers prend pour corps un tableau d'objets dont l'appel n'envoie qu'un (DataForSEO : `[{"keyword": "…", "location_code": 2250}]`, une tâche par requête). Le drapeau ne change pas la façon de construire le corps : une table, emboîtée ou non, après sa constante, ou l'argument du corps entier, tel que validé ; il l'emballe, au dernier geste.

```yaml
call:
  method: POST
  path: /serp/google/organic/live/advanced
  body: { keyword: keyword, location_code: location_code, depth: depth }
  body_list: true
```

- **Ce qui part** : `[ <le corps construit> ]`, en JSON (`Content-Type: application/json`) ; sans aucun argument donné, un corps en table part `[{}]`.
- **Un drapeau, pas une forme** : `body_list` ne vaut que `true` (le schéma refuse `false`, qui ne dirait rien) ; il se combine avec chacune des trois formes de `body` et avec `constants.body`, la pagination au corps comprise (le curseur se pose dans l'objet, chaque page comme la première).
- **La fonction a un corps** (`body_list_without_body`) : une table non vide, l'argument du corps entier ou une constante du corps.
- **La méthode envoie un corps** (`body_list_method`) : jamais `GET`.
- **Pas de partie fichier** (`body_list_multipart`) : un corps multipart est un formulaire, jamais une liste JSON.
- **Sortie TypeScript** : `RequestSpec.bodyList: true`. Un paquet d'hôte antérieur ne lit pas la clé et enverrait l'objet nu, un corps faux que le tiers refuse (DataForSEO : `40503 POST Data Is Invalid`) : l'épingle de ce dépôt dont une description s'en sert monte avec celle d'oto-core.
- La fabrique refuse ce qui précède avec ces codes, dans cet ordre, les mêmes que ceux d'oto-core ; les cas communs (`connectors/_cases/list_body.yaml`, [fabrique](fabrique.md)) disent, sur des appels simulés, la requête envoyée (table, table emboîtée et constante, constante seule, argument entier), les entrées refusées avant tout envoi et les fonctions refusées.

## Décisions et alternatives écartées

- **Un corps en liste dit par une forme de `body`** (`body: [ { … } ]`, une table dans une liste) : écarté le 2026-11-11. Il aurait fallu doubler chaque forme (table, emboîtée, argument entier) et ses contrôles ; le drapeau les garde toutes, et une table dans une liste laisserait croire qu'une liste de plusieurs tables se dit.

- **Une clé de corps en chemin pointé** (`"variables.input.teamId": team_id`) : écartée le 2026-10-26. Une table emboîtée a la forme du corps envoyé et celle de `constants.body` ; un chemin pointé demanderait une syntaxe, ses refus (segment vide, chemins qui se chevauchent) et une clé qui contient un point ne se dirait plus ; et un paquet d'hôte antérieur enverrait la clé pointée telle quelle, un corps faux, là où il refuse une table emboîtée à la déclaration.
- **Un corps qui serait à la fois table et argument** : écarté le 2026-10-22, le schéma ne connaît qu'une clé `body`.
- **`maxLength` sur les clés libres** : non admis le 2026-10-22, faute de besoin ; il s'ajoutera avec la description qui en aura un.

## Sécurité et confidentialité

- Les clés libres d'un corps entier sont toutes donnée personnelle : rien ne dit ce qu'elles portent, et l'hôte ne journalise pas leur valeur.
- Un argument qui est un secret de tiers (le mot de passe d'une boîte) serait journalisé par l'hôte : `personal` ne dit qu'une personne (« Questions ouvertes »).

## Écart avec le code

- Oto-core exécute le corps entier (`body-argument.ts`) et le corps emboîté (`body-table.ts`) à partir des commits qui les écrivent ; un paquet antérieur refuse à la déclaration un corps qui n'est pas une table plate, et avec lui toute la déclaration de l'hôte : l'épingle de ce dépôt dont une description s'en sert monte avec celle d'oto-core.
- Le moteur Python de la lib (`oto/connectors/engine/request.py`) envoie les trois formes et le corps en liste : `tests/test_body_cases.py`, `tests/test_nested_body_cases.py` et `tests/test_list_body_cases.py` jouent les cas communs contre lui, devant un faux tiers.
- Oto-core envoie le corps en liste (`engine.ts`) et refuse à la déclaration, avec les codes de la règle 23, un drapeau sans corps, en `GET` ou avec une partie fichier (`body-list.ts`), à partir du commit qui les écrit.

## Questions ouvertes

- Un corps qui est un tableau nu de plusieurs éléments ou de valeurs simples (les membres d'une liste, les catégories d'une écriture comptable), un corps en formulaire (`meta_ads`, les groupes d'utilisateurs de `slack`). Le tableau d'un seul objet se dit par `body_list`.
- Un argument emballé dans une liste d'objets (`filterGroups: [{filters}]`), un argument scalaire emballé en liste d'un élément, deux arguments fusionnés en une liste, un argument dans une liste (l'assigné d'une tâche) ; un argument envoyé à deux places (chemin et query) ; un objet formé depuis un argument (`entity: {id}` depuis un identifiant nu) ; une valeur dérivée (une échéance complétée à minuit) ; un second appel ; un corps qui est un dictionnaire auquel le client ajoute l'identifiant.
- Un attribut à choix multiple (une liste de textes) parmi des clés libres à valeur simple, que la règle 20 refuse ; une liste d'enregistrements dont chacun porte un `attributes: {type}` que pose le client.
- Un argument qui est un secret de tiers (mots de passe d'une boîte), que l'hôte journaliserait.

## Historique

- 2026-10-06 : constantes de requête et encodage JSON d'un argument — décidé par le mainteneur.
- 2026-10-22 : le corps entier : `call.body` nomme un argument, un objet strict et requis, envoyé tel que validé en corps JSON ; ni ailleurs, ni encodé, ni avec une constante de corps ou une partie fichier, ni en `GET`, ni paginé ; règle 20 de la fabrique et cas communs `connectors/_cases/body.yaml` — décidé par le superviseur.
- 2026-10-22 : le corps entier à clés libres en texte (`additionalProperties: { type: string, personal: true }`, à la racine de l'argument seulement) — décidé par le mainteneur.
- 2026-10-22 : le corps entier à clés libres à valeur simple (`{ type: [string, number, boolean], personal: true }`, les trois types dans cet ordre) — décidé par le mainteneur.
- 2026-10-26 : le corps emboîté : dans un corps en table, la valeur d'une clé est un argument ou une table de même forme ; constante et table ne se rejoignent que par des objets (`body_key_twice`) ; règle 22 et cas communs `connectors/_cases/nested_body.yaml` — décidé par le mainteneur.
- 2026-11-03 : sorti de la note « Format de description » lors de son redécoupage en documents par sujet ; les résumés par connecteur sortent pour la carte du parc et l'en-tête de chaque description — décidé par le responsable produit (source : demande de rationalisation des connecteurs).
- 2026-11-11 : le corps en liste : `call.body_list: true`, le corps construit seul élément d'une liste JSON ; jamais sans corps, en `GET` ni avec une partie fichier ; règle 23 de la fabrique et cas communs `connectors/_cases/list_body.yaml` — décidé par le responsable produit (source : demande de description de DataForSEO, « tout décrire »).
