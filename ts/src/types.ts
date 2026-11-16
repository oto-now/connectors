// Le contrat des définitions que la fabrique génère depuis `connectors/*/connector.yaml` (écrit à la main, pas généré).
// Des données structurelles seulement : l'hôte adapte chaque définition à son propre contrat de fonction et exécute la
// requête avec son client HTTP, son coffre et son journal. Rien ici n'envoie de requête ni ne lit de secret.

/** read : lecture ; write : écriture ; sensitive : envoie, supprime ou paie, confirmé avant exécution. */
export type FunctionClass = "read" | "write" | "sensitive"

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE"

/** Un JSON Schema (2020-12), porté tel quel : l'hôte le valide avec un validateur JSON Schema standard. */
export type JsonSchema = { readonly [key: string]: unknown }

/** Valeur JSON, telle qu'une constante la porte. */
export type JsonValue = string | number | boolean | null | readonly JsonValue[] | { readonly [key: string]: JsonValue }

/**
 * Qui fournit un champ du credential. `account` : saisi avec le compte, gardé avec lui. `host` : fourni par l'application
 * hôte, une fois pour toutes ses organisations ; jamais stocké avec le compte, montré ni saisissable.
 */
export type CredentialHolder = "account" | "host"

/**
 * Un champ du credential que l'hôte fournit à l'appel ; un compte garde tous les champs que déclare son connecteur dont
 * `holder` est `account`, l'hôte ajoute les siens.
 */
export type CredentialField = {
  readonly name: string
  readonly label: string
  readonly secret: boolean
  readonly holder: CredentialHolder
}

/**
 * L'identité d'un compte rattaché chez un tiers qui héberge les comptes de ses utilisateurs (une messagerie hébergée) :
 * l'identifiant du compte chez le tiers, une valeur par compte, qui satisfait `pattern` (expression ancrée). Elle n'est
 * citée que par `baseUrl` (ou chaque adresse de `baseUrls`), après l'hôte, sous `{name}` ; aucun argument ni aucun
 * paramètre de requête ne la porte : l'agent ne la fournit jamais. L'hôte l'écrit par un seul service qui la vérifie
 * auprès du tiers, jamais par une saisie, et l'assemble à l'appel avec son secret et les champs du compte.
 */
export type AccountIdentity = { readonly name: string; readonly label: string; readonly pattern: string }

/**
 * Un réglage non secret d'un compte, saisi par qui le connecte. `choice` : une valeur d'une liste fermée (une région),
 * `default` à défaut. `text` : un texte libre qui satisfait `pattern` (le sous-domaine d'un tenant). `url` : une adresse
 * `https://` libre, saisie par un admin. Un réglage n'est cité que par une adresse (`baseUrl`, `baseUrls`, `tokenUrl`,
 * `tokenUrls`, `authorizeUrl`).
 *
 * L'hôte garde l'exécution : une adresse libre (réglage `url` ou valeur de `fromToken` en tête d'un gabarit) est en
 * `https` seulement, son hôte ne résout jamais vers une adresse interne (boucle locale, plages privées, lien-local), à
 * chaque résolution, et aucune redirection n'est suivie ; un réglage `text` ou `choice` ne se substitue que s'il ne
 * porte que des lettres, des chiffres, `-` et `_`.
 */
export type Setting =
  | { readonly name: string; readonly label: string; readonly type: "choice"; readonly choices: readonly string[]; readonly default?: string }
  | { readonly name: string; readonly label: string; readonly type: "text"; readonly pattern: string }
  | { readonly name: string; readonly label: string; readonly type: "url" }

/** Une adresse par valeur d'un réglage `choice` : chaque valeur a la sienne. */
export type UrlsBySetting = { readonly setting: string; readonly values: { readonly [choice: string]: string } }

/**
 * Comment l'identifiant et le secret du client atteignent le point de jeton : dans le corps de la requête, ou en HTTP
 * Basic.
 */
export type ClientAuth = "body" | "basic"

/** Le point de jeton : une adresse (gabarit possible sur les réglages) ou une adresse par valeur d'une liste fermée. */
export type TokenEndpoint = { readonly tokenUrl: string; readonly tokenUrls?: never } | { readonly tokenUrls: UrlsBySetting; readonly tokenUrl?: never }

