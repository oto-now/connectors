# Le compte d'un connecteur

- **Statut** : validé par le mainteneur le 29/09/2026 (issu du [format de description](format-de-description.md))
- **Dernière révision** : 2026-11-06

## Résumé

Le bloc `connector` d'une description dit ce qu'est un compte : les champs de son credential, ses réglages non secrets (région, adresse), son authentification, consentement OAuth d'une personne et compte de service compris, et, pour un compte rattaché chez un tiers qui héberge les comptes de ses utilisateurs, l'identité de ce compte et les champs que fournit l'hôte. Le fichier nomme et décrit ; garder, chiffrer, rafraîchir et résoudre relèvent de l'hôte (oto-pkg : docs/conception/connecteurs-et-comptes.md). Les niveaux d'accès d'un consentement ont leur document : [niveaux d'accès](niveaux-d-acces.md).

## Contexte

- Une API tierce s'ouvre par une clé, un couple identifiant et mot de passe, un jeton d'application (client credentials), le consentement d'une personne (code d'autorisation) ou un compte de service : une clé privée qui signe une assertion, échangée contre un jeton, sans personne qui consente (Google, pour Search Console ou Analytics).
- Certains services ont un hôte par région (`us`, `eu`) ou par tenant, ou vivent sur une adresse propre à chaque installation (un standard téléphonique).
- Une messagerie hébergée (LinkedIn, WhatsApp…) tient les sessions de ses utilisateurs derrière une seule clé d'abonnement, celle de l'hôte : chaque compte n'y est qu'un identifiant.

## Objectifs et non-objectifs

- Décrire une fois, pour les deux langages, tout ce qu'il faut pour signer une requête et ouvrir un consentement.
- Un compte garde tous ses champs ; un réglage non secret est typé, vérifié à l'exécution, cité par un gabarit d'adresse.
- Hors objectif : porter un secret, un compte ou une activation. Le fichier nomme les champs, jamais une valeur.

## Conception

### Champs, réglages et adresses

Les champs de `connector` qui disent le compte : `auth` (le mode, ci-dessous), `credential` (sauf `none` et `oauth2_user` : `name`, `label`, `secret` booléen, `holder` ; un seul champ, secret, en `oauth2_jwt_bearer`), `settings` (facultatif), `account_identity` (facultatif), `base_url` ou `base_urls` (exclusifs ; aucun des deux quand toutes les fonctions sont `handwritten`).

Un compte garde tous les champs que déclare `credential` (les secrets chiffrés par l'hôte) et ses réglages, `settings` : `name`, `label`, `type`, et selon le type, `choice` (`choices`, liste fermée d'au moins deux valeurs, `default` facultatif qui en est une : `{ name: region, label: Data region, type: choice, choices: [us, eu], default: us }`), `text` (`pattern`, expression ancrée : `{ name: domain, label: Domain, type: text, pattern: "^[a-z0-9-]+$" }`) ou `url` (adresse `https://` libre, saisie par un admin : serveur d'un standard, site).

Un réglage n'est cité que par une adresse, et tout réglage est cité. `base_url`, `token_url` et `authorize_url` sont des gabarits : `https://{domain}.example.net` (domaine d'un tenant), `{server}/xapi/v1`, sans `/` final. Un réglage `url` (ou une valeur de `from_token`) ne se cite qu'en tête, à la place du schéma et de l'hôte ; un autre réglage, qu'après `https://`. `base_urls` et `token_urls` donnent une adresse à chaque valeur d'un `choice` (`{ setting: region, values: { us: …, eu: … } }`), quand les hôtes ne se déduisent pas l'un de l'autre. Un nom n'est déclaré qu'une fois parmi `credential`, `settings` et `from_token`. L'exécution garde ces adresses : `https` seul, jamais un hôte qui résout vers une adresse interne, aucune redirection suivie ; une valeur `text` ou `choice` ne porte que lettres, chiffres, `-` et `_`.

### Les modes d'authentification

