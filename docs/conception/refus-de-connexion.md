# Le refus « connexion requise »

- **Statut** : proposé
- **Dernière révision** : 2026-11-24

## Résumé

Quand un agent appelle une fonction d'un connecteur qui n'est pas activé, sans compte, ou dont le compte est à reconnecter, la plateforme refuse l'appel par un refus commun : `not_enabled`, en 403. Ce refus dit pourquoi (`reason`), quel geste le lève (`gesture`), pour quel connecteur, quel accès et quel espace, et donne l'adresse d'une page qui mène ce geste (`url`). Les deux plateformes qui servent les connecteurs de la lib, oto.now (oto-core) et oto.cx, servent ce même contrat, et la fonction de catalogue `connector.connect` rend la même adresse sur demande. La lib ne sert pas ce refus. Elle fixe le contrat, et `auth.kind` détermine le geste.

## Contexte

- Un agent qui tombe sur un connecteur sans compte n'a, aujourd'hui, qu'un message à relayer. La personne doit ensuite trouver seule où se connecter, et dans quel espace.
- Deux plateformes exécutent les mêmes descriptions. Sans contrat commun, un agent ou un client qui passe de l'une à l'autre lit deux refus différents pour la même situation.
- Le geste qui lève le refus dépend du mode d'authentification du connecteur ([le compte d'un connecteur](compte.md)), et parfois de son accès quand il en a plusieurs. Ce sont des faits de la description, donc de la lib.

## Objectifs et non-objectifs