/**
 * Le renouvellement d'un consentement. `refresh_token` : la demande par jeton de rafraîchissement ; si `rotates`, chaque
 * réponse porte un nouveau jeton de rafraîchissement qui remplace l'ancien, que l'hôte réécrit seulement si le jeton
 * stocké est encore celui qu'il a lu. `exchange` : le jeton d'accès courant, envoyé dans `exchange.tokenParam` avec
 * `grant_type` = `exchange.grantType`, contre un nouveau. `none` : le jeton vaut jusqu'à expiration ou révocation, puis
 * la personne consent de nouveau.
 */
export type OAuthRefresh =
  | { readonly refresh: "refresh_token"; readonly rotates: boolean; readonly exchange?: never }
  | { readonly refresh: "exchange"; readonly exchange: { readonly grantType: string; readonly tokenParam: string }; readonly rotates?: never }
  | { readonly refresh: "none"; readonly rotates?: never; readonly exchange?: never }

/** Les droits que demande le consentement d'un niveau d'accès, joints par des espaces. */
export type AccessLevel = { readonly scopes: readonly string[] }

/**
 * Les droits d'un consentement. Sans niveaux : `scopes`, qui est le niveau `full`. Avec niveaux : `accessLevels`, qui
 * porte `full` (il ne filtre rien) et au moins un niveau restreint (`read`, `support`…), et `scopeImplies`, un droit qui
 * en couvre d'autres, tel que le fournisseur le définit (jamais une règle devinée ; close : un droit impliqué n'en
 * implique pas d'autres que la clé ne liste pas). L'hôte choisit un niveau par organisation, puis par équipe, qui le
 * restreint sans jamais l'élargir ; `full` par défaut (un compte de service peut en nommer un autre, `defaultLevel`).
 * Une fonction est servie à un niveau quand chacun de ses `scopes` est un droit du niveau ou impliqué par l'un d'eux ; sinon elle sort de ce que voit l'agent et l'appel est refusé par
 * `access_level_denied`, qui nomme le niveau et l'opération. Le consentement demande les droits du niveau en vigueur au
 * départ, signé dans l'état du flux ; passer d'un niveau restreint à `full` demande un nouveau consentement.
 */
export type OAuthScopes =
  | { readonly scopes?: readonly string[]; readonly accessLevels?: never; readonly scopeImplies?: never }
  | {
      readonly accessLevels: { readonly full: AccessLevel; readonly [level: string]: AccessLevel }
      readonly scopeImplies?: { readonly [scope: string]: readonly string[] }
      readonly scopes?: never
    }

/**
 * Les droits d'un compte de service : `scopes` (le niveau `full`), ou des niveaux d'accès comme ceux d'un consentement
 * (`OAuthScopes`), dont `defaultLevel`, le niveau d'un compte sans réglage, qui doit être un niveau déclaré.
 */
export type ServiceAccountScopes =
  | { readonly scopes: readonly string[]; readonly accessLevels?: never; readonly scopeImplies?: never; readonly defaultLevel?: never }
  | {
      readonly accessLevels: { readonly full: AccessLevel; readonly [level: string]: AccessLevel }
      readonly scopeImplies?: { readonly [scope: string]: readonly string[] }
      readonly defaultLevel?: string
      readonly scopes?: never
    }

/**
 * Le consentement d'une personne (code d'autorisation, OAuth 2.0). L'application OAuth (identifiant et secret du client)
 * n'est ni dans la description ni dans `credential` : c'est celle de l'hôte, ou celle que pose l'organisation. Les jetons
 * qu'il rend ne sont pas des champs de `credential`. Le point de jeton reçoit un corps en formulaire ; les `scopes` se
 * joignent par des espaces ; `authorizeParams` s'ajoutent à l'adresse d'autorisation, jamais un paramètre que l'hôte
 * pose lui-même (`client_id`, `redirect_uri`, `response_type`, `scope`, `state`, `code_challenge*`).
 */
