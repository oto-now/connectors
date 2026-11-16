"""Pennylane BANK — bank accounts and their balance, banks, transactions, and
the changes of an object since a date.

A module of the client (same split as `ledger.py`): `PennylaneClient` inherits
it and provides the transport. Each method is the function of the same name of
`connectors/pennylane/connector.yaml`, with its arguments.

`balance` of a bank account is a decimal string, as Pennylane holds it: it is
returned untouched, never turned into a float. Pennylane's API exposes neither
the IBAN of an account nor the date of its last bank sync.

Scopes: `bank_accounts:*`, `bank_establishments:readonly` (distinct: a key can
read balances without naming the banks), `transactions:*`.
"""
from __future__ import annotations

from typing import Optional

from ._calls import UNSET, body, nullable, pages, query

#: The kinds of object whose changes `list_changes` reads (`GET /changelogs/{kind}`).
CHANGELOGS = (
    "transactions", "customer_invoices", "supplier_invoices", "customers", "suppliers",
    "products", "quotes", "ledger_entries", "ledger_entry_lines",
    "ledger_entries_categories", "ledger_entry_lines_categories",
)


class BankMixin:

    def list_bank_accounts(self, limit: Optional[int] = None, cursor: Optional[str] = None,
                           sort: Optional[str] = None,
                           max_pages: Optional[int] = None) -> list:
        """`GET /bank_accounts` — id, name, currency, balance (decimal string),
        bank_establishment {id}, journal, ledger_account."""
        return pages(self, "bank_accounts", limit=limit, cursor=cursor, sort=sort,
                     max_pages=max_pages)

    def get_bank_account(self, bank_account_id: int) -> dict:
        """`GET /bank_accounts/{id}` — one account and its balance."""
        return self.fetch(f"bank_accounts/{int(bank_account_id)}")

    def list_bank_establishments(self, filter: Optional[list] = None,
                                 limit: Optional[int] = None, cursor: Optional[str] = None,
                                 sort: Optional[str] = None,
                                 max_pages: Optional[int] = None) -> list:
        """`GET /bank_establishments` — the banks {id, name}."""
        return pages(self, "bank_establishments", filter=filter, limit=limit, cursor=cursor,
                     sort=sort, max_pages=max_pages)

    def get_transaction(self, transaction_id: int) -> dict:
        """`GET /transactions/{id}` — one bank transaction."""
        return self.fetch(f"transactions/{int(transaction_id)}")

    def create_transaction(self, bank_account_id: int, label: str, date: str, amount: str,
                           fee: Optional[str] = None) -> dict:
        """`POST /transactions` — a bank transaction recorded by hand; `amount`
        a decimal string, negative for money out."""
        return self.post("transactions", body(
            bank_account_id=int(bank_account_id), label=label, date=date, amount=amount,
            fee=fee))

    def update_transaction(self, transaction_id: int, customer_id=UNSET,
                           supplier_id=UNSET) -> dict:
        """`PUT /transactions/{id}` — attach the transaction to ONE customer or
        ONE supplier, or detach it with `None` (sent as null)."""
        fields = nullable(customer_id=customer_id, supplier_id=supplier_id)
        if len(fields) != 1:
            raise ValueError("update_transaction: pass exactly one of customer_id, supplier_id")
        return self.put(f"transactions/{int(transaction_id)}", fields)

    def list_transaction_categories(self, transaction_id: int, limit: Optional[int] = None,
                                    cursor: Optional[str] = None,
                                    max_pages: Optional[int] = None) -> list:
        """`GET /transactions/{id}/categories` — each with its weight."""
        return pages(self, f"transactions/{int(transaction_id)}/categories", limit=limit,
                     cursor=cursor, max_pages=max_pages)

    def list_transaction_matched_invoices(self, transaction_id: int,
                                          limit: Optional[int] = None,
                                          cursor: Optional[str] = None,
                                          max_pages: Optional[int] = None) -> list:
        """`GET /transactions/{id}/matched_invoices` — {id, type, url}."""
        return pages(self, f"transactions/{int(transaction_id)}/matched_invoices",
                     limit=limit, cursor=cursor, max_pages=max_pages)

    def list_changes(self, resource: str, start_date: Optional[str] = None,
                     limit: Optional[int] = None, cursor: Optional[str] = None) -> dict:
        """`GET /changelogs/{resource}` — ONE page of changes, oldest first,
        `{items, has_more, next_cursor}`: the caller passes `next_cursor` back.
        Not followed here, because Pennylane refuses `start_date` and `cursor`
        together (400). Four weeks of changes are kept."""
        if resource not in CHANGELOGS:
            raise ValueError(f"resource: {resource!r} has no changelog "
                             f"(expected: {', '.join(CHANGELOGS)})")
        if start_date is not None and cursor is not None:
            raise ValueError("list_changes: pass start_date or cursor, never both")
        return self.fetch(f"changelogs/{resource}",
                          query(start_date=start_date, limit=limit, cursor=cursor))