- Un refus unique, typé, lisible par un agent (`details`) et par une personne (le message, qui se termine par l'adresse à ouvrir).
- Une adresse sûre à relayer : stable, sans secret, qui revérifie tout à l'ouverture.
- La même correspondance entre `auth.kind` et le geste sur les deux plateformes.
- Hors objectif : les pages elles-mêmes (consentement, saisie de clé, activation), le stockage des comptes et la règle de partage des comptes, qui relèvent de chaque plateforme (oto-pkg : docs/conception/connecteurs-et-comptes.md). Hors objectif aussi : les refus du service tiers, qui restent ceux de la table `errors` ([erreurs et signal de refus](signal-de-refus.md)), sauf une clé refusée par le fournisseur sur un compte à clé, qui est désormais un refus de connexion (« Les cas de refus », ci-dessous).

## Conception

### Le refus

Un 403, de code `not_enabled`. Le message est en anglais, en clair, et se termine par `open <url>` : `Gmail is not connected for you: open https://platform.example.test/connect/gmail?space=me`. `details` :

- `reason` : `connector_off` (le connecteur n'est pas activé dans l'espace), `no_account` (aucun compte utilisable) ou `reconnect` (un compte existe, mais il est marqué ou son consentement a expiré) ;
- `gesture` : `oauth` (un consentement), `key` (une clé ou des identifiants à saisir), `hosted_link` (un lien hébergé par le tiers qui rattache le compte) ou `admin_activation` (un administrateur active le connecteur) ;
- `connector` : le nom du connecteur ;
- `access` : le nom de l'accès, seulement quand le connecteur en déclare plusieurs (`accesses`) ;
- `space` : l'espace où le geste se fait, `me`, `team:<slug>` ou `org` ;
- `url` : la page du geste ;
- `account_id` (facultatif) : le compte existant que le geste répare (sa clé à saisir), quand il y en a un ;
- `administrators` (facultatif) : quand le geste revient à un administrateur, ceux qui peuvent le faire, par leur nom seul, `[{ name }]` ;
- `shared_accounts` (facultatif) : les comptes personnels déjà partagés avec la personne, dont elle peut se servir en les nommant, `[{ id, label, owner, description }]`, où `owner` est le nom de la personne qui partage et `description` celle que le compte porte (`null` sans description).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object", "additionalProperties": false, "required": ["reason", "gesture", "connector", "space", "url"],
  "properties": {
    "reason": { "enum": ["connector_off", "no_account", "reconnect"] },
    "gesture": { "enum": ["oauth", "key", "hosted_link", "admin_activation"] },
    "connector": { "type": "string", "pattern": "^[a-z][a-z0-9_]*$" },
    "access": { "type": "string", "minLength": 1 },
    "space": { "$ref": "#/$defs/space" },
    "url": { "type": "string", "format": "uri", "pattern": "^https://" },
    "account_id": { "type": "string", "minLength": 1 },
    "administrators": { "$ref": "#/$defs/administrators" },
    "shared_accounts": { "type": "array", "items": {
      "type": "object", "additionalProperties": false, "required": ["id", "label", "owner", "description"],
      "properties": { "id": { "type": "string", "minLength": 1 }, "label": { "type": "string", "minLength": 1 },
                      "owner": { "type": "string", "minLength": 1 }, "description": { "type": ["string", "null"] } } } }
  },
  "$defs": {
    "space": { "type": "string", "pattern": "^(me|org|team:[^\\s:]+)$" },
    "administrators": { "type": "array", "items": {
      "type": "object", "additionalProperties": false, "required": ["name"], "properties": { "name": { "type": "string", "minLength": 1 } } } }
  }
}
```

### L'adresse

- **Stable**, sur l'origine de la plateforme. Elle ne nomme que le connecteur, l'accès et l'espace, sans secret, sans code ni jeton : on peut la relayer, l'afficher ou la journaliser.
- **Un compte désigné** : l'adresse peut porter `account_id=<id>`, pour saisir la clé de ce compte (après une clé refusée, par exemple). La page la réserve à qui gère le compte, et revérifie que le compte est bien du connecteur, de l'accès et de l'espace de l'adresse. `activate` (l'adresse d'une activation par un administrateur) et `account_id` ensemble sont refusés.
- **Revérifiée** : la page lit la personne connectée et revérifie ses droits dans l'espace. Le `space` de l'adresse n'est qu'une proposition, jamais cru : une personne qui n'a pas de droit sur l'espace demandé ne le reçoit pas.
- **Ouvrir ne fait rien** : l'ouverture de la page (`GET`) ne démarre rien et n'écrit rien. La page montre le geste et un bouton, qui envoie un `POST` de même origine. Ce `POST` pose la demande, puis redirige vers le tiers (consentement, lien hébergé) ou mène la saisie ou l'activation.
- **OAuth** : le `state` naît au `POST`. Il est à usage unique, valable 10 minutes, et lié à la personne, à l'organisation et au connecteur. Un retour dont le `state` ne correspond pas aux trois est refusé.
- **Relink** : rattacher de nouveau un compte déjà rattaché depuis une autre organisation n'a lieu que par ce `POST`, et la page l'annonce avant le bouton.
- **Activation** : l'activation par un administrateur est une confirmation explicite sur la page, jamais l'effet de l'ouverture du lien.
- **Retour** : la page finale dit à la personne de revenir à son agent, qui rejoue l'appel. Elle n'affiche jamais un texte lu dans l'adresse : une raison d'échec passe par une liste blanche de raisons connues, et le succès est dit par un texte générique.

### Les cas de refus

| Situation | `reason` | `gesture` | `account_id` |
|---|---|---|---|
| connecteur non activé dans l'espace | `connector_off` | `admin_activation` | non |
| aucun compte utilisable | `no_account` | selon `auth.kind` | non |
| un compte existe, sans secret | `no_account` | `key` | oui |
| consentement expiré ou révoqué | `reconnect` | `oauth` | non |
| clé refusée par le fournisseur sur un compte à clé (`marks_account: credential_rejected`) | `reconnect` | `key` | oui |

Une clé refusée sur un compte à clé n'est plus un `credential_rejected` rendu tel quel : c'est un `not_enabled`, qui mène à la saisie d'une nouvelle clé. Ne sont pas des refus de connexion, et restent sans adresse : des crédits épuisés (`quota_exhausted`), et la clé de l'hôte refusée (`credential_rejected` sur un champ `holder: host`), que personne de l'organisation ne peut réparer.

### Le geste selon `auth.kind`

Pour `reason` `no_account` ou `reconnect`, le geste suit le mode d'authentification ([le compte d'un connecteur](compte.md)), celui de l'accès nommé par `access` quand le connecteur en a plusieurs :

| `auth.kind` | `gesture` |
|---|---|
| `oauth2_user` | `oauth` |
| un accès qui déclare `account_identity` (compte rattaché chez un tiers qui héberge les comptes de ses utilisateurs), quel que soit son mode | `hosted_link` |
| `api_key`, `bearer`, `basic`, `oauth2_client_credentials`, `oauth2_jwt_bearer` | `key` |
| `host_delegated` | aucun tant que l'échange de jeton réussit ([le profil hôte](profil-hote.md)) ; `oauth` si l'émetteur refuse l'échange parce que le client est tiers : le consentement de la personne à oto, sur la même page `/connect` (détail : oto-core, docs/conception/fonctions-d-un-hote.md, « Fonctions d'un hôte (2/3) ») |
| `none` | aucun : le connecteur n'a pas de compte, donc ni `no_account` ni `reconnect` |

Pour `reason` `connector_off`, le geste est `admin_activation`, quel que soit le mode.

Le geste se fait par la personne elle-même, ou revient à un administrateur quand l'espace ne lui est pas permis : un compte de l'organisation se connecte par un administrateur de l'organisation, un compte d'équipe par un administrateur ou un responsable de l'équipe. `administrators` les nomme alors.

### La fonction de catalogue `connector.connect`

Elle rend sur demande ce que donne le refus, sans attendre un appel qui échoue.

- Entrée : `{ connector, space?, access? }`. `space` est par défaut celui que la plateforme choisirait pour la personne. `access` est exigé quand le connecteur a plusieurs accès, et refusé sinon, comme à l'exécution.
- Sortie : `{ url, gesture, connector, access?, space, by, administrators? }`, avec les mêmes règles que `details`. `by` dit qui fait le geste : `user` (la personne) ou `administrator` (l'adresse est à transmettre à l'un des `administrators`).
- Quand un compte marche déjà dans l'espace, pour l'accès demandé : `{ gesture: "none", connector, access?, space, account }`, sans adresse. `account` est `{ id, label, mode, owner }`. `owner` vaut `{ kind: "org" | "team", description }` pour un compte d'organisation ou d'équipe, ou `{ kind: "user", name, yours }` pour le compte de la personne. `account.id`, comme l'`id` de chaque `shared_accounts`, est l'identifiant d'une ressource, pas d'une personne : l'agent le cite pour désigner un compte dans un appel.

`$defs` est celui du schéma de `details`, ci-dessus.

```json
{
  "input": {
    "type": "object", "additionalProperties": false, "required": ["connector"],
    "properties": { "connector": { "type": "string", "pattern": "^[a-z][a-z0-9_]*$" }, "space": { "$ref": "#/$defs/space" },
                    "access": { "type": "string", "minLength": 1 } }
  },
  "output": { "oneOf": [
    {
      "type": "object", "additionalProperties": false, "required": ["url", "gesture", "connector", "space", "by"],
      "properties": {
        "url": { "type": "string", "format": "uri", "pattern": "^https://" },
        "gesture": { "enum": ["oauth", "key", "hosted_link", "admin_activation"] },
        "connector": { "type": "string" }, "access": { "type": "string", "minLength": 1 },
        "space": { "$ref": "#/$defs/space" }, "by": { "enum": ["user", "administrator"] },
        "administrators": { "$ref": "#/$defs/administrators" }
      }
    },
    {
      "type": "object", "additionalProperties": false, "required": ["gesture", "connector", "space", "account"],
      "properties": {
        "gesture": { "const": "none" }, "connector": { "type": "string" }, "access": { "type": "string", "minLength": 1 },
        "space": { "$ref": "#/$defs/space" },
        "account": {
          "type": "object", "additionalProperties": false, "required": ["id", "label", "mode", "owner"],
          "properties": {
            "id": { "type": "string", "minLength": 1 }, "label": { "type": "string", "minLength": 1 },
            "mode": { "type": "string", "minLength": 1 },
            "owner": { "oneOf": [
              { "type": "object", "additionalProperties": false, "required": ["kind", "description"],
                "properties": { "kind": { "enum": ["org", "team"] }, "description": { "type": "string" } } },
              { "type": "object", "additionalProperties": false, "required": ["kind", "name", "yours"],
                "properties": { "kind": { "const": "user" }, "name": { "type": "string" }, "yours": { "type": "boolean" } } }
            ] }
          }
        }
      }
    }
  ] }
}
```

## Décisions et alternatives écartées

- **Un seul code, la raison dans `details`** (proposé) : un code par raison aurait obligé chaque client à en reconnaître trois pour la même conduite, ouvrir l'adresse. `reason` distingue les cas pour qui veut les nuancer.
- **L'adresse dans le message, en dernier** (proposé) : un agent qui ne lit pas `details` relaie quand même le geste à la personne.
- **Une adresse porteuse d'un jeton ou d'un `state`** : écartée. Elle serait un secret à relayer par l'agent, visible dans ses traces. Le `state` naît à l'ouverture, côté plateforme.
- **Le `space` de l'adresse cru tel quel** : écarté. L'adresse passe par l'agent, et n'importe qui peut la réécrire.
- **La redirection vers le tiers dès l'ouverture (`GET`)** : abandonnée le 2026-11-24, pour la falsification de requête (CSRF). Un lien ouvert par un aperçu, ou glissé dans une page tierce, lançait un consentement ou un relink que la personne n'avait pas demandé. Le `POST` de même origine porte désormais le geste.
- **Les identifiants de personnes** (`userId` dans `administrators` et dans le propriétaire d'un compte) : écartés le 2026-11-24. Le refus et `connector.connect` partent vers des clients MCP, qui n'ont pas à connaître les identifiants internes des personnes ; le nom suffit. L'identifiant d'un compte reste : c'est celui d'une ressource, que l'agent cite pour la désigner.
- **L'activation à l'ouverture du lien** : écartée. Un aperçu de lien, ou un clic par erreur, activerait un connecteur pour tout un espace.

## Sécurité et confidentialité

- L'adresse ne porte ni secret, ni code, ni jeton. Le `state` OAuth naît au `POST` ; il est à usage unique, borné à 10 minutes, et lié à la personne, à l'organisation et au connecteur.
- L'ouverture (`GET`) ne démarre ni n'écrit rien. Seul un `POST` de même origine pose une demande, un relink compris.
- La page revérifie l'identité et les droits à chaque ouverture. Rien de ce que porte l'adresse n'accorde un droit.
- La page de retour n'affiche aucun texte lu dans l'adresse : raisons en liste blanche, succès générique.
- `administrators` ne nomme que des administrateurs de l'espace, à une personne qui en est membre, par leur nom seul : ni identifiant interne, ni adresse électronique.
- `shared_accounts` ne montre d'un compte partagé que ce que la règle de partage de la plateforme montre déjà à la personne, jamais son secret.

## Écart avec le code

- La lib ne sert ni ce refus ni `connector.connect`. Aucun type ni schéma de la lib ne les porte encore : le contrat vit dans ce document, et chaque plateforme l'implémente.
- Le code `not_enabled` existe déjà dans la table `errors` de connecteurs (`mailpool`, `modjo`), pour un refus du service tiers (une fonction non ouverte chez lui). Ce refus-là n'a pas de `details.reason`, ce qui le distingue de celui de la plateforme.

## Questions ouvertes

- Crédits épuisés : si la lib déclare un jour la page de facturation du fournisseur, le refus `quota_exhausted` pourra la porter. Faut-il l'ajouter au format ?
- Quand le geste revient aux responsables d'une équipe, ou à la personne propriétaire d'un compte personnel, ils ne sont nommés que dans le message, pas dans `details`. Faut-il un champ `managers: [{ name }]`, comme `administrators` ?
- Un champ ou un réglage du compte manquant alors que le secret est présent (« has no <champ> yet ») reste un refus sans adresse. Faut-il le mener à la même page ?
- Le code `not_enabled` partagé avec des refus de services tiers (« Écart avec le code ») : faut-il renommer ceux des connecteurs ?

## Historique

- 2026-11-24 : contrat commun du refus « connexion requise » (`not_enabled`, `details`, règles de l'adresse, geste selon `auth.kind`, `connector.connect`) servi par oto.now et oto.cx — proposé par le mainteneur.
- 2026-11-24 : formes fixées d'après l'implémentation d'oto-core : `administrators` en `[{ name }]` sans identifiant, `account` et `shared_accounts`, `team:<slug>`, `connector`, `access` et `by` dans la sortie de `connector.connect`, `hosted_link` par `account_identity`, `none` sans geste ; l'ouverture (`GET`) ne fait plus rien, le geste passe par un `POST` de même origine (CSRF), retour sans texte lu dans l'adresse — décidé par le mainteneur.
- 2026-11-24 : `host_delegated` donne `oauth` quand l'émetteur refuse l'échange à un client tiers ; le propriétaire personnel d'un compte perd `userId` — décidé par le mainteneur.
- 2026-11-24 : `account_id` dans l'adresse et dans `details`, les cas de refus (compte sans secret, clé refusée devenue un refus de connexion ; crédits épuisés et clé de l'hôte hors du contrat) — décidé par le mainteneur, d'après l'implémentation d'oto-core.
