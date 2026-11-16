"""Pennylane OTHER DOCUMENTS, read — billing subscriptions, commercial
documents (proformas, shipping and purchasing orders), purchase requests,
SEPA mandates, the appendices of a quote, the lines of the whole ledger and
the documents of an entry; and sending a quote or a commercial document.

A module of the client (same split as `ledger.py`): `PennylaneClient` inherits
it and provides the transport. Each method is the function of the same name of
`connectors/pennylane/connector.yaml`, with its arguments.

Sending (`send_quote`, `send_commercial_document`) reaches the customer: a
409 means the PDF is not generated yet.
"""
from __future__ import annotations

from typing import Optional

from ._calls import body, pages


class DocumentsMixin:

    # --- billing subscriptions ---

    def list_billing_subscriptions(self, filter: Optional[list] = None,
                                   limit: Optional[int] = None, cursor: Optional[str] = None,
                                   sort: Optional[str] = None,
                                   max_pages: Optional[int] = None) -> list:
        """`GET /billing_subscriptions`."""
        return pages(self, "billing_subscriptions", filter=filter, limit=limit, cursor=cursor,
                     sort=sort, max_pages=max_pages)

    def get_billing_subscription(self, billing_subscription_id: int) -> dict:
        """`GET /billing_subscriptions/{id}`."""
        return self.fetch(f"billing_subscriptions/{int(billing_subscription_id)}")

    def list_billing_subscription_lines(self, billing_subscription_id: int,
                                        limit: Optional[int] = None,
                                        cursor: Optional[str] = None,
                                        sort: Optional[str] = None,
                                        max_pages: Optional[int] = None) -> list:
        """`GET /billing_subscriptions/{id}/invoice_lines`."""
        return pages(self, f"billing_subscriptions/{int(billing_subscription_id)}/invoice_lines",
                     limit=limit, cursor=cursor, sort=sort, max_pages=max_pages)

    # --- commercial documents ---

    def list_commercial_documents(self, filter: Optional[list] = None,
                                  limit: Optional[int] = None, cursor: Optional[str] = None,
                                  sort: Optional[str] = None,
                                  max_pages: Optional[int] = None) -> list:
        """`GET /commercial_documents`."""
        return pages(self, "commercial_documents", filter=filter, limit=limit, cursor=cursor,
                     sort=sort, max_pages=max_pages)

    def get_commercial_document(self, commercial_document_id: int) -> dict:
        """`GET /commercial_documents/{id}`."""
        return self.fetch(f"commercial_documents/{int(commercial_document_id)}")

    def list_commercial_document_lines(self, commercial_document_id: int,
                                       limit: Optional[int] = None,
                                       cursor: Optional[str] = None,
                                       sort: Optional[str] = None,
                                       max_pages: Optional[int] = None) -> list:
        """`GET /commercial_documents/{id}/invoice_lines`."""
        return pages(self, f"commercial_documents/{int(commercial_document_id)}/invoice_lines",
                     limit=limit, cursor=cursor, sort=sort, max_pages=max_pages)

    def list_commercial_document_appendices(self, commercial_document_id: int,
                                            limit: Optional[int] = None,
                                            cursor: Optional[str] = None,
                                            max_pages: Optional[int] = None) -> list:
        """`GET /commercial_documents/{id}/appendices`."""
        return pages(self, f"commercial_documents/{int(commercial_document_id)}/appendices",
                     limit=limit, cursor=cursor, max_pages=max_pages)

    def send_commercial_document(self, commercial_document_id: int,
                                 recipients: Optional[list] = None,
                                 email_template_id: Optional[int] = None) -> dict:
        """`POST /commercial_documents/{id}/send_by_email` — to `recipients`,
        else to the customer's addresses."""
        return self.post(f"commercial_documents/{int(commercial_document_id)}/send_by_email",
                         body(recipients=recipients, email_template_id=email_template_id))

    # --- quotes ---

    def send_quote(self, quote_id: int, recipients: Optional[list] = None,
                   email_template_id: Optional[int] = None) -> dict:
        """`POST /quotes/{id}/send_by_email` — to `recipients`, else to the
        customer's addresses."""
        return self.post(f"quotes/{int(quote_id)}/send_by_email",
                         body(recipients=recipients, email_template_id=email_template_id))

    def list_quote_appendices(self, quote_id: int, limit: Optional[int] = None,
                              cursor: Optional[str] = None,
                              max_pages: Optional[int] = None) -> list:
        """`GET /quotes/{id}/appendices`."""
        return pages(self, f"quotes/{int(quote_id)}/appendices", limit=limit, cursor=cursor,
                     max_pages=max_pages)

    # --- purchase requests and SEPA mandates ---

    def list_purchase_requests(self, filter: Optional[list] = None,
                               limit: Optional[int] = None, cursor: Optional[str] = None,
                               sort: Optional[str] = None,
                               max_pages: Optional[int] = None) -> list:
        """`GET /purchase_requests`."""
        return pages(self, "purchase_requests", filter=filter, limit=limit, cursor=cursor,
                     sort=sort, max_pages=max_pages)

    def get_purchase_request(self, purchase_request_id: int) -> dict:
        """`GET /purchase_requests/{id}`."""
        return self.fetch(f"purchase_requests/{int(purchase_request_id)}")

    def list_sepa_mandates(self, filter: Optional[list] = None, limit: Optional[int] = None,
                           cursor: Optional[str] = None, sort: Optional[str] = None,
                           max_pages: Optional[int] = None) -> list:
        """`GET /sepa_mandates`."""
        return pages(self, "sepa_mandates", filter=filter, limit=limit, cursor=cursor,
                     sort=sort, max_pages=max_pages)

    def get_sepa_mandate(self, sepa_mandate_id: int) -> dict:
        """`GET /sepa_mandates/{id}`."""
        return self.fetch(f"sepa_mandates/{int(sepa_mandate_id)}")

    # --- the ledger, across entries ---

    def search_ledger_entry_lines(self, filter: Optional[list] = None,
                                  limit: Optional[int] = None, cursor: Optional[str] = None,
                                  sort: Optional[str] = None,
                                  max_pages: Optional[int] = None) -> list:
        """`GET /ledger_entry_lines` — lines of every entry, filtered at the
        source (ledger_account_id, journal_id, ledger_entry_id, date, id)."""
        return pages(self, "ledger_entry_lines", filter=filter, limit=limit, cursor=cursor,
                     sort=sort, max_pages=max_pages)

    def get_ledger_entry_line(self, line_id: int) -> dict:
        """`GET /ledger_entry_lines/{id}`."""
        return self.fetch(f"ledger_entry_lines/{int(line_id)}")

    def list_ledger_entry_documents(self, entry_id: int, limit: Optional[int] = None,
                                    cursor: Optional[str] = None,
                                    max_pages: Optional[int] = None) -> list:
        """`GET /ledger_entries/{id}/dms_files` — {id, name, url}."""
        return pages(self, f"ledger_entries/{int(entry_id)}/dms_files", limit=limit,
                     cursor=cursor, max_pages=max_pages)