export type OAuthUser = {
  readonly kind: "oauth2_user"
  readonly authorizeUrl: string
  readonly clientAuth: ClientAuth
  readonly authorizeParams?: { readonly [param: string]: string }
  /** Preuve de possession du code (PKCE), par SHA-256. */
  readonly pkce?: "S256"
  /** Durée de vie, en secondes, d'un jeton d'accès dont la réponse n'en donne pas. */
  readonly expiresInDefault?: number
  /**
   * Qui a consenti, lu juste après le consentement pour nommer le compte, et le chemin pointé d'une valeur non vide de
   * la réponse (l'adresse de la personne). Soit `function` (nom qualifié, une lecture sans argument requis), appelée avec
   * `{}` ; soit `url`, une adresse complète hors de `baseUrl` (le service d'identité commun d'un fournisseur), lue en
   * `GET` avec le jeton d'accès et sans argument, et `scopes`, les droits qu'elle exige, que le consentement demande à
   * chaque niveau.
   */
  readonly identity:
    | { readonly function: string; readonly path: string; readonly url?: never; readonly scopes?: never }
    | { readonly url: string; readonly path: string; readonly scopes: readonly string[]; readonly function?: never }
  /**
   * Champs de la réponse de jeton gardés avec le compte, non secrets, renouvelés à chaque réponse de jeton, cités par
   * `baseUrl` comme un réglage (une `instance_url` qui devient l'adresse de l'API).
   */
  readonly fromToken?: readonly string[]
} & TokenEndpoint & OAuthRefresh & OAuthScopes

/**
 * Chaque valeur qui désigne un secret nomme un champ de `credential`, jamais une valeur. Une clé passée en query
 * (`in: "query"`) est masquée par l'hôte dans tout journal, adresse comprise.
 */
export type Auth =
  | { readonly kind: "api_key"; readonly in: "header"; readonly name: string; readonly prefix?: string; readonly key: string }
  | { readonly kind: "api_key"; readonly in: "query"; readonly name: string; readonly prefix?: never; readonly key: string }
  | { readonly kind: "bearer"; readonly token: string }
  /**
   * `Authorization: Basic base64(username + ":" + password)`, le texte encodé en UTF-8. Sans `username`, l'identifiant est
   * vide : `Basic base64(":" + password)`, une clé seule passée en mot de passe. Cas communs : `./auth-cases`.
   */
  | { readonly kind: "basic"; readonly username?: string; readonly password: string }
  | ({
      readonly kind: "oauth2_client_credentials"
      readonly tokenRequest: "json" | "form"
      readonly clientAuth: ClientAuth
      readonly clientId: string
      readonly clientSecret: string
      readonly scope?: string
      readonly expiresInDefault?: number
    } & TokenEndpoint)
  | OAuthUser
  /**
   * Un compte de service (JWT bearer, RFC 7523). `key` nomme le seul champ de `credential`, secret : le fichier JSON entier
   * de la clé du compte de service, dont l'hôte exige `client_email` et `private_key` (PEM PKCS#8) ; `private_key_id`,
   * facultatif, devient le `kid` de l'en-tête ; une clé dont `token_uri` diffère de `tokenUrl` est refusée. L'hôte signe
   * en RS256 l'assertion `{ iss: client_email, scope: scopes joints par des espaces, aud: tokenUrl, iat, exp: iat + 3600 }`,
   * l'échange à `tokenUrl` (POST en formulaire, `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`, `assertion`,
   * aucune redirection suivie) contre un jeton envoyé en `Authorization: Bearer`. Cas communs : `./jwt-bearer-cases`.
   * Les droits : `scopes`, ou des niveaux d'accès (`accessLevels`, `scopeImplies`, comme un consentement) ; le `scope` de
   * l'assertion est alors celui du niveau du compte, et `defaultLevel` nomme le niveau d'un compte dont l'organisation
   * ou l'équipe n'a choisi aucun (`full` sans lui). Aucun consentement : changer de niveau change seulement les droits de
   * la prochaine assertion (le jeton gardé est indexé par ses droits).
   */
  | ({ readonly kind: "oauth2_jwt_bearer"; readonly tokenUrl: string; readonly key: string } & ServiceAccountScopes)
  | { readonly kind: "none" }

/**
 * Ce qu'un refus révèle du compte, pour que l'hôte le marque jusqu'à son prochain appel réussi : `credential_rejected`, le
 * service refuse la clé ou le jeton du compte ; `quota_exhausted`, ses crédits ou le quota de son offre sont épuisés.
 * Seul ce qui est déclaré marque : rien ne marque par défaut.
 */
export type AccountMark = "credential_rejected" | "quota_exhausted"

