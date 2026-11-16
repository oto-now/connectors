"""Nextmotion — sales: quotes, invoices, payments and payment mediums, income
statistics, a patient's financial totals, and the product stock.

Never instantiated alone: composed into `NextmotionClient`, which provides the
transport (`_get`, `_list`, `_post`, `_put`, `_delete`).

Deleting an invoice or a payment has no method: an accounting record is
corrected (credit note, payment update), not erased. Creating a quote or an
invoice neither: the API only creates them under a consultation.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .._http import _body, _id, _opt_id

_PERIOD_TYPES = ("year", "month", "week", "day")

#: What `get_treatment_provider` keeps of the treatment's `provider` (a staff
#: member): the id and the name, never the email, the professional numbers, the
#: photo, the permissions or the clinic.
PROVIDER_FIELDS = ("id", "first_name", "last_name")


class _SalesMixin:

    # ---- quotes & invoices ---------------------------------------------------

    def list_quotes(self, clinic_id: str, *, patient_id: Optional[str] = None,
                    limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/quotes (`patient` filter)."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/quotes",
                          limit, offset, patient=_opt_id(patient_id, "patient_id"))

    def get_quote(self, quote_id: str) -> Any:
        """GET /v4/quotes/{quote_id}."""
        return self._get(f"/v4/quotes/{_id(quote_id, 'quote_id')}")

    def list_invoices(self, clinic_id: str, *, limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/invoices (no filter documented)."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/invoices",
                          limit, offset)

    def get_invoice(self, invoice_id: str) -> Any:
        """GET /v4/invoices/{invoice_id}."""
        return self._get(f"/v4/invoices/{_id(invoice_id, 'invoice_id')}")

    def update_quote(self, quote_id: str, *, body: Dict[str, Any]) -> Any:
        """PUT /v4/quotes/{quote_id}."""
        return self._put(f"/v4/quotes/{_id(quote_id, 'quote_id')}", _body(body))

    def delete_quote(self, quote_id: str) -> Any:
        """DELETE /v4/quotes/{quote_id} — answers 204."""
        return self._delete(f"/v4/quotes/{_id(quote_id, 'quote_id')}")

    def validate_quote(self, quote_id: str, *, body: Optional[Dict[str, Any]] = None) -> Any:
        """POST /v4/quotes/{quote_id}/validate — a draft becomes a validated quote,
        optionally updated in the same request (same fields as `update_quote`)."""
        return self._post(f"/v4/quotes/{_id(quote_id, 'quote_id')}/validate",
                          _body(body if body is not None else {}))

    def update_invoice(self, invoice_id: str, *, body: Dict[str, Any]) -> Any:
        """PUT /v4/invoices/{invoice_id}."""
        return self._put(f"/v4/invoices/{_id(invoice_id, 'invoice_id')}", _body(body))

    def validate_invoice(self, invoice_id: str, *,
                         body: Optional[Dict[str, Any]] = None) -> Any:
        """POST /v4/invoices/{invoice_id}/validate — a draft becomes a validated
        invoice; `issued_time` / `invoiced_time` optional, the body may be empty."""
        return self._post(f"/v4/invoices/{_id(invoice_id, 'invoice_id')}/validate",
                          _body(body if body is not None else {}))

    def pay_invoice(self, invoice_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/invoices/{invoice_id}/pay — records a payment (amount per means,
        custom mediums, voucher, deferred date; `do_validate`)."""
        return self._post(f"/v4/invoices/{_id(invoice_id, 'invoice_id')}/pay", _body(body))

    def create_credit_note(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/credit_notes (`patient`, `items` required)."""
        return self._post(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/credit_notes",
                          _body(body))

    # ---- payments ------------------------------------------------------------

    def list_payments(self, clinic_id: str, *, invoice_id: Optional[str] = None,
                      paid_from: Optional[str] = None, paid_to: Optional[str] = None,
                      limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/payments — each payment embeds its invoice.

        A payment carries `payment_time` (date-time) and its amount per means of
        payment (`cash`, `card`, `check`, `transfer`, `stripe`, `voucher`, `other`,
        `custom_medium_list`). It does not carry the practitioner: its invoice lines
        (`invoice.invoiced_treatments`) name their treatment by id, which
        `get_treatment_provider` turns into the practitioner of the act.

        Args:
            invoice_id: payments of that invoice (sent as `invoice`).
            paid_from / paid_to: `YYYY-MM-DD`, payment days in the clinic's time
                zone, both inclusive (sent as `payment_time__gte` /
                `payment_time__lte`): a month is its first and last day.
        """
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/payments",
                          limit, offset, invoice=_opt_id(invoice_id, "invoice_id"),
                          payment_time__gte=paid_from, payment_time__lte=paid_to)

    def get_treatment_provider(self, treatment_id: str) -> Any:
        """GET /v4/treatments/{treatment_id}, REDUCED to the practitioner of the act.

        The treatment id comes from a payment's invoice line
        (`invoice.invoiced_treatments[].treatment`). The answer is cut down HERE to
        `{data: {id, invoice, provider}}`, `provider` reduced to `PROVIDER_FIELDS`
        (or `None` when the API names none). The API also sends the
        practitioner's notes, the treated zone, a free text, the consultation, the
        consent form, the pricing and the other staff: none of them is kept, and no
        caller can ask for them — an allow-list, so a field Nextmotion adds never
        reaches the caller. A staff member's id and name are not health data
        (decided by the maintainer); nothing of the patient is read.
        """
        answer = self._get(f"/v4/treatments/{_id(treatment_id, 'treatment_id')}")
        row = answer.get("data") if isinstance(answer, dict) else None
        if not isinstance(row, dict):
            raise ValueError("unexpected Nextmotion answer: no `data` object.")
        provider = row.get("provider")
        if provider is not None and not isinstance(provider, dict):
            raise ValueError(f"unexpected provider on treatment {row.get('id')}: "
                             "an object or null.")
        return {"data": {
            "id": row.get("id"), "invoice": row.get("invoice"),
            "provider": None if provider is None
            else {k: provider.get(k) for k in PROVIDER_FIELDS}}}

    def get_payment(self, payment_id: str) -> Any:
        """GET /v4/payments/{payment_id}."""
        return self._get(f"/v4/payments/{_id(payment_id, 'payment_id')}")

    def update_payment(self, payment_id: str, *, body: Dict[str, Any]) -> Any:
        """PUT /v4/payments/{payment_id}."""
        return self._put(f"/v4/payments/{_id(payment_id, 'payment_id')}", _body(body))

    def list_payment_mediums(self, clinic_id: str, *, limit: int = 50,
                             offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/payment_mediums — custom payment means."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/payment_mediums",
                          limit, offset)

    def get_payment_medium(self, payment_medium_id: str) -> Any:
        """GET /v4/payment_mediums/{payment_medium_id}."""
        return self._get(
            f"/v4/payment_mediums/{_id(payment_medium_id, 'payment_medium_id')}")

    def create_payment_medium(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/payment_mediums (`name` required)."""
        return self._post(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/payment_mediums",
                          _body(body))

    def update_payment_medium(self, payment_medium_id: str, *,
                              body: Dict[str, Any]) -> Any:
        """PUT /v4/payment_mediums/{payment_medium_id} (`name` required)."""
        return self._put(
            f"/v4/payment_mediums/{_id(payment_medium_id, 'payment_medium_id')}",
            _body(body))

    def delete_payment_medium(self, payment_medium_id: str) -> Any:
        """DELETE /v4/payment_mediums/{payment_medium_id} — answers 204."""
        return self._delete(
            f"/v4/payment_mediums/{_id(payment_medium_id, 'payment_medium_id')}")

    # ---- statistics ----------------------------------------------------------

    def _statistics(self, clinic_id: str, path: str, start_date: Optional[str],
                    end_date: Optional[str], period_type: Optional[str]) -> Any:
        if period_type is not None and period_type not in _PERIOD_TYPES:
            raise ValueError(f"period_type must be one of {_PERIOD_TYPES} — "
                             f"got {period_type!r}.")
        return self._get(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/statistics/{path}",
                         start_time=start_date, end_time=end_date,
                         period_type=period_type)

    def get_appointment_income_statistics(self, clinic_id: str, *,
                                          start_date: Optional[str] = None,
                                          end_date: Optional[str] = None,
                                          period_type: Optional[str] = None) -> Any:
        """GET /v4/clinics/{clinic_id}/statistics/appointment_income — one chart.

        Args:
            start_date / end_date: `YYYY-MM-DD` (sent as `start_time`/`end_time`,
                end inclusive); omitted, the API spans 6 periods up to today.
            period_type: `year` | `month` (API default) | `week` | `day`.
        """
        return self._statistics(clinic_id, "appointment_income", start_date, end_date,
                                period_type)

    def list_treatment_type_statistics(self, clinic_id: str, *,
                                       start_date: Optional[str] = None,
                                       end_date: Optional[str] = None,
                                       period_type: Optional[str] = None) -> Any:
        """GET /v4/clinics/{clinic_id}/statistics/treatment_types — draft-quoted,
        quoted and invoiced treatment counts, one chart per treatment type."""
        return self._statistics(clinic_id, "treatment_types", start_date, end_date,
                                period_type)

    def list_treatment_type_income_statistics(self, clinic_id: str, *,
                                              start_date: Optional[str] = None,
                                              end_date: Optional[str] = None,
                                              period_type: Optional[str] = None) -> Any:
        """GET /v4/clinics/{clinic_id}/statistics/treatment_types/income — income
        from invoiced treatments, one chart per treatment type."""
        return self._statistics(clinic_id, "treatment_types/income", start_date,
                                end_date, period_type)

    def get_patient_stats(self, patient_id: str) -> Any:
        """GET /v4/patients/{patient_id}/stats — quoted, invoiced, paid,
        credit-note and reimbursement totals, first/last visit times, review
        requests, media counts. No identity in the response."""
        return self._get(f"/v4/patients/{_id(patient_id, 'patient_id')}/stats")

    # ---- product stock ---------------------------------------------------------

    def list_products(self, clinic_id: str, *, search: Optional[str] = None,
                      stock_state: Optional[str] = None,
                      expiring_within_days: Optional[int] = None,
                      order: Optional[str] = None,
                      limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/products — the clinic's stock, one row per lot.

        A product row embeds its catalogue entry (`global_product`: name, brand).

        Args:
            search: free-text search.
            stock_state: `low` | `out` | `ok`.
            expiring_within_days: >= 1 — lots expiring within that many days.
            order: `name`, `brand_name` (API default), `stock_level` or
                `warning_level`, each optionally prefixed by `-`.
        """
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/products",
                          limit, offset, search=search, stock_state=stock_state,
                          expiring_within_days=expiring_within_days, order=order)

    def get_product(self, product_id: str) -> Any:
        """GET /v4/products/{product_id}."""
        return self._get(f"/v4/products/{_id(product_id, 'product_id')}")

    def create_product(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/products — a lot (`global_product` required)."""
        return self._post(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/products",
                          _body(body))

    def update_product(self, product_id: str, *, body: Dict[str, Any]) -> Any:
        """PUT /v4/products/{product_id}."""
        return self._put(f"/v4/products/{_id(product_id, 'product_id')}", _body(body))

    def delete_product(self, product_id: str) -> Any:
        """DELETE /v4/products/{product_id} — answers 200 with the product."""
        return self._delete(f"/v4/products/{_id(product_id, 'product_id')}")
