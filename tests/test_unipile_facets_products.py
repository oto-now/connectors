"""Formes de facettes par PRODUIT (contrat API v2) + account_alive + reconnect.

location/company/industry n'ont PAS la même forme selon l'API :
  classic         → liste plate d'ids (inclusion seule)
  sales_navigator → {include, exclude}
  recruiter       → [{id, priority?}]
Envoyer la forme classic à recruiter/SN renvoyait 400 « Expected object »."""
import pytest

from oto.tools.unipile import UnipileClient
from oto.tools.unipile.client import UnipileError


def _client(rec=None):
    c = UnipileClient(api_key="k", account_id="acc")

    def fake(method, path, params=None, json=None, timeout=None):
        if rec is not None:
            rec.append({"method": method, "path": path, "json": json})
        return {"data": []}

    c._request = fake  # type: ignore[method-assign]
    c.resolve_facet = lambda ft, kw, limit=100: [{"id": f"id-{kw}", "title": kw}]  # type: ignore
    return c


# ---- location par produit -------------------------------------------------

def test_location_classic_flat_ids():
    rec = []
    _client(rec).search(keywords="x", location=["Paris"], api="classic")
    assert rec[0]["json"]["location"] == ["id-Paris"]


def test_location_recruiter_objects():
    rec = []
    _client(rec).search(keywords="x", location=["Paris"], api="recruiter")
    assert rec[0]["json"]["location"] == [{"id": "id-Paris"}]


def test_location_sales_navigator_include_object():
    rec = []
    _client(rec).search(keywords="x", location=["Paris"], api="sales_navigator")
    assert rec[0]["json"]["location"] == {"include": ["id-Paris"]}


# ---- company (people → current_company) -----------------------------------

def test_company_recruiter_objects():
    rec = []
    _client(rec).search(keywords="x", company=["Acme"], api="recruiter")
    assert rec[0]["json"]["current_company"] == [{"id": "id-Acme"}]


# ---- industry include/exclude ---------------------------------------------

def test_industry_classic_exclude_raises():
    with pytest.raises(UnipileError) as e:
        _client().search(keywords="x", industry={"include": ["Tech"], "exclude": ["Bank"]})
    assert "exclude" in str(e.value) and "classic" in str(e.value)


def test_industry_sales_navigator_include_exclude():
    rec = []
    _client(rec).search(keywords="x", api="sales_navigator",
                        industry={"include": ["Tech"], "exclude": ["Bank"]})
    assert rec[0]["json"]["industry"] == {"include": ["id-Tech"], "exclude": ["id-Bank"]}


def test_industry_recruiter_is_object():
    # recruiter industry = objet {include, exclude} (comme sales_nav), PAS [{id}]
    # (vérifié live : `[{id}]` → 400 "Expected object").
    rec = []
    _client(rec).search(keywords="x", api="recruiter",
                        industry={"include": ["Tech"], "exclude": ["Bank"]})
    assert rec[0]["json"]["industry"] == {"include": ["id-Tech"], "exclude": ["id-Bank"]}


def test_skills_recruiter_name_holds_id():
    # recruiter skills = [{name: <id>}] — le champ `name` porte l'ID (vérifié live :
    # `[{name: libellé}]` ne filtre pas ; `[{id,...}]` → 400). Exclusion = priority.
    rec = []
    _client(rec).search(keywords="x", api="recruiter",
                        skills={"include": ["Excel"], "exclude": ["VBA"]})
    assert rec[0]["json"]["skills"] == [
        {"name": "id-Excel"}, {"name": "id-VBA", "priority": "DOESNT_HAVE"}]


# ---- account_alive --------------------------------------------------------

def test_account_alive(monkeypatch):
    c = UnipileClient(api_key="k", account_id="acc")
    monkeypatch.setattr(c.session, "request",
                        lambda *a, **k: type("R", (), {"status_code": 200})())
    assert c.account_alive("acc_x") is True
    monkeypatch.setattr(c.session, "request",
                        lambda *a, **k: type("R", (), {"status_code": 401})())
    assert c.account_alive("acc_x") is False


# ---- reconnect ------------------------------------------------------------

# createAuthLink v2 = anyOf of two `additionalProperties: false` bodies (API 2.67).
_CREATE_KEYS = {"expires_on", "redirect_uri", "providers", "state", "config", "domain",
                "account_scope_id"}
_RECONNECT_KEYS = {"expires_on", "redirect_uri", "account_id", "state", "config", "domain"}


def test_hosted_auth_reconnect_type_and_account():
    rec = []
    c = UnipileClient(api_key="k", account_id="acc")

    def fake(method, path, params=None, json=None, timeout=None):
        rec.append(json)
        return {"link": "https://auth.unipile.com/?t=x"}
    c._request = fake  # type: ignore[method-assign]
    c.hosted_auth_link(providers=["LINKEDIN"], premium="recruiter", name="nonce",
                       success_redirect_url="https://x/ok",
                       reconnect_account="acc_existing")
    body = rec[0]
    # v2: the variant is picked by `account_id` — no `type`, no `providers`,
    # no v1 `reconnect_account` (live 400 on 2026-11-11/12).
    assert body["account_id"] == "acc_existing"
    assert set(body) <= _RECONNECT_KEYS, set(body) - _RECONNECT_KEYS
    assert "providers" not in body and "type" not in body
    assert "state" not in body and "name" not in body
    assert body["config"]["linkedin"]["products"] == ["classic", "recruiter"]


def test_hosted_auth_create_body_matches_v2_schema():
    rec = []
    c = UnipileClient(api_key="k", account_id="acc")

    def fake(method, path, params=None, json=None, timeout=None):
        rec.append(json)
        return {"link": "u"}
    c._request = fake  # type: ignore[method-assign]
    c.hosted_auth_link(providers=["LINKEDIN"], name="n", notify_url="https://x/hook",
                       success_redirect_url="https://x/ok", premium="sales_navigator",
                       allow_cookies=True)
    body = rec[0]
    assert set(body) <= _CREATE_KEYS, set(body) - _CREATE_KEYS
    assert {"expires_on", "redirect_uri", "providers"} <= set(body)
    assert "account_id" not in body
