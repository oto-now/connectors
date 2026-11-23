"""Le relevé des routes publiques de FOD : `tests/fixtures/fod/routes-publiques.txt`, tiré de l'OpenAPI du service.

`python -m fabrique.releve_fod <source>` le régénère ; `<source>` est un fichier ou une adresse, celle du service
publié étant https://data.oto.zone/openapi.json. `--check` ne l'écrit pas : il échoue quand le fichier commité ne
suit plus la source.

Une ligne par route, `MÉTHODE /chemin`, triées, sans rien des réponses. Une route est publique quand FOD la garde par
sa clé de consommateur : la dépendance `require_api_key` du service (`app/auth.py`) déclare l'en-tête
`Authorization`, que FastAPI publie comme paramètre de l'opération. Santé, statut, administration (session d'un
administrateur) et permaliens ne le déclarent pas ; les pages du site ne sont pas dans l'OpenAPI.

`tests/test_fod_routes.py` confronte le relevé à `connectors/fod/connector.yaml`. Le relevé est une donnée de test,
pas le code du connecteur : le mettre à jour ne demande pas de monter sa version.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
RELEVE = REPO / "tests" / "fixtures" / "fod" / "routes-publiques.txt"
METHODS = ("get", "post", "put", "patch", "delete")
USER_AGENT = "oto-connectors-releve-fod"
_PARAM = re.compile(r"\{[^}]+\}")


class ReleveError(Exception):
    """L'OpenAPI ne se lit pas, ou n'a aucune route publique : le critère ne la reconnaît plus."""


def gabarit(path: str) -> str:
    """Le chemin, chaque paramètre réduit à `{}` : `/api/fr/bodacc/{siren}` et `/api/fr/bodacc/{id}` se valent."""
    return _PARAM.sub("{}", path)


def _keyed(operation: dict) -> bool:
    return any(p.get("in") == "header" and p.get("name", "").lower() == "authorization"
               for p in operation.get("parameters", []))


def routes(openapi: dict) -> list[str]:
    """Les routes publiques, `MÉTHODE /chemin`, triées et uniques."""
    if not isinstance(openapi.get("paths"), dict):
        raise ReleveError("no `paths` object: not an OpenAPI document")
    lines = sorted({f"{method.upper()} {path}"
                    for path, operations in openapi["paths"].items()
                    for method, operation in operations.items()
                    if method in METHODS and _keyed(operation)})
    if not lines:
        raise ReleveError("no operation declares the Authorization header: the public-route criterion no longer "
                          "matches FOD (has require_api_key changed?)")
    return lines


def load(source: str) -> dict:
    if re.match(r"https?://", source):
        # The default Python-urllib user agent is refused (403) by the proxy in front of the service.
        request = urllib.request.Request(source, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.URLError as error:
            raise ReleveError(f"{source}: {error}") from error
    return json.loads(pathlib.Path(source).read_text(encoding="utf-8"))


def render(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fabrique.releve_fod", description=__doc__.split("\n\n")[0])
    parser.add_argument("source", help="FOD OpenAPI: a file or a URL (https://data.oto.zone/openapi.json)")
    parser.add_argument("--check", action="store_true", help="fail if the committed list does not follow the source")
    args = parser.parse_args(argv)
    try:
        text = render(routes(load(args.source)))
    except ReleveError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    current = RELEVE.read_text(encoding="utf-8") if RELEVE.exists() else ""
    if args.check:
        if text != current:
            before, after = set(current.splitlines()), set(text.splitlines())
            for line in sorted(after - before):
                print(f"+ {line}", file=sys.stderr)
            for line in sorted(before - after):
                print(f"- {line}", file=sys.stderr)
            print(f"out of date: {RELEVE.relative_to(REPO)} (run `python -m fabrique.releve_fod {args.source}`)",
                  file=sys.stderr)
            return 1
        return 0
    RELEVE.parent.mkdir(parents=True, exist_ok=True)
    RELEVE.write_text(text, encoding="utf-8")
    print(f"{RELEVE.relative_to(REPO)}: {text.count(chr(10))} routes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
