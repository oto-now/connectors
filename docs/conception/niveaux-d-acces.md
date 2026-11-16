# Niveaux d'accès d'un connecteur

- **Statut** : validé par le mainteneur le 14/10/2026
- **Dernière révision** : 2026-11-13

## Résumé

Un connecteur à consentement OAuth, ou à compte de service, peut déclarer des niveaux d'accès nommés, au moins `read` et `full`, chacun avec la liste des droits (scopes) que le consentement demande, ou que revendique l'assertion du compte de service. Chaque fonction déclare les droits qu'elle exige. L'hôte choisit un niveau par organisation ou par équipe, `full` par défaut. Une fonction dont les droits ne sont pas couverts par le niveau n'est ni servie ni appelable, et le consentement ne demande que les droits du niveau. `full` ne filtre rien : c'est le comportement d'un connecteur sans niveaux.

## Contexte

Un consentement OAuth demande une liste de droits fixée par le connecteur. Une organisation qui veut ouvrir un outil tiers en lecture seule, le temps d'un essai, n'avait que deux choix : tout accorder, ou ne pas brancher le connecteur. Le marquage `class` d'une fonction (`read`, `write`, `sensitive`, voir [Format de description](format-de-description.md)) dit ce que fait une fonction, pas ce qu'elle exige : deux lectures peuvent demander des droits différents, et un niveau « lecture » qui n'accorde qu'une partie des lectures laisserait servir des fonctions vouées à l'échec.

## Objectifs et non-objectifs

Objectifs :
- un niveau restreint se déclare dans la description du connecteur, sans code ;
- une fonction non couverte disparaît de ce que voit l'agent et se refuse à l'appel, avec un refus nommé ;
- le consentement ne demande que les droits du niveau ;
- un connecteur sans niveaux ne change pas.

Non-objectifs :
- des droits par personne : le niveau se règle par organisation ou par équipe ;
- vérifier, à chaque appel, les droits réellement accordés par le fournisseur : le niveau est une politique de l'hôte, appliquée même à un jeton plus large ;
- les connecteurs à clé d'API : leurs droits se fixent chez le fournisseur, à la création de la clé.

## Conception

Dans `connector.auth`, en `oauth2_user`, `access_levels` remplace `scopes` :

```yaml
auth:
  kind: oauth2_user
  access_levels:
    read: { scopes: [Records.READ, Settings.READ] }
    full: { scopes: [Records.ALL, Settings.READ, Users.READ] }
  scope_implies:
    Records.ALL: [Records.READ, Records.CREATE, Records.UPDATE, Records.DELETE]
```

- `access_levels` porte au moins `full`. Un autre nom (`read`, `support`…) est un niveau restreint. `scopes` et `access_levels` s'excluent : un connecteur sans niveaux garde `scopes`, et c'est son niveau `full`.
- `scope_implies` dit qu'un droit en couvre d'autres. C'est une donnée, propre au fournisseur, jamais une règle devinée : un droit « tout » ne couvre un droit « lecture » que si le fichier le dit.
- Chaque fonction d'un connecteur à niveaux déclare `scopes`, les droits qu'elle exige. Elle est servie dans un niveau si chacun de ses droits figure parmi ceux du niveau ou de ce qu'ils impliquent. `class` reste : il dit ce que fait la fonction, et se lit à côté.

Côté hôte :
- le niveau se règle par organisation, puis par équipe. Une équipe peut restreindre le niveau de son organisation, jamais l'élargir ;
- au niveau `full`, rien n'est filtré ;
- une fonction non couverte est retirée de ce que voit l'agent. Quand un outil regroupe plusieurs fonctions sous un paramètre d'opération, les opérations non couvertes sortent de son schéma. Un appel direct est refusé avec `access_level_denied`, qui nomme le niveau et l'opération ;
- le consentement demande les droits du niveau en vigueur au départ, signe ce niveau dans l'état du flux et le rejoue au retour. Les droits que le fournisseur dit avoir accordés sont gardés avec le compte ;
- passer d'un niveau restreint à `full` demande un nouveau consentement : un jeton obtenu pour `read` n'a pas les droits d'écriture.

### Le compte de service