/**
 * Une condition sur le corps JSON de la réponse, sur la valeur au chemin pointé `path` (un indice de liste en chiffres),
 * par un seul opérateur. `equals` : la valeur est l'une des valeurs listées, de même type, exacte (`190` n'est pas `"190"`,
 * un texte garde sa casse). `contains` : la valeur est un texte qui contient l'un des textes listés, les deux mis en
 * minuscules par `toLowerCase()` (la casse Unicode par défaut, sans locale) ; une valeur absente, nulle, un nombre, un
 * booléen, une liste ou un objet ne contient rien, ni un corps qui n'est pas du JSON. L'entrée ne vaut que si la condition
 * tient ; sinon l'hôte essaie l'entrée suivante du même statut. Un statut 2xx n'est un refus que sous une condition (une
 * erreur GraphQL sous un 200). Cas communs : `./error-cases`.
 */
export type BodyCondition =
  | { readonly path: string; readonly equals: readonly (string | number | boolean)[]; readonly contains?: undefined }
  | { readonly path: string; readonly contains: readonly string[]; readonly equals?: undefined }

/**
 * Une ligne de la table d'erreurs du connecteur : un ou plusieurs statuts HTTP du tiers, une condition sur le corps
 * facultative, le refus nommé ; la première ligne qui s'applique l'emporte. `marksAccount` : le refus marque le compte
 * (jamais `credential_rejected` sur un 403, ni `quota_exhausted` sur un 429, sans condition sur le corps).
 */
export type ApiError = {
  readonly status: number | readonly number[]
  readonly code: string
  readonly message: string
  readonly retryable: boolean
  readonly body?: BodyCondition
  readonly marksAccount?: AccountMark
}

/**
 * Un refus propre à une fonction : surcharge ou complète la table d'erreurs ; `when` est un statut HTTP ou une condition.
 * `body` et `marksAccount` comme dans la table d'erreurs, sous un statut seulement.
 */
export type Refusal = {
  readonly code: string
  readonly when: number | string
  readonly message: string
  readonly body?: BodyCondition
  readonly marksAccount?: AccountMark
}

/** Un exemple d'arguments, valide contre le schéma de sa fonction (vérifié par la fabrique et par un test). */
export type Example = { readonly title: string; readonly input: { readonly [argument: string]: unknown } }

/** Nom côté API → nom de l'argument d'entrée. Chaque argument va à un seul endroit de la requête. */
export type ArgumentMap = { readonly [apiName: string]: string }

/**
 * Le corps en table : nom côté API → argument, ou → une table de même forme, emboîtée à toute profondeur (règle 22 de la
 * fabrique). L'hôte part de la constante du corps (`constants.body`) et pose chaque argument donné à son chemin, en
 * créant les objets parents qui manquent ; un argument absent ne part pas et ne crée rien. Un chemin n'est jamais à la
 * fois constant et argument : table et constante ne se rejoignent que par des objets.
 */
export type BodyMap = { readonly [apiName: string]: string | BodyMap }

/** En-tête → valeur constante ; jamais un secret. */
export type ConstantHeaders = { readonly [header: string]: string }

