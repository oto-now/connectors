"""Pennylane CUSTOMERS AND SUPPLIERS, beyond the lists — one customer, the
individual customers, the contacts of a customer, a supplier changed.

A module of the client (same split as `ledger.py`): `PennylaneClient` inherits
it and provides the transport. Each method is the function of the same name of
`connectors/pennylane/connector.yaml`, with its arguments.

`/customers/{id}` reads a customer of either kind; companies and individuals
each have their own route to read, create and change (`company_customers`,
`individual_customers`). A contact is not an invoice recipient: the customer's
`emails` are, and they do not follow the contacts.
"""
from __future__ import annotations

from typing import Optional

from ._calls import UNSET, body, nullable, pages

#: The fields of an individual customer that can be written.
INDIVIDUAL_FIELDS = frozenset({
    "first_name", "last_name", "billing_address", "delivery_address", "emails", "phone",
    "payment_conditions", "billing_iban", "recipient", "reference", "notes",
    "external_reference", "billing_language",
})

#: The fields of a contact.
CONTACT_FIELDS = frozenset({"first_name", "last_name", "email", "role", "telephone_number",
                            "mobile_number"})

#: The fields of a supplier that can be changed, and those where null clears.
SUPPLIER_FIELDS = frozenset({"name", "reg_no", "vat_number", "postal_address", "emails", "iban",
                             "external_reference"})
SUPPLIER_NULLABLE = frozenset({"establishment_no", "supplier_payment_method",
                               "supplier_due_date_delay", "supplier_due_date_rule"})


def _only(fields: dict, allowed: frozenset, what: str) -> None:
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"{what}: unknown field(s) {sorted(unknown)}")


class PartiesMixin:

    def get_customer(self, customer_id: int) -> dict:
        """`GET /customers/{id}` — a company or an individual."""
        return self.fetch(f"customers/{int(customer_id)}")

    def get_company_customer(self, customer_id: int) -> dict:
        """`GET /company_customers/{id}`."""
        return self.fetch(f"company_customers/{int(customer_id)}")

    def get_individual_customer(self, customer_id: int) -> dict:
        """`GET /individual_customers/{id}`."""
        return self.fetch(f"individual_customers/{int(customer_id)}")

    def create_individual_customer(self, first_name: str, last_name: str,
                                   billing_address: dict, **fields) -> dict:
        """`POST /individual_customers` — a person; the full billing address
        (address, postal_code, city, country_alpha2) is required."""
        _only(fields, INDIVIDUAL_FIELDS, "create_individual_customer")
        return self.post("individual_customers", body(
            first_name=first_name, last_name=last_name, billing_address=billing_address,
            **fields))

    def update_individual_customer(self, customer_id: int, **fields) -> dict:
        """`PUT /individual_customers/{id}` — only the fields given change."""
        _only(fields, INDIVIDUAL_FIELDS, "update_individual_customer")
        sent = body(**fields)
        if not sent:
            raise ValueError("update_individual_customer: nothing to change")
        return self.put(f"individual_customers/{int(customer_id)}", sent)

    # --- contacts of a customer ---

    def list_customer_contacts(self, customer_id: int, limit: Optional[int] = None,
                               cursor: Optional[str] = None, sort: Optional[str] = None,
                               max_pages: Optional[int] = None) -> list:
        """`GET /customers/{id}/contacts`."""
        return pages(self, f"customers/{int(customer_id)}/contacts", limit=limit,
                     cursor=cursor, sort=sort, max_pages=max_pages)

    def get_customer_contact(self, customer_id: int, contact_id: int) -> dict:
        """`GET /customers/{id}/contacts/{contact_id}`."""
        return self.fetch(f"customers/{int(customer_id)}/contacts/{int(contact_id)}")

    def create_customer_contact(self, customer_id: int, first_name: str, last_name: str,
                                email: str, **fields) -> dict:
        """`POST /customers/{id}/contacts` — role, telephone_number,
        mobile_number optional."""
        _only(fields, CONTACT_FIELDS, "create_customer_contact")
        return self.post(f"customers/{int(customer_id)}/contacts", body(
            first_name=first_name, last_name=last_name, email=email, **fields))

    def update_customer_contact(self, customer_id: int, contact_id: int, **fields) -> dict:
        """`PUT /customers/{id}/contacts/{contact_id}` — only the fields given
        change; an empty string clears role or a phone number."""
        _only(fields, CONTACT_FIELDS, "update_customer_contact")
        sent = body(**fields)
        if not sent:
            raise ValueError("update_customer_contact: nothing to change")
        return self.put(f"customers/{int(customer_id)}/contacts/{int(contact_id)}", sent)

    def delete_customer_contact(self, customer_id: int, contact_id: int) -> dict:
        """`DELETE /customers/{id}/contacts/{contact_id}`."""
        return self.delete(f"customers/{int(customer_id)}/contacts/{int(contact_id)}")

    # --- suppliers ---

    def update_supplier(self, supplier_id: int, establishment_no=UNSET,
                        supplier_payment_method=UNSET, supplier_due_date_delay=UNSET,
                        supplier_due_date_rule=UNSET, **fields) -> dict:
        """`PUT /suppliers/{id}` — only the fields given change; `None` clears
        establishment_no and the payment settings (sent as null)."""
        _only(fields, SUPPLIER_FIELDS, "update_supplier")
        sent = {**body(**fields), **nullable(
            establishment_no=establishment_no, supplier_payment_method=supplier_payment_method,
            supplier_due_date_delay=supplier_due_date_delay,
            supplier_due_date_rule=supplier_due_date_rule)}
        if not sent:
            raise ValueError("update_supplier: nothing to change")
        return self.put(f"suppliers/{int(supplier_id)}", sent)