En `oauth2_jwt_bearer` ([compte](compte.md), « Le compte de service »), `access_levels` remplace aussi `scopes`, avec `scope_implies`, et un champ de plus, `default_level` :

```yaml
auth:
  kind: oauth2_jwt_bearer
  token_url: https://oauth2.googleapis.com/token
  access_levels:
    read: { scopes: ["https://www.googleapis.com/auth/webmasters.readonly"] }
    full: { scopes: ["https://www.googleapis.com/auth/webmasters"] }
  scope_implies:
    "https://www.googleapis.com/auth/webmasters": ["https://www.googleapis.com/auth/webmasters.readonly"]
  default_level: read
  key: service_account_key
```

- Il n'y a pas de consentement : le niveau du compte décide seulement du `scope` de l'assertion. Passer de `read` à `full` ne demande rien à personne ; la prochaine assertion porte les droits de `full` (le jeton gardé est indexé par ses droits, un jeton de `read` ne sert pas à `full`).
- `default_level` nomme le niveau d'un compte dont l'organisation ou l'équipe n'a choisi aucun ; sans lui, `full`, comme ailleurs. Il garde en lecture les comptes qui existaient avant qu'un connecteur ne gagne un niveau d'écriture : sans lui, l'ajout d'un niveau `full` donnerait l'écriture à tous, sans geste de personne. La fabrique exige qu'il nomme un niveau déclaré (règle 7).
- Le niveau borne, il n'accorde pas : les droits effectifs restent ceux que le fournisseur donne au compte (en Search Console, son rôle sur chaque propriété).
- L'identité de consentement n'existe pas ; la sonde est servie à chaque niveau, comme ailleurs.

## Décisions et alternatives écartées

