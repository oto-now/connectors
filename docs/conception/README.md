# Conception

Les documents de conception de ce dépôt : pour chaque sujet, **comment** c'est construit et **pourquoi**, avec les alternatives écartées. Ils couvrent les connecteurs partagés d'oto 2 : leur fichier de description et la fabrique qui en tire Python et TypeScript. Le détail d'un connecteur (sources, choix, écarts avec son client) vit dans l'en-tête de son `connector.yaml`, et la vue d'ensemble du parc dans `docs/carte-du-parc.md`. Ce qui se passe chez l'hôte qui exécute un connecteur (catalogue, comptes, secret fourni à l'appel, coffre) vit dans oto-pkg : docs/conception/connecteurs-et-comptes.md.

**Pour un agent qui code dans ce dépôt** : lire le document du sujet avant de toucher `connectors/` ou la fabrique. Une demande qui contredit un document se signale ; elle ne s'implémente pas en silence.

## Règle de vie

- Un document par sujet, qui répond à une question avec laquelle le lecteur arrive ; 200 lignes au plus. Une décision proche d'un sujet existant enrichit son document ; un sujet nouveau s'ajoute à la table ci-dessous dans le même commit.
- En tête : le statut (`proposé`, ou `validé avec X le JJ/MM/AAAA`) et la date de dernière révision.
- Neuf sections, dans cet ordre et jamais vides : Résumé ; Contexte ; Objectifs et non-objectifs ; Conception ; Décisions et alternatives écartées ; Sécurité et confidentialité ; Écart avec le code ; Questions ouvertes ; Historique.
- Le corps décrit l'état présent et se corrige en place ; une option abandonnée passe dans « Décisions et alternatives écartées », avec sa raison. « Écart avec le code » se tient à jour avec le code, dans le même commit.
- Historique : une ligne par révision de fond, `AAAA-MM-JJ : ce qui change — décidé par <rôle> (source : …)`.
- Dépôt public : aucun nom de client, de partenaire ni de personne ; « décidé par » nomme un rôle. Aucun numéro d'issue, de PR ni d'URL GitHub.

« Proposé » : guide le code mais peut encore changer. « Validé » : décidé à la date dite, et réalisé au moins en partie.

## Les documents

| Document | La question à laquelle il répond | Statut |
|---|---|---|
| [Concevoir un connecteur](concevoir-un-connecteur.md) | Dans quel ordre conçoit-on un connecteur partagé, de la qualification du service à sa publication ? Le point d'entrée pour ajouter un connecteur. | proposé |
| [Format de description](format-de-description.md) | Comment décrit-on un connecteur partagé, et que doit contenir son fichier ? Document d'entrée du format, qui renvoie au document de chaque bloc. | validé avec Alexis le 29/09/2026 ; révisé le 03/11/2026 |
| [Le compte d'un connecteur](compte.md) | Comment une description dit-elle les champs, réglages et l'authentification d'un compte, consentement OAuth, compte de service et compte rattaché compris ? | validé avec Alexis le 29/09/2026 ; révisé le 06/11/2026 |
| [Le corps d'une requête](corps-de-requete.md) | Comment les arguments forment-ils le corps JSON : en table, emboîté ou entier ? | validé avec Alexis le 29/09/2026 ; révisé le 03/11/2026 |
| [La sortie d'une fonction](sortie.md) | Que reçoit l'agent de la réponse, et que perd-elle avant de l'atteindre ? | validé avec Alexis le 29/09/2026 ; révisé le 03/11/2026 |
| [Fichiers envoyés et rendus](fichiers.md) | Comment une fonction envoie-t-elle ou rend-elle un fichier sans que l'agent en voie les octets ni l'adresse ? | validé avec Alexis le 29/09/2026 ; révisé le 03/11/2026 |
| [Erreurs et signal de refus](signal-de-refus.md) | Comment une description nomme-t-elle les refus d'un service, et quand marque-t-elle le compte ? | validé avec Alexis le 29/09/2026 ; révisé le 03/11/2026 |
| [Coût d'un appel et sonde à solde](cout-et-solde.md) | Que coûte un appel, avant et après, et comment la sonde lit-elle le solde d'un compte payant ? | validé avec Alexis le 29/09/2026 ; révisé le 03/11/2026 |
| [Travaux longs](travaux-longs.md) | Comment dire un travail asynchrone tenu par le tiers, ou un appel synchrone de plusieurs minutes ? | validé avec Alexis le 29/09/2026 ; révisé le 03/11/2026 |
| [La fabrique](fabrique.md) | Comment un fichier de description devient-il des définitions TypeScript que le paquet d'oto 2 adapte, et une description que le moteur Python de la lib exécute pour oto 1 ? | proposé |
| [Niveaux d'accès](niveaux-d-acces.md) | Comment ouvrir un connecteur OAuth en lecture seule, ou à un sous-ensemble de ses droits ? | validé avec Alexis le 14/10/2026 |

## Anciens identifiants → document

| Ancien identifiant | Document |
|---|---|
| Conception connecteurs d'oto 2 (oto-enterprise, archivé) : « Le format de connecteur », « Le fichier de description », « Faits du relevé de la lib Python », lot 2 | [Format de description](format-de-description.md) |
| Conception connecteurs d'oto 2 (oto-enterprise, archivé) : « Ce que la fabrique génère », « Périmètre, sortie TypeScript, secrets », « Deux sortes de connecteurs », « Bout 1 » | [La fabrique](fabrique.md) ; le contrat côté hôte : oto-pkg : docs/conception/connecteurs-et-comptes.md |
| ADR 0070 §7.8 (oto-enterprise, archivé) : un connecteur activé, ses outils ou `oto_call` | [Format de description](format-de-description.md), « Décisions et alternatives écartées » |
| ADR 0070 §7.4 (a) et §7.9 (oto-enterprise, archivé) : la lib reste un paquet à part, la cible est un deuxième oto | oto-saas : docs/conception/cible-et-depots.md |
| Format de description, avant le 03/11/2026 : « Le compte » | [Le compte d'un connecteur](compte.md) |
| Format de description, avant le 03/11/2026 : « Le corps entier », « Le corps emboîté » | [Le corps d'une requête](corps-de-requete.md) |
| Format de description, avant le 03/11/2026 : « Retirer de la sortie » | [La sortie d'une fonction](sortie.md) |
| Format de description, avant le 03/11/2026 : « Fichiers », « Un fichier rendu » | [Fichiers envoyés et rendus](fichiers.md) |
| Format de description, avant le 03/11/2026 : « Signal de refus du service » | [Erreurs et signal de refus](signal-de-refus.md) |
| Format de description, avant le 03/11/2026 : « Coût d'un appel », « Sonde à solde » | [Coût d'un appel et sonde à solde](cout-et-solde.md) |
| Format de description, avant le 03/11/2026 : « Travail asynchrone », « Fonction longue » | [Travaux longs](travaux-longs.md) |
| Format de description, avant le 03/11/2026 : « Les connecteurs décrits » | l'en-tête de chaque `connectors/<nom>/connector.yaml` ; `docs/carte-du-parc.md` |
