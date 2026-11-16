"""La carte du parc : `docs/carte-du-parc.md`, une ligne par connecteur décrit, générée des descriptions contrôlées.

Elle dit, pour chaque connecteur, son type (la règle de `kind`), ses fonctions par classe, sa sonde, ses coûts
déclarés, ses niveaux d'accès et ses fonctions non générées (celles que `python -m fabrique` nomme). Aucun second
parseur : elle lit les descriptions que `load_all` a validées et les modules que la fabrique a produits.
`python -m fabrique --check` échoue quand le fichier commité ne les suit plus.
"""
from __future__ import annotations

from .descriptions import REPO, Description
from .typescript import Module

OUTPUT = REPO / "docs" / "carte-du-parc.md"
RELATIVE = "docs/carte-du-parc.md"
GENERATED_MARK = (
    "Généré par la fabrique depuis `connectors/*/connector.yaml`. Ne pas modifier : lancer `python -m fabrique`.\n"
)

PERSON_OAUTH = "connexion d'une personne (OAuth)"
CLIENT_APPLICATION = "application du client (OAuth entre machines)"
SERVICE_ACCOUNT = "compte de service (assertion signée)"
HOST_KEY = "clé de l'hôte"
NO_KEY = "sans clé"
KEY = "clé"
KINDS = (PERSON_OAUTH, CLIENT_APPLICATION, SERVICE_ACCOUNT, HOST_KEY, NO_KEY, KEY)

CLASSES = ("read", "write", "sensitive")


def kind(connector: dict) -> str:
    """Le type d'un connecteur, de son bloc `connector` : la première branche qui s'applique, dans cet ordre."""
    auth_kind = connector["auth"]["kind"]
    if auth_kind == "oauth2_user":
        return PERSON_OAUTH
    if auth_kind == "oauth2_client_credentials":
        return CLIENT_APPLICATION
    if auth_kind == "oauth2_jwt_bearer":
        return SERVICE_ACCOUNT
    if any(field.get("holder") == "host" for field in connector.get("credential", [])):
        return HOST_KEY
    if auth_kind == "none":
        return NO_KEY
    return KEY


def _probe(connector: dict) -> str:
    probe = connector.get("probe")
    if probe is None:
        return "non"
    return "oui, avec solde" if "balance" in probe else "oui"


def _yes(value: bool) -> str:
    return "oui" if value else "non"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _row(cells: list[object]) -> str:
    return "| " + " | ".join(_cell(str(c)) for c in cells) + " |"


HEADER = ["connecteur", "libellé", "version", "type", "clé de la plateforme", "fonctions", "lecture", "écriture",
          "sensible", "sonde", "avec coût", "niveaux d'accès", "non générées"]
ALIGN = ["---", "---", "---", "---", "---", "---:", "---:", "---:", "---:", "---", "---:", "---", "---:"]


def markdown_source(descriptions: list[Description], modules: list[Module]) -> str:
    skipped = {m.connector: len(m.skipped) for m in modules}
    rows = []
    totals = {"functions": 0, "skipped": 0, "cost": 0, **{c: 0 for c in CLASSES}}
    by_kind = {k: [0, 0] for k in KINDS}
    for description in sorted(descriptions, key=lambda d: d.name):
        connector = description.data["connector"]
        functions = description.data["functions"]
        classes = {c: sum(1 for f in functions if f["class"] == c) for c in CLASSES}
        cost = sum(1 for f in functions if "cost" in f)
        connector_kind = kind(connector)
        rows.append(_row([
            f"`{connector['name']}`", connector["label"], connector["version"], connector_kind,
            _yes("platform" in connector["modes"]), len(functions), classes["read"], classes["write"],
            classes["sensitive"], _probe(connector), cost, _yes("access_levels" in connector["auth"]),
            skipped[connector["name"]],
        ]))
        totals["functions"] += len(functions)
        totals["skipped"] += skipped[connector["name"]]
        totals["cost"] += cost
        for c in CLASSES:
            totals[c] += classes[c]
        by_kind[connector_kind][0] += 1
        by_kind[connector_kind][1] += len(functions)
    rows.append(_row([
        f"**total : {len(descriptions)}**", "", "", "", "", totals["functions"], totals["read"], totals["write"],
        totals["sensitive"], "", totals["cost"], "", totals["skipped"],
    ]))
    lines = [
        GENERATED_MARK.rstrip("\n"),
        "",
        "# La carte du parc",
        "",
        "Une ligne par connecteur décrit, par ordre alphabétique. Le type se déduit de la description, "
        "première règle qui s'applique :",
        "",
        f"1. `auth.kind: oauth2_user` : {PERSON_OAUTH} ;",
        f"2. `auth.kind: oauth2_client_credentials` : {CLIENT_APPLICATION} ;",
        f"3. `auth.kind: oauth2_jwt_bearer` : {SERVICE_ACCOUNT} ;",
        f"4. un champ du `credential` en `holder: host` : {HOST_KEY} ;",
        f"5. `auth.kind: none` : {NO_KEY} ;",
        f"6. sinon (`api_key`, `bearer`, `basic`) : {KEY}.",
        "",
        "« Clé de la plateforme » : `modes` contient `platform`. Fonctions : par `class` (lecture, écriture, "
        "sensible). Sonde : `probe`, et son solde `probe.balance`. Avec coût : les fonctions qui portent `cost`. "
        "Niveaux d'accès : `auth.access_levels`. Non générées : les fonctions que la fabrique saute, nommées par "
        "`python -m fabrique`.",
        "",
        _row(HEADER),
        "| " + " | ".join(ALIGN) + " |",
        *rows,
        "",
        "## Par type",
        "",
        _row(["type", "connecteurs", "fonctions"]),
        "| --- | ---: | ---: |",
        *(_row([k, n, f]) for k, (n, f) in by_kind.items()),
        "",
    ]
    return "\n".join(lines)
