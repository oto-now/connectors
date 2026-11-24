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
- Hors objectif : les pages elles-mêmes (consentement, saisie de clé, activation), le stockage des comptes et la règle de partage des comptes, qui relèvent de chaque plateforme (oto-pkg : docs/conception/connecteurs-et-comptes.md). Hors objectif aussi : les refus du service tiers, qui restent ceux de la table `errors` ([erreurs et signal de refus](signal-de-refus.md)).

## Conception

### Le refus

Un 403, de code `not_enabled`. Le message est en anglais, en clair, et se termine par `open <url>` : `Gmail is not connected for you: open https://platform.example.test/connect/gmail?space=me`. `details` :

- `reason` : `connector_off` (le connecteur n'est pas activé dans l'espace), `no_account` (aucun compte utilisable) ou `reconnect` (un compte existe, mais il est marqué ou son consentement a expiré) ;
- `gesture` : `oauth` (un consentement), `key` (une clé ou des identifiants à saisir), `hosted_link` (un lien hébergé par le tiers qui rattache le compte) ou `admin_activation` (un administrateur active le connecteur) ;
- `connector` : le nom du connecteur ;
- `access` : le nom de l'accès, seulement quand le connecteur en déclare plusieurs (`accesses`) ;
- `space` : l'espace où le geste se fait, `me`, `team:<id>` ou `org` ;
- `url` : la page du geste ;
- `administrators` (facultatif) : quand le geste revient à un administrateur, ceux qui peuvent le faire, `[{ userId, name }]` ;
- `shared_accounts` (facultatif) : les comptes déjà partagés dans l'espace dont la personne pourrait se servir.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["reason", "gesture", "connector", "space", "url"],
  "properties": {
    "reason": { "enum": ["connector_off", "no_account", "reconnect"] },
    "gesture": { "enum": ["oauth", "key", "hosted_link", "admin_activation"] },
    "connector": { "type": "string", "pattern": "^[a-z][a-z0-9_]*$" },
    "access": { "type": "string", "minLength": 1 },
    "space": { "type": "string", "pattern": "^(me|org|team:[^\\s:]+)$" },
    "url": { "type": "string", "format": "uri", "pattern": "^https://" },
    "administrators": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["userId", "name"],
        "properties": { "userId": { "type": "string", "minLength": 1 }, "name": { "type": "string", "minLength": 1 } }
      }
    },
    "shared_accounts": { "type": "array" }
  }
}
```

### L'adresse

- **Stable**, sur l'origine de la plateforme. Elle ne nomme que le connecteur, l'accès et l'espace, sans secret, sans code ni jeton : on peut la relayer, l'afficher ou la journaliser.
- **Revérifiée** : la page lit la personne connectée et revérifie ses droits dans l'espace. Le `space` de l'adresse n'est qu'une proposition, jamais cru : une personne qui n'a pas de droit sur l'espace demandé ne le reçoit pas.
- **OAuth** : la page redirige aussitôt vers le tiers. Le `state` est créé à ce moment. Il est à usage unique, valable 10 minutes, et lié à la personne, à l'organisation et au connecteur. Un retour dont le `state` ne correspond pas aux trois est refusé.
- **Activation** : l'activation par un administrateur est une confirmation explicite sur la page, jamais l'effet de l'ouverture du lien.
- **Retour** : la page finale dit à la personne de revenir à son agent, qui rejoue l'appel.

### Le geste selon `auth.kind`

Pour `reason` `no_account` ou `reconnect`, le geste suit le mode d'authentification ([le compte d'un connecteur](compte.md)), celui de l'accès nommé par `access` quand le connecteur en a plusieurs :

| `auth.kind` | `gesture` |
|---|---|
| `oauth2_user` | `oauth` |
| `api_key`, `bearer`, `basic`, `oauth2_client_credentials`, `oauth2_jwt_bearer` | `key` |
| `host_delegated` | aucun : le jeton vient de l'hôte qui exécute ([le profil hôte](profil-hote.md)), et la personne n'a rien à connecter |

Pour `reason` `connector_off`, le geste est `admin_activation`, quel que soit le mode.

### La fonction de catalogue `connector.connect`

Elle rend sur demande ce que donne le refus, sans attendre un appel qui échoue.

- Entrée : `{ connector, space?, access? }`. `space` est par défaut celui que la plateforme choisirait pour la personne. `access` est exigé quand le connecteur a plusieurs accès, et refusé sinon, comme à l'exécution.
- Sortie : `{ url, gesture, space, administrators? }`, avec les mêmes règles que `details`. Quand un compte marche déjà, la sortie est `{ gesture: "none", account }`, où `account` désigne ce compte.

```json
{
  "input": {
    "type": "object",
    "additionalProperties": false,
    "required": ["connector"],
    "properties": {
      "connector": { "type": "string", "pattern": "^[a-z][a-z0-9_]*$" },
      "space": { "type": "string", "pattern": "^(me|org|team:[^\\s:]+)$" },
      "access": { "type": "string", "minLength": 1 }
    }
  },
  "output": {
    "oneOf": [
      {
        "type": "object",
        "required": ["url", "gesture", "space"],
        "properties": {
          "url": { "type": "string", "format": "uri" },
          "gesture": { "enum": ["oauth", "key", "hosted_link", "admin_activation"] },
          "space": { "type": "string" },
          "administrators": { "type": "array" }
        }
      },
      {
        "type": "object",
        "required": ["gesture", "account"],
        "properties": { "gesture": { "const": "none" }, "account": {} }
      }
    ]
  }
}
```

## Décisions et alternatives écartées

- **Un seul code, la raison dans `details`** (proposé) : un code par raison aurait obligé chaque client à en reconnaître trois pour la même conduite, ouvrir l'adresse. `reason` distingue les cas pour qui veut les nuancer.
- **L'adresse dans le message, en dernier** (proposé) : un agent qui ne lit pas `details` relaie quand même le geste à la personne.
- **Une adresse porteuse d'un jeton ou d'un `state`** : écartée. Elle serait un secret à relayer par l'agent, visible dans ses traces. Le `state` naît à l'ouverture, côté plateforme.
- **Le `space` de l'adresse cru tel quel** : écarté. L'adresse passe par l'agent, et n'importe qui peut la réécrire.
- **L'activation à l'ouverture du lien** : écartée. Un aperçu de lien, ou un clic par erreur, activerait un connecteur pour tout un espace.

## Sécurité et confidentialité

- L'adresse ne porte ni secret, ni code, ni jeton. Le `state` OAuth est à usage unique, borné à 10 minutes, et lié à la personne, à l'organisation et au connecteur.
- La page revérifie l'identité et les droits à chaque ouverture. Rien de ce que porte l'adresse n'accorde un droit.
- `administrators` ne nomme que des administrateurs de l'espace, à une personne qui en est membre, par leur identifiant et leur nom, jamais leur adresse électronique.
- `shared_accounts` ne montre d'un compte partagé que ce que la règle de partage de la plateforme montre déjà à la personne, jamais son secret.

## Écart avec le code

- La lib ne sert ni ce refus ni `connector.connect`. Aucun type ni schéma de la lib ne les porte encore : le contrat vit dans ce document, et chaque plateforme l'implémente.
- Le code `not_enabled` existe déjà dans la table `errors` de connecteurs (`mailpool`, `modjo`), pour un refus du service tiers (une fonction non ouverte chez lui). Ce refus-là n'a pas de `details.reason`, ce qui le distingue de celui de la plateforme.

## Questions ouvertes

- `hosted_link` : aucun `auth.kind` n'y mène dans la table. Le cas visé est un compte rattaché chez un tiers qui héberge les comptes de ses utilisateurs (`account_identity`, champs en `holder: host`), dont l'identité s'écrit par un service qui la vérifie auprès du tiers. Faut-il le dire par `account_identity` plutôt que par `auth.kind` ?
- `auth.kind: none` : aucun compte, donc ni `no_account` ni `reconnect`. Seul `connector_off` s'applique.
- La forme d'un élément de `shared_accounts`, et celle d'`account` dans la sortie de `connector.connect`, restent à fixer avec la règle de partage des plateformes.
- Un compte d'organisation (`byo_org`) à connecter : le geste revient-il toujours à un administrateur, avec `administrators` ?
- Le code `not_enabled` partagé avec des refus de services tiers (« Écart avec le code ») : faut-il renommer ceux des connecteurs ?

## Historique

- 2026-11-24 : contrat commun du refus « connexion requise » (`not_enabled`, `details`, règles de l'adresse, geste selon `auth.kind`, `connector.connect`) servi par oto.now et oto.cx — proposé par le mainteneur.