export type RequestSpec = {
  readonly method: HttpMethod
  /** Relatif à `baseUrl`, avec des `{param}` qui nomment des arguments, listés dans `pathParams`. */
  readonly path: string
  readonly pathParams: readonly string[]
  /** Les listes s'y encodent selon `Connector.queryArrays`. */
  readonly query: ArgumentMap
  /**
   * Corps JSON, quelle que soit la méthode : un `DELETE` peut en porter un ; une table (`BodyMap`), emboîtée ou non ;
   * avec `files`, les parties texte du corps multipart, une table plate. Ou le nom d'un argument, un objet strict du schéma (`type: object`, `additionalProperties: false`, des
   * propriétés déclarées), requis, qui forme le corps entier : l'hôte envoie l'objet validé tel quel, en JSON, sans
   * constante de corps, sans fichier, sans pagination, jamais en `GET` (règle 20 de la fabrique).
   */
  readonly body: BodyMap | string
  /**
   * `true` : le corps construit (table, emboîtée ou non, constantes comprises, ou argument entier) part seul élément
   * d'une liste JSON, pour un tiers dont le corps est un tableau nu d'un objet. Exige un corps ; jamais en `GET` ni avec
   * `files` (règle 23 de la fabrique).
   */
  readonly bodyList?: true
  readonly headers: ArgumentMap
  /**
   * Nom de partie → argument qui porte l'identifiant d'un fichier en transit de l'appelant (un texte). Présent, le corps
   * part en `multipart/form-data` : chaque entrée de `body` et constante de corps devient une partie texte, chaque fichier
   * une partie au nom et au type du fichier. L'hôte lit le fichier sous l'identité de l'appelant, le consomme après un
   * envoi réussi, jamais après un refus ni un envoi ambigu. Seulement en `POST`, `PUT` ou `PATCH`, sans pagination.
   */
  readonly files?: ArgumentMap
  /**
   * Valeurs envoyées à chaque appel, sous leur nom côté API, en plus des arguments ; elles ne sont pas dans le schéma.
   * Un nom n'y est jamais aussi un nom d'argument de la même place ; au corps, la constante et la table (`BodyMap`) ne se
   * rejoignent que par des objets des deux côtés, à toute profondeur.
   */
  readonly constants?: {
    readonly query?: { readonly [apiName: string]: string | number | boolean }
    readonly body?: { readonly [apiName: string]: JsonValue }
    readonly headers?: ConstantHeaders
  }
  /** Argument → encodage appliqué avant de le placer : `json` sérialise l'argument structuré en une chaîne (`JSON.stringify`). */
  readonly encode?: { readonly [argument: string]: "json" }
}

/**
 * Présente, elle a ajouté au schéma les arguments `all_pages` et `max_pages`, qui ne vont pas dans la requête : l'hôte
 * suit `next` (chemin pointé dans la réponse) vers `requestParam` (un argument, placé en query ou au corps, emboîté ou non :
 * chaque page se compose comme la première, le curseur à la place de l'argument) tant que `all_pages` est vrai,
 * `max_pages` pages au plus.
 * Il s'arrête quand `next` est absent ou nul, ou quand `more` est présent et que la réponse y porte `false`.
 */
export type Pagination = {
  readonly kind: "cursor" | "page"
  readonly requestParam: string
  readonly next: string
  /** Chemin pointé d'un booléen de la réponse : `false` dit que la page est la dernière, quoi que porte `next`. */
  readonly more?: string
  readonly total?: string
  readonly maxPages: number
}

/**
 * Un fichier rendu par la fonction, toute sa sortie : l'hôte le lit, le range en fichier en transit de l'appelant (24 h,
 * la même durée pour tout fichier) et ne rend à l'agent que son identifiant, jamais les octets ni l'adresse du tiers.
 *
 * - `from` : où sont les octets. `body`, le corps de la réponse ; `url`, une adresse que porte la réponse JSON au chemin
 *   pointé `path`, téléchargée aussitôt (`https`, hôte public, sans identifiants, aucune redirection suivie) ; `base64`
 *   ou `base64url`, les octets encodés au chemin `path` de la réponse JSON, décodés strictement. La réponse JSON qui
 *   porte l'adresse ou l'encodage ne sort jamais, ni au résultat ni au journal. `json`, la réponse JSON elle-même (4 Mo
 *   au plus), ou sa branche au chemin pointé facultatif `path`, lu comme les autres chemins (un indice de liste en
 *   nombre), écrite en JSON (`JSON.stringify` de ce que lit `JSON.parse`), de type `application/json`, sans `types`, ni
 *   `auth`, nommée par son seul gabarit, qui finit par `.json` ; une branche absente, nulle, ou chaîne, liste ou objet
 *   vides refuse (`file_missing`), jamais un fichier vide.
 * - `auth` (`from: "url"` seulement, toujours dit) : `none`, l'adresse reçoit une requête sans authentification ;
 *   `account`, celle du compte, seulement si l'adresse est à l'origine (schéma, hôte, port) de l'adresse de base
 *   résolue, sinon `file_foreign_origin` sans rien télécharger.
 * - `types` (absent avec `from: "json"`, toujours dit sinon) : extension → types MIME que le tiers peut annoncer, le premier étant celui du fichier rangé. Un type
 *   annoncé (`content-type` sans paramètres) qui n'est ni l'un de ceux de l'extension retenue, ni
 *   `application/octet-stream` ou `binary/octet-stream`, ni absent, refuse (`file_type_refused`).
 * - `name` : essayé dans cet ordre, `header` (`Content-Disposition` selon la RFC 6266, `filename*` avant `filename` ;
 *   celui du téléchargement pour `from: "url"`), `path` (un champ de la réponse JSON), puis `template` sur les arguments
 *   requis ; le premier nom dont l'extension est déclarée l'emporte, réduit à son dernier segment, sans caractère de
 *   contrôle, 255 caractères au plus.
 * - `maxBytes` : la borne, de 1 octet à 52 428 800 (50 Mo), ou à 4 194 304 (4 Mo) avec `from: "json"`, toujours dite. Un `content-length` au-delà refuse avant toute
 *   lecture, sinon la lecture s'arrête au premier octet de trop (`file_too_large`) ; un fichier vide refuse
 *   (`file_missing`). Le délai de la fonction vaut de bout en bout, lecture du corps et téléchargement compris.
 *
 * Les refus communs aux deux côtés : `file_missing`, `file_download_refused`, `file_too_large`, `file_type_refused`,
 * `file_unreadable`, `file_foreign_origin` (`@otomata_tech/connectors/file-cases`), et ceux de la déclaration (règle 17
 * de la fabrique) : `file_output_not_alone`, `file_auth_not_url`, `file_json_types`, `file_json_name`,
 * `file_json_max_bytes`.
 */
