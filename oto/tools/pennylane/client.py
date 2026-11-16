"""
Pennylane API Client - Fetch accounting data from Pennylane.

Requires: requests

Usage:
    client = PennylaneClient(api_key="your-api-key")

    # Get company info
    me = client.fetch("me")

    # Get trial balance for a year
    trial = client.fetch("trial_balance", {
        'period_start': '2025-01-01',
        'period_end': '2025-12-31'
    })

    # Fetch all pages of ledger accounts
    accounts = client.fetch_all_pages("ledger_accounts")
"""

import io
import json
import time
from typing import Optional

import requests

from ..common.credentials import require
from ..common import FieldFilter
from ..common.errors import raise_for_upstream
from .analytics import AnalyticsMixin
from .bank import BankMixin
from .documents import DocumentsMixin
from .exports import ExportsMixin
from .invoicing import InvoicingMixin
from .ledger import LedgerMixin
from .parties import PartiesMixin
from .quotes import QuotesMixin
from .reference import ReferenceMixin
from .supplier_invoices import SupplierInvoicesMixin


def _is_outstanding(transaction) -> bool:
    """True if the transaction has a non-zero `outstanding_balance` (still to be
    matched). Missing or unreadable field → True (kept: filtering on a doubtful
    field must not silently make data disappear)."""
    if not isinstance(transaction, dict):
        return True
    value = transaction.get("outstanding_balance")
    if value is None:
        return True
    try:
        return float(str(value)) != 0.0
    except (TypeError, ValueError):
        return True


