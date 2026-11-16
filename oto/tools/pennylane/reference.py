"""Pennylane REFERENCE DATA, written — analytical axes and categories,
journals, ledger accounts, products; numberings and gated features, read.

A module of the client (same split as `ledger.py`): `PennylaneClient` inherits
it and provides the transport. Each method is the function of the same name of
`connectors/pennylane/connector.yaml`, with its arguments; the lists of these
objects are read by the older methods (`get_journals`, `get_ledger_accounts`,
`get_categories`, `list_category_groups`, `list_products`).

The API deletes none of these objects: a creation stays. A ledger account
numbered 401… also creates a supplier, 411… a company customer.
"""
from __future__ import annotations

from typing import Optional

from ._calls import body, pages


class ReferenceMixin:

    # --- analytical axes and categories ---

    def list_category_group_categories(self, category_group_id: int,
                                       limit: Optional[int] = None,
                                       cursor: Optional[str] = None,
                                       max_pages: Optional[int] = None) -> list:
        """`GET /category_groups/{id}/categories`."""
        return pages(self, f"category_groups/{int(category_group_id)}/categories",
                     limit=limit, cursor=cursor, max_pages=max_pages)

    def get_category_group(self, category_group_id: int) -> dict:
        """`GET /category_groups/{id}` — with its kind and categories."""
        return self.fetch(f"category_groups/{int(category_group_id)}")

    def create_category_group(self, label: str, kind: Optional[str] = None) -> dict:
        """`POST /category_groups` — kind: profit_and_loss, treasury or building."""
        return self.post("category_groups", body(label=label, kind=kind))

    def update_category_group(self, category_group_id: int, label: str,
                              kind: Optional[str] = None) -> dict:
        """`PUT /category_groups/{id}` — `label` required even when unchanged."""
        return self.put(f"category_groups/{int(category_group_id)}",
                        body(label=label, kind=kind))

    def get_category(self, category_id: int) -> dict:
        """`GET /categories/{id}`."""
        return self.fetch(f"categories/{int(category_id)}")

    def create_category(self, label: str, category_group_id: int,
                        direction: Optional[str] = None, analytical_code: Optional[str] = None,
                        ledger_account_id: Optional[int] = None) -> dict:
        """`POST /categories` — in one axis; `direction` for treasury axes."""
        return self.post("categories", body(
            label=label, category_group_id=int(category_group_id), direction=direction,
            analytical_code=analytical_code, ledger_account_id=ledger_account_id))

    def update_category(self, category_id: int, label: Optional[str] = None,
                        direction: Optional[str] = None, analytical_code: Optional[str] = None,
                        ledger_account_id: Optional[int] = None) -> dict:
        """`PUT /categories/{id}` — only the fields given change."""
        fields = body(label=label, direction=direction, analytical_code=analytical_code,
                      ledger_account_id=ledger_account_id)
        if not fields:
            raise ValueError("update_category: nothing to change")
        return self.put(f"categories/{int(category_id)}", fields)

    # --- journals and chart of accounts ---

    def get_journal(self, journal_id: int) -> dict:
        """`GET /journals/{id}`."""
        return self.fetch(f"journals/{int(journal_id)}")

    def create_journal(self, code: str, label: str) -> dict:
        """`POST /journals` — `code` of 2 to 5 letters."""
        return self.post("journals", {"code": code, "label": label})

    def get_ledger_account(self, ledger_account_id: int) -> dict:
        """`GET /ledger_accounts/{id}`."""
        return self.fetch(f"ledger_accounts/{int(ledger_account_id)}")

    def create_ledger_account(self, number: str, label: str, vat_rate: Optional[str] = None,
                              country_alpha2: Optional[str] = None) -> dict:
        """`POST /ledger_accounts` — 401… also creates a supplier, 411… a customer."""
        return self.post("ledger_accounts", body(
            number=number, label=label, vat_rate=vat_rate, country_alpha2=country_alpha2))

    def update_ledger_account(self, ledger_account_id: int, label: Optional[str] = None,
                              letterable: Optional[bool] = None) -> dict:
        """`PUT /ledger_accounts/{id}` — label and letterable; the number stays."""
        fields = body(label=label, letterable=letterable)
        if not fields:
            raise ValueError("update_ledger_account: nothing to change")
        return self.put(f"ledger_accounts/{int(ledger_account_id)}", fields)

    # --- products ---

    def update_product(self, product_id: int, **fields) -> dict:
        """`PUT /products/{id}` — label, description, external_reference,
        price_before_tax (decimal string), vat_rate, unit, currency, reference,
        ledger_account_id, substance; only the fields given change."""
        allowed = {"label", "description", "external_reference", "price_before_tax",
                   "vat_rate", "unit", "currency", "reference", "ledger_account_id",
                   "substance"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"update_product: unknown field(s) {sorted(unknown)}")
        sent = body(**fields)
        if not sent:
            raise ValueError("update_product: nothing to change")
        return self.put(f"products/{int(product_id)}", sent)

    # --- settings of the company ---

    def list_numberings(self, limit: Optional[int] = None, cursor: Optional[str] = None,
                        sort: Optional[str] = None, max_pages: Optional[int] = None) -> list:
        """`GET /numberings` — the numbering set up per document type."""
        return pages(self, "numberings", limit=limit, cursor=cursor, sort=sort,
                     max_pages=max_pages)

    def get_company_features(self) -> dict:
        """`GET /company/features` — which gated features the company can use."""
        return self.fetch("company/features")