`auth.kind`, chaque secret nommant un champ de `credential`, jamais une valeur :
- `api_key` : `in` (`header` ou `query`), `name` (de l'en-tête ou du paramètre, jamais le nom d'un paramètre de query de la fonction), `key`, `prefix` en en-tête seulement : `{ kind: api_key, in: header, name: X-Claap-Key, key: api_key }` ;
- `bearer` (`token`) ; `basic` (`password`, et `username` facultatif : `Authorization: Basic base64(username + ":" + password)`, le texte encodé en UTF-8 ; sans `username`, l'identifiant est vide, `Basic base64(":" + password)`, pour un service qui prend sa clé seule en mot de passe : `{ kind: basic, password: api_key }`) ;
- `oauth2_client_credentials` : `token_url` ou `token_urls`, `token_request` (`json`, `form`), `client_auth` (`body` ou `basic` : où vont l'identifiant et le secret du client), `client_id`, `client_secret`, `scope` et `expires_in_default` facultatifs ;
- `oauth2_user`, le consentement d'une personne (code d'autorisation) : `authorize_url`, `token_url` ou `token_urls` (corps en formulaire), `client_auth`, `scopes`, `authorize_params` (constantes, jamais un paramètre que pose l'hôte : `client_id`, `redirect_uri`, `state`…), `pkce: S256`, `refresh` (`refresh_token` avec `rotates` booléen, `exchange` avec `exchange: { grant_type, token_param }`, ou `none`), `expires_in_default`, `identity` (qui a consenti, pour nommer le compte), `from_token: [instance_url]` (champs de la réponse de jeton gardés avec le compte, citables par `base_url`) ; à la place de `scopes`, `access_levels` et `scope_implies`, les [niveaux d'accès](niveaux-d-acces.md) ;
- `oauth2_jwt_bearer`, un compte de service (RFC 7523) : `token_url`, `scopes`, `key` (« Le compte de service », ci-dessous) : `{ kind: oauth2_jwt_bearer, token_url: https://oauth2.googleapis.com/token, scopes: [https://www.googleapis.com/auth/webmasters.readonly], key: service_account_key }` ; à la place de `scopes`, `access_levels`, `scope_implies` et `default_level`, les [niveaux d'accès](niveaux-d-acces.md) ;
- `none`.

L'identité d'un `oauth2_user` est exigée : `identity: { function: get_me, path: userPrincipalName }`, une lecture sans argument requis, ou, quand l'API n'a pas de telle lecture, `identity: { url, path, scopes }` : une adresse complète en `https`, fixe (ni gabarit, ni query), hors de `base_url` (le service d'identité commun du fournisseur), lue en `GET` avec le jeton et sans argument, et les droits qu'elle exige, que le consentement demande à chaque niveau.

En `oauth2_user`, `credential` est interdit (le schéma le refuse) : les jetons du consentement ne sont pas des champs, et `client_id`/`client_secret` appartiennent à l'application OAuth (celle de l'hôte par défaut, celle de l'organisation si elle en pose une), pas au compte ni au fichier.

**Un accès propre, un connecteur.** Un compte se signe par un seul accès. Un service qui s'ouvre par deux accès distincts (l'application et la personne : le jeton du bot et celui d'un utilisateur Slack ; le courrier d'une boîte en accès d'application et l'accès délégué d'une personne chez Microsoft) se décrit en deux connecteurs, chacun son compte, plutôt qu'en un compte à deux jetons.

### Le compte de service

`oauth2_jwt_bearer` : l'hôte signe une assertion avec la clé privée du compte et l'échange contre un jeton d'accès, posé ensuite en `Authorization: Bearer`, sans consentement ni renouvellement (une nouvelle assertion à l'échéance).

- **La description** : `token_url`, une adresse `https` fixe (ni gabarit, ni query, ni `token_urls`), qui est aussi l'audience de l'assertion ; `scopes`, une liste non vide, joints par des espaces dans l'assertion ; `key`, le seul champ de `credential`, secret (règle 2 de la [fabrique](fabrique.md)), qui porte le fichier JSON entier de la clé, collé tel que le fournisseur le donne. Pas de `sub`.
- **Les niveaux d'accès** (décidé le 2026-11-13) : à la place de `scopes`, `access_levels` (au moins `full` et un niveau restreint), `scope_implies` et `default_level`, comme pour un consentement ([niveaux d'accès](niveaux-d-acces.md)) ; chaque fonction déclare alors ses `scopes`. Le `scope` de l'assertion est celui du niveau du compte : celui que l'hôte a réglé pour l'organisation ou l'équipe, sinon `default_level`, sinon `full` ; un niveau non déclaré lève, jamais un repli. Aucun consentement : le compte de service ne consent à rien, le niveau décide seulement de ce que demande l'assertion ; passer de `read` à `full` ne demande rien à personne, la prochaine assertion porte les droits du nouveau niveau (le jeton gardé est indexé par ses droits). Les droits effectifs restent ceux que le fournisseur accorde au compte (un utilisateur Restricted de Search Console ne soumet pas de sitemap, même au niveau `full`) : le niveau borne, il n'accorde pas.
- **La clé** : l'hôte la lit et la refuse, avant tout envoi et dans cet ordre, quand elle n'est pas un objet JSON (`service_account_key_not_json`), quand `client_email` ou `private_key` manque ou est vide (`service_account_key_incomplete`), quand elle porte un `token_uri` autre que `token_url` (`service_account_key_foreign_token_uri` : la clé est un texte saisi, elle ne choisit pas où part une assertion signée), quand `private_key` n'est pas une clé RSA en PEM PKCS#8, étiquette `PRIVATE KEY` (`service_account_key_unreadable`). `private_key_id`, facultatif, devient le `kid` de l'en-tête ; les autres champs sont ignorés.
- **L'assertion** : en-tête `{ alg: RS256, typ: JWT }`, et `kid` quand la clé a un `private_key_id` ; revendications `{ iss: client_email, scope, aud: token_url, iat: maintenant, exp: iat + 3600 }`, en secondes. Chaque partie est sérialisée en JSON compact, dans cet ordre des clés, UTF-8, puis en base64url sans remplissage ; la signature RSASSA-PKCS1-v1_5 SHA-256 de `en-tête.revendications`, déterministe, est ajoutée de même. L'ordre et la sérialisation ne changent rien pour le fournisseur ; ils rendent l'assertion exacte, donc comparable d'un langage à l'autre.
- **La requête de jeton** : un seul `POST` à `token_url`, corps en formulaire `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=…`, jamais l'assertion en query, aucune redirection suivie. La réponse donne `access_token` et `expires_in`. Un refus `invalid_grant` marque le compte `credential_rejected` (la clé est révoquée, supprimée, ou le compte désactivé) ; tout autre refus, une redirection comprise, est une erreur du tiers (`upstream_error`) qui ne marque pas le compte.

