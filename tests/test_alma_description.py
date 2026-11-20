"""La description `alma` 1.0.0 : le paiement fractionné d'Alma, en lecture seule, sans client Python.

Gardes : chaque fonction est une lecture (`class: read`, jamais `act`) servie par un `GET`, et aucune route qui crée,
annule, capture ou rembourse un paiement, ni n'envoie un lien de paiement, n'est décrite (la clé d'Alma n'a pas de
portée : elle peut déplacer de l'argent) ; aucune fonction ne cherche une personne ; l'identité du client, ses
adresses, le vendeur, les données libres du marchand (`custom_data`, `cart`) et les adresses ouvertes sans connexion (page du paiement, suivi de commande) ne
sortent jamais vers l'agent, ce que prouve la sortie servie sur une réponse simulée.
"""
from __future__ import annotations

import copy

import yaml

from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors import serve

ALMA = CONNECTORS / "alma" / "connector.yaml"
PAYMENT_READERS = {"list_payments", "get_payment"}
PERSONAL = {"customer", "shipping_address", "billing_address", "url", "seller", "custom_data", "cart"}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(ALMA.read_text(encoding="utf-8")))


def _functions() -> list[dict]:
    return _description()["functions"]


def _payment() -> dict:
    """Un paiement tel qu'Alma le rend, valeurs inventées."""
    return {
        "id": "payment_0",
        "purchase_amount": 120000,
        "kind": "P3X",
        "payment_plan": [{"due_date": 1790805600, "date_paid": 1790805600, "state": "paid", "purchase_amount": 40000}],
        "customer": {"id": "customer_0", "first_name": "Jane", "last_name": "Doe", "email": "jane@example.test",
                     "phone": "+33000000000", "business_name": None},
        "shipping_address": {"line1": "1 rue de Test", "city": "Paris"},
        "billing_address": None,
        "url": "https://pay.example.test/payment_0",
        "seller": {"id": "user_0", "name": "Seller", "display_name": "S", "email": "seller@example.test"},
        "orders": [{"id": "order_0", "merchant_reference": "inv-0", "customer_url": "https://shop.example.test/o/0"}],
        "custom_data": {"customer_name": "Jane Doe", "invoice": "inv-0"},
        "cart": {"items": [{"title": "Item", "customer_email": "jane@example.test"}]},
    }


def _assert_without_personal_data(payment: dict):
    assert PERSONAL.isdisjoint(payment), sorted(PERSONAL & payment.keys())
    assert all("customer_url" not in order for order in payment["orders"])
    assert "jane@example.test" not in repr(payment) and "seller@example.test" not in repr(payment)
    assert payment["orders"][0]["merchant_reference"] == "inv-0"
    assert payment["payment_plan"][0]["date_paid"] == 1790805600
    assert "Jane" not in repr(payment) and "Seller" not in repr(payment)


def test_description_passes_the_fabrique():
    module = connector_module(check(_description(), ALMA))
    assert module.generated and module.skipped == []


def test_every_function_is_a_read_served_by_a_get():
    functions = _functions()
    assert {f["name"] for f in functions} == {"list_payments", "get_payment", "list_balance_transactions"}
    for function in functions:
        assert function["class"] == "read", function["name"]
        assert function.get("act", False) is False, function["name"]
        assert function["call"]["method"] == "GET", function["name"]
        assert "body" not in function["call"] and "confirm" not in function, function["name"]


def test_the_account_is_the_merchant_key_in_the_alma_auth_header():
    connector = _description()["connector"]
    assert connector["auth"] == {"kind": "api_key", "in": "header", "name": "Authorization", "key": "api_key",
                                 "prefix": "Alma-Auth "}
    assert connector["base_url"] == "https://api.getalma.eu/v1"


def test_no_function_looks_a_person_up():
    for function in _functions():
        assert "customer_email" not in function["input"]["properties"], function["name"]
        assert "customer_email" not in function["call"].get("query", {}).values(), function["name"]


def test_the_payments_list_never_serves_customer_data():
    fn = serve.function(_description(), "list_payments")
    served = serve.items_of(fn, [{"data": [_payment(), _payment()], "has_more": False}])
    assert len(served) == 2
    for payment in served:
        _assert_without_personal_data(payment)


def test_a_payment_never_serves_customer_data():
    fn = serve.function(_description(), "get_payment")
    _assert_without_personal_data(serve.shaped(_payment(), fn["output"]))


def test_both_payment_readers_strip_the_same_keys():
    by_name = {f["name"]: f for f in _functions() if f["name"] in PAYMENT_READERS}
    assert by_name["list_payments"]["output"]["strip"] == by_name["get_payment"]["output"]["strip"]
