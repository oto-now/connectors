# Coût d'un appel et sonde à solde

- **Statut** : validé par le mainteneur le 29/09/2026 (issu du [format de description](format-de-description.md))
- **Dernière révision** : 2026-11-11

## Résumé

`cost`, sur une fonction, dit ce que coûte un appel en deux temps : avant lui, son pire cas, calculé sur ses seuls arguments ; après lui, la quantité réelle, relue dans sa réponse. Un service de facturation réserve le pire cas avant l'appel et règle la quantité réelle après. `probe.balance`, sur le connecteur, fait lire à la sonde d'un compte payant son solde, et la dit non saine sous un seuil. La description ne dit ni prix ni solde attendu : des quantités, dans l'unité de l'éditeur.

## Contexte

- Une sonde qui ne teste que l'autorisation répond « sain » sur un compte à zéro : la clé vaut, l'appel réussit.
- Un éditeur facture à la requête, à la page, à l'élément d'un lot, ou à la valeur trouvée à un prix par champ ; certains rendent la quantité consommée, d'autres seulement le solde, d'autres rien.

## Objectifs et non-objectifs

- Un pire cas toujours calculable, jamais sous le réel, jamais deviné.
- Une quantité réelle lue quand l'éditeur la rend, réglée au pire cas sinon.
- Hors objectif : le prix, la réservation et le règlement, qui relèvent du service de facturation de l'hôte.

## Conception

### La sonde

`probe` (sur `connector`) : `{ function: get_company, non_empty: [scopes] }`, une fonction de lecture appelée par `call`, sans argument requis, jamais décomptée d'un quota ; chaque chemin de `non_empty` doit être non vide dans sa réponse. Un connecteur sans telle lecture n'a pas de sonde. Une sonde se sert à chaque niveau d'accès ([niveaux d'accès](niveaux-d-acces.md)).

### Sonde à solde

La sonde d'un compte payant lit aussi son solde quand l'API du service le permet. Lire un solde n'est pas le vérifier : `balance` dit où le lire et sous quel seuil la sonde n'est pas saine.

```yaml
probe: { function: get_account, balance: { path: credits.remaining, min: 1, unit: credits } }
```

- `path` : chemin pointé du solde dans la réponse de la fonction de sonde, un indice de liste en chiffres (`accounts.0.balance`) ; chaque segment est non vide, sans espace.
- `min` : le seuil, un nombre positif ou nul. Un solde égal ou supérieur est sain ; sous le seuil, absent ou qui n'est pas un nombre JSON (un texte `"12"` compris), la sonde n'est pas saine : un solde illisible ne passe jamais pour sain.
- `unit` : ce que compte le solde, tel que l'éditeur le nomme (`credits`, `requests`, `EUR`), rendu avec la valeur et le seuil.

On ne paie pas pour savoir si l'on peut payer : la fonction d'une sonde à solde ne déclare aucun coût (règle 5 de la [fabrique](fabrique.md)), et le commentaire du fichier cite la source qui établit que l'appel de solde est gratuit et le chemin où le lire. Un solde qui se calcule (crédits de l'offre moins ceux consommés, plus un crédit prépayé) ne se dit pas : `path` lit une seule valeur ; un solde lu dans des en-têtes de réponse non plus. Sans source qui établisse le chemin et la gratuité, le fichier ne déclare pas de solde. Quand la référence dit plusieurs compteurs, on lit celui qui baisse à l'usage (`remaining`, pas un `available` qui compte aussi des packs).

### Le coût

```yaml
# Un lot facturé au champ demandé pour chaque contact, réglé au champ trouvé.
cost:
  per: item
  items: contacts                  # la liste dont chaque élément compte
  times: fields                    # la liste dont la taille multiplie (facultative)
  unit: fields                     # ce que comptent le pire cas et la quantité réglée
  settle: { path: fields_found }   # la quantité réelle, dans la réponse
```