Les cas communs `connectors/_cases/jwt_bearer.yaml` disent, pour une clé RSA de test jetable et une horloge, l'assertion exacte, la requête de jeton exacte, ce que donne la réponse, et le refus de chaque clé refusée. La clé de test n'y est jamais écrite en PEM, ni l'assertion entière : les gardes de secrets d'un dépôt public, celle d'oto-core comprise, qui copie la sortie TypeScript, refusent un en-tête de clé privée et un JWT en clair ; chaque côté encadre la clé à l'exécution.

### Compte de l'hôte et compte rattaché

Un champ de `credential` porte `holder` : `account` (par défaut : saisi avec le compte) ou `host` (fourni par l'application hôte, une fois pour toutes ses organisations ; jamais stocké avec le compte, montré ni saisissable). Un compte rattaché chez un tiers qui héberge les comptes de ses utilisateurs déclare `account_identity` : `{ name: account_id, label: LinkedIn account, pattern: "^[A-Za-z0-9_-]{1,64}$" }`, l'identifiant du compte chez le tiers, une valeur par compte, à motif ancré. Il ne se cite que dans `base_url`, ou dans chaque adresse de `base_urls`, après l'hôte (jamais à la place du schéma ou de l'hôte), et dans aucune autre adresse ; aucun argument, paramètre de chemin, de requête, de corps ou d'en-tête, ni aucune constante ne porte son nom, ni aucun champ de `credential`, réglage ou valeur de `from_token` (règle 8 de la [fabrique](fabrique.md)) : l'agent ne le fournit jamais, il vient du compte que la règle de partage de l'hôte a désigné. Un connecteur dont tous les champs sont `holder: host` est valide : le compte n'a alors que son identité, ou rien (une clé de l'hôte pour toutes ses organisations). L'hôte assemble à l'appel son secret, les champs du compte et l'identité ; il écrit l'identité par un seul service qui la vérifie auprès du tiers, jamais par une saisie (la clé de l'hôte atteint tous les comptes de son abonnement).

