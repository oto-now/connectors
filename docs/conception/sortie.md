# La sortie d'une fonction

- **Statut** : validé par le mainteneur le 29/09/2026 (issu du [format de description](format-de-description.md))
- **Dernière révision** : 2026-11-06

## Résumé

`output` dit ce que l'agent reçoit de la réponse d'un appel : la liste à déballer (`items`), les clés qu'elle perd avant de l'atteindre (`strip`, chacune par un chemin à points), les clés de premier niveau gardées (`projection`), un schéma de sortie (`schema`), et, pour juger les chemins de `strip`, un exemple de réponse inventé (`sample`). Un fichier rendu est une autre forme d'`output`, exclusive de celles-ci ([fichiers](fichiers.md)).

## Contexte

- Une réponse de tiers porte souvent ce que l'agent ne doit pas voir : une adresse signée (un enregistrement, une photo, un lien de signature), un secret (le secret de signature d'un webhook), un pixel de suivi.
- Une réponse emballe ses données (`{"call": {…}}`, `{"data": […]}`) : la clé à retirer est sous un objet ou dans chaque élément d'une liste.

## Objectifs et non-objectifs

- Aucun secret ni adresse ouverte sans connexion ne sort vers l'agent.
- Désigner une clé emboîtée sans langage de requête, et vérifier qu'un chemin désigne quelque chose.
- Hors objectif : un contrat de la réponse du tiers. L'exemple est une garde de la fabrique, pas une documentation de l'API.

## Conception

`items` est le chemin de la liste à déballer ; un objet unique n'est pas déballé, la description dit où lire. `projection` garde des clés de premier niveau. `schema` est un schéma de sortie fermé, à propriétés déclarées.

**Retirer de la sortie.** `output.strip` `output.strip` liste des clés que la réponse perd avant d'atteindre l'agent, chacune par son chemin, des clés séparées par des points. Une entrée sans point est une clé de premier niveau ; `call.recording` désigne la clé `recording` de l'objet `call` d'une réponse emballée.

```yaml
# Une téléphonie qui emballe sa réponse : { "call": { "id": 1, "recording": "<adresse signée>", … } }.
- name: get_call
  output:
    strip: [call.recording, call.voicemail]
    sample: { call: { id: 1, recording: "https://files.example.test/r.mp3", voicemail: "https://files.example.test/v.mp3" } }
# La liste des appels : chaque élément de `calls` est un appel, sans emballage.
- name: list_calls
  output: { items: calls, strip: [recording, voicemail] }
```

