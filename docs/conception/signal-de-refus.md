# Erreurs et signal de refus du service

- **Statut** : validé par le mainteneur le 29/09/2026 (issu du [format de description](format-de-description.md))
- **Dernière révision** : 2026-11-06

## Résumé

Une description dit les refus d'un appel dans une table structurée : `errors`, commune au connecteur, et `refusals`, propres à une fonction. Chaque entrée nomme un refus (`code`), le dit en anglais (`message`) et dit s'il se rejoue. Une entrée peut aussi dire que le service refuse la clé du compte ou que ses crédits sont épuisés (`marks_account`), au besoin sous une condition sur le corps de la réponse (`body`), pour que l'hôte marque le compte et lève la marque au premier appel réussi.

## Contexte

- Un tiers dit un refus par son statut HTTP, mais pas toujours : une erreur GraphQL sous un 200, un code d'erreur sous un 400, un message en phrase sous un 422.
- Un compte dont la clé est révoquée ou les crédits épuisés échoue à chaque appel ; l'hôte doit le savoir pour le dire à l'organisation, sans bloquer à tort un compte sain.

## Objectifs et non-objectifs

- Des refus nommés et typés dans les deux langages, jamais un texte libre.
- Ne marquer un compte que sur un signal certain.
- Hors objectif : le rejeu. L'hôte ne rejoue jamais seul une requête ; `retryable` dit à l'appelant ce qu'il peut refaire.

## Conception

### La table

- `errors` (exigée, sur `connector`) : `status` (un ou plusieurs codes HTTP), `code` (refus nommé), `message` (anglais), `retryable`, et facultatifs `body` et `marks_account` ; la première entrée qui s'applique l'emporte : les entrées les plus précises d'abord, une entrée générale en dernier pour un statut.
- `refusals` (sur une fonction) : `code`, `when` (statut HTTP ou condition en texte), `message` : surcharge ou complète la table commune ; sous un statut seulement, `body` et `marks_account` comme dans la table. L'hôte essaie les refus à statut de la fonction, puis la table du connecteur.
- Un message de refus dit le fait, au plus une condition, jamais le nom d'un outil : la lib ne connaît pas les outils de l'appelant. Il est fixe : un détail que rend le tiers (le droit manquant que nomme Slack sous `missing_scope`) n'est pas relayé.
- Un 5xx ne se déclare pas rejouable sur une écriture : elle a pu passer.

### Le signal de refus

Une entrée de `errors`, ou un refus d'une fonction dont `when` est un statut, porte :
- `marks_account` : `credential_rejected` (le service refuse la clé ou le jeton du compte : clé invalide ou révoquée, session déconnectée, jeton révoqué) ou `quota_exhausted` (crédits ou quota de l'offre épuisés) ;
- `body`, facultatif : une condition sur le corps JSON de la réponse, sur la valeur au chemin pointé `path` (un indice de liste en chiffres : `errors.0.extensions.code`), par un seul opérateur, `equals` ou `contains`, jamais les deux (le schéma refuse). `equals` : la valeur est égale à l'une des valeurs listées (texte, entier ou booléen), de même type et exacte (`190` n'est pas `"190"`, un texte garde sa casse). `contains` : la valeur est un texte qui contient l'un des textes listés, où qu'il soit, dans un mot compris, la casse à part : les deux côtés sont mis en minuscules par la casse Unicode par défaut, sans locale (`toLowerCase()` en JavaScript, `str.lower()` en Python, pas `casefold`, qui replierait aussi `ß` en `ss`). Une valeur absente, nulle, un nombre, un booléen, une liste ou un objet ne contient rien, ni un corps qui n'est pas du JSON ; un texte vide ne se déclare pas (le schéma exige des textes non vides, distincts, au moins un). L'entrée ne vaut que si la condition tient ; sinon l'hôte essaie l'entrée suivante du même statut.

Seul ce qui est déclaré marque : aucune entrée ne marque par défaut, un 401 compris. Une condition sur le corps dit un refus que le statut ne distingue pas.

```yaml
errors:
  - { status: 400, body: { path: error.code, equals: [190, 102] }, code: authorization_expired, retryable: false,
      marks_account: credential_rejected, message: "Meta no longer accepts this authorization (Graph error code 190 or 102): reconnect the account." }
  - { status: 400, code: invalid_request, retryable: false, message: "Meta rejected the request: check the ids, fields and parameters." }
  - { status: 401, code: authorization_expired, retryable: false, marks_account: credential_rejected, message: "…" }
  # une erreur GraphQL sous un 200 :
  - { status: 200, body: { path: errors.0.extensions.code, equals: [UNAUTHENTICATED] }, code: key_refused, retryable: false,
      marks_account: credential_rejected, message: "…" }
  # un message qui n'est pas fixe (« You have insufficient credits », « Insufficient credits on this plan ») :
  - { status: 422, body: { path: error, contains: [insufficient credits] }, code: credits_exhausted,
      retryable: false, marks_account: quota_exhausted, message: "…" }
  - { status: 422, code: invalid_request, retryable: false, message: "…" }
```

`contains` dit un refus que le service écrit en phrase, dont `equals` ne saurait lister toutes les formes. Pour plusieurs chemins possibles du même message, une entrée par chemin.