- `per` : `request` ou `page`, `quantity` unités par requête envoyée, une sans elle ; `item`, une par élément de la liste `items`, fois la taille de la liste `times`.
- `quantity` (`request` et `page` seulement) : ce que coûte une requête, dans `unit`, un entier de deux ou plus ; absente, une. Une requête que l'éditeur facture plusieurs unités (une page Maps, 3 crédits) déclare sa quantité, pour que le pire cas ne soit jamais sous le réel. Le schéma refuse `quantity: 1`, qui ne dirait rien, une quantité sur `per: item`, et une quantité sans `unit`.
- `items` et `times` (`per: item` seulement, `items` exigé) : deux arguments distincts de l'entrée, des listes (`type: array`) requises, d'au moins un élément (`minItems` d'un ou plus).
- `weights` (`per: item` seulement, à la place de `times`) : le poids d'une valeur. `field` nomme un champ de chaque élément de `items`, une liste de valeurs ; `values` donne à chaque valeur admise son poids, un entier d'un ou plus, dans `unit`. Chaque élément compte la somme des poids des valeurs qu'il liste. Le champ est, dans chaque élément, une liste requise d'au moins une valeur, sans doublon (`uniqueItems: true`), dont les valeurs admises (`items.enum`) sont exactement les clés de `values` (règle 16) : une valeur sans poids est refusée par le schéma d'entrée, jamais comptée pour rien ; un poids sans valeur admise est refusé de même. Le calcul refuse aussi, sans repli, une valeur sans poids qui lui parviendrait d'un appel non validé. Le barème est celui que publie l'éditeur, cité avec sa date dans le fichier.
- `unit` : ce que comptent le pire cas et la quantité réglée, tel que l'éditeur le nomme (`credits`, `contacts`, `fields`). Exigée par `per: item`, par `settle` et par `quantity` ; absente, des requêtes (`request`) ou des pages (`page`).
- `settle.path` : chemin pointé de la quantité réelle, de la même forme que `balance.path`.
- `settle.scale` (facultatif) : le facteur de la valeur lue à `unit`, un entier de deux ou plus, quand l'éditeur dit ce qu'il a facturé en un montant décimal (DataForSEO : `"cost": 0.0006`, des dollars) et que le coût compte une sous-unité entière (des microdollars). La quantité réglée est la valeur lue fois `scale`, arrondie à six décimales (le bruit d'un double : `0.0006 × 1e6` se lit `600`, pas `600.0000000000001`), puis à l'entier supérieur : `0.0012345678` dollar se règle `1235` microdollars. Le pire cas reste un entier de la même sous-unité (`quantity`).

```yaml
# Une requête facturée en dollars, au plus 0,04 $ ; la réponse dit les dollars facturés.
cost: { per: request, quantity: 40000, unit: microdollars, settle: { path: cost, scale: 1000000 } }
```

**Un coût en monnaie.** Le format ne dit jamais un prix ; un éditeur qui facture un solde en monnaie (DataForSEO, au dollar) a pour unité cette monnaie, comptée en sous-unité entière : `unit: microdollars`, le pire cas en microdollars (`quantity`), la quantité réelle lue en dollars et convertie (`settle.scale: 1000000`). Le pire cas est la borne que donne le barème publié pour l'appel le plus cher que le schéma admet (la limite d'éléments au maximum), citée avec sa date dans l'en-tête du fichier ; ce n'est pas un prix que l'hôte refacturerait, seulement ce qu'il réserve, réglé ensuite au montant que dit la réponse. Oto 1 réglait déjà un appel DataForSEO en microdollars, arrondis au-dessus.

```yaml
# Une page Maps facturée 3 crédits, réglée aux crédits que dit la réponse.
cost: { per: request, quantity: 3, unit: credits, settle: { path: credits } }
# Un lot facturé en crédits à la valeur trouvée, à un prix par champ, réglé aux crédits consommés.
cost:
  per: item
  items: contacts
  weights:
    field: enrich_fields           # dans chaque contact, les champs demandés
    values: { contact.work_emails: 1, contact.personal_emails: 3, contact.phones: 10 }
  unit: credits
  settle: { path: cost.credits }   # lu dans les réponses de la relève
```

**Ce que le fichier chiffre.** Les seuls coûts écrits sont ceux que la référence de l'éditeur chiffre. Quand un barème n'a ni une seule unité ni des poids entiers (un demi-crédit par personne trouvée, un crédit par valeur révélée selon des drapeaux de la requête), le pire cas compte les éléments du lot (des leads, des sociétés), pas des crédits : le prix reste au service qui facture. Un argument qui borne le coût (`limit`) se borne dans le schéma pour que le pire cas tienne en une requête.

### La sémantique, que suit le service de facturation

- **Pire cas**, avant l'appel. `request` et `page` comptent les requêtes que l'appel peut envoyer, fois `quantity` : une requête, ou, pour une lecture paginée appelée avec `all_pages`, son `max_pages` borné par celui de la description (celui-ci quand l'appel n'en donne pas). `item` : la taille de `items` fois celle de `times`, une si `times` n'est pas déclaré ; avec `weights`, la somme, sur les éléments de `items`, des poids des valeurs que chacun liste.
- **Liste absente ou vide** : impossible. La fabrique exige `items` et `times` requis et d'au moins un élément, et le champ pesé requis dans chaque élément (règles 12 et 16), et l'hôte valide l'entrée avant l'appel : le pire cas est toujours un entier d'au moins 1. Une liste vide laisserait l'éditeur appliquer son défaut (tous les champs), qu'aucune description ne sait chiffrer.
- **Pagination** : elle multiplie le coût d'un `request` comme d'un `page`, chaque page étant une requête. Une fonction paginée ne déclare ni `per: item` ni `settle` (règle 12) : un lot ne se pagine pas, et une quantité lue dans une seule réponse ne dirait pas celle de l'appel.
- **Quantité réelle**, après un appel réussi. Avec `settle`, le nombre JSON fini, positif ou nul, lu au chemin (fois `scale`, arrondi comme dit plus haut, quand il est déclaré) dans la réponse JSON telle que l'éditeur l'envoie, avant `output` ; pour une fonction asynchrone, dans les réponses de sa relève ([travaux longs](travaux-longs.md)). Sans `settle` : pour `request` et `page`, le nombre de requêtes effectivement envoyées, fois `quantity` ; pour `item`, le pire cas.
- **Quantité absente ou illisible** (chemin absent, valeur qui n'est pas un nombre JSON fini, un texte `"12"` compris, ou négative) : on règle le pire cas, et l'écart se signale. Refuser de régler laisserait ouverte une réservation que le service réglerait de toute façon, alors que l'éditeur a pu facturer. Une quantité illisible ne passe jamais pour nulle.
- **Nombre fini** : lu comme le lit un hôte JavaScript, un double ; un entier JSON au-delà de sa plage (lu `Infinity`) n'est pas fini.
- **Quantité au-dessus du pire cas** : réglée telle quelle, l'éditeur l'a facturée ; la description sous-estime (ou une offre sur mesure coûte plus), et l'écart se signale.
- Un appel refusé (une erreur de la table, un contrôle) n'a pas de quantité réelle : sa réservation est libérée sur un refus net de l'éditeur, jamais sur un envoi ambigu (délai dépassé, 5xx).
- Pour un appel synchrone, l'échéance d'une réservation est le délai de la fonction (`timeout_s`, par requête), plus une marge que fixe le service.

### Contrôles

Règles 5, 12 et 16 de la [fabrique](fabrique.md) ; cas communs `connectors/_cases/cost.yaml`. Le test du schéma fait refuser, pour la sonde à solde, onze variantes (chemin vide, à point en tête ou en fin, à segment vide, à espace, seuil qui n'est pas un nombre ou négatif, unité vide, clé inconnue, sans seuil, sans unité) ; pour le coût, quatorze (`per` inconnu, `item` sans `items` ou sans unité, `items` ou `times` sur `request`, `times` sans `items`, `settle` sans unité ou sans chemin, chemin à point en tête ou à espace, `settle` à clé inconnue, unité vide, `items` qui n'est pas un identifiant, clé inconnue) ; pour le poids d'une valeur, treize (`weights` sur `request`, avec `times`, sans unité, sans table, sans champ, table vide, poids nul, négatif, non entier, en texte, valeur vide, champ qui n'est pas un identifiant, clé inconnue) ; pour la quantité d'une requête, sept (`quantity` sur `item`, sans unité, d'un, nulle, négative, non entière, en texte) ; pour l'échelle de la quantité relue, quatre (`scale` d'un, nulle, non entière, en texte).

