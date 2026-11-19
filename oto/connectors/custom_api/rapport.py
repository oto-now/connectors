"""The dry-run report of the takeover of oto 1's Custom API definitions. READ ONLY: it reads an export, writes one
report, and touches nothing else, neither a database nor the network.

    python -m oto.connectors.custom_api.rapport <export.json> <report.json>

The export is produced by the operator of oto 1, from its tables, without any secret:

    {"applications": [
      {"id": "app-12",                 # what names the application in oto 1, kept as is in the report
       "source": "api_apps",           # or "card": a card from before the applications
       "account": "Acme Payroll",      # api_apps.account, or the vault row's account of the card
       "connection": {...},            # api_apps.connection; for a card, the non-secret fields of its vault row
                                       # (base_url, auth_mode, header_name, query_param, token_url, scope, doc_path,
                                       # session, label)
       "definition": {...}}            # api_apps.definition; for a card, connector_instances.config of its instance
    ]}                                 # (label, description, logo_domain, headers, variables, operations)

Archived applications (`api_apps.archived_at` set) are left out of the export. The report says, for each application,
whether it converts and, by code, what is deduced, what is left behind and what blocks; then the totals:

    {"applications": 2, "converted": 1, "blocked": 1,
     "blocking_codes": {"custom_api_example_missing": 3},
     "by_application": [{"id", "source", "account", "status", "functions", "deduced", "dropped", "blocking"}]}

No secret: the export holds none (an export that carries `token`, `password` or `client_secret` blocks its application,
the value never repeated), and the report says paths, codes, reasons and the values deduced. An existing report is
never overwritten.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

from .oto1 import from_oto1

SOURCES = ("api_apps", "card")
APPLICATION_KEYS = ("id", "source", "account", "connection", "definition")


class ExportInvalid(ValueError):
    """The export does not have the expected form; nothing is written."""


def _applications(export: object) -> list[dict]:
    if not isinstance(export, dict) or not isinstance(export.get("applications"), list):
        raise ExportInvalid('the export is an object {"applications": [...]}')
    for i, application in enumerate(export["applications"]):
        if not isinstance(application, dict):
            raise ExportInvalid(f"application #{i} is not an object")
        unknown = sorted(set(application) - set(APPLICATION_KEYS))
        if unknown:
            raise ExportInvalid(f"application #{i} has unknown key(s): {', '.join(unknown)}")
        if application.get("source") not in SOURCES:
            raise ExportInvalid(f"application #{i}: source is one of {', '.join(SOURCES)}")
    return export["applications"]


def report(export: object) -> dict:
    """The aggregated report of an export (module docstring); pure."""
    entries = []
    codes: Counter = Counter()
    for application in _applications(export):
        document, verdict = from_oto1({k: application.get(k) for k in ("account", "connection", "definition")})
        codes.update(problem["code"] for problem in verdict["blocking"])
        entries.append(
            {
                "id": application.get("id"),
                "source": application["source"],
                "account": verdict["account"],
                "status": verdict["status"],
                "functions": len(document["functions"]) if document else None,
                "deduced": verdict["deduced"],
                "dropped": verdict["dropped"],
                "blocking": verdict["blocking"],
            }
        )
    converted = sum(1 for entry in entries if entry["status"] == "converted")
    return {
        "applications": len(entries),
        "converted": converted,
        "blocked": len(entries) - converted,
        "blocking_codes": dict(sorted(codes.items())),
        "by_application": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m oto.connectors.custom_api.rapport",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("export", type=pathlib.Path, help="the JSON export of oto 1's definitions")
    parser.add_argument("output", type=pathlib.Path, help="the report to write (never overwritten)")
    args = parser.parse_args(argv)
    if args.output.resolve() == args.export.resolve():
        print("refused: the report would overwrite the export", file=sys.stderr)
        return 2
    try:
        result = report(json.loads(args.export.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    try:
        with args.output.open("x", encoding="utf-8") as out:
            json.dump(result, out, ensure_ascii=False, indent=2)
            out.write("\n")
    except FileExistsError:
        print(f"refused: {args.output} exists, and a report is never overwritten", file=sys.stderr)
        return 2
    print(
        f"{result['applications']} application(s): {result['converted']} converted, {result['blocked']} blocked "
        f"— {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
