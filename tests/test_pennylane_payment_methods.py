"""Pennylane: choosing how a customer invoice is paid (oto-now/connectors#310).

Pennylane takes the payment methods per installment (`installments[].payment_methods`), on the creation of an invoice
(a list of installments, each with its deadline and its amount with tax) and on the update of a draft
(`installments.update`, by installment id). The schemas served are those of the OpenAPI « Company V2 » read on
2026-11-27; the request is the one the Python engine builds from the description, against a fake vendor.
"""
from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from engine_fake import answer, play, sent
from oto.connectors import descriptions
from oto.tools.pennylane import PennylaneClient

BASE = "https://app.pennylane.com/api/external/v2"
REQUEST_METHODS = ["transfer", "e_transfer", "credit_card", "sepa", "cheque", "cash", "lcr", "pro_account_pos"]
LINE = {"label": "Workshop", "quantity": 1, "unit": "day", "raw_currency_unit_price": "1200.00", "vat_rate": "FR_200"}
INVOICE = {"customer_id": 3001, "date": "2026-11-27", "deadline": "2026-12-27", "invoice_lines": [LINE]}


def _function(name: str) -> dict:
    return next(f for f in descriptions.load("pennylane")["functions"] if f["name"] == name)


def _valid(name: str, arguments: dict) -> bool:
    return Draft202012Validator(_function(name)["input"]).is_valid(arguments)


def test_the_enum_is_the_one_a_request_takes():
    create = _function("create_customer_invoice")["input"]["properties"]["installments"]
    update = _function("update_customer_invoice")["input"]["properties"]["installments"]["properties"]
    for methods in (create["items"]["properties"]["payment_methods"],
                    update["create"]["items"]["properties"]["payment_methods"],
                    update["update"]["items"]["properties"]["payment_methods"]):
        assert methods["items"]["enum"] == REQUEST_METHODS
        assert methods["uniqueItems"] is True


def test_the_card_fees_are_told_to_the_agent():
    text = _function("create_customer_invoice")["input"]["properties"]["installments"]["items"]["properties"][
        "payment_methods"]["description"]
    assert "Adyen" in text and "0.75% excl. VAT" in text and "2% excl. VAT" in text


@pytest.mark.parametrize("installments, valid", [
    ([{"deadline": "2026-12-27", "amount": "1440.00", "payment_methods": ["credit_card", "e_transfer"]}], True),
    ([{"deadline": "2026-12-27", "amount": "1440"}], True),
    ([{"deadline": "2026-12-27", "amount": "720.00", "payment_methods": ["sepa"]},
      {"deadline": "2027-01-27", "amount": "720.00", "payment_methods": ["sepa"]}], True),
    ([], False),
    ([{"amount": "1440.00", "payment_methods": ["credit_card"]}], False),
    ([{"deadline": "2026-12-27", "payment_methods": ["credit_card"]}], False),
    ([{"deadline": "2026-12-27", "amount": 1440, "payment_methods": ["credit_card"]}], False),
    ([{"deadline": "2026-12-27", "amount": "-1440.00"}], False),
    ([{"deadline": "2026-12-27", "amount": "1440.00", "percentage": 100}], False),
    ([{"deadline": "2026-12-27", "amount": "1440.00", "payment_methods": ["paypal"]}], False),
    ([{"deadline": "2026-12-27", "amount": "1440.00", "payment_methods": ["pro_account_payment_link"]}], False),
    ([{"deadline": "2026-12-27", "amount": "1440.00", "payment_methods": ["sepa", "sepa"]}], False),
])
def test_the_installments_of_a_creation(installments, valid):
    assert _valid("create_customer_invoice", {**INVOICE, "installments": installments}) is valid


@pytest.mark.parametrize("installments, valid", [
    ({"update": [{"id": 61, "payment_methods": ["credit_card", "e_transfer"]}]}, True),
    ({"update": [{"id": 61, "payment_methods": []}]}, True),
    ({"create": [{"deadline": "2027-01-27", "amount": "720.00"}],
      "update": [{"id": 61, "amount": "720.00"}]}, True),
    ({"delete": [{"id": 61}], "create": [{"deadline": "2027-01-27", "amount": "1440.00"}]}, True),
    ({}, False),
    ({"update": []}, False),
    ({"update": [{"id": 61}]}, False),
    ({"update": [{"payment_methods": ["credit_card"]}]}, False),
    ({"update": [{"id": 61, "payment_methods": ["paypal"]}]}, False),
    ({"create": [{"amount": "720.00"}]}, False),
    ({"replace": [{"id": 61}]}, False),
])
def test_the_installments_of_a_draft_update(installments, valid):
    assert _valid("update_customer_invoice", {"invoice_id": 5001, "installments": installments}) is valid


def test_a_creation_sends_its_installments_with_the_draft():
    installments = [{"deadline": "2026-12-27", "amount": "1440.00", "payment_methods": ["credit_card", "e_transfer"]}]
    _, vendor = play(descriptions.load("pennylane"), "create_customer_invoice", {**INVOICE, "installments": installments},
                     answer(201, {"id": 5001}))
    request = sent(vendor.requests[0], BASE)
    assert (request["method"], request["path"]) == ("POST", "/customer_invoices")
    assert request["body"] == {**INVOICE, "installments": installments, "draft": True}


def test_a_creation_without_installments_sends_none():
    _, vendor = play(descriptions.load("pennylane"), "create_customer_invoice", INVOICE, answer(201, {"id": 5001}))
    assert "installments" not in sent(vendor.requests[0], BASE)["body"]


def test_a_draft_update_sets_the_methods_of_its_installment():
    arguments = {"invoice_id": 5001, "installments": {"update": [{"id": 61, "payment_methods": ["credit_card", "e_transfer"]}]}}
    _, vendor = play(descriptions.load("pennylane"), "update_customer_invoice", arguments, answer(200, {"id": 5001}))
    request = sent(vendor.requests[0], BASE)
    assert (request["method"], request["path"]) == ("PUT", "/customer_invoices/5001")
    assert request["body"] == {"installments": {"update": [{"id": 61, "payment_methods": ["credit_card", "e_transfer"]}]}}


def test_the_update_is_served_on_the_act_side_and_the_creation_on_the_read_side():
    assert _function("create_customer_invoice").get("act") is False
    assert "act" not in _function("update_customer_invoice")


class _Answer:
    status_code = 200
    content = b"{}"
    text = "{}"

    def json(self):
        return {"id": 5001}


def _client():
    client = PennylaneClient(api_key="k", rate_limit_delay=0)
    client.sent = []

    def request(method, url, params=None, json=None, timeout=None):
        client.sent.append((method, url, json))
        return _Answer()
    client.session.request = request
    return client


def test_the_python_client_sends_the_installments_given_and_none_otherwise():
    installments = [{"deadline": "2026-12-27", "amount": "1440.00", "payment_methods": ["credit_card", "e_transfer"]}]
    client = _client()
    client.create_customer_invoice(3001, "2026-11-27", "2026-12-27", [LINE], installments=installments)
    client.create_customer_invoice(3001, "2026-11-27", "2026-12-27", [LINE])
    (method, url, body), (_, _, without) = client.sent
    assert (method, url) == ("POST", f"{BASE}/customer_invoices")
    assert body["installments"] == installments
    assert "installments" not in without