## Décisions et alternatives écartées

- **Une sonde qui ne teste que l'autorisation pour un compte payant** : écartée le 2026-10-19, elle répond « sain » à zéro.
- **Refuser de régler une quantité illisible** : écarté le 2026-10-19, la réservation serait réglée de toute façon à son échéance.
- **Un prix dans la description** : écarté. Des quantités dans l'unité de l'éditeur ; le prix est au service qui facture.
- **Une `quantity` décimale, dans l'unité de l'éditeur (des dollars)** : écartée le 2026-11-11. Les deux hôtes comptent des entiers (oto 1 règle une quantité arrondie à l'entier supérieur : `0,0006` dollar s'y réglerait `1`), et un pire cas décimal porterait le bruit des doubles dans chaque multiplication. Une sous-unité entière et un facteur à la lecture gardent des entiers partout.

## Sécurité et confidentialité

- La sonde et sa lecture de solde ne coûtent rien et ne lisent que le compte ; aucune donnée d'une personne n'y passe.
- Un solde rendu dit la valeur, le seuil et l'unité, jamais le secret du compte.

## Écart avec le code

- Oto-core lit le solde au chemin de `balance` et juge le compte contre son seuil dans `probeAccount` (relu à son commit d26f513, `probe.ts`), que le paquet exporte et qu'aucun de ses codes n'appelle : rien ne lance encore la sonde.
- Oto-core contrôle le coût comme la fabrique et en tire, par des fonctions pures (`costOf`, `settledQuantity`), le pire cas et la quantité à régler (relu à son commit d26f513, `cost-declaration.ts`, `cost.ts`), fonctions que son moteur n'appelle pas : aucun service ne réserve ni ne règle encore. Un paquet antérieur ignore `cost` et refuse `per: "item"`, `weights`, `noDeadline` ou `quantity` à la déclaration : une description qui s'en sert n'est épinglée qu'avec un paquet qui les accepte, les deux épingles montant ensemble.
- Côté lib, la sortie Python de données (`oto/connectors/declared.py`, générée) porte le `cost` de chaque fonction, et `oto.connectors.cost` en tire le pire cas et la quantité à régler, avec la sémantique d'oto-core ([fabrique](fabrique.md)), `settle.scale` compris (`scaled`).
- Oto-core lit `settle.scale` dans `settledQuantity` et l'accepte à la déclaration à partir du commit qui l'écrit ; un paquet antérieur refuse `settle` avec une clé de plus : une description qui s'en sert n'est épinglée qu'avec un paquet qui l'accepte.

