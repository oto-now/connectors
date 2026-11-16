"""Pennylane EXPORTS — the FEC, the general ledger and the analytical general
ledger of a period.

A module of the client (same split as `ledger.py`): `PennylaneClient` inherits
it and provides the transport. Each method is the function of the same name of
`connectors/pennylane/connector.yaml`, with its arguments.

An export is built in the background: its creation answers `{id, status:
pending}`, and reading it again gives `status` ready (or error) and `file_url`,
an address that expires 30 minutes after the read. Scopes `exports:fec`,
`exports:gl`, `exports:agl`, one per kind.
"""
from __future__ import annotations

from typing import Optional

from ._calls import body

#: The kind of export → its path on the API.
EXPORTS = {
    "fec": "exports/fecs",
    "general_ledger": "exports/general_ledgers",
    "analytical_general_ledger": "exports/analytical_general_ledgers",
}


class ExportsMixin:

    def _start_export(self, kind: str, period_start: str, period_end: str, **extra) -> dict:
        return self.post(EXPORTS[kind], body(period_start=period_start,
                                             period_end=period_end, **extra))

    def _read_export(self, kind: str, export_id: int) -> dict:
        return self.fetch(f"{EXPORTS[kind]}/{int(export_id)}")

    def create_fec_export(self, period_start: str, period_end: str) -> dict:
        """`POST /exports/fecs`."""
        return self._start_export("fec", period_start, period_end)

    def get_fec_export(self, export_id: int) -> dict:
        """`GET /exports/fecs/{id}`."""
        return self._read_export("fec", export_id)

    def create_general_ledger_export(self, period_start: str, period_end: str) -> dict:
        """`POST /exports/general_ledgers` — an xlsx file."""
        return self._start_export("general_ledger", period_start, period_end)

    def get_general_ledger_export(self, export_id: int) -> dict:
        """`GET /exports/general_ledgers/{id}`."""
        return self._read_export("general_ledger", export_id)

    def create_analytical_general_ledger_export(self, period_start: str, period_end: str,
                                                mode: Optional[str] = None) -> dict:
        """`POST /exports/analytical_general_ledgers` — mode in_line (default)
        or in_column."""
        if mode is not None and mode not in ("in_line", "in_column"):
            raise ValueError(f"mode: {mode!r} (expected in_line or in_column)")
        return self._start_export("analytical_general_ledger", period_start, period_end,
                                  mode=mode)

    def get_analytical_general_ledger_export(self, export_id: int) -> dict:
        """`GET /exports/analytical_general_ledgers/{id}`."""
        return self._read_export("analytical_general_ledger", export_id)
