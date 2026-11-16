"""`python -m fabrique` régénère `ts/src/`, `oto/connectors/declared.py`, `oto/connectors/descriptions/` et `docs/carte-du-parc.md` ; `--check` échoue si la sortie commitée n'est pas à jour."""
from __future__ import annotations

import argparse
import sys

from .build import OUTPUT, generate, stale, write
from .descriptions import DescriptionError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fabrique", description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed output is not up to date")
    args = parser.parse_args(argv)
    try:
        result = generate()
    except DescriptionError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    for module in result.modules:
        print(f"{module.connector}: {len(module.generated)} generated, {len(module.skipped)} not generated")
        for skipped in module.skipped:
            print(f"  - {skipped.function}: {skipped.reason}")
    if args.check:
        differ = stale(result)
        if differ:
            print(f"out of date ({OUTPUT}, oto/connectors and docs): {', '.join(differ)} (run `python -m fabrique`)", file=sys.stderr)
            return 1
        return 0
    write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
