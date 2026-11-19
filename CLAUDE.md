# oto-core

**Lib de connecteurs Oto** — clients API pour agents IA, sans surface de commande. Repo **public**
(`oto-now/connectors` ; le paquet PyPI et le dossier local restent `oto-core`), **open source**.

Namespace package `oto` (PEP 420, **pas d'`oto/__init__.py`**) :

- `oto.tools.*` — les clients (serper, attio, hunter, google, linkedin via o-browser, pennylane, reddit,
  slack, gocardless, planity, zoho, sirene/inpi/bodacc/boamp/dvf/culture via france-opendata…).
  Messagerie (WhatsApp/LinkedIn) = Unipile, côté backend.
- `oto.tools.common.credentials` — **le secret est toujours fourni par le consommateur.** Un constructeur
  reçoit son secret en paramètre et l'exige par `require(valeur, "NOM")`, qui lève `MissingCredential("NOM")`.
  La lib ne lit **aucun** secret : ni environnement, ni fichier, ni fournisseur. Garde mécanique :
  `tests/test_no_secret_read_guard.py`.
- `oto.tools.common.local_dirs` — répertoires locaux (caches, sessions navigateur) et blocs non secrets de
  `~/.otomata/config.yaml` (`field_filters`).

**Source unique des clients connecteurs**. Consommateurs : le **backend d'oto 1** (serveur MCP, qui épingle une
version git, importe `oto.tools.*` directement et injecte les secrets de son coffre) et, à venir, le **paquet npm
d'oto 2**, qui recevra les connecteurs partagés par la fabrique.