```yaml
connector:
  name: linkedin_unipile
  base_url: https://api.unipile.com/v2/{account_id}
  auth: { kind: api_key, in: header, name: X-API-KEY, key: api_key }
  credential:
    - { name: api_key, label: Unipile API key, secret: true, holder: host }
  account_identity: { name: account_id, label: LinkedIn account, pattern: "^[A-Za-z0-9_-]{1,64}$" }
```

### Contrôles

`tests/test_connector_descriptions.py` fait refuser, sur des variantes du fichier Sellsy : `client_auth` absent, `token_url` absent, `credential` absent, `token_url` et `token_urls` ensemble, `base_url` et `base_urls` ensemble, liste fermée d'une valeur, `text` sans motif ou à motif non ancré, `url` à liste, gabarit à `/` final, clé en query préfixée, clé sans place, `oauth2_user` avec un credential, avec `client_id`, sans identité, `rotates` hors `refresh_token`, `exchange` sans sa demande, paramètre d'autorisation réservé à l'hôte, PKCE inconnu ; pour l'identité à une adresse, sept de plus (sans droits ou à liste vide, avec aussi une fonction, hors `https`, avec une query, en gabarit, sans chemin) ; pour le compte de l'hôte et le compte rattaché, six de plus (`holder` inconnu, champ fourni par l'hôte dans un `oauth2_user`, identité à motif non ancré, sans motif, à nom invalide, à clé inconnue) ; pour `basic`, deux de plus (sans mot de passe, à identifiant vide) ; pour les niveaux d'accès, treize de plus (niveaux et `scopes` ensemble, sans `full`, `full` seul, niveau sans droits ou à liste vide, à clé inconnue ou à nom invalide, `scope_implies` sans niveaux ou à liste vide, niveaux hors `oauth2_user`, `scopes` d'une fonction sans niveaux, fonction sans `scopes` ou à liste vide dans un connecteur à niveaux). Les règles hors schéma sont les règles 2, 5, 6, 7 et 8 de la [fabrique](fabrique.md) ; pour le compte de service, onze de plus (sans `token_url`, sans `scopes` ou à liste vide, sans `key`, `token_url` en gabarit, à query, hors `https`, `token_urls`, niveaux d'accès, `sub`, sans `credential`). Les règles hors schéma sont aussi jugées par `tests/test_fabrique.py` (`key` qui ne nomme pas un champ, un second champ, un champ non secret, un en-tête `Authorization` constant). Les cas communs `connectors/_cases/auth.yaml` disent, sur des valeurs de champs, la requête signée ; `connectors/_cases/jwt_bearer.yaml`, l'échange du compte de service.

## Décisions et alternatives écartées

- **Un seul secret par compte, l'adresse ou la région dans un champ du credential** : écarté le 2026-10-08. Un compte garde tous ses champs ; une région ou une adresse est un réglage non secret, typé, cité par un gabarit, que l'écran de l'hôte propose et que l'exécution vérifie.
- **Un connecteur par région** (`typeform_eu`) : écarté le 2026-10-08 ; les fonctions et le contrat ne changent pas avec l'hôte.
- **`oauth2_user` renvoyé à du code écrit à la main, le jeton passé en `bearer`** : écarté le 2026-10-08. Le consentement (adresses, scopes, PKCE, renouvellement, rotation, identité) se décrit, et l'hôte l'exécute une fois pour tous.
- **L'identifiant et le secret du client OAuth dans `credential`** : écarté le 2026-10-08. Ils appartiennent à l'application, pas au compte d'une personne.
- **Deux jetons dans un même compte, choisis par fonction ou par argument** : écarté le 2026-10-22, un second connecteur (« Un accès propre, un connecteur »).
- **Le compte de service en champs séparés (`client_email`, `private_key`) dans `credential`** : écarté le 2026-11-06. Le fournisseur donne un fichier ; le coller entier évite une recopie fautive (une clé PEM aux retours à la ligne perdus) et l'hôte le lit une fois.
- **L'adresse de jeton lue dans la clé (`token_uri`)** : écarté le 2026-11-06. Un texte saisi désignerait la destination d'une assertion signée ; `token_url` est fixé par la description, et une clé qui en dit un autre est refusée.
- **Le compte de service comme une variante d'`oauth2_client_credentials`** : écarté le 2026-11-06. Il n'a ni identifiant ni secret de client, et sa requête porte une assertion signée de la clé du compte ; un mode à part garde chaque branche du schéma fermée.

## Sécurité et confidentialité

- Le fichier ne porte aucun secret : il nomme les champs du credential (`secret: true`) et `auth` y renvoie par leur nom ; un réglage n'est jamais un secret. Le secret est fourni par le consommateur à chaque appel ; la lib n'en lit aucun (garde `tests/test_no_secret_read_guard.py`).
- Une clé passée en query (`in: query`) est admise : le tiers l'a voulue là et la voit de toute façon ; l'exécution la masque dans tout journal, adresse comprise.
- Une adresse saisie par un admin (`url`, valeur de `from_token`) n'est suivie qu'en `https`, vers un hôte qui ne résout jamais vers une adresse interne, sans redirection ; le format le déclare, l'exécution le garde.
- `base_url` nomme la racine documentée de l'API de l'éditeur. Une coordonnée tirée du front d'un tiers (clé d'API publique, identifiant d'application) n'entre jamais dans un fichier (`docs/conventions.md`).
- L'identité d'un compte rattaché ne vient jamais de l'agent : la clé de l'hôte atteint tous les comptes de son abonnement.
- Compte de service : la clé privée ne quitte jamais l'hôte, seule l'assertion part, dans le corps (jamais en query, donc jamais dans une adresse journalisée), vers `token_url` seulement, sans redirection suivie ; un `token_uri` étranger fait refuser la clé (falsification de requête côté serveur). Un refus cite ce qu'a répondu le tiers (`error`, `error_description`), jamais la requête.

## Écart avec le code

- Oto-core exécute les réglages et leurs gabarits, `baseUrls`, `tokenUrls` en `oauth2_client_credentials`, la clé en en-tête ou en query, `clientAuth`, le consentement `oauth2_user` (`state` à usage unique, PKCE S256, renouvellement par `refresh_token` ou `exchange`, identité par une fonction ou une adresse), `holder` et `account_identity`, `basic` sans `username` (relu à son commit d26f513). En `oauth2_user`, il refuse encore à la déclaration les réglages, `tokenUrls` et `fromToken` (relu à son commit d3500b4) : `salesforce`, qui cite un réglage et l'instance de la réponse de jeton, n'y est pas servi tant qu'il ne les exécute pas ; il contrôle les niveaux d'accès sans les appliquer (le consentement demande les droits du niveau `full`).
- `in: query`, `token_urls` et `exchange` ne servent à aucune description.
- `oauth2_jwt_bearer` (2026-11-06) est écrit dans le schéma, la fabrique (règle 2), la sortie TypeScript et son contrat (`ts/src/types.ts`) et les cas communs ; `google_search_console` s'en sert, avec des niveaux d'accès depuis sa version 1.1.0 (2026-11-13 : schéma, règle 7, `defaultLevel` dans la sortie, cas `assertion_at_default_level` et `assertion_at_level_full`, moteur Python par `access_level`). Oto-core ne l'exécute pas encore : un paquet antérieur refuse à la déclaration un mode inconnu. Le client Python `google_analytics` mène le même échange, écrit à la main (`oto/tools/google_analytics/auth.py`) : il refuse en plus une clé dont le `type` n'est pas `service_account` (un identifiant de client OAuth collé par erreur), ce que le format n'exige pas.
- Les clients Python `typeform` et `amplitude` prennent leur région en paramètre ; `microsoft` garde son module de consentement écrit à la main (`oto/tools/microsoft/auth.py`), que la description recopie ; la lib ne connaît pas les niveaux.
- `jefacture` : le renouvellement passe par une adresse propre (`/oauth2/refresh`) que le client appelle, alors que le format renouvelle à `token_url`.

## Questions ouvertes

- Ce que le format ne sait pas encore dire : une signature par requête (`boondmanager`) ; en `oauth2_client_credentials`, l'authentification du client par certificat (assertion signée), que Microsoft recommande en production (`microsoft_app`), qui reposerait sur la signature de `oauth2_jwt_bearer` mais dans un autre flux (l'assertion authentifie le client d'un échange `client_credentials`, elle n'est pas la demande de jeton) : une suite possible ; un réglage cité ailleurs que dans une adresse (en-tête qui choisit la société ; `x-origami-project` d'`origami`) ; l'identité lue dans la réponse de jeton plutôt que par une fonction ; un paramètre d'application autre que le client (`config_id`) ; des scopes séparés par des virgules ; le renouvellement à une adresse distincte de `token_url` (`jefacture`).
- `threecx` : une connexion par identifiant et mot de passe d'un compte utilisateur, un corps JSON dont la réponse porte le jeton à un chemin (`Token.access_token`) et dont un refus se lit dans le corps (`Status`), que ni `basic` ni `oauth2_client_credentials` ne disent ; une adresse saisie (réglage `url`) qui doit être une racine, sans chemin : le client la refuse, le réglage l'admet.
- `salesforce` : une adresse d'identité qui suive l'hôte du compte (production, bac à sable ou instance), que `identity.url`, fixe, ne dit pas. Côté oto-core : un réglage et `fromToken` en `oauth2_user`.
- `slack` : l'authentification du compte vers un autre hôte que `base_url` (les fichiers privés, sur `files.slack.com`) ; un client qui est le code de deux connecteurs, que `connectors/clients.yaml` rattache à un seul (un changement d'`oto.tools.slack` ne fait monter que la version de `slack`).
- Google : un connecteur par service ou un seul connecteur `google`, un consentement pour tous les services.
- Compte de service, hors périmètre du mode : la délégation à l'échelle du domaine (`sub`, le compte agit au nom d'une personne de l'organisation).