- Le chemin part de chaque élément de la liste quand `items` est déclaré, de la réponse entière sinon.
- Une liste rencontrée en chemin se traverse : le reste du chemin vaut pour chacun de ses éléments (`participants.recording`), listes de listes comprises ; une réponse qui est elle-même une liste, sans `items`, se traverse de même. Un segment n'est jamais un indice : `0` est une clé.
- Une valeur absente, nulle ou scalaire là où le chemin attend un objet n'a rien à retirer, sans erreur : un champ facultatif peut manquer (un appel sans messagerie n'a pas de `voicemail`). Une clé présente est retirée quelle que soit sa valeur, `null` comprise.
- `strip` s'applique avant `projection`, qui reste au premier niveau.
- Limite assumée : une clé qui contient elle-même un point ne se désigne pas.
- La fabrique refuse (règle 18, [fabrique](fabrique.md)) un chemin mal formé (segment vide, point en tête, en fin ou doublé : `strip_path_malformed`), déclaré deux fois (`strip_path_twice`), ou sous un autre chemin de la liste, qui le retire déjà en entier (`strip_path_overlaps`) ; le schéma refuse aussi une liste vide.
- **L'exemple de réponse** (`output.sample`) : une réponse brute de l'API, inventée, exigée dès qu'un chemin de `strip` porte un point. La fabrique y juge chaque chemin de `strip` (règle 19) : un chemin désigne quelque chose s'il atteint sa clé dans au moins un élément de l'exemple, relatif à `items` et à travers les listes comme à l'exécution ; sinon elle refuse (`strip_path_absent` : une faute de frappe sur la dernière clé, un parent absent). Elle refuse aussi un chemin à points sans exemple (`strip_path_unsampled`) et un exemple sans chemin à points (`strip_sample_unused`) : il n'a pas d'autre usage, et un exemple sans usage vieillit en silence. Une clé de premier niveau seule ne l'exige pas.
- L'exemple est **une garde, pas un contrat de réponse** : ni validation à l'exécution, ni documentation de l'API, ni source de types. Il ne sort ni dans la sortie TypeScript ni dans les données Python : l'hôte ne le voit jamais.
- Il ne contient que des **valeurs inventées**, jamais recopiées d'une réponse réelle ni d'une documentation publique : adresses en `example.test`, identifiants manifestement factices (`c-0`). La fabrique refuse (`strip_sample_foreign_address`) toute adresse (`scheme://hôte`, courriel) hors des domaines réservés aux tests (`.test`, `.example`, `.invalid`, `localhost`, `example.com`, `.net`, `.org`), et des identifiants dans une adresse (un nom et un mot de passe avant l'hôte) ; son message dit où, jamais la valeur. Un jeton ne se reconnaît pas à sa forme générale : la relecture reste la garde.
- Les cas communs (`connectors/_cases/strip.yaml`, [fabrique](fabrique.md)) disent, sur des réponses simulées, ce que l'agent reçoit.

**Ce qui se retire.** Le secret de signature d'un webhook, l'adresse d'un enregistrement d'appel, d'une photo, d'un lien de signature ou d'un pixel de suivi, et toute adresse ouverte sans connexion se retirent par `output.strip` ; le client écrit à la main retire de même. Un fichier que l'agent doit pouvoir passer à une autre fonction se rend par une fonction à sortie fichier ([fichiers](fichiers.md)).

### Contrôles

Règles 18 et 19 de la [fabrique](fabrique.md). Le test du schéma fait refuser, pour `strip`, huit variantes : liste vide, chemin vide, à point en tête, en fin, à segment vide, réduit à un point, en double, `strip` qui n'est pas une liste ; et un schéma de sortie ouvert ou sans propriétés.

## Décisions et alternatives écartées

- **Juger un chemin de `strip` contre un schéma de la réponse brute, ou contre `output.schema` fermé** : écarté le 2026-10-21 au profit de l'exemple de réponse, inventé, exigé seulement pour un chemin à points.
- **Un indice de liste dans un chemin de `strip`** : écarté ; une liste se traverse, `0` est une clé.

## Sécurité et confidentialité

- L'exemple de réponse ne contient que des valeurs inventées, jamais recopiées d'une réponse réelle : la règle 19 est la garde des adresses d'un exemple ; un jeton ne se reconnaît pas à sa forme générale, la relecture reste la garde.
- Un chemin à points ignoré par un paquet d'hôte trop ancien laisserait sortir la clé visée : l'épingle d'une description qui en déclare un monte avec celle d'oto-core.

## Écart avec le code

- Oto-core applique `items`, `strip` à points et `projection` (relu à son commit f14aa1b, `output.ts`) et refuse à la déclaration un chemin mal formé, en double, sous un autre ou une liste vide ; il ne juge pas qu'un chemin désigne une clé de la réponse (la règle 19 reste à la fabrique). Un paquet antérieur ignore sans bruit un chemin à points, ce qui laisse sortir la clé visée.
- La lib Python applique `items`, `strip` et `projection` pour un hôte qui sert la description (`oto/connectors/serve.py` : `shaped`, `items_of`, `result`), jouée contre les cas communs (`tests/test_strip_cases.py`) ; une page sans sa liste `items` y est refusée (`ServeError`), jamais lue vide, comme dans oto-core. Les clients écrits à la main retirent toujours ce qu'ils retirent. `output.sample` n'est lu que par la fabrique, et ne part pas dans les descriptions servies.

## Questions ouvertes

- Un `strip` de premier niveau seul n'est jugé contre rien : une faute de frappe sur la clé y passe encore. L'étendre à toute clé retirée d'une donnée sensible reste à trancher.
- `output.strip` sur un objet unique (relevé le 2026-10-08).
- Un total de la réponse que perd une liste déclarée par `items`.

## Historique

- 2026-10-21 : retirer de la sortie par un chemin à points (`output.strip`), relatif à chaque élément de `items` ou à la réponse entière, à travers les listes ; règle 18 ; une adresse signée ne sort plus jamais vers l'agent — décidé par le mainteneur.
- 2026-10-21 : l'exemple de réponse (`output.sample`), voie retenue parmi trois (un schéma de la réponse brute, un jugement contre `output.schema` fermé) : exigé dès qu'un chemin de `strip` porte un point ; valeurs inventées ; une garde de la fabrique, jamais un contrat de réponse ; règle 19 — décidé par le mainteneur, via le superviseur.
- 2026-10-23 : aucun secret ne sort vers l'agent (le secret de signature d'un webhook se retire par `output.strip`) — décidé par le mainteneur.
- 2026-11-03 : sorti de la note « Format de description » lors de son redécoupage en documents par sujet ; les résumés par connecteur sortent pour la carte du parc et l'en-tête de chaque description — décidé par le responsable produit (source : demande de rationalisation des connecteurs).
