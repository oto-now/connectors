"""BrevoClient — verrouille le contrat HTTP construit par le client.

Mocke `requests.Session.request` : on vérifie verbe + URL + params + body, sans
réseau ni clé réelle. Cible en priorité ce que le client ABSORBE et qui pourrait
dériver en silence : les trois asymétries CRM (chemin, pagination, préfixe de
filtre), les gardes d'entrée, et le décodage d'un 204 sans corps.
"""
import json

import pytest

from oto.tools.brevo import BrevoClient
from oto.tools.common import UpstreamHTTPError

BASE = "https://api.brevo.com/v3"


class _Resp:
    def __init__(self, payload=None, status_code=200, raw=None):
        self.status_code = status_code
        self._payload = payload
        self.text = raw if raw is not None else ""
        if raw is not None:
            self.content = raw.encode()
        else:
            self.content = b"" if payload is None else json.dumps(payload).encode()

    def json(self):
        if self.content == b"":
            raise ValueError("no content")
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def calls(monkeypatch):
    """Capture les appels HTTP et renvoie une réponse 200 vide par défaut."""
    seen = []

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url, **kwargs})
        return _Resp({"ok": True})

    monkeypatch.setattr("requests.Session.request", _request)
    return seen


@pytest.fixture
def client():
    return BrevoClient(api_key="xkeysib-test")


def test_auth_header_is_api_key(client):
    assert client.session.headers["api-key"] == "xkeysib-test"


def test_clean_drops_none(client, calls):
    client.list_contacts(limit=10, sort=None, segment_id=None)
    params = calls[0]["params"]
    assert params == {"limit": 10, "offset": 0}
    assert "sort" not in params and "segmentId" not in params


def test_list_contacts_caps_limit_and_passes_arrays(client, calls):
    client.list_contacts(limit=99999, list_ids=[1, 2], filter='equals(FIRSTNAME,"Alex")')
    p = calls[0]["params"]
    assert p["limit"] == 1000                       # plafond Brevo
    assert p["listIds"] == [1, 2]                   # encodé listIds=1&listIds=2
    assert p["filter"] == 'equals(FIRSTNAME,"Alex")'


def test_empty_body_204_returns_dict(client, calls, monkeypatch):
    monkeypatch.setattr("requests.Session.request",
                        lambda *a, **k: _Resp(payload=None, status_code=204))
    assert client.update_contact("a@b.c", attributes={"NOM": "X"}) == {}


def test_non_json_body_is_wrapped(client, monkeypatch):
    monkeypatch.setattr("requests.Session.request",
                        lambda *a, **k: _Resp(payload=None, raw="plain text"))
    assert client.get_account() == {"raw": "plain text"}


def test_upstream_error_is_typed(client, monkeypatch):
    monkeypatch.setattr(
        "requests.Session.request",
        lambda *a, **k: _Resp({"code": "unauthorized"}, status_code=401))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.get_account()
    assert exc.value.status_code == 401
    assert exc.value.service == "brevo"


# --- Listes ------------------------------------------------------------------

def test_list_lists_switches_path_for_folder(client, calls):
    client.list_lists()
    client.list_lists(folder_id=7)
    assert calls[0]["url"] == f"{BASE}/contacts/lists"
    assert calls[1]["url"] == f"{BASE}/contacts/folders/7/lists"


def test_add_to_list_rejects_mixed_identifiers(client, calls):
    with pytest.raises(ValueError, match="ONE identifier type"):
        client.add_to_list(1, emails=["a@b.c"], ids=[2])
    assert calls == []


def test_add_to_list_rejects_no_identifier(client):
    with pytest.raises(ValueError):
        client.add_to_list(1)


def test_remove_from_list_all(client, calls):
    client.remove_from_list(3, all_contacts=True)
    assert calls[0]["url"] == f"{BASE}/contacts/lists/3/contacts/remove"
    assert calls[0]["json"] == {"all": True}


# --- CRM : les trois asymétries absorbées ------------------------------------

def test_crm_companies_paginate_by_page(client, calls):
    client.crm_list("companies", limit=50, offset=100)
    p = calls[0]["params"]
    assert calls[0]["url"] == f"{BASE}/companies"    # pas /crm/companies
    assert p["page"] == 3 and "offset" not in p


def test_crm_deals_paginate_by_offset(client, calls):
    client.crm_list("deals", limit=50, offset=100)
    p = calls[0]["params"]
    assert calls[0]["url"] == f"{BASE}/crm/deals"
    assert p["offset"] == 100 and "page" not in p


def test_crm_filter_prefix_differs_by_entity(client, calls):
    client.crm_list("deals", filters={"attributes.deal_name": "Acme"})
    client.crm_list("tasks", filters={"status": "done"})
    client.crm_list("notes", filters={"entity": "deals"})
    assert "filters[attributes.deal_name]" in calls[0]["params"]
    assert "filter[status]" in calls[1]["params"]      # singulier pour tasks
    assert calls[2]["params"]["entity"] == "deals"     # plat pour notes


def test_crm_unknown_entity(client):
    with pytest.raises(ValueError, match="unknown entity"):
        client.crm_list("tickets")


def test_crm_create_checks_required_fields(client, calls):
    with pytest.raises(ValueError, match="taskTypeId, date"):
        client.crm_create("tasks", {"name": "Rappeler"})
    assert calls == []


def test_crm_create_deal(client, calls):
    client.crm_create("deals", {"name": "Acme", "attributes": {"amount": 1000}})
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == f"{BASE}/crm/deals"
    assert calls[0]["json"]["name"] == "Acme"