class PennylaneClient(AnalyticsMixin, BankMixin, DocumentsMixin, ExportsMixin, InvoicingMixin,
                      LedgerMixin, PartiesMixin, QuotesMixin, ReferenceMixin,
                      SupplierInvoicesMixin):
    """Client for Pennylane API v2.

    The general ledger (entries, journals, line matching) lives in
    `LedgerMixin` — same split as `brevo`, see `ledger.py`; quotes in
    `QuotesMixin` (`quotes.py`); import, correction and validation of
    supplier invoices in `SupplierInvoicesMixin` (`supplier_invoices.py`);
    analytical groups and categories, and categorizing an invoice, a
    ledger entry line, a transaction, a customer or a supplier, in
    `AnalyticsMixin` (`analytics.py`); bank accounts, transactions and
    changelogs in `BankMixin` (`bank.py`); the follow-up of an invoice
    (payments, installments, matched transactions) in `InvoicingMixin`
    (`invoicing.py`); reference data written in `ReferenceMixin`
    (`reference.py`); customers, contacts and suppliers in `PartiesMixin`
    (`parties.py`); exports in `ExportsMixin` (`exports.py`); subscriptions,
    commercial documents, purchase requests, mandates and the ledger lines
    in `DocumentsMixin` (`documents.py`). Their methods carry the names and
    arguments of the functions of `connectors/pennylane/connector.yaml`.
    """

    BASE_URL = "https://app.pennylane.com/api/external/v2"

    def __init__(self, api_key: str = None, rate_limit_delay: float = 0.3,
                 field_filter: "FieldFilter" = None):
        """
        Initialize the Pennylane client.

        Args:
            api_key: Pennylane API bearer token
            rate_limit_delay: Delay between requests (default 0.3s for 4 req/sec limit)
            field_filter: Redacts sensitive fields (IBAN, names…) from every
                response. Defaults to the `field_filters.pennylane` policy in
                ~/.otomata/config.yaml (no-op when none is configured).
        """
        self.api_key = require(api_key, "PENNYLANE_API_KEY")
        self.rate_limit_delay = rate_limit_delay
        self.field_filter = field_filter or FieldFilter.from_config("pennylane")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })

    def _appel(self, methode: str, endpoint: str, *, params: Optional[dict] = None,
               data: Optional[dict | list] = None, retries: int = 3) -> dict:
        """ONE call to the API, and the only place that translates a refusal.

        An upstream refusal is an **exception**, never a return value. This
        rule was already written twice downstream — in `fetch_all_pages` and
        in `_filter_eq` — because the transport did not carry it: each one
        caught the dict on its own, and any caller that did not think of it
        read a refusal as a result (oto-backend#223 for the first half,
        oto-core#77 for this one). Both copies went away with this single
        passage.

        The `try` only covers the network round trip: without that, it would
        catch the exception we just raised and turn it back into a
        value — the exact defect being closed.
        """
        url = f"{self.BASE_URL}/{endpoint}"

        for attempt in range(retries):
            try:
                response = self.session.request(
                    methode, url, params=params, json=data, timeout=30)
            except Exception as e:
                raise RuntimeError(f"pennylane: {methode} {endpoint} — {e}") from e

            if response.status_code == 429:
                time.sleep(2 ** attempt)
                continue

            raise_for_upstream(response, service="pennylane")

            if response.status_code == 204 or not response.content:
                return {"ok": True}
            try:
                charge = response.json()
            except ValueError as e:
                raise RuntimeError(
                    f"pennylane: {methode} {endpoint} answered "
                    f"{response.status_code} without readable JSON — "
                    f"{response.text[:200]!r}") from e
            return self.field_filter.apply(charge)

        raise RuntimeError(
            f"pennylane: {methode} {endpoint} — rate limited (429) after "
            f"{retries} attempts.")

    def post(self, endpoint: str, data: dict, retries: int = 3) -> dict:
        """POST on the Pennylane API. Raises on upstream refusal."""
        return self._appel("POST", endpoint, data=data, retries=retries)

    def put(self, endpoint: str, data: dict, retries: int = 3) -> dict:
        """PUT on the Pennylane API. Raises on upstream refusal."""
        return self._appel("PUT", endpoint, data=data, retries=retries)

    def delete(self, endpoint: str, data: Optional[dict] = None,
               retries: int = 3) -> dict:
        """DELETE on the Pennylane API. Raises on upstream refusal.

        `data`: request body. Unusual on a DELETE, but Pennylane requires one
        for unmatching (`DELETE /ledger_entry_lines/lettering` takes the lines
        to unmatch) — without it, this action cannot be expressed.
        """
        return self._appel("DELETE", endpoint, data=data, retries=retries)

    def fetch(self, endpoint: str, params: Optional[dict] = None,
              retries: int = 3) -> dict:
        """Read on the Pennylane API. Raises on upstream refusal.

        Args:
            endpoint: path (e.g. "me", "trial_balance", "ledger_accounts")
            params: query parameters
            retries: attempts on rate limiting
        """
        return self._appel("GET", endpoint, params=params, retries=retries)

    def fetch_all_pages(
        self,
        endpoint: str,
        params: Optional[dict] = None,
        max_pages: Optional[int] = None,
        per_page: int = 100
    ) -> list:
        """
        Fetch all pages of a paginated endpoint (cursor pagination).

        Pennylane API 2026: only **cursor** pagination (`cursor` + `limit`)
        is supported; the old `page`/`per_page` return HTTP 400. The response
        carries `items`, `has_more` and `next_cursor` — we pass `next_cursor` back in
        `cursor` for the next page. `max_pages` bounds the number of iterations,
        `per_page` is sent as `limit` (max 100).
        """
        all_data = []
        if params is None:
            params = {}
        params = dict(params)
        params['limit'] = min(per_page, 100)
        params.pop('page', None)
        params.pop('per_page', None)
        cursor = None
        pages = 0

        while True:
            if cursor:
                params['cursor'] = cursor

            # `fetch` RAISES on upstream refusal: an error can no longer be swallowed
            # into an empty list, which used to confuse "auth error" with "no
            # result" and recreate duplicate credit notes (oto-backend#223).
            data = self.fetch(endpoint, params)
            time.sleep(self.rate_limit_delay)

            if isinstance(data, dict) and 'items' in data:
                items = data['items']
                has_more = data.get('has_more', False)
                next_cursor = data.get('next_cursor')
            else:
                # non-paginated endpoint (returns a list or a raw object)
                return data if isinstance(data, list) else [data]

            if items:
                all_data.extend(items)

            pages += 1
            if not has_more or not next_cursor:
                break
            if max_pages and pages >= max_pages:
                break
            cursor = next_cursor

        return all_data

    def get_company_info(self) -> dict:
        """Get company information."""
        return self.fetch("me")

    def get_fiscal_years(self) -> list:
        """Get fiscal years."""
        return self.fetch("fiscal_years")

    def get_trial_balance(self, start_date: str, end_date: str) -> list:
        """
        Get trial balance for a period.

        Args:
            start_date: Period start (YYYY-MM-DD)
            end_date: Period end (YYYY-MM-DD)
        """
        return self.fetch_all_pages("trial_balance", {
            'period_start': start_date,
            'period_end': end_date
        })

    def get_customer_invoices(self, max_pages: Optional[int] = None) -> list:
        """Get customer invoices."""
        return self.fetch_all_pages("customer_invoices", max_pages=max_pages)

    def get_supplier_invoices(self, max_pages: Optional[int] = None) -> list:
        """Get supplier invoices."""
        return self.fetch_all_pages("supplier_invoices", max_pages=max_pages)

    def get_transactions(self, max_pages: Optional[int] = None,
                         period_start: Optional[str] = None,
                         period_end: Optional[str] = None,
                         only_outstanding: bool = False,
                         per_page: int = 100) -> list:
        """Get bank transactions, with optional source-side reduction levers.

        Without a lever, the endpoint returns the WHOLE history (observed: 307
        transactions ≈ 247k chars — unusable by an agent). The filters
        are OPTIONAL (raw remains the default):

        Args:
            max_pages: bounds the number of pages fetched.
            period_start / period_end: date bounds (YYYY-MM-DD), filtered
                SERVER-SIDE (`filter` param of API v2, operators gteq/lteq
                on `date`) — the volume is reduced at the source.
            only_outstanding: keeps only unsettled transactions
                (`outstanding_balance` ≠ 0) — client-side filter, applied to
                the fetched pages. A missing/unreadable amount is KEPT
                (no data is lost on a doubtful field).
            per_page: page size (≤100) — refines the granularity of max_pages.
        """
        params: dict = {}
        filters = []
        if period_start:
            filters.append({"field": "date", "operator": "gteq", "value": period_start})
        if period_end:
            filters.append({"field": "date", "operator": "lteq", "value": period_end})
        if filters:
            params["filter"] = json.dumps(filters)
        items = self.fetch_all_pages("transactions", params or None,
                                     max_pages=max_pages, per_page=per_page)
        if only_outstanding:
            items = [t for t in items if _is_outstanding(t)]
        return items

    # --- Matching (lettrage) ---

    def match_transaction(self, invoice_id: int, transaction_id: int,
                          invoice_type: str = "customer") -> dict:
        """Lettre (reconcile) a bank transaction with an invoice.

        Reversible accounting link, not a new entry. invoice_type is
        "customer" (sales) or "supplier" (purchases).
        """
        endpoint = f"{invoice_type}_invoices/{invoice_id}/matched_transactions"
        return self.post(endpoint, {"transaction_id": transaction_id})

    # --- File Attachments ---

    def upload_file(self, file_path: str) -> dict:
        """Upload a file (PDF) to Pennylane. Returns dict with id, filename, url."""
        import os
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            return self.upload_file_bytes(f.read(), filename)

    def upload_file_bytes(self, data: bytes, filename: str,
                          content_type: str = "application/pdf") -> dict:
        """Upload BYTES (PDF) to Pennylane without going through disk.

        Variant of `upload_file` for a caller that already holds the bytes
        ("oto-side" file: Drive, Gmail attachment, URL — resolved upstream). Posts
        as multipart to `POST /file_attachments`. Returns `{id, filename, url}` and
        RAISES on upstream refusal. The `id` is the `file_attachment_id` to pass to
        `import_supplier_invoice`.
        """
        url = f"{self.BASE_URL}/file_attachments"
        try:
            response = self.session.post(
                url,
                files={"file": (filename, io.BytesIO(data), content_type)},
                timeout=60,
            )
        except Exception as e:
            raise RuntimeError(f"pennylane: upload of {filename} — {e}") from e
        # Multipart: does not go through `_appel`, but follows the same rule — a
        # refusal is an exception, never a value.
        raise_for_upstream(response, service="pennylane")
        return response.json()

    # --- Customers ---

    def list_customers(self, max_pages: Optional[int] = None) -> list:
        """List all customers.

        v2 quirk: the LIST endpoint is `customers` (GET), while create/update use
        `company_customers` (POST/PUT). `company_customers` has no GET → 404."""
        return self.fetch_all_pages("customers", max_pages=max_pages)

    def _filter_eq(self, endpoint: str, field: str, value) -> list:
        """Read through the native SERVER-SIDE FILTER (`filter=[{field,operator,value}]`).

        An upstream error must never read as "no result": both callers
        are ANTI-DUPLICATE guards, for which a false negative creates an
        extra entry. Same rule as `fetch_all_pages` (oto-backend#223), which this
        single-call path used to bypass.
        """
        import json as _json
        flt = _json.dumps([{"field": field, "operator": "eq", "value": str(value)}])
        data = self.fetch(endpoint, {"filter": flt})
        return data.get("items", []) if isinstance(data, dict) else []

    def find_customer_by_external_reference(self, external_reference: str):
        """Return the customer carrying this `external_reference`, or None.

        Anti-duplicate guard for customer creation: a company that already had an
        avoir exists (external_reference = the back-office companyId), and creating
        it again fails 422 « External reference has already been taken ». Uses the
        NATIVE server-side filter on `customers` (single call, no scan)."""
        items = self._filter_eq("customers", "external_reference", external_reference)
        return items[0] if items else None

    def create_customer(self, name: str, emails: list[str] = None,
                        address: str = None, postal_code: str = None,
                        city: str = None, country_alpha2: str = "FR",
                        external_reference: str = None) -> dict:
        """Create a customer."""
        body = {"name": name}
        if emails:
            body["emails"] = emails
        if address or postal_code or city:
            body["billing_address"] = {
                k: v for k, v in {
                    "address": address, "postal_code": postal_code,
                    "city": city, "country_alpha2": country_alpha2,
                }.items() if v
            }
        if external_reference:
            body["external_reference"] = external_reference
        return self.post("company_customers", body)

    def update_customer(self, customer_id: int, **fields) -> dict:
        """Update a customer. Accepts any top-level field (name, vat_number, emails, billing_address, etc.)."""
        return self.put(f"company_customers/{customer_id}", fields)

    # --- Suppliers ---
    # NB: unlike customers (`company_customers`), the v2 supplier resource is `suppliers`
    # (GET/POST /suppliers, GET/PUT /suppliers/{id}). Needed to create/reuse a supplier
    # before `import_supplier_invoice` (which requires an existing supplier_id).

    def list_suppliers(self, max_pages: Optional[int] = None) -> list:
        """List all suppliers (id + name → resolve a supplier_id from a name)."""
        return self.fetch_all_pages("suppliers", max_pages=max_pages)

    def create_supplier(self, name: str, **fields) -> dict:
        """Create a supplier. `name` is required; pass any other documented top-level
        field (vat_number, reg_no, emails, address, iban, external_reference…)."""
        return self.post("suppliers", {"name": name, **fields})

    def get_supplier(self, supplier_id: int) -> dict:
        """Retrieve a single supplier by id."""
        return self.fetch(f"suppliers/{supplier_id}")

    # --- Products ---

    def list_products(self, max_pages: Optional[int] = None) -> list:
        """List all products."""
        return self.fetch_all_pages("products", max_pages=max_pages)

    def get_product(self, product_id: int) -> dict:
        """One product by id."""
        return self.fetch(f"products/{product_id}")

    def list_invoice_templates(self, max_pages: Optional[int] = None) -> list:
        """List customer invoice templates (models — scope
        customer_invoice_templates:readonly). The template drives the PDF
        rendering (e.g. an « Avoir » model without the payment/IBAN block)."""
        return self.fetch_all_pages("customer_invoice_templates", max_pages=max_pages)

    def create_product(self, label: str, unit_price: str, unit: str = "day",
                       vat_rate: str = "FR_200", description: str = None) -> dict:
        """Create a product. unit_price as string (e.g. '700.00')."""
        body = {
            "label": label,
            "price_before_tax": unit_price,
            "unit": unit,
            "vat_rate": vat_rate,
        }
        if description:
            body["description"] = description
        return self.post("products", body)

    # --- Customer Invoices ---

    def create_customer_invoice(self, customer_id: int, date: str, deadline: str,
                                lines: list[dict], draft: bool = True,
                                external_reference: str = None,
                                pdf_free_text: str = None,
                                customer_invoice_template_id: int = None,
                                currency: str = "EUR") -> dict:
        """
        Create a customer invoice.

        lines: list of dicts with keys: product_id, quantity, and optionally
               label, raw_currency_unit_price, unit, vat_rate.
        pdf_free_text: free text printed on the PDF (customer-visible comment,
               API field `pdf_invoice_free_text`).
        """
        body = {
            "customer_id": customer_id,
            "date": date,
            "deadline": deadline,
            "draft": draft,
            "currency": currency,
            "invoice_lines": lines,
        }
        if external_reference:
            body["external_reference"] = external_reference
        if pdf_free_text:
            body["pdf_invoice_free_text"] = pdf_free_text
        if customer_invoice_template_id:
            body["customer_invoice_template_id"] = customer_invoice_template_id
        return self.post("customer_invoices", body)

    def create_credit_note(self, customer_id: int, date: str, deadline: str,
                           lines: list[dict], external_reference: str = None,
                           pdf_free_text: str = None,
                           customer_invoice_template_id: int = None,
                           draft: bool = True, currency: str = "EUR") -> dict:
        """Create a STANDALONE credit note (avoir) — an invoice with negative amounts.

        Official v2 convention (changelog « V2 - List Credit Notes and Customer
        Invoices ») : there is no dedicated credit-note endpoint — an avoir IS a
        `customer_invoice` whose amounts are NEGATIVE. The caller provides
        POSITIVE business lines (e.g. 195 credits at 1.45) ; this method flips
        each line's quantity sign so the « avoir » nature is structural, never
        left to the caller.

        NO linking at creation : the create-endpoint attribute
        `credited_invoice_id` is broken upstream (« not working as expected …
        will be removed » — Pennylane changelog). Link afterwards with
        `link_credit_note()` if ever needed ; the MM practice links nothing
        (the AUT-… reference lives in free text on the invoice label).

        `external_reference` traces the source event (e.g. a GoCardless payment
        id `PMxxxx`) and keeps the caller idempotent (one failed payment → one
        avoir). Draft by default — finalize separately after human validation.

        lines: same shape as create_customer_invoice (product_id, quantity, and
               optionally label, raw_currency_unit_price, unit, vat_rate).
        """
        neg_lines = []
        for line in lines:
            li = dict(line)
            qty = li.get("quantity")
            if isinstance(qty, bool) or not isinstance(qty, (int, float)) or qty == 0:
                raise ValueError(
                    "credit-note line needs a non-zero numeric `quantity` "
                    f"(got {qty!r})")
            li["quantity"] = -abs(qty)
            neg_lines.append(li)
        body = {
            "customer_id": customer_id,
            "date": date,
            "deadline": deadline,
            "draft": draft,
            "currency": currency,
            "invoice_lines": neg_lines,
        }
        if external_reference:
            body["external_reference"] = external_reference
        if pdf_free_text:
            body["pdf_invoice_free_text"] = pdf_free_text
        if customer_invoice_template_id:
            body["customer_invoice_template_id"] = customer_invoice_template_id
        return self.post("customer_invoices", body)

    def link_credit_note(self, invoice_id: int, credit_note_id: int) -> dict:
        """Link an existing credit note to the customer invoice it credits.

        Dedicated v2 endpoint — the only working way to link (the create-time
        `credited_invoice_id` attribute is broken upstream)."""
        return self.post(f"customer_invoices/{invoice_id}/link_credit_note",
                         {"credit_note_id": credit_note_id})

    def find_invoice_by_external_reference(self, external_reference: str) -> Optional[dict]:
        """Return the customer invoice carrying this `external_reference`, or None.

        Anti-duplicate guard for credit notes: before creating an avoir for a
        GoCardless payment id, check none already references it. Uses the NATIVE
        server-side filter (single call, EXHAUSTIVE), like customers.

        This used to be a client scan bounded to 5 pages: beyond that, an existing
        invoice returned `None` — indistinguishable from "none", on the action whose
        very role is to prevent a duplicate. Seen on AUT-70943, invoice really
        present (the update on its id answered "archived invoice") and yet
        not found (signal #268). The server filter exists on customer_invoices,
        verified live on 2026-08-03 — the "no documented server-side filter" note
        was no longer true.
        """
        items = self._filter_eq("customer_invoices", "external_reference", external_reference)
        return items[0] if items else None

    def update_invoice(self, invoice_id: int, **fields) -> dict:
        """Update a draft invoice. Accepts any field (customer_id, date, deadline, etc.)."""
        return self.put(f"customer_invoices/{invoice_id}", fields)

    def update_invoice_line(self, invoice_id: int, line_id: int, **fields) -> dict:
        """Update a line on a draft invoice (quantity, raw_currency_unit_price, label, ...).

        Pennylane expects Rails-style nested attributes, so a dedicated wrapper
        avoids clients having to know the shape.
        """
        body = {"invoice_lines": {"update": [{"id": line_id, **fields}]}}
        return self.put(f"customer_invoices/{invoice_id}", body)

    def finalize_invoice(self, invoice_id: int) -> dict:
        """Finalize a draft invoice."""
        return self.put(f"customer_invoices/{invoice_id}/finalize", {})

    def delete_invoice(self, invoice_id: int) -> dict:
        """Delete a draft customer invoice. Only drafts can be deleted;
        finalized invoices must be cancelled with a credit note instead."""
        return self.delete(f"customer_invoices/{invoice_id}")

    def send_invoice(self, invoice_id: int) -> dict:
        """Send a finalized invoice to the customer by email (uses customer's email on file)."""
        return self.post(f"customer_invoices/{invoice_id}/send_by_email", {})

    def get_invoice_lines(self, invoice_id: int) -> list:
        """Get the lines of a customer invoice."""
        data = self.fetch(f"customer_invoices/{invoice_id}/invoice_lines")
        return data.get("items", []) if isinstance(data, dict) else []

    # --- Aggregates ---

    def fetch_complete_data(self, year: int = 2025) -> dict:
        """
        Fetch complete financial data for a year.

        Args:
            year: Fiscal year to fetch

        Returns:
            Dict with all financial data
        """
        data = {
            'company': self.get_company_info(),
            'fiscal_years': self.get_fiscal_years(),
            'ledger_accounts': self.get_ledger_accounts(),
            f'trial_balance_{year}': self.get_trial_balance(
                f'{year}-01-01', f'{year}-12-31'
            ),
            'customer_invoices': self.get_customer_invoices(max_pages=50),
            'supplier_invoices': self.get_supplier_invoices(max_pages=50),
            'categories': self.get_categories(),
        }
        return data