export type OutputFile = {
  readonly from: "body" | "url" | "base64" | "base64url" | "json"
  readonly path?: string
  readonly auth?: "none" | "account"
  readonly types?: { readonly [extension: string]: readonly string[] }
  readonly name: { readonly header?: string; readonly path?: string; readonly template: string }
  readonly maxBytes: number
}

export type Output = {
  /** Chemin pointé de la liste dans la réponse. */
  readonly items?: string
  /**
   * Clés retirées, chacune par son chemin (`call.recording`), avant `projection` : relatif à chaque élément de `items`
   * quand il est déclaré, à la réponse entière sinon ; une liste rencontrée se traverse, le reste du chemin valant pour
   * chacun de ses éléments ; une valeur absente, nulle ou scalaire n'a rien à retirer. Une clé qui contient un point ne
   * se désigne pas. Cas communs : `./strip-cases`.
   */
  readonly strip?: readonly string[]
  /** Clés de premier niveau gardées, après `strip`. */
  readonly projection?: readonly string[]
  /** Forme d'un enregistrement, après `items`, `strip` et `projection`. */
  readonly schema?: JsonSchema
  /** La sortie est un fichier (`OutputFile`), exclusif des quatre autres clés. */
  readonly file?: OutputFile
}

/**
 * Contrôle des arguments avant l'appel ; s'il échoue, rien n'est envoyé et l'hôte rend le refus nommé (`refusal`, un code
 * de `refusals`). `equal_sums` : sur les éléments de la liste `items`, la somme du premier champ égale celle du second ;
 * une chaîne décimale se somme exactement, sans virgule flottante.
 */
export type Check = {
  readonly kind: "equal_sums"
  readonly refusal: string
  readonly items: string
  readonly fields: readonly [string, string]
}

/**
 * Contrôle de la réponse ; s'il échoue, l'hôte rend le refus nommé au lieu de la réponse. `non_empty` : la valeur au
 * chemin pointé `path` existe et n'est ni nulle, ni une chaîne, une liste ou un objet vides.
 */
export type Expectation = { readonly kind: "non_empty"; readonly refusal: string; readonly path: string }

/**
 * Où lire, dans la réponse d'un appel, la quantité réelle qu'il a coûté : un nombre JSON fini, positif ou nul, au chemin
 * pointé `path`. `scale` (un entier de deux ou plus) : le facteur de la valeur lue à `unit`, la quantité réglée étant la
 * valeur fois `scale`, arrondie à six décimales puis à l'entier supérieur (un coût lu en dollars, réglé en microdollars).
 */
export type CostSettle = { readonly path: string; readonly scale?: number }

/**
 * Le poids d'une valeur, à la place de `times` : chaque élément de `items` compte la somme des poids des valeurs que
 * liste son champ `field` (une liste requise, d'au moins une valeur, sans doublon, dont les valeurs admises sont
 * exactement les clés de `values`). Un poids est un entier d'un ou plus, dans `unit`.
 */
