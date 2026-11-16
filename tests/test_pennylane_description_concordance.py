"""Pennylane: the client methods of the modules written with their description
send the request that the description declares.

For each function of `connectors/pennylane/connector.yaml` that one of these
modules serves (`bank`, `invoicing`, `reference`, `parties`, `exports`,
`documents`), each example input is passed to the method of the same name, and
the request that leaves the transport is compared with `call`: verb, path,
query (`filter` serialised as JSON, `limit` set by the page reader), body. The
older methods of the client do not follow their description (format, « Écart
avec le code ») and are not read here.
"""
import json
from pathlib import Path

import pytest
import yaml

from oto.tools.pennylane import PennylaneClient
from oto.tools.pennylane import bank, documents, exports, invoicing, parties, reference

DESCRIPTION = yaml.safe_load(
    (Path(__file__).parent.parent / "connectors" / "pennylane" / "connector.yaml").read_text())
MIXINS = (bank.BankMixin, invoicing.InvoicingMixin, reference.ReferenceMixin,
          parties.PartiesMixin, exports.ExportsMixin, documents.DocumentsMixin)
SERVED = {name for mixin in MIXINS for name in vars(mixin) if not name.startswith("_")}
FUNCTIONS = [f for f in DESCRIPTION["functions"] if f["name"] in SERVED]
BASE = "https://app.pennylane.com/api/external/v2"


class _Reponse:
    status_code = 200
    content = b"{}"
    text = "{}"

    def json(self):
        return {"items": [], "has_more": False, "next_cursor": None}


def _client():
    c = PennylaneClient(api_key="k", rate_limit_delay=0)
    c.requetes = []

    def request(method, url, params=None, json=None, timeout=None):
        c.requetes.append((method, url, params, json))
        return _Reponse()
    c.session.request = request
    return c


def _attendu(fn: dict, arguments: dict):
    call = fn["call"]
    path = call["path"]
    for name, value in arguments.items():
        path = path.replace("{" + name + "}", str(value))
    encode = call.get("encode", {})
    query = {api: (json.dumps(arguments[arg]) if encode.get(arg) == "json" else arguments[arg])
             for api, arg in (call.get("query") or {}).items() if arg in arguments}
    if "pagination" in fn:
        query.setdefault("limit", 100)
    corps = {api: arguments[arg] for api, arg in (call.get("body") or {}).items()
             if arg in arguments}
    return call["method"], BASE + path, query, corps


def test_every_function_of_the_new_modules_is_described():
    described = {f["name"] for f in DESCRIPTION["functions"]}
    assert SERVED - described == set()
    assert len(FUNCTIONS) == len(SERVED)


@pytest.mark.parametrize("fn, example", [
    (fn, example) for fn in FUNCTIONS for example in fn["examples"]
], ids=lambda value: value["name"] if "call" in value else value["title"])
def test_the_method_sends_what_the_description_declares(fn, example):
    c = _client()
    getattr(c, fn["name"])(**example["input"])
    methode, url, query, corps = _attendu(fn, example["input"])
    assert len(c.requetes) == 1
    sent_method, sent_url, sent_query, sent_body = c.requetes[0]
    assert (sent_method, sent_url) == (methode, url)
    assert (sent_query or {}) == query
    assert (sent_body or {}) == corps


def test_null_detaches_a_transaction_and_one_side_only():
    c = _client()
    c.update_transaction(30001, customer_id=None)
    assert c.requetes[0][3] == {"customer_id": None}
    with pytest.raises(ValueError, match="exactly one"):
        c.update_transaction(30001)
    with pytest.raises(ValueError, match="exactly one"):
        c.update_transaction(30001, customer_id=1, supplier_id=2)


def test_null_clears_a_supplier_setting_and_none_elsewhere_is_not_sent():
    c = _client()
    c.update_supplier(4001, establishment_no=None, name=None, iban="FR76")
    assert c.requetes[0][3] == {"establishment_no": None, "iban": "FR76"}


def test_changes_are_one_page_and_never_start_date_with_cursor():
    c = _client()
    page = c.list_changes("transactions", cursor="abc")
    assert page == {"items": [], "has_more": False, "next_cursor": None}
    with pytest.raises(ValueError, match="never both"):
        c.list_changes("transactions", start_date="2026-11-01T00:00:00Z", cursor="abc")
    with pytest.raises(ValueError, match="no changelog"):
        c.list_changes("bank_accounts")
    assert len(c.requetes) == 1


def test_a_list_reads_from_the_cursor_given():
    c = _client()
    c.list_bank_accounts(cursor="abc", limit=10)
    assert c.requetes[0][2] == {"cursor": "abc", "limit": 10}


@pytest.mark.parametrize("call", [
    lambda c: c.update_category(7101),
    lambda c: c.update_ledger_account(4101),
    lambda c: c.update_product(1201),
    lambda c: c.update_product(1201, price=1),
    lambda c: c.update_individual_customer(3002),
    lambda c: c.update_customer_contact(3001, 91),
    lambda c: c.create_customer_contact(3001, "J", "S", "j@example.com", phone="1"),
    lambda c: c.update_supplier(4001),
    lambda c: c.update_supplier_invoice_payment_status(8001, "partly"),
    lambda c: c.create_analytical_general_ledger_export("2025-01-01", "2025-12-31", "rows"),
    lambda c: c.list_bank_establishments(filter=[]),
])
def test_a_call_with_nothing_to_send_or_an_unknown_field_is_refused_before_the_call(call):
    c = _client()
    with pytest.raises(ValueError):
        call(c)
    assert c.requetes == []


def test_a_transaction_a_customer_and_a_supplier_carry_categories():
    c = _client()
    for target, path in (("transaction", "transactions/7/categories"),
                         ("customer", "customers/7/categories"),
                         ("supplier", "suppliers/7/categories")):
        c.categorize(target, 7, [{"id": 1, "weight": "1"}])
        assert c.requetes[-1][:2] == ("PUT", f"{BASE}/{path}")