**Quand marquer.** Une marque n'est jamais plus large que le signal : marquer à tort bloque un compte sain. Une description qui suit un client existant ne marque pas plus que ce que lit le client. Ne marquent pas, faute de certitude : une même entrée qui couvre 401 et 403 ; un 402 qui dit le quota de l'offre pour une ressource et non un crédit du compte ; un 403 qui dit crédits épuisés ou droit manquant sans que le statut les distingue ; un 401 dont la référence dit aussi un droit manquant de l'utilisateur ; un 401 qui dit un en-tête absent ou mal formé, faute de l'appel et non de la clé (on marque alors sous le code qui dit la clé inconnue) ; le 401 d'un `oauth2_client_credentials` dont l'hôte renouvelle le jeton ; les codes de débit. Les connecteurs `oauth2_user` ne déclarent rien : leur 401 relève du renouvellement du consentement par l'hôte. Une clé de l'hôte (`holder: host`) marque comme une autre : la marque vaut alors pour un compte sans champ à lui, et l'hôte dit de demander à qui fait tourner l'application (oto-pkg : docs/conception/connecteurs-et-comptes.md, « Le refus nommé »).

### Contrôles

La fabrique refuse (règle 10, [fabrique](fabrique.md)) : `body` ou `marks_account` sur un refus dont `when` est une condition en texte, que l'hôte ne sait pas évaluer ; un statut 2xx sans condition sur le corps, qui ferait de chaque succès un refus ; `credential_rejected` sur un 403 sans condition (un droit manquant n'est pas une clé refusée) et `quota_exhausted` sur un 429 sans condition (un débit dépassé n'est pas un crédit épuisé) ; une entrée qui marque et se dit rejouable ; une entrée à condition placée, dans sa table, après une entrée sans condition qui partage un de ses statuts, et qui ne serait donc jamais lue. Le test du schéma fait refuser douze variantes : marque inconnue, condition sans opérateur, `equals` à liste vide, à valeur qui n'est ni texte ni entier ni booléen, condition à clé inconnue, à chemin vide, `equals` et `contains` ensemble, `contains` à liste vide, à texte vide, à valeur qui n'est pas un texte, qui n'est pas une liste, à texte en double. Les cas communs (`connectors/_cases/errors.yaml`) disent, réponse par réponse, l'entrée retenue.

## Décisions et alternatives écartées

- **Refus en texte libre** : écarté le 29/09. Une table structurée (`code`, `when`, `message`) que la fabrique traduit en erreurs typées dans les deux langages.
- **Marquer tout 401 par défaut** : écarté. Seul ce qui est déclaré marque ; une marque à tort bloque un compte sain.
- **Un seul opérateur, `equals`** : complété le 2026-10-21 par `contains`, un refus écrit en phrase n'ayant pas de valeur fixe.

## Sécurité et confidentialité

- Un message de refus ne recopie pas le corps du tiers : il est fixe, en anglais, et ne nomme aucun outil.
- La marque d'un compte ne dit rien du secret : elle dit seulement que le service le refuse.

## Écart avec le code

- Oto-core exécute la table et le signal (relu à son commit d26f513 : `error-table.ts`, `signal-declaration.ts`, `engine.ts`) : les refus à statut de la fonction, puis la table du connecteur, la première ligne dont un statut couvre la réponse et dont la condition tient ; une ligne qui marque rend le code de sa marque et pose la marque du compte, levée au premier appel réussi. Un paquet antérieur au commit qui écrit l'état d'un compte lit une entrée à condition comme une entrée sans condition (il rendrait tout 400 de `meta_ads` comme `authorization_expired`) ; un paquet antérieur à d26f513 refuse `contains` à la déclaration.
- La lib lit la table pour un hôte qui sert la description (`oto/connectors/serve.py`, `refusal` : les refus à statut de la fonction, puis la table du connecteur, conditions `equals` et `contains` comprises), jouée contre les cas communs (`tests/test_error_cases.py`). Les clients écrits à la main nomment toujours leurs refus à leur façon : c'est l'hôte qui traduit leur erreur HTTP en refus nommé.

## Questions ouvertes

- Google : un 403 est un droit manquant ou une limite de débit, rejouable dans le second cas seulement ; le format le dit par `body`, mais aucune description Google ne s'en sert.
- Une réponse d'erreur qui veut dire « pas encore » (un 404 sur la relève de la révélation de téléphone d'`apollo`).
- Une même route dont un 401 dit un refus d'adresse IP, qui ne doit pas marquer la clé (`brevo`).
- Le code et le champ `needed` d'un refus (`slack`), qu'un message fixe ne relaie pas.

## Historique

- 2026-09-29 : refus en table structurée — décidé par le mainteneur (source : séance du 29/09, « Le fichier de description »).
- 2026-09-30 : l'erreur 400 `invalid_request` — choix du projet (source : lot 2 du 30/09).
- 2026-10-16 : signal de refus et d'épuisement du service décrit — décidé par le mainteneur (source : revue des connecteurs d'oto 2 du 16/10).
- 2026-10-17 : signal écrit : `marks_account` (`credential_rejected`, `quota_exhausted`) et condition `body` sur une entrée de `errors` ou un refus à statut, gardés par la règle 10 de la fabrique et portés par la sortie TypeScript — décidé par le mainteneur.
- 2026-10-21 : une condition sur le corps a deux opérateurs exclusifs, `equals` et `contains` (minuscules Unicode par défaut, sans locale ; une valeur qui n'est pas un texte ne contient rien) ; cas communs de la table d'erreurs — décidé par le superviseur.
- 2026-10-23 : un 401 que la référence dit aussi un droit manquant ne marque pas — décidé par le mainteneur.
- 2026-11-03 : sorti de la note « Format de description » lors de son redécoupage en documents par sujet ; les résumés par connecteur sortent pour la carte du parc et l'en-tête de chaque description — décidé par le responsable produit (source : demande de rationalisation des connecteurs).