## Historique

- 2026-09-30 : le champ de `auth` qui nomme le secret (`key`, `token`, `username`, `password`), `scope` en OAuth2 — choix du projet (source : lot 2 du 30/09).
- 2026-10-08 : un compte garde tous les champs de son credential et des réglages non secrets (liste fermée, motif, adresse libre gardée à l'exécution) ; `base_url` et `token_url` dépendent d'un réglage (`base_urls`, `token_urls` ou gabarit) ; clé admise en query, masquée à l'exécution ; `client_auth` ; `oauth2_user` déclaratif, sans credential, l'application OAuth hors du compte — décidé par le mainteneur.
- 2026-10-16 : `scopes` d'une fonction et niveaux d'accès en `oauth2_user` ([niveaux d'accès](niveaux-d-acces.md)) ; l'identité d'un `oauth2_user` peut se lire à une adresse complète (`identity: { url, path, scopes }`), ses droits demandés à chaque niveau — décidé par le mainteneur.
- 2026-10-16 : champ fourni par l'hôte (`holder: host`) et identité d'un compte rattaché (`account_identity`), pour les messageries hébergées — décidé par le mainteneur (source : revue des connecteurs d'oto 2 du 16/10).
- 2026-10-17 : `holder` et `account_identity` (citée par l'adresse de base seulement, après l'hôte) écrits dans le schéma, la fabrique (règle 8) et la sortie TypeScript ; premier compte rattaché — choix du projet (source : décision du mainteneur du 16/10).
- 2026-10-21 : `auth.kind: basic` admet un `username` absent, l'identifiant vide (une clé seule en mot de passe) ; cas communs de l'authentification — décidé par le superviseur.
- 2026-10-22 : un accès propre, un connecteur : deux jetons d'un même service font deux connecteurs, sans changer le format — décidé par le mainteneur.
- 2026-11-03 : sorti de la note « Format de description » lors de son redécoupage en documents par sujet ; les résumés par connecteur sortent pour la carte du parc et l'en-tête de chaque description — décidé par le responsable produit (source : demande de rationalisation des connecteurs).
- 2026-11-06 : septième mode, `oauth2_jwt_bearer`, le compte de service (RFC 7523) : un seul champ secret, le fichier JSON de la clé ; `token_url` fixe, `token_uri` étranger refusé ; assertion RS256 exacte et requête de jeton en formulaire ; cas communs `connectors/_cases/jwt_bearer.yaml` ; `sub` et l'assertion client par certificat hors périmètre — décidé par le mainteneur (source : lecture de Search Console par un agent).
- 2026-11-13 : le compte de service reçoit des niveaux d'accès (`access_levels`, `scope_implies`) et un niveau par défaut (`default_level`), plutôt qu'un second connecteur ; le `scope` de l'assertion est celui du niveau du compte, sans consentement — décidé par le mainteneur (source : envoi d'un sitemap à Search Console).