export type CostWeights = { readonly field: string; readonly values: Readonly<Record<string, number>> }

/**
 * Ce que coûte un appel. Avant lui, son pire cas, calculé sur les arguments : `request` et `page`, `quantity` unités (une
 * sans elle) par requête envoyée (une, ou chaque page d'une lecture paginée avec `all_pages`, jusqu'à `max_pages`) ; `item`, la taille de la
 * liste `items` fois celle de la liste `times` (une si absente), deux arguments requis d'au moins un élément, ou la
 * somme, sur les éléments de `items`, des poids des valeurs que chacun liste (`weights`), jamais sur une fonction paginée. Après lui, `settle` : la quantité réelle, lue dans sa réponse ; absente ou illisible, on règle le
 * pire cas ; sans `settle`, `request` et `page` règlent les requêtes envoyées, fois `quantity`. Les deux comptent `unit`,
 * telle que l'éditeur la nomme (`credits`), exigée par `item`, `settle` et `quantity` ; absente, des requêtes (`request`)
 * ou des pages (`page`). `quantity`, un entier de deux ou plus, ne vaut qu'avec `request` et `page`. Cas communs :
 * `./cost-cases`.
 */
export type Cost =
  | { readonly per: "request" | "page"; readonly quantity?: undefined; readonly unit?: string; readonly settle?: CostSettle }
  | { readonly per: "request" | "page"; readonly quantity: number; readonly unit: string; readonly settle?: CostSettle }
  | { readonly per: "item"; readonly items: string; readonly times?: string; readonly unit: string; readonly settle?: CostSettle; readonly weights?: undefined }
  | { readonly per: "item"; readonly items: string; readonly weights: CostWeights; readonly unit: string; readonly settle?: CostSettle; readonly times?: undefined }

/**
 * Une fonction qui soumet un travail, relevé plus tard par `collect` (nom qualifié, une lecture du connecteur dont le seul
 * argument requis est `jobId.to`), appelée avec l'identifiant lu au chemin pointé `jobId.from` de la réponse de la
 * soumission. `withinMs` : le délai de relève après la soumission, au-delà duquel le travail est fini et sa quantité
 * lisible ; exigé quand le coût se règle (`settle`), dont la quantité se lit alors dans les réponses de la relève, sauf
 * `noDeadline` : la référence de l'éditeur dit qu'un travail n'a pas d'échéance, et la réservation court jusqu'à la
 * quantité lue ou jusqu'au plafond que fixe le moteur qui réserve. L'un exclut l'autre.
 */
export type AsyncJob = {
  readonly collect: string
  readonly jobId: { readonly from: string; readonly to: string }
  readonly withinMs?: number
  readonly noDeadline?: true
}

export type ConnectorFunction = {
  /** `<connecteur>.<fonction>`. */
  readonly name: string
  readonly connector: string
  readonly class: FunctionClass
  /**
   * La fonction engage ou détruit (envoi, corbeille, archive, déplacement) : l'hôte la sert sous `<connecteur>_act.<fonction>`,
   * les autres sous `<connecteur>.<fonction>` (lire et simuler, brouillon compris). Une seule description, un seul compte.
   * Valeur résolue : déclarée, sinon `false` pour `read` et `true` pour `write` et `sensitive` ; jamais `false` pour
   * `sensitive`, jamais `true` pour `read`.
   */
  readonly act: boolean
  /**
   * Présent si et seulement si le connecteur a des niveaux d'accès (`OAuthScopes`) : les droits que la fonction exige,
   * tous. `class` dit ce que fait la fonction, `scopes` ce qu'elle exige.
   */
  readonly scopes?: readonly string[]
  /** Anglais : ce que lit l'agent. */
  readonly description: string
  /** JSON Schema 2020-12 de l'entrée, celui de la description : racine `type: object`, `additionalProperties: false`. */
  readonly schema: JsonSchema
  readonly examples: readonly Example[]
  readonly refusals: readonly Refusal[]
  readonly checks?: readonly Check[]
  readonly expect?: readonly Expectation[]
  readonly request: RequestSpec
  /**
   * Délai propre à la fonction, en millisecondes : il remplace `timeoutMs` du connecteur pour chacun de ses appels
   * (chaque page d'une lecture paginée), plus court ou plus long, jamais égal. Absent : celui du connecteur.
   */
  readonly timeoutMs?: number
  /**
   * Fonction longue : l'hôte l'exécute hors de la requête de l'assistant, en tâche de fond, et rend aussitôt un
   * identifiant de travail, que relève sa fonction native `job.read` ; la réponse finale est celle qu'aurait rendue
   * l'appel synchrone (données, fichier rendu, refus). La phrase sur l'identifiant de travail est de l'hôte, jamais de
   * `description`. Toujours avec `timeoutMs`, le délai de l'appel lui-même, file non comprise ; jamais avec `async`, sur
   * la relève d'un travail asynchrone ni avec `pagination` (règle 21 de la fabrique). `confirm` se juge avant la mise en
   * file ; le coût se règle à la fin du travail, `cost.settle` lu sur la réponse finale. Absent : un appel synchrone.
   */
  readonly background?: true
  /** Présent pour une fonction qui soumet un travail relevé plus tard (`AsyncJob`). */
  readonly async?: AsyncJob
  readonly pagination?: Pagination
  readonly output?: Output
  readonly cost?: Cost
  /** Gabarit du résumé montré avant d'exécuter une fonction sensible, avec des `{param}`. */
  readonly confirm?: { readonly summary: string }
}

