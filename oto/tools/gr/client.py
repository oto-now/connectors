"""Greek companies — identity from public registers (open data, no key).

- GEMI (general commercial register): search through the publicity portal's autocomplete →
  entities (name, GEMI no., VAT/ΑΦΜ no., active/inactive status).
- VIES (EU): for a single result, validity of the VAT number and address.

Out of scope: the deep profile (directors, capital, activity codes), which needs the
official GEMI API and its key.
"""
from __future__ import annotations

import time
from urllib.parse import quote

import requests

from ..common.errors import raise_for_upstream

_TIMEOUT = 20
_RETRIES = 3
# The register answers 429 to a client without a browser User-Agent: one is presented.
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
}


def _norm(m: dict) -> dict:
    afm = (m.get("afm") or "").strip() or None
    return {
        "name": m.get("co_name") or m.get("title"),
        "also_known_as": m.get("title"),
        "gemi": str(m["arGemi"]) if m.get("arGemi") else None,
        "vat": f"EL{afm}" if afm else None,
        "afm": afm,
        "active": m.get("companyStatusId") == 3,
        "status": m.get("companyStatus"),
        "type": m.get("type"),
    }


class ViesUnavailable(RuntimeError):
    """VIES gave no verdict (`userError` other than VALID / INVALID): the member state's
    register is down or busy. Never an invalid number."""


class GrClient:
    """GEMI register (autocomplete) + VIES. No credential."""

    AUTOCOMPLETE_URL = "https://publicity.businessportal.gr/api/autocomplete/{term}"
    VIES_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/EL/vat/{vat}"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)

    def _autocomplete(self, term: str) -> list[dict]:
        url = self.AUTOCOMPLETE_URL.format(term=quote(term, safe=""))
        for attempt in range(_RETRIES):
            if attempt:
                time.sleep(2 * attempt)   # the register throttles: short backoff
            r = self.session.get(url, timeout=_TIMEOUT)
            if r.status_code != 429:
                break
        raise_for_upstream(r, service="gemi")
        return ((r.json() or {}).get("payload") or {}).get("autocomplete") or []

    def vat(self, afm: str) -> dict:
        """VIES check of a Greek VAT number (9 digits, without `EL`): validity and address."""
        r = self.session.get(self.VIES_URL.format(vat=quote(afm, safe="")), timeout=_TIMEOUT)
        raise_for_upstream(r, service="vies")
        d = r.json()
        # VIES answers 200 when the member state's register is down: `isValid` is then
        # false and `userError` names the outage. Only VALID / INVALID are verdicts.
        verdict = d.get("userError") or ("VALID" if d.get("isValid") else "INVALID")
        if verdict not in ("VALID", "INVALID"):
            raise ViesUnavailable(verdict)
        address = (d.get("address") or "").replace("\n", " ").strip()
        return {"valid": verdict == "VALID",
                "address": None if address in ("", "---") else address}

    def lookup(self, query: str) -> dict:
        """A Greek entity by company name, GEMI no. or VAT no. (ΑΦΜ, with or without `EL`).

        Returns up to ten matches (name, GEMI no., VAT, active/inactive status) and `count`,
        the number found. For a single match with a VAT number, adds its address and
        `vat_valid` from VIES; a VIES failure is named in `vat_check_error`, never read as
        an invalid number."""
        q = (query or "").strip()
        if not q:
            raise ValueError("empty `query`")
        term = q[2:].strip() if q[:2].upper() == "EL" and q[2:].strip().isdigit() else q
        matches = self._autocomplete(term)
        if not matches:
            return {"query": q, "count": 0, "results": [], "note": "No company found."}
        results = [_norm(m) for m in matches[:10]]
        if len(results) == 1 and results[0]["afm"]:
            self._add_vies(results[0])
        return {
            "source": "GEMI registry (autocomplete) + VIES",
            "query": q,
            "count": len(matches),
            "results": results,
        }

    def _add_vies(self, row: dict) -> None:
        try:
            vies = self.vat(row["afm"])
        except Exception as e:
            # The enrichment is optional: its failure is named, the lookup still answers.
            detail = str(e) if isinstance(e, ViesUnavailable) else type(e).__name__
            row["vat_check_error"] = f"VIES unavailable ({detail})"
            return
        row["address"] = vies["address"]
        row["vat_valid"] = vies["valid"]