def test_crm_link_maps_complementary_object(client, calls):
    client.crm_link("deals", "d1", link_ids=["c1"], link_contact_ids=[7])
    client.crm_link("companies", "c1", link_ids=["d1"])
    assert calls[0]["json"] == {"linkContactIds": [7], "linkCompanyIds": ["c1"]}
    assert calls[1]["json"] == {"linkDealsIds": ["d1"]}


def test_crm_link_rejects_tasks(client):
    with pytest.raises(ValueError, match="deals and companies"):
        client.crm_link("tasks", "t1", link_contact_ids=[1])


def test_crm_attributes_rejects_notes(client):
    with pytest.raises(ValueError):
        client.crm_attributes("notes")


# --- Transactionnel ----------------------------------------------------------

def test_send_email_template_mode(client, calls):
    client.send_email(to=[{"email": "a@b.c"}], template_id=12, params={"NOM": "X"})
    body = calls[0]["json"]
    assert calls[0]["url"] == f"{BASE}/smtp/email"
    assert body == {"to": [{"email": "a@b.c"}], "templateId": 12,
                    "params": {"NOM": "X"}}


def test_transactional_report_switches_endpoint(client, calls):
    client.transactional_report()
    client.transactional_report(by_day=True, days=7)
    assert calls[0]["url"].endswith("/smtp/statistics/aggregatedReport")
    assert calls[1]["url"].endswith("/smtp/statistics/reports")


def test_list_blocked_domains_switch(client, calls):
    client.list_blocked()
    client.list_blocked(domains=True)
    assert calls[0]["url"].endswith("/smtp/blockedContacts")
    assert calls[1]["url"].endswith("/smtp/blockedDomains")


def test_list_templates_single(client, calls):
    client.list_templates(template_id=5)
    assert calls[0]["url"] == f"{BASE}/smtp/templates/5"


def test_get_account_never_returns_the_relay_login_nor_the_automation_key(client, monkeypatch):
    """Le login du relais SMTP et la clé du suivi d'automatisation ne sortent jamais du client ; le reste du compte,
    relais compris, reste lisible."""
    account = {
        "email": "owner@acme.example", "companyName": "Acme",
        "plan": [{"type": "free", "creditsType": "sendLimit", "credits": 300}],
        "relay": {"enabled": True, "data": {"userName": "smtp-login@acme.example", "relay": "smtp.example.test",
                                            "port": 587}},
        "marketingAutomation": {"enabled": True, "key": "tracker-key-0"},
    }
    monkeypatch.setattr("requests.Session.request", lambda self, method, url, **kw: _Resp(account))
    got = client.get_account()
    assert "smtp-login" not in json.dumps(got) and "tracker-key-0" not in json.dumps(got)
    assert got["relay"] == {"enabled": True, "data": {"relay": "smtp.example.test", "port": 587}}
    assert got["marketingAutomation"] == {"enabled": True}
    assert got["plan"] == account["plan"] and got["email"] == "owner@acme.example"


def test_get_account_without_relay_nor_automation_is_returned_as_is(client, monkeypatch):
    monkeypatch.setattr("requests.Session.request",
                        lambda self, method, url, **kw: _Resp({"email": "o@acme.example", "relay": None}))
    assert client.get_account() == {"email": "o@acme.example", "relay": None}


def test_delete_list_and_delete_contact(client, calls):
    client.delete_list(3)
    client.delete_contact("42", identifier_type="contact_id")
    client.delete_contact("marie@example.test")
    assert [(c["method"], c["url"], c.get("params")) for c in calls] == [
        ("DELETE", f"{BASE}/contacts/lists/3", None),
        ("DELETE", f"{BASE}/contacts/42", {"identifierType": "contact_id"}),
        ("DELETE", f"{BASE}/contacts/marie@example.test", None),
    ]


# --- Campagnes : les writes dangereux ne sont pas exposés ---------------------

@pytest.mark.parametrize("forbidden", [
    "send_campaign", "send_now", "delete_campaign", "delete_template", "delete_hardbounces", "set_campaign_status",
])
def test_destructive_writes_are_absent(client, forbidden):
    assert not hasattr(client, forbidden)


def test_create_campaign_stays_draft_without_schedule(client, calls):
    client.create_campaign(name="N", sender={"email": "a@b.c"}, subject="S",
                           html_content="<p/>", recipients={"listIds": [1]})
    assert "scheduledAt" not in calls[0]["json"]


class _Redirige:
    """Une 3xx vers un autre hôte, dont le `Location` garde la clé en query."""
    status_code = 302
    headers = {"Location": "https://ailleurs.test/v1/x?api_key=SECRET#f"}
    content = b""
    text = ""
    ok = False

    def json(self):
        return {}

    def raise_for_status(self):
        return None


def test_une_redirection_est_refusee_jamais_suivie(monkeypatch):
    """La clé part dans un en-tête maison que `requests` ne retire pas sur un changement
    d'hôte : une 3xx est refusée (`RedirectRefused`), jamais suivie ni retentée, et
    sa cible est réduite à schéma, hôte et chemin — jamais sa query."""
    from oto.tools.http.client import RedirectRefused
    vus = []

    def fake(method, url, **kw):
        vus.append(kw)
        return _Redirige()

    c = BrevoClient(api_key="k")
    c.session.request = fake
    with pytest.raises(RedirectRefused) as e:
        c.get_account()
    assert vus[0]["allow_redirects"] is False
    assert e.value.location == "https://ailleurs.test/v1/x"
    assert "SECRET" not in str(e.value)
    assert len(vus) == 1