- **Les droits exigés, plutôt que la classe.** Un niveau « lecture » défini par `class: read` servirait toutes les lectures, y compris celles dont le droit n'est pas accordé. Les droits exigés ne servent que ce qui marchera.
- **`full` ne filtre rien.** `full` reproduit l'état d'avant, à l'octet près, pour tous les connecteurs existants. La déclaration des droits d'une fonction ne peut donc rien casser tant qu'aucune organisation ne choisit un niveau restreint.
- **L'implication déclarée.** Une règle générique (« `ALL` couvre `READ` ») varie d'un fournisseur à l'autre et se trompe en silence. Écartée.
- **Un outil par niveau** (un outil de lecture, un outil d'écriture). Il double la surface servie pour un réglage que la plupart des organisations ne touchent pas. Écarté au profit du filtrage des opérations.
- **Le niveau porté par le jeton seul.** Lire les droits accordés à chaque appel suffirait pour un nouveau consentement, mais laisserait écrire un jeton obtenu avant le réglage. Écarté : le niveau est une politique, le jeton n'en est qu'une conséquence.

## Sécurité et confidentialité

Le niveau borne ce que l'hôte laisse faire, indépendamment du jeton : un ancien jeton aux droits complets ne peut pas écrire sous un niveau restreint. Le consentement d'un niveau restreint ne demande que ses droits : le fournisseur n'en accorde pas plus. L'état signé du flux porte le niveau : il ne peut pas être élargi entre le départ et le retour.

## Écart avec le code

- Portés par le schéma (`connectors/connector.schema.json`) : `access_levels` (en `oauth2_user` et `oauth2_jwt_bearer` ; `full` exigé, au moins un niveau restreint, chaque niveau une liste de droits non vide), `scope_implies` et, en compte de service, `default_level` (seulement à côté de `access_levels`), l'exclusion de `scopes` et `access_levels`, et le `scopes` d'une fonction, exigé dans un connecteur à niveaux et refusé dans un connecteur sans niveaux.
- Portés par la fabrique (règle 7, [fabrique](fabrique.md)) : `scope_implies` est clos (la clé liste aussi ce qu'impliquent les droits qu'elle implique) et chacune de ses clés est un droit d'un niveau ; `full` couvre chaque droit d'un niveau restreint et chaque droit exigé par une fonction ; l'identité du consentement et la sonde sont servies à chaque niveau ; une identité lue à une adresse complète déclare ses propres droits, servis eux aussi à chaque niveau. La sortie TypeScript porte `accessLevels`, `scopeImplies` et le `scopes` de chaque fonction ; `ts/src/types.ts` les type.
- Un connecteur sans niveaux ne change pas : même validation, et son module TypeScript est le même à l'octet près (tests `test_connector_without_levels_*` et `test_committed_output_is_current`).
- Décrits avec niveaux (`read` et `full`) : `gmail`, `google_drive`, `google_calendar`, `google_sheets` (identité par userinfo OpenID, droits `openid` et `email` à chaque niveau) et `microsoft` (`read` : `Files.Read.All` et `Sites.Read.All` ; `full` : les droits `ReadWrite.All` ; ses treize fonctions sont des lectures, servies aux deux niveaux). Le client Python `microsoft` de la lib ne connaît pas les niveaux : ses droits de fichiers restent les `ReadWrite.All` (`oto/tools/microsoft/scopes.py`).
- Décrit avec niveaux en compte de service : `google_search_console` 1.1.0 (`read` par défaut, `webmasters.readonly` ; `full`, `webmasters`, qui sert en plus `add_sitemap`).
- Le moteur Python de la lib exécute les niveaux d'un compte de service (`oto/connectors/engine/levels.py`) : `engine.call(…, access_level=…)` ; sans niveau donné, `default_level`, sinon `full` ; un niveau non déclaré lève `UnknownAccessLevel` ; une fonction non servie est refusée avant tout envoi, `access_level_denied`, qui nomme le niveau et la fonction ; l'assertion revendique les droits du niveau. Cas communs `connectors/_cases/jwt_bearer.yaml` (`access_level`, `levelScopes` dans la sortie TypeScript).
- Rien d'autre n'exécute encore les niveaux : ni le réglage par organisation et par équipe chez un hôte, ni le filtrage des fonctions et des opérations servies à l'agent, ni le consentement au niveau en vigueur signé dans l'état du flux. Le paquet d'oto 2 n'exécute pas encore `oauth2_user` ni `oauth2_jwt_bearer` ([fabrique](fabrique.md), « Écart avec le code »).
- Premier usage : un CRM et un centre d'aide dont les outils sont écrits à la main chez l'hôte d'oto 1, qui déclare leurs niveaux dans le même vocabulaire, en Python.

## Questions ouvertes

- Un niveau restreint par défaut, posé par le connecteur plutôt que par l'organisation ? Tranché pour le compte de service (`default_level`, 2026-11-13) ; ouvert pour un consentement.
- Faut-il refuser, au départ du consentement, un niveau dont les droits ne couvrent aucune fonction ?
- Le niveau `read` est-il exigé (« au moins `read` et `full` », comme le dit le résumé), ou `full` et un niveau restreint quelconque suffisent-ils, comme le schéma l'accepte aujourd'hui ?
- Une fonction d'un connecteur sans niveaux peut-elle déclarer `scopes`, pour documenter ses droits avant que le connecteur n'ait de niveaux ? Le schéma le refuse aujourd'hui.
- L'implication est-elle transitive ? Le document ne le dit pas ; la fabrique exige une implication close, pour qu'une lecture en un pas et une lecture transitive servent les mêmes fonctions.

## Historique

- 2026-10-14 : création — niveaux d'accès, droits exigés par fonction, implication déclarée, réglage par organisation et par équipe — décidé par le mainteneur (source : besoin d'un essai en lecture seule).
- 2026-10-16 : niveaux portés par le schéma et la fabrique (règle 7) ; `gmail`, `google_drive` et `google_calendar` décrits avec `read` et `full` — décidé par le mainteneur.
- 2026-10-16 : l'identité d'un consentement peut se lire à une adresse complète, avec ses droits, servis à chaque niveau ; `google_sheets` et `microsoft` décrits avec `read` et `full` — décidé par le mainteneur.
- 2026-11-13 : niveaux d'accès en compte de service (`oauth2_jwt_bearer`), avec `default_level` ; `google_search_console` 1.1.0, `read` par défaut et `add_sitemap` au niveau `full` ; le moteur Python les exécute — décidé par le mainteneur (source : envoi d'un sitemap à Search Console, plutôt qu'un second connecteur).
