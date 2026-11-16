"""Pennylane ANALYTICS — category groups, categories, and the categories of an
invoice or a ledger entry line.

Fifth module of the client (same split as `ledger.py`, `quotes.py` and
`supplier_invoices.py`): `PennylaneClient` inherits it and provides the
transport (`fetch_all_pages`, `_appel`).

A category belongs to ONE group (an analytical axis: "Projects", "Cost
centres"…). Categorizing an object REPLACES all its categories with the list
sent, each `{id, weight}`; within a group the weights of the object sum to 1,
across groups they are independent. An empty list removes every category.

Scopes: `categories:readonly` for groups and categories; the categories of an
object follow the scope of that object (`supplier_invoices:*`,
`customer_invoices:*`, `ledger_entries:*`, `transactions:*`, `customers:*`,
`suppliers:*`), `:all` to categorize.
"""
from __future__ import annotations

from typing import Optional

# What can carry categories → its path on the API.
CATEGORIZABLE = {
    "supplier_invoice": "supplier_invoices",
    "customer_invoice": "customer_invoices",
    "ledger_entry_line": "ledger_entry_lines",
    "transaction": "transactions",
    "customer": "customers",
    "supplier": "suppliers",
}


def _path(target: str, target_id: int) -> str:
    if target not in CATEGORIZABLE:
        raise ValueError(f"target: {target!r} carries no categories "
                         f"(expected: {', '.join(CATEGORIZABLE)})")
    return f"{CATEGORIZABLE[target]}/{int(target_id)}/categories"


def _weight(value) -> str:
    """The API takes the weight as a string, between 0 and 1 (1 included)."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"weight: expected a number between 0 and 1, got {value!r}")
    try:
        number = float(value)
    except ValueError as e:
        raise ValueError(f"weight: expected a number between 0 and 1, got {value!r}") from e
    if not 0 <= number <= 1:
        raise ValueError(f"weight: {value!r} is outside 0..1")
    return value if isinstance(value, str) else repr(value)


class AnalyticsMixin:

    def list_category_groups(self, max_pages: Optional[int] = None) -> list:
        """`GET /category_groups` — the analytical axes `{id, label}`."""
        return self.fetch_all_pages("category_groups", max_pages=max_pages)

    def get_categories(self, category_group_id: Optional[int] = None,
                       max_pages: Optional[int] = None) -> list:
        """The analytical categories `{id, label, category_group, analytical_code,
        ledger_account}`, all pages; with `category_group_id`, those of that
        group only (`GET /category_groups/{id}/categories`)."""
        if category_group_id is None:
            return self.fetch_all_pages("categories", max_pages=max_pages)
        return self.fetch_all_pages(
            f"category_groups/{int(category_group_id)}/categories", max_pages=max_pages)

    def get_categories_of(self, target: str, target_id: int) -> list:
        """The categories of one object, each with its `weight`. `target`:
        supplier_invoice, customer_invoice, ledger_entry_line, transaction,
        customer or supplier."""
        return self.fetch_all_pages(_path(target, target_id))

    def categorize(self, target: str, target_id: int, categories: list[dict]) -> dict | list:
        """`PUT /{object}/{id}/categories` — REPLACES the categories of the object.

        categories: `[{"id": …, "weight": "0.5"}, …]`; the weights of one group
            sum to 1 (checked by Pennylane, which knows the groups); `[]` removes
            them all. A customer invoice still in draft cannot be categorized.
        """
        if not isinstance(categories, list):
            raise ValueError("categories: expected a list of {id, weight}")
        body = []
        for category in categories:
            if not isinstance(category, dict) or category.get("id") is None \
                    or "weight" not in category:
                raise ValueError("categories: each item requires `id` and `weight`")
            unknown = set(category) - {"id", "weight"}
            if unknown:
                raise ValueError(f"categories: unknown key(s) {sorted(unknown)}")
            body.append({"id": int(category["id"]), "weight": _weight(category["weight"])})
        return self._appel("PUT", _path(target, target_id), data=body)