/** Où lire le solde d'un compte payant dans la réponse de la sonde, et le seuil sous lequel elle n'est pas saine. */
export type ProbeBalance = { readonly path: string; readonly min: number; readonly unit: string }

export type Connector = {
  readonly name: string
  readonly label: string
  readonly namespace: string
  /** Version du fichier de description, semver. */
  readonly version: string
  /** Version de l'API du tiers, telle que l'éditeur la nomme. */
  readonly apiVersion: string
  /**
   * Racine des chemins, gabarit possible sur les réglages (`https://{domain}.example.net`, `{server}`) et sur l'identité
   * du compte, après l'hôte (`https://api.example.com/v2/{account_id}`). Absente quand `baseUrls` la remplace, ou quand
   * toutes les fonctions sont écrites à la main.
   */
  readonly baseUrl?: string
  /** Racine des chemins par valeur d'un réglage `choice` (une région) ; chacune peut citer l'identité du compte. */
  readonly baseUrls?: UrlsBySetting
  readonly auth: Auth
  /** Vide pour `oauth2_user` et `none`. */
  readonly credential: readonly CredentialField[]
  /** Présente pour un compte rattaché chez un tiers qui héberge les comptes de ses utilisateurs. */
  readonly accountIdentity?: AccountIdentity
  readonly settings?: readonly Setting[]
  readonly modes: readonly ("platform" | "byo_user" | "byo_org")[]
  readonly timeoutMs: number
  readonly queryArrays?: "repeat" | "brackets" | "comma"
  /** En-têtes constants de toute requête du connecteur ; jamais l'en-tête d'authentification. */
  readonly headers?: ConstantHeaders
  /** Débit maximal que le tiers accepte, par credential : `requests` requêtes par fenêtre de `intervalMs`. */
  readonly rateLimit?: { readonly requests: number; readonly intervalMs: number }
  readonly quota?: { readonly [key: string]: unknown }
  readonly errors: readonly ApiError[]
  /**
   * La sonde d'une connexion : `function` (nom qualifié, une lecture sans argument requis) appelée avec `{}`, jamais
   * décomptée d'un quota ; chaque chemin de `nonEmpty` doit être présent et non vide dans sa réponse. `balance`, le
   * solde d'un compte payant : un nombre au chemin `path` de sa réponse, sain à `min` ou au-dessus, en `unit` ; sous
   * `min`, absent ou qui n'est pas un nombre, la sonde n'est pas saine. Sa fonction ne déclare alors aucun coût.
   */
  readonly probe?: { readonly function: string; readonly nonEmpty: readonly string[]; readonly balance?: ProbeBalance }
  readonly exposure?: { readonly mode?: "per_action" | "per_connector" | "via_call"; readonly tool?: string }
  /** Les fonctions générées ; une fonction que la fabrique ne sait pas traduire n'y est pas. */
  readonly functions: readonly ConnectorFunction[]
}

export function defineFunction<F extends ConnectorFunction>(definition: F): F {
  return definition
}

export function defineConnector<C extends Connector>(definition: C): C {
  return definition
}