Un **bridge** (micro-service HTTP interne appelé par le connecteur `http` générique : calcul déterministe,
garde d'un credential client) n'est pas un client et ne vit pas ici : il part du squelette privé
`otomata-tech/bridge-boilerplate`.

## Connecteurs partagés et fabrique

- **Concevoir un connecteur** (« ajoute le connecteur X ») : suivre `docs/conception/concevoir-un-connecteur.md`,
  sept étapes dans l'ordre, la première étant la fiche de qualification. La vue d'ensemble du parc est
  `docs/carte-du-parc.md`, générée par la fabrique.
- Un connecteur partagé se décrit dans `connectors/<nom>/connector.yaml`, validé par
  `connectors/connector.schema.json` ; la fabrique en tirera un client Python et des fonctions TypeScript.
- Lire le document du sujet **avant** de toucher `connectors/` ou la fabrique : `docs/conception/README.md`
  (règle de vie, index), `docs/conception/format-de-description.md` (le document d'entrée du format, qui renvoie
  au document de chaque bloc), `docs/conception/fabrique.md`. Le détail d'un connecteur vit dans l'en-tête de son
  `connector.yaml`.

## Stack

- Python ≥3.10, setuptools (namespace package). Version dans `pyproject.toml`, recopiée par `uv.lock` et par le paquet
  npm `ts/` (`@otomata_tech/connectors`), publié à la même version par le même tag → `docs/release.md`.
- Deps cœur : requests, france-opendata, pyyaml, defusedxml, jsonschema (profil Custom API). **Pas de typer**.
- Extras : `google`, `browser` (o-browser), `planity` (async), `vivatech`, `anthropic`, `stock`, `anonymize`.
  `all` les tire tous.
- **`uv.lock` est commité et ne gouverne aucune install** — il sert à rendre le dépôt observable par le graphe
  de dépendances GitHub, rien d'autre ; le régénérer par `uv lock` quand une dép bouge. ⚠️ Monter un plancher
  reste un geste séparé. **`MANIFEST.in` borne le sdist** (la `packages.find` ne gouverne que la roue) : toute
  reprise du packaging se vérifie **sur le tarball**, jamais sur le pyproject → `docs/packaging.md`.

## Conventions

Une règle par ligne ; l'incident qui l'a produite et ses cas limites vivent dans **`docs/conventions.md`**.

- **Clients purs, sans typer ni `print`** — un client rend des objets/dicts.
  Imports **lazy** des deps optionnelles pour ne pas casser si l'extra manque.
- ⚠️ **Le secret est un paramètre, jamais une lecture** : `require(x, "NOM")`, pas d'`os.environ`, pas de
  fichier, pas de défaut qui masque l'absence.
- ⚠️ **Pas d'`oto/__init__.py`** (namespace) → jamais `from oto import __version__` ; utiliser
  `importlib.metadata.version("oto-core")`.
- **Connecteur client-sensible → jamais ici** (repo public) : package privé + bridge (ADR 0003). ⚠️ Ça veut dire
  « le back-office PROPRE d'un client » ; un produit commercial vendu à des milliers d'entreprises n'en est pas
  un, même si son API est fermée. La règle se lit sur le **propriétaire de l'accès**, jamais sur la difficulté.
- ⚠️ **Aucune COORDONNÉE d'un tiers en dur — même publique** (clé d'API front, App ID, racine d'endpoints) : un
  client publié décrit un **protocole**, il n'embarque pas l'identité d'une entreprise. Elles se passent en
  paramètre, **sans valeur par défaut** — un défaut les remettrait ici sous un autre nom.
- ⚠️ **Nommer ce qu'on appelle est le métier d'un client ; raconter comment on l'a trouvé ne l'est pas.** Hôtes
  et endpoints restent (c'est du code) ; le **récit de la reconstitution** et le **diagnostic sur le tiers** ne
  s'écrivent nulle part. Ça se vérifie à la relecture d'un fichier ENTIER, pas ligne à ligne.
- ⚠️ **Un secret ne part JAMAIS en `params=`** (il finit dans le message d'exception, les logs et Sentry) :
  toujours `data=`, et pas de `raise_for_status()` sur un endpoint token. Garde-fou AST dans oto-backend.
- **Auth d'une FAMILLE de connecteurs = un module partagé** (ex. `oto/tools/zoho/auth.py`), jamais recopiée par
  client. **Cache de token = process-wide keyé par credential** (hash, jamais un secret en clair) : le serveur
  construit un client **par appel MCP**, donc un cache porté par l'instance ne sert jamais.
- **Un refus d'un client ne prescrit JAMAIS un outil MCP** : la lib ne connaît pas le jeu d'outils de l'appelant.
  Dire le FAIT, au plus une condition — jamais un nom d'outil ni une famille `xxx_*`.
- **Un client est SYNCHRONE, sauf quand l'amont ne le permet pas** (`planity` est l'exception, pas un précédent)
  · ⚠️ **un extra manquant se retraduit À L'ORIGINE**, jamais chez le consommateur.
- **Fichier de code < 500 lignes** — un gros connecteur se découpe **sans bouger son chemin d'import** :
  `<svc>/client.py` garde construction et transport et compose des mixins `<svc>/_api/*.py`, les modules frères
  sont réexportés via `__all__`. Les consommateurs pinnent **par tag** : un symbole qui déménage casse au bump
  du pin, ailleurs, plus tard. Cliquets : `tests/test_*_surface_frozen.py`.
- **Certains annuaires et sites professionnels interdisent le moissonnage** dans leurs conditions d'usage :
  vérifier avant d'ouvrir un accès ou d'écrire un connecteur qui les viserait.

## Gotchas

- ⚠️ **La CI doit installer tout extra dont un TEST importe la dépendance** : sinon `ModuleNotFoundError` **à la
  collecte**, pytest s'arrête avant le premier test et le job échoue sans rien avoir vérifié. Un extra ajouté ici
  se répercute dans `.github/workflows/ci.yml`.

## Docs

- `docs/conception/` — format de description des connecteurs partagés et fabrique (voir plus haut)
- `docs/conventions.md` — le pourquoi de chaque règle ci-dessus, avec ses incidents et ses cliquets
- `docs/packaging.md` — `uv.lock`, `MANIFEST.in`, ce que publient la roue et le sdist
- `docs/release.md` — bump + tag → publication PyPI automatique, et le pin d'oto-backend
