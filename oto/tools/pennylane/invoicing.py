"""Pennylane INVOICE FOLLOW-UP — one customer invoice, the payments and
installments of an invoice, its matched transactions, its attachments.

A module of the client (same split as `ledger.py`): `PennylaneClient` inherits
it and provides the transport. Each method is the function of the same name of
`connectors/pennylane/connector.yaml`, with its arguments.

Marking an invoice or an installment paid matches no transaction and has no
way back through the API; unmatching a transaction is restored by matching it
again (`match_transaction`).
"""
from __future__ import annotations

from typing import Optional

from ._calls import pages

#: The side of an invoice → its path on the API.
SIDES = {"customer": "customer_invoices", "supplier": "supplier_invoices"}


def _invoice(side: str, invoice_id: int) -> str:
    if side not in SIDES:
        raise ValueError(f"side: {side!r} (expected: {', '.join(SIDES)})")
    return f"{SIDES[side]}/{int(invoice_id)}"


class InvoicingMixin:

    def get_customer_invoice(self, invoice_id: int) -> dict:
        """`GET /customer_invoices/{id}` — one invoice or credit note."""
        return self.fetch(_invoice("customer", invoice_id))

    def _of_invoice(self, side: str, invoice_id: int, what: str, *, limit=None,
                    cursor=None, sort=None, max_pages=None) -> list:
        return pages(self, f"{_invoice(side, invoice_id)}/{what}", limit=limit,
                     cursor=cursor, sort=sort, max_pages=max_pages)

    def list_customer_invoice_payments(self, invoice_id: int, limit: Optional[int] = None,
                                       cursor: Optional[str] = None,
                                       sort: Optional[str] = None,
                                       max_pages: Optional[int] = None) -> list:
        """`GET /customer_invoices/{id}/payments`."""
        return self._of_invoice("customer", invoice_id, "payments", limit=limit,
                                cursor=cursor, sort=sort, max_pages=max_pages)

    def list_supplier_invoice_payments(self, invoice_id: int, limit: Optional[int] = None,
                                       cursor: Optional[str] = None,
                                       sort: Optional[str] = None,
                                       max_pages: Optional[int] = None) -> list:
        """`GET /supplier_invoices/{id}/payments`."""
        return self._of_invoice("supplier", invoice_id, "payments", limit=limit,
                                cursor=cursor, sort=sort, max_pages=max_pages)

    def list_customer_invoice_installments(self, invoice_id: int,
                                           limit: Optional[int] = None,
                                           cursor: Optional[str] = None,
                                           sort: Optional[str] = None,
                                           max_pages: Optional[int] = None) -> list:
        """`GET /customer_invoices/{id}/installments` — by deadline."""
        return self._of_invoice("customer", invoice_id, "installments", limit=limit,
                                cursor=cursor, sort=sort, max_pages=max_pages)

    def mark_customer_invoice_installment_paid(self, invoice_id: int,
                                               installment_id: int) -> dict:
        """`PUT /customer_invoices/{id}/installments/{id}/mark_as_paid`."""
        return self.put(f"{_invoice('customer', invoice_id)}/installments/"
                        f"{int(installment_id)}/mark_as_paid", {})

    def mark_customer_invoice_paid(self, invoice_id: int) -> dict:
        """`PUT /customer_invoices/{id}/mark_as_paid`."""
        return self.put(f"{_invoice('customer', invoice_id)}/mark_as_paid", {})

    def list_customer_invoice_matched_transactions(self, invoice_id: int,
                                                   limit: Optional[int] = None,
                                                   cursor: Optional[str] = None,
                                                   sort: Optional[str] = None,
                                                   max_pages: Optional[int] = None) -> list:
        """`GET /customer_invoices/{id}/matched_transactions`."""
        return self._of_invoice("customer", invoice_id, "matched_transactions", limit=limit,
                                cursor=cursor, sort=sort, max_pages=max_pages)

    def list_supplier_invoice_matched_transactions(self, invoice_id: int,
                                                   limit: Optional[int] = None,
                                                   cursor: Optional[str] = None,
                                                   sort: Optional[str] = None,
                                                   max_pages: Optional[int] = None) -> list:
        """`GET /supplier_invoices/{id}/matched_transactions`."""
        return self._of_invoice("supplier", invoice_id, "matched_transactions", limit=limit,
                                cursor=cursor, sort=sort, max_pages=max_pages)

    def unmatch_customer_invoice_transaction(self, invoice_id: int,
                                             transaction_id: int) -> dict:
        """`DELETE /customer_invoices/{id}/matched_transactions/{transaction_id}`."""
        return self.delete(f"{_invoice('customer', invoice_id)}/matched_transactions/"
                           f"{int(transaction_id)}")

    def unmatch_supplier_invoice_transaction(self, invoice_id: int,
                                             transaction_id: int) -> dict:
        """`DELETE /supplier_invoices/{id}/matched_transactions/{transaction_id}`."""
        return self.delete(f"{_invoice('supplier', invoice_id)}/matched_transactions/"
                           f"{int(transaction_id)}")

    def update_supplier_invoice_payment_status(self, invoice_id: int,
                                               payment_status: str) -> dict:
        """`PUT /supplier_invoices/{id}/payment_status` — paid or to_be_paid."""
        if payment_status not in ("paid", "to_be_paid"):
            raise ValueError(f"payment_status: {payment_status!r} (expected paid or to_be_paid)")
        return self.put(f"{_invoice('supplier', invoice_id)}/payment_status",
                        {"payment_status": payment_status})

    def list_customer_invoice_appendices(self, invoice_id: int, limit: Optional[int] = None,
                                         cursor: Optional[str] = None,
                                         max_pages: Optional[int] = None) -> list:
        """`GET /customer_invoices/{id}/appendices` — {id, filename, url}."""
        return self._of_invoice("customer", invoice_id, "appendices", limit=limit,
                                cursor=cursor, max_pages=max_pages)

    def list_customer_invoice_custom_header_fields(self, invoice_id: int,
                                                   limit: Optional[int] = None,
                                                   cursor: Optional[str] = None,
                                                   sort: Optional[str] = None,
                                                   max_pages: Optional[int] = None) -> list:
        """`GET /customer_invoices/{id}/custom_header_fields`."""
        return self._of_invoice("customer", invoice_id, "custom_header_fields", limit=limit,
                                cursor=cursor, sort=sort, max_pages=max_pages)