## Questions ouvertes

- « Nombre JSON fini » : un entier JSON qui dépasse la plage d'un double est fini en JSON, mais un hôte JavaScript le lit `Infinity`. Lu à ce jour comme un double (illisible : on règle le pire cas), la lecture prudente, à confirmer.
- Un solde qui se calcule à partir de plusieurs valeurs de la réponse (crédits de l'offre moins ceux consommés, plus le prépayé), que `balance.path` ne sait pas dire ; une quantité réelle qui se calcule, de même.
- Une quantité qui se calcule depuis un argument (les segments d'un SMS, selon sa longueur et son alphabet : `allo.send_sms`), que `quantity`, fixe, ne sait pas dire.
- `pappers` à revoir avec `weights` : ses champs supplémentaires, à des prix différents, sont une chaîne séparée par des virgules, pas une liste, et sa fiche n'a ni prix de base ni quantité relue.
- Coûts écartés au relevé du 2026-10-19 : `mailpool`, qui facture des domaines et des boîtes à un prix lu par une autre fonction.
- Lectures de solde écartées au relevé du 2026-10-19, faute de source qui établisse un appel gratuit et un chemin unique : `pappers` (crédits de l'offre, consommés et prépayé, sans solde en une valeur), `affinity` (le quota mensuel se lit à une route v1 exemptée de ce quota que la description ne porte pas ; la valeur d'une offre sans limite n'est pas documentée), `sellsy` (quota restant dans des en-têtes), `claap` (quota d'enregistrement sans lecture), `mailpool` (aucune lecture de solde).

## Historique

- 2026-10-06 : la sonde (`probe`) — décidé par le mainteneur.
- 2026-10-19 : sonde à solde : `probe.balance` (`path`, `min`, `unit`), non saine sous le seuil, absent ou illisible ; sa fonction ne déclare pas de coût (règle 5) — décidé par le mainteneur.
- 2026-10-19 : coût d'un appel : `per: request` et `per: page` (une unité par requête envoyée, chaque page comprise), `per: item` (`items` fois `times`, jamais sur une fonction paginée), `unit` et `settle` (la quantité réelle relue, réglée au pire cas si elle est absente ou illisible) ; règle 12 de la fabrique, sortie TypeScript — décidé par le mainteneur.
- 2026-10-19 : coût pondéré `cost.weights` (`field`, `values`), à la place de `times`, les valeurs admises exactement les clés de la table (règle 16) ; sortie Python de données et ses fonctions pures (`oto.connectors.cost`) — décidé par le mainteneur.
- 2026-10-21 : `cost.quantity`, ce que coûte une requête (`request`, `page`), un entier de deux ou plus avec `unit` ; cas de la quantité — décidé par le superviseur.
- 2026-11-03 : sorti de la note « Format de description » lors de son redécoupage en documents par sujet ; les résumés par connecteur sortent pour la carte du parc et l'en-tête de chaque description — décidé par le responsable produit (source : demande de rationalisation des connecteurs).
- 2026-11-11 : `settle.scale`, le facteur de la valeur lue à l'unité (un entier de deux ou plus), la quantité réglée arrondie à six décimales puis à l'entier supérieur ; un coût en monnaie compté en sous-unité entière (`microdollars`) ; cas de l'échelle dans `connectors/_cases/cost.yaml` — décidé par le responsable produit (source : demande de description de DataForSEO, « tout décrire »).
