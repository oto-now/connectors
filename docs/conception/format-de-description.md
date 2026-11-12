# Format de description d'un connecteur

- **Statut** : validé par le mainteneur le 29/09/2026
- **Dernière révision** : 2026-11-11

## Résumé

Un connecteur partagé se décrit dans un fichier YAML, `connectors/<nom>/connector.yaml`, validé par `connectors/connector.schema.json` (JSON Schema 2020-12). L'unité du format est la fonction servie avec son appel : ce que l'agent voit (nom, description en anglais, schéma d'entrée strict, classe, exemples, refus nommés) et la requête derrière (méthode, chemin, répartition des arguments, pagination, sortie). Ce qui ne se décrit pas reste du code écrit à la main. Ce document est l'entrée : le fichier, le bloc `connector`, le bloc `functions` et les contrôles ; les blocs spécialisés ont chacun leur document (table ci-dessous). La [fabrique](fabrique.md) en tire Python et TypeScript ; tout client ajouté à la lib entre au format.

## Contexte

- La lib porte une centaine de clients Python écrits à la main. Au relevé du 29/09, le schéma d'un outil y vient de la signature et sa description de la docstring ; aucune classe n'est déclarée, aucun refus n'est nommé, la confirmation se fait au cas par cas.
- Le paquet d'oto 2 sert des fonctions qui déclarent classe, exemples, refus et résumé de confirmation. Elles ne sont pas des outils MCP : elles se trouvent, se lisent et s'appellent par les six outils figés du paquet (oto-pkg : docs/conception/connecteurs-et-comptes.md).
- Un connecteur partagé sert deux langages : Python pour la lib, TypeScript pour le paquet. Écrit deux fois à la main, il divergerait ; d'où une description unique.
- Relevé du 29/09 sur 114 connecteurs de la lib, par lecture du code et à seuil arbitraire : 54 enveloppes d'API REST, 44 REST avec logique, 16 hors format (3 navigateur, 7 données ouvertes ou base embarquée, 3 SDK, 2 traitement local, 1 asynchrone).
- Se décrit : authentification et réglages d'un compte, URL de base, verbe et chemin, répartition des arguments, corps en table, emboîté ou entier, constantes et en-têtes constants, encodage JSON d'un argument, schéma, table des erreurs et signal de refus du service, délai d'un appel, fichiers envoyés et rendus, contrôles simples avant l'appel et sur la réponse, projection de sortie, coût d'un appel, travail asynchrone et fonction longue, pagination, rythme maximal du tiers, sonde et solde d'un compte payant. Reste à la main : fusion de plusieurs appels, refus locaux que les contrôles déclarés ne disent pas, construction d'un mail, identité opérée.

## Objectifs et non-objectifs

- Une seule source par connecteur partagé, d'où sortent les deux langages.
- Le contrat vu de l'agent s'écrit une fois : classe, exemples validés, refus nommés, en anglais.
- Un fichier invalide est refusé avant toute génération.
- Ce qui ne se décrit pas reste possible, par une entrée `handwritten` dont le schéma, la classe et les refus restent déclaratifs.
- Hors objectif : convertir les connecteurs existants. Ils restent en Python tels quels ; seuls les nouveaux connecteurs partagés et ceux qu'oto 2 demande entrent au format, et un existant réclamé entre comme un nouveau.
- Hors objectif : décrire un connecteur propre à un hôte, écrit dans l'hôte au contrat du paquet (oto-pkg : docs/conception/connecteurs-et-comptes.md).
- Hors objectif : porter un secret, un compte ou une activation ([compte](compte.md)).
- Hors objectif : résumer chaque connecteur. La vue d'ensemble du parc (type, fonctions, sonde, coût) est la carte générée par la fabrique, `docs/carte-du-parc.md` ; le détail d'un connecteur (sources et dates, choix, écarts avec son client, ce qui reste hors du fichier) est le commentaire d'en-tête de son `connector.yaml`.

## Conception

### Les documents du format

| Document | Ce qu'il dit |
|---|---|
| Ce document | Le fichier, le bloc `connector`, le bloc `functions` (classe, côté d'action, donnée personnelle, délai, pagination), le bloc `exposure`, les contrôles. |
| [Sortie d'une fonction](sortie.md) | `output.items`, `strip`, `projection`, `sample`. |
| [Compte](compte.md) | `auth`, `credential`, `settings`, gabarits d'adresse, `oauth2_user`, compte de service (`oauth2_jwt_bearer`), compte de l'hôte et compte rattaché. |
| [Niveaux d'accès](niveaux-d-acces.md) | `access_levels`, `scope_implies`, `scopes` d'une fonction. |
| [Corps d'une requête](corps-de-requete.md) | Corps en table, emboîté, entier, et ses clés libres. |
| [Fichiers](fichiers.md) | `call.files` et `output.file`. |
| [Erreurs et signal de refus](signal-de-refus.md) | `errors`, `refusals`, `marks_account`, `body`. |
| [Coût et solde](cout-et-solde.md) | `cost`, `probe`, `probe.balance`. |
| [Travaux longs](travaux-longs.md) | `async` et `background`. |

### Le fichier

- Un dossier par connecteur sous `connectors/` : son `connector.yaml` et, si besoin, son code écrit à la main dans les deux langages. `connector.name` est le nom du dossier.
- YAML, déclaré en version 1.2, validé par un schéma JSON 2020-12 que les deux langages savent lire. Le fichier porte en tête `# yaml-language-server: $schema=../connector.schema.json`, puis un commentaire d'en-tête : sources relues et leurs dates, choix et leurs raisons, écarts nommés avec le client Python, ce qui reste hors du fichier et pourquoi.
- Trois blocs : `connector` (un par fichier), `functions` (au moins une), `exposure` (facultatif). Toute clé inconnue fait refuser le fichier.
- Chaque valeur vient du client Python ou de la référence publique de l'éditeur ; quand les deux divergent, le fichier suit le client, et l'écart se nomme dans l'en-tête.

### Le bloc `connector`

| Champ | Obligatoire | Sens |
|---|---|---|
| `name` | oui | Identifiant stable (`^[a-z][a-z0-9_]*$`), premier segment des outils. |
| `label` | oui | Nom affiché. |
| `namespace` | oui | Préfixe des outils exposés, souvent égal à `name`. |
| `version` | oui | Version du connecteur, semver : elle monte avec tout commit qui change sa description ou son client (« Contrôles », ci-dessous). |
| `api_version` | oui | Version de l'API tierce, telle que l'éditeur la nomme. |
| `base_url` | sauf `base_urls` ou tout `handwritten` | Racine `https://` des chemins d'appel, sans `/` final ; gabarit possible sur les réglages. |
| `base_urls` | non, exclusif de `base_url` | Une racine par valeur d'un réglage `choice` : `{ setting: region, values: { us: …, eu: … } }`. |
| `auth` | oui | Mode d'authentification, voir [compte](compte.md). |
| `credential` | sauf `none` et `oauth2_user` | Champs fournis par le consommateur : `name`, `label`, `secret` (booléen), `holder` (`account` par défaut, ou `host`) ; un seul, secret, en `oauth2_jwt_bearer` : voir [compte](compte.md). |
| `account_identity` | non | Identité d'un compte rattaché chez un tiers : `name`, `label`, `pattern` (expression ancrée), citée par `base_url` seulement, voir [compte](compte.md). |
| `settings` | non | Réglages non secrets d'un compte, voir [compte](compte.md). |
| `modes` | oui | Qui peut porter un compte : `platform`, `byo_user`, `byo_org`. |
| `timeout_s` | non (30) | Délai d'un appel, en secondes, de 1 à 300 (le plafond qu'exécute le paquet d'oto 2) ; une fonction peut déclarer le sien. |
| `quota` | non | Quota par défaut sur une clé de la plateforme ; n'existe que si `modes` contient `platform`. |
| `query_arrays` | non | Encodage des listes en query : `repeat`, `brackets`, `comma`. |
| `headers` | non | En-têtes constants de toute requête : `{ Notion-Version: "2025-09-03" }`. Jamais un secret ni l'en-tête d'authentification. |
| `rate_limit` | non | Rythme maximal du tiers, par credential : `{ requests: 4, seconds: 1 }`. |
| `probe` | non | Sonde de la connexion : `{ function: get_company, non_empty: [scopes] }`, une lecture sans argument requis, jamais décomptée d'un quota ; chaque chemin de `non_empty` doit être non vide dans sa réponse ; `balance`, le solde d'un compte payant ([coût et solde](cout-et-solde.md)). |
| `errors` | oui | Table commune à toutes les fonctions : `status` (un ou plusieurs codes HTTP), `code` (refus nommé), `message` (anglais), `retryable`, et facultatifs `body` (condition sur le corps) et `marks_account` ([erreurs et signal de refus](signal-de-refus.md)) ; la première entrée qui s'applique l'emporte. |

Les champs du compte (`auth`, `credential`, `settings`, `account_identity`, `base_urls`) sont dits dans [compte](compte.md).

### Le bloc `functions`

| Champ | Obligatoire | Sens |
|---|---|---|
| `name` | oui | Un verbe et son objet : `list_estimates`. |
| `class` | oui | `read`, `write` ou `sensitive`. |
| `act` | non (déduit de la classe) | La fonction engage ou détruit (envoi, corbeille, archive, déplacement) : booléen, `false` par défaut pour `read`, `true` pour `write` et `sensitive`. Une simple préparation, comme un brouillon, déclare `act: false`. Jamais `false` sur `sensitive`, jamais `true` sur `read` (la fabrique refuse). |
| `effect` | non, `sensitive` seulement | Ce que fait une fonction sensible une fois confirmée : `send` (quelque chose part vers un tiers : un message, des pistes inscrites dans une campagne active) ou `destroy` (supprime, arrête pour de bon, remplace sans garder). Pas de défaut ; refusé sur `read` et `write` (le schéma et la règle 9). Un hôte qui sert les fonctions sensibles par effet, le lecteur d'oto 1, refuse au montage une fonction sensible qui ne le dit pas. |
| `scopes` | si le connecteur a des niveaux d'accès, interdit sinon | Les droits OAuth que la fonction exige, tous ([niveaux d'accès](niveaux-d-acces.md)). |
| `description` | oui | En anglais, 1000 caractères au plus (borne du paquet d'oto 2) : ce que l'agent lit. |
| `input` | oui | JSON Schema 2020-12 d'un objet strict (`type: object`, `additionalProperties: false`, `properties`), servi et validé tel quel. Une propriété peut porter `personal: true` (« Donnée personnelle », ci-dessous). |
| `examples` | oui, au moins un | `title` et `input`, chaque `input` validé contre `input`. |
| `refusals` | non | `code`, `when` (statut HTTP ou condition), `message` : surcharge ou complète la table commune ; sous un statut seulement, `body` et `marks_account` comme dans la table. |
| `checks` | non | Contrôles de l'entrée avant l'appel, chacun nomme un refus : `{ kind: equal_sums, refusal: entry_unbalanced, items: ledger_entry_lines, fields: [debit, credit] }` (sommes égales, chaînes décimales sommées exactement). |
| `expect` | non | Contrôles de la réponse, chacun nomme un refus : `{ kind: non_empty, refusal: pdf_missing, path: public_file_url }`. |
| `call` | sauf `handwritten` | `method`, `path` relatif avec `{param}`, puis `query`, `body` (envoyé quelle que soit la méthode, `DELETE` compris), `headers` qui associent un nom côté API à un argument ; dans `body`, la valeur d'une clé peut aussi être une table de même forme, emboîtée ; `body` peut aussi nommer un seul argument, un objet strict, qui forme le corps entier ([corps d'une requête](corps-de-requete.md)) ; `constants` (`query`, `body`, `headers`) : des valeurs envoyées à chaque appel, hors du schéma, comme `{ body: { draft: true } }` ; `encode` : `{ filter: json }` sérialise un argument structuré en une chaîne ; `files` : les parties fichier d'un corps multipart ([fichiers](fichiers.md)) ; `body_list: true` : le corps construit part seul élément d'une liste JSON ([corps d'une requête](corps-de-requete.md)). Chaque argument va à un seul endroit, chaque nom côté API reçoit une seule valeur. |
| `handwritten` | exclusif de `call` | `python` (`module`, `function`) et `typescript` (`file`, `export`). |
| `pagination` | non | `kind` (`cursor` ou `page`), `request_param` (l'argument qui porte le curseur, placé en query ou au corps, emboîté ou non), `next`, `more` facultatif (booléen de la réponse, `false` à la dernière page : `has_more`), `total` facultatif, `max_pages`. Présente, elle ajoute `all_pages` et `max_pages` au schéma servi. |
| `output` | non | `items` (chemin de la liste), `strip` (clés retirées, chacune par son chemin), `projection` (clés de premier niveau gardées), `schema` (schéma de sortie, fermé et à propriétés), `sample` (exemple de réponse) : [sortie d'une fonction](sortie.md), `file` ([fichiers](fichiers.md)). |
| `timeout_s` | non (celui du connecteur) | Délai propre à la fonction, en secondes, de même type et mêmes bornes que celui du connecteur, qu'il remplace pour chacun de ses appels (chaque page d'une lecture paginée) : plus court (une page qui peut rester pendue) ou plus long (une réponse d'IA synchrone de plusieurs minutes), jamais égal (la fabrique refuse, règle 11) : « Délai par fonction ». |
| `async` | non | La fonction soumet un travail relevé plus tard : `collect` (la relève), `job_id` (`from`, `to`), `within_s` ou `no_deadline` ; voir [travaux longs](travaux-longs.md). |
| `background` | non | `true` : le tiers répond en minutes, l'hôte exécute la fonction en tâche de fond et rend aussitôt un identifiant de travail ; exige un `timeout_s` propre ; voir [travaux longs](travaux-longs.md). |
| `cost` | non | Ce que coûte un appel : `per` (`request`, `page` ou `item`), `quantity` (ce que coûte une requête, `request` et `page`), `items` et `times` (les listes comptées, `per: item`) ou `weights` (le poids des valeurs que liste chaque élément), `unit`, `settle` (la quantité réelle relue, et `scale`, son facteur vers `unit`) ; voir [coût et solde](cout-et-solde.md). |
| `confirm` | si `sensitive` | `summary`, gabarit du résumé montré avant exécution (`Validate invoice {id}: irreversible.`). |


**Lire et agir.** Chaque connecteur à effet se partage en deux côtés : `<connecteur>` (lire et simuler, brouillon compris) et `<connecteur>_act` (tout ce qui engage ou détruit : envoi, corbeille, archive, déplacement). Le partage se lit sur `act`, fonction par fonction : une seule description, un seul compte, et l'hôte sert les fonctions `act` sous le nom `<connecteur>_act.<fonction>`, les autres sous `<connecteur>.<fonction>`. `class` dit ce que fait une fonction et si elle se confirme ; `act`, de quel côté elle est servie : un brouillon est une écriture (`write`) qui reste du côté lecture (`act: false`). Une écriture qui crée un enregistrement, même réversible, garde `act: true` (une facture modifiable après finalisation, un devis sans brouillon), comme tout appel qui dépense des crédits du compte sur une personne ou une entreprise, ou qui envoie réellement (un test anti-spam). Une suppression est sensible.

**Donnée personnelle** : `personal: true` sur une propriété de l'entrée, à toute profondeur (élément d'une liste, branche d'un `anyOf`, clés libres d'un objet par `additionalProperties`), dit que sa valeur désigne une personne (un courriel, un nom, une adresse) : l'hôte ne la journalise jamais, ni dans les arguments, ni dans la cible, ni dans un message d'erreur du tiers qui la recopie. Le mot passe tel quel dans le schéma de la sortie TypeScript, que le paquet de l'hôte sert et valide ; il ne change ni l'appel ni la validation. Le client Python n'en fait rien. Jamais sur la racine, jamais dans `output.schema`, toujours `true` (la fabrique refuse, règle 15). Il dit une nature de l'argument, vraie chez tous les hôtes ; réserver une fonction au serveur de l'hôte reste le choix de l'hôte, jamais celui de la description.

```yaml
input:
  type: object
  additionalProperties: false
  required: [email]
  properties:
    email: { type: string, personal: true }
```

**Délai par fonction** : `timeout_s` sur une fonction remplace, pour elle seule, celui du connecteur, plus court ou plus long. La sortie TypeScript le porte en millisecondes (`timeoutMs` de la fonction, à côté de celui du connecteur) :

```yaml
connector:
  timeout_s: 120
functions:
  - name: get_company
    call: { method: GET, path: "/linkedin/company/{identifier}" }
    timeout_s: 60
```

**La sortie** (`output.items`, `strip`, `projection`, `sample`) : ce que l'agent reçoit de la réponse, et ce qu'elle perd avant de l'atteindre, est dit dans [sortie d'une fonction](sortie.md).

**Décrire un client existant.** Une description qui suit un client de la lib en prend une fonction par méthode, sous son nom, avec les arguments que la méthode nomme, sa query et son corps ; un test `tests/test_<nom>_description.py` lit le client et garde les deux égaux, pour chaque argument donné ou absent, et la classe de chaque fonction. Écart admis et nommé dans l'en-tête : une méthode qui choisit sa route ou son verbe par un argument devient une fonction par route ou par verbe, l'argument fixé par la fonction et jamais pris. Une méthode que le format ne sait pas dire reste hors du fichier plutôt que d'y être approchée, nommée dans l'en-tête.

**Ce que l'hôte envoie.** L'hôte n'envoie que ce que donne l'appel : `default` d'un argument n'est qu'une annotation, jamais remplie. Une valeur que le client pose toujours et que le tiers exige, ou dont l'absence change ce que fait le tiers, est donc exigée par le schéma ; une valeur égale au défaut du tiers ne se déclare pas. Le schéma refuse ce que le client refuse localement, et ce que la description ne sait pas convertir (une date seule là où le client la récrit en instant). Un drapeau qui ne part que vrai se déclare `const: true`.

**Pagination.** `pagination` ne se déclare que si la réponse dit la page suivante (un curseur, un numéro de page, `more`). Une réponse qui ne dit que la page courante et le total, une pagination par `offset`, ou une page suivante donnée par une adresse complète ne se déclarent pas : l'agent repasse lui-même l'argument, et la description de cet argument dit comment.

### Le bloc `exposure`

L'exposition est une projection, pas une propriété de la fonction. `mode` vaut `per_action` (un outil `<namespace>_<name>` par fonction), `per_connector` (un outil par connecteur, l'action en paramètre) ou `via_call` (rien n'est exposé, le verbe d'appel lit le registre). Le défaut est `per_connector`, fixé par le consommateur ; `mode` le surcharge pour un connecteur, `tool` nomme l'outil unique en `per_connector`. Les modes côté hôte, et le regroupement par famille proposé pour un ERP : [exposition des fonctions](https://github.com/oto-now/oto-core/blob/main/docs/conception/exposition-des-fonctions.md) (oto-core).

`exposure` et `modes` ne servent qu'à oto 1 : un hôte d'oto 2 sert ses fonctions par des outils fixes et dit qui fournit le compte par son propriétaire. Les deux blocs restent, marqués « oto 1 seulement », et partent quand oto 1 ne lira plus ce dépôt (décidé le 16/10).

### Le premier fichier : Sellsy

`connectors/sellsy/connector.yaml` décrit deux fonctions de lecture, `list_estimates` (pagination par curseur, dix pages au plus) et `get_estimate` (refus `estimate_not_found` sur 404), derrière `oauth2_client_credentials` en corps JSON. Ses valeurs ont été vérifiées le 30/09 contre la référence publique de l'API Sellsy v2 (spec v2.300.0) : champs de tri, `embed` de la liste et du détail, coût par requête, requête de jeton. Il sert de base aux variantes invalides du test, et d'exemple d'une fonction complète.

### Contrôles

`tests/test_connector_descriptions.py` vérifie que le schéma est un JSON Schema valide, que chaque description le respecte et porte le nom de son dossier, et que chaque exemple respecte l'entrée de sa fonction. Des variantes invalides du fichier Sellsy doivent être refusées : classe absente, clé inconnue, `call` et `handwritten` ensemble ou aucun des deux, fonction sensible sans `confirm`, entrée non stricte, aucun exemple, exemple hors bornes ou à argument inconnu, `embed` non documenté, `quota` sans `platform`, version hors semver, `exposure.mode` inconnu, schéma de sortie ouvert ou sans propriétés, `rate_limit` incomplet, en-tête constant non textuel, encodage inconnu, contrôle d'un genre inconnu, attente sans chemin, sonde à clé inconnue, `more` vide ; pour le délai d'une fonction, six de plus : nul, négatif, non entier, en texte, `null`, au-delà de 300 s (borne qui vaut aussi pour le connecteur). Les variantes propres à un bloc spécialisé sont dites dans son document. Les règles hors schéma sont vérifiées par la fabrique ([fabrique](fabrique.md)). `jsonschema` est un outil de test, pas une dépendance de la lib.

Deux gardes de CI :

- **Un client, une description.** Un client de la lib est une entrée directe de `oto/tools/` (paquet ou module), ou un sous-paquet d'une famille (`google`, un paquet par service). Le code qu'une description couvre est le client du même nom que le connecteur, entier, ou ce que `described_by` de `connectors/clients.yaml` y rattache : un client entier d'un autre nom (les paquets Google à `gmail`, `google_drive`, `google_calendar` et `google_sheets`), ou seulement des fichiers d'un client, le reste du client n'étant alors le code d'aucun connecteur (`microsoft` : la surface fichiers, le consentement, ses droits et le transport commun). Un client sans description est nommé dans `without_description`, avec une catégorie et sa raison. Trois catégories de principe admettent une entrée nouvelle : `not_a_service_client`, `not_describable` (le format ne sait pas le dire), `oto_service` (client d'un service d'oto) ; deux catégories en attente ne font que décroître : `before_the_rule` (antérieur à la règle du 05/10) et `not_described_yet`. `tests/test_clients_described.py` refuse un client qui n'est ni décrit ni nommé, et une entrée qui ne dit plus vrai (client ou fichier absent, connecteur sans description, exception dont le client a reçu sa description). Un client rattaché à un connecteur ne l'est qu'à un seul.
- **Gardes par commit.** `python -m fabrique.commits <avant> <après>`, lancé par le job `commits` de la CI avec l'historique entier, juge chaque commit nouveau d'une PR (de sa base à sa tête) ou d'un push de `main` (d'avant à après), un par un, contre son premier parent : le verdict ne dépend ni du regroupement des commits ni d'un rejeu. Un avant inconnu (premier push, `0000…`) ne fait juger que le commit de tête, et la sortie le dit ; un commit antérieur à `connectors/clients.yaml` est jugé avec celui de la tête ; un commit de fusion n'est jugé que sur ce qu'il change lui-même. Ni un tag ni un dispatch ne la lancent. Deux règles. *La version monte avec le code* : quand un commit change le code d'un connecteur (tout fichier de `connectors/<nom>/`, et son client tel que rattaché ci-dessus) qui existe avant et après lui, `connector.version` monte (semver), ou le commit porte dans son bloc de trailers, celui de `Changelog:`, la dispense `Version-dispense: <connecteur> — <raison>` (tiret cadratin), qui ne vaut que pour lui ; `Version-dispense: * — <raison>` dispense tous les connecteurs du commit, seulement s'il touche `connectors/connector.schema.json` ou `fabrique/` (une passe sur tout le format). Une version qui descend n'est jamais dispensée ; une dispense mal formée, hors du bloc de trailers, ou globale sans passe sur le format fait échouer le commit, dont le message d'échec donne la version cible ou la ligne à ajouter. La lib commune, la fabrique, la sortie générée, les tests et la version de la lib (`pyproject.toml`, `uv.lock`, le paquet npm, qui suivent le flux de publication) ne sont le code d'aucun connecteur : un commit de publication ne regarde aucun connecteur. Un correctif urgent passe par une dispense motivée. *Les exceptions en attente ne font que décroître* : un commit n'ajoute aucune entrée aux catégories `before_the_rule` et `not_described_yet` ; un client nouveau entre au format, ou dans une catégorie de principe avec son motif. Tests : `tests/test_commit_guards.py`. Une PR fusionnée par squash garde ses dispenses seulement si le message du commit fusionné les reprend en trailers.

## Décisions et alternatives écartées

- **Du code source dans l'un des deux langages** : écarté le 29/09. L'autre langage n'en serait qu'une traduction ; un fichier déclaratif se lit des deux côtés.
- **TOML** : écarté, illisible dès trois niveaux d'imbrication, qu'un schéma d'entrée dépasse souvent. **JSON seul** : écarté, sans commentaires et pénible pour une description sur plusieurs lignes.
- **Référencer la spec OpenAPI de l'éditeur** (`operation: getEstimates`) et n'ajouter que classe, refus, exemples et projection : non retenu à ce jour. Moins à écrire, mais chaque connecteur dépendrait de la qualité d'une spec tierce.
- **Un client ajouté à la lib sans description** : écarté le 2026-10-05. Tout client entre au format ; une fonction ou un client que le format ne sait pas dire est nommé, avec sa raison, dans l'en-tête de sa description ou dans `connectors/clients.yaml`.
- **L'outil MCP comme unité** (le modèle d'oto 1, où le nom de l'outil est le contrat et où Sellsy est servi par `sellsy_document(kind, op)`, quatre documents de vente fusionnés) : écarté. L'unité est la fonction ; l'outil, une projection. Cela répond au point « verbe `oto_call` ou outils dédiés » d'ADR 0070 §7.8 : les deux, par projection.
- **Un langage d'expressions pour les contrôles** (une formule « débits = crédits ») : écarté le 06/10. Des genres fermés et nommés (`equal_sums`, `non_empty`), chacun lié à un refus de la table, se lisent et s'exécutent pareil dans les deux langages ; un genre s'ajoute quand une description en a besoin.
- **Une seule version** : écarté. La version du fichier (semver) et celle de l'API tierce (en clair) sont deux champs.
- **Convertir les 713 outils existants, ou leur attribuer une classe** : écarté. La classe s'écrit à l'entrée d'un connecteur dans le format.
- **Garder les descriptions en français** : écarté. Ce que lit l'agent est en anglais ; une description française se traduit à son entrée.
- **Un résumé de chaque connecteur dans cette note** : écarté le 2026-11-03. Il doublait l'en-tête de chaque description et vieillissait sans elle ; la carte du parc, générée, dit la vue d'ensemble.

## Sécurité et confidentialité

- Le fichier ne porte aucun secret ([compte](compte.md)). Le secret est fourni par le consommateur à chaque appel ; la lib n'en lit aucun (garde `tests/test_no_secret_read_guard.py`).
- Le dépôt est public : un connecteur dont l'accès appartient à un client, son propre back-office, n'a pas de description ici ; c'est un connecteur propre à son hôte.
- Un message de refus dit le fait, au plus une condition, jamais le nom d'un outil : la lib ne connaît pas les outils de l'appelant.
- Une donnée personnelle (`personal: true`) n'est jamais journalisée par l'hôte.

## Écart avec le code

- Les clients Python restent écrits à la main et rien n'est généré ; les descriptions, écrites après eux pour la plupart, n'en sont pas encore la source. Quelques clients ne suivent pas leur description : `yousign` ; les clients Google (`oto.tools.google.*`, par le SDK de Google) ; `oto.tools.unipile` (il prend l'identité du compte en paramètre, ou découvre le premier compte LinkedIn de la clé) ; `fullenrich` (une liste de champs pour tout le lot) ; `pennylane` (une seule page des listes que le fichier dit paginées, et des noms de méthode à part, pour les méthodes antérieures au 2026-11-09 ; les modules ajoutés ce jour-là, banque, suivi des factures, référentiel, tiers, exports et autres documents, suivent leur description, gardés par `tests/test_pennylane_description_concordance.py`). Ailleurs, un test de concordance garde l'égalité, et les écarts sont nommés dans l'en-tête de chaque description.
- Les descriptions ne partent pas dans la distribution PyPI : seul le dépôt les porte.
- `modes` garde le vocabulaire d'oto 1 (`platform`, `byo_user`, `byo_org`). `quota` n'a pas de forme : le schéma n'exige qu'un objet non vide.
- Le format se déclare YAML 1.2, mais le test et la fabrique lisent avec PyYAML, qui suit YAML 1.1.
- `handwritten` exige un fichier TypeScript, qui n'existe pour aucun connecteur, et la fabrique, qui n'en génère aucun, fait échouer tout fichier qui en porte : une fonction écrite à la main reste donc hors du fichier.
- Toute forme neuve s'exécute dans oto-core à partir du commit qui l'écrit ; un paquet antérieur la refuse à la déclaration ou l'ignore. L'épingle de ce dépôt et celle d'oto-core montent donc ensemble ; chaque document de bloc dit le commit qui compte. Pour ce document : `act` (`<connecteur>_act.<fonction>`, `actName` ; un appel sous le nom simple est refusé en nommant le nom servi) et le délai par fonction sont exécutés, relus au commit d26f513 ; un paquet antérieur au délai par fonction applique celui du connecteur.
- Aucune garde du dépôt ne lit le contenu des fichiers à la recherche d'un secret (`tests/test_no_secret_read_guard.py` ne regarde que les lectures d'environnement de la lib).

## Questions ouvertes

- La forme de `quota`.
- YAML 1.2 déclaré, lecteur 1.1 : changer de lecteur, ou se limiter au sous-ensemble commun et le dire ?
- Référencer la spec OpenAPI de l'éditeur quand elle existe ?
- Requête, ce que le format ne sait pas encore dire : une réponse autre que JSON ; une pagination par adresse complète (`@odata.nextLink`, `links.next`, `nextLink`, `nextRecordsUrl`) ; une pagination par numéro de page dont la réponse dit le total, pas la suivante, ou par `offset` ; une pagination dont chaque page répète un élément de tête ; un argument qui ne part plus une fois le curseur posé ; des clauses de filtre bâties depuis des arguments nommés ; un encodage des listes en query propre à une fonction, quand `query_arrays` vaut pour tout le connecteur ; des query à clés libres (encodées `%20`, jamais `+`) ; une constante de query posée seulement avec un argument ; un booléen de query que le client écrit `True` ; un texte que le client récrit (découpe au-delà d'une longueur) ; un rythme par fonction ou par route, en plusieurs fenêtres ou selon un argument, qu'un rythme du connecteur ne dit pas.
- Confirmation et droits : les scopes d'une fonction hors niveaux d'accès, qui ne vivent que dans sa description ; un argument liste dans `confirm.summary`.

## Historique

- 2026-09-29 : le connecteur partagé se décrit dans un fichier déclaratif ; l'unité est la fonction servie avec son appel ; l'exposition est une projection ; les descriptions sont en anglais — décidé par le mainteneur (source : séance du 29/09 ; conception connecteurs d'oto 2, oto-enterprise, archivé).
- 2026-09-29 : YAML validé par un schéma JSON ; trois blocs ; version du fichier et version de l'API séparées ; projection par défaut `per_connector` ; existants non convertis, Sellsy d'abord — décidé par le mainteneur (source : séance du 29/09, « Le fichier de description »).
- 2026-09-30 : schéma `connector.schema.json` et fichier Sellsy vérifié contre la référence publique de l'API — choix du projet (source : lot 2 du 30/09).
- 2026-10-05 : reprise en document de conception vivant depuis la conception connecteurs d'oto 2 (oto-enterprise, archivé) — décidé par le mainteneur.
- 2026-10-05 : tout client ajouté à la lib entre au format, et la liste de ce que le format ne sait pas encore dire — décidé par le mainteneur (source : séance du 05/10).
- 2026-10-06 : en-têtes constants, arrêt de pagination (`more`), rythme maximal, contrôles avant l'appel (`checks`) et sur la réponse (`expect`) ; l'entrée est servie en JSON Schema tel quel — décidé par le mainteneur.
- 2026-10-16 : la description d'une fonction est bornée à 1000 caractères par le schéma, comme par le paquet d'oto 2 — choix du projet.
- 2026-10-16 : délai par fonction décidé ; `modes` et `exposure` marqués « oto 1 seulement » ; gardes de CI sur la version et sur la description d'un client — décidé par le mainteneur (source : revue des connecteurs d'oto 2 du 16/10).
- 2026-10-17 : `act` par fonction, déduit de la classe ; un connecteur à effet se partage en `<connecteur>` et `<connecteur>_act`, une seule description et un seul compte ; les brouillons et préparations restent du côté lecture (`act: false`), une écriture qui crée un enregistrement, même réversible, garde `act: true` — décidé par le mainteneur.
- 2026-10-19 : délai par fonction écrit : `timeout_s` facultatif sur une fonction, de 1 à 300 s (le plafond qu'exécute le paquet d'oto 2, écrit dans le schéma pour les deux), jamais égal à celui du connecteur (règle 11) ; sortie TypeScript `timeoutMs` — décidé par le mainteneur.
- 2026-10-19 : gardes de CI écrites : un client a sa description, en tout ou en partie, ou une exception à catégorie et raison dans `connectors/clients.yaml`, et les catégories en attente ne font que décroître ; chaque commit monte la version d'un connecteur dont il change la description ou le client, sauf trailer `Version-dispense` — décidé par le mainteneur.
- 2026-10-19 : donnée personnelle : `personal: true` sur une propriété de l'entrée, passé tel quel dans le schéma servi, que l'hôte ne journalise pas ; règle 15 de la fabrique — décidé par le mainteneur.
- 2026-10-21 : toute forme neuve du format s'écrit pour les deux côtés à la fois, selon la règle de convergence ([fabrique](fabrique.md), « La règle de convergence ») — décidé par le mainteneur.
- 2026-11-03 : redécoupage de la note en documents par sujet ; les résumés par connecteur sortent pour la carte du parc et l'en-tête de chaque description — décidé par le responsable produit (source : demande de rationalisation des connecteurs).
- 2026-11-06 : `effect` (`send`, `destroy`) sur une fonction sensible, facultatif, sans défaut, pour qu'un hôte distingue un envoi d'une destruction — décidé par Alexis (source : lecteur générique d'oto 1, décision du 05/11).
- 2026-11-06 : septième mode d'authentification, `oauth2_jwt_bearer`, le compte de service ([compte](compte.md), « Le compte de service ») — décidé par le mainteneur (source : lecture de Search Console par un agent).
