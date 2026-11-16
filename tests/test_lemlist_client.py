"""Lead lifecycle additions to LemlistClient (create/launch/variables).

Mocks `requests.request` (the shared `_request` helper): verifies method/URL,
JSON body vs query params per endpoint, and that a 4xx surfaces as
`UpstreamHTTPError` with the upstream body intact (lemlist's launch-lead 400s
carry a structured error code an agent needs to see).
"""
from __future__ import annotations

import pytest

from oto.tools.lemlist import client as lm
from oto.tools.common.errors import UpstreamHTTPError


class _Resp:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.content = b"x"
        self.text = str(body)

    def json(self):
        return self._body


def test_create_lead_posts_body_and_no_flags_by_default(monkeypatch):
    captured = {}

    def fake_request(method, url, headers=None, **kwargs):
        captured.update(method=method, url=url, headers=headers, **kwargs)
        return _Resp(200, {"_id": "lea_123", "campaignId": "camp_1", "email": "a@acme.fr"})

    monkeypatch.setattr(lm.requests, "request", fake_request)
    c = lm.LemlistClient(api_key="k")
    out = c.create_lead("camp_1", {"email": "a@acme.fr", "firstName": "A"})

    assert out["_id"] == "lea_123"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/campaigns/camp_1/leads/")
    assert captured["json"] == {"email": "a@acme.fr", "firstName": "A"}
    assert captured["params"] == {}


def test_create_lead_flags_become_true_query_params(monkeypatch):
    captured = {}

    def fake_request(method, url, headers=None, **kwargs):
        captured.update(kwargs)
        return _Resp(200, {"_id": "lea_123"})

    monkeypatch.setattr(lm.requests, "request", fake_request)
    c = lm.LemlistClient(api_key="k")
    c.create_lead(
        "camp_1", {"email": "a@acme.fr"},
        deduplicate=True, find_email=True,
    )

    assert captured["params"] == {"deduplicate": "true", "findEmail": "true"}


def test_launch_lead_posts_to_review_path(monkeypatch):
    captured = {}

    def fake_request(method, url, headers=None, **kwargs):
        captured.update(method=method, url=url)
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(lm.requests, "request", fake_request)
    out = lm.LemlistClient(api_key="k").launch_lead("lea_8xJSc7sV7ggpiVnXe")

    assert out == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/leads/review/lea_8xJSc7sV7ggpiVnXe")


def test_launch_lead_400_raises_upstream_error_with_code(monkeypatch):
    monkeypatch.setattr(
        lm.requests, "request",
        lambda method, url, headers=None, **kw: _Resp(
            400, {"error": "CAMPAIGN_LEAD_REVIEW_LEAD_ALREADY_LAUNCHED",
                  "message": "Lead already launched"}),
    )
    with pytest.raises(UpstreamHTTPError) as exc:
        lm.LemlistClient(api_key="k").launch_lead("lea_1")

    assert exc.value.status_code == 400
    assert "CAMPAIGN_LEAD_REVIEW_LEAD_ALREADY_LAUNCHED" in str(exc.value)


def test_add_lead_variables_sends_query_params_not_body(monkeypatch):
    captured = {}

    def fake_request(method, url, headers=None, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(lm.requests, "request", fake_request)
    out = lm.LemlistClient(api_key="k").add_lead_variables(
        "lea_1", {"customField1": "Lemlist", "customField2": "Will Rule"},
    )

    assert out == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/leads/lea_1/variables")
    # En QUERY, pas en corps — ce que ce banc garde. Il comparait un dict, donc il
    # figeait l'encodage par défaut de `requests` (espace → `+`), qui était le
    # défaut lui-même : lemlist stocke ce `+` littéralement. La query est désormais
    # construite ici, en `%20` (cf. `_vars_query`).
    assert captured["params"] == "customField1=Lemlist&customField2=Will%20Rule"
    assert "json" not in captured


# --- enrichissement ----------------------------------------------------------


def _capture(monkeypatch, status=200, body=None):
    captured = {}

    def fake_request(method, url, headers=None, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return _Resp(status, {} if body is None else body)

    monkeypatch.setattr(lm.requests, "request", fake_request)
    return captured


def test_enrich_sends_actions_and_identity_as_query_params(monkeypatch):
    captured = _capture(monkeypatch, body={"id": "enr_1"})
    c = lm.LemlistClient(api_key="k")

    out = c.enrich(
        first_name="John", last_name="Lempire", company_domain="lempire.com",
        find_email=True, find_phone=True,
    )

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/enrich")
    # Query params, pas de corps JSON — c'est le contrat de cet endpoint.
    assert "json" not in captured
    params = captured["params"]
    # Les booléens partent en "true" : `True` se sérialiserait "True".
    assert params["findEmail"] == "true" and params["findPhone"] == "true"
    assert "verifyEmail" not in params and "linkedinEnrichment" not in params
    assert params["firstName"] == "John"
    assert params["companyDomain"] == "lempire.com"
    assert out == {"id": "enr_1"}


def test_enrich_without_any_action_raises_before_the_call(monkeypatch):
    captured = _capture(monkeypatch)
    c = lm.LemlistClient(api_key="k")

    with pytest.raises(ValueError):
        c.enrich(email="a@acme.fr")

    assert captured == {}  # rien n'est parti : pas de crédit dépensé


def test_get_enrichment_returns_not_found_body_instead_of_raising(monkeypatch):
    # 404 porte ici une charge utile légitime (`enrichmentStatus: not-found`),
    # pas une erreur : la lever masquerait un état terminal derrière une exception.
    _capture(monkeypatch, status=404, body={
        "enrichmentId": "enr_x", "enrichmentStatus": "not-found",
        "error": "Enrichment not found", "data": {},
    })
    c = lm.LemlistClient(api_key="k")

    out = c.get_enrichment("enr_x")

    assert out["enrichmentStatus"] == "not-found"


def test_get_enrichment_in_progress_202_is_a_normal_body(monkeypatch):
    _capture(monkeypatch, status=202, body={
        "enrichmentId": "enr_1", "enrichmentStatus": "in-progress", "data": {},
    })
    c = lm.LemlistClient(api_key="k")

    assert c.get_enrichment("enr_1")["enrichmentStatus"] == "in-progress"


def test_get_enrichment_still_raises_on_a_real_error(monkeypatch):
    _capture(monkeypatch, status=401, body={"error": "unauthorized"})
    c = lm.LemlistClient(api_key="k")

    with pytest.raises(UpstreamHTTPError) as e:
        c.get_enrichment("enr_1")
    assert e.value.status_code == 401


def test_enrich_lead_posts_to_the_lead_path(monkeypatch):
    captured = _capture(monkeypatch, body={"id": "enr_2"})
    c = lm.LemlistClient(api_key="k")

    c.enrich_lead("lea_1", linkedin_enrichment=True)

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/leads/lea_1/enrich")
    assert captured["params"] == {"linkedinEnrichment": "true"}


def test_enrich_lead_without_any_action_raises_before_the_call(monkeypatch):
    captured = _capture(monkeypatch)
    c = lm.LemlistClient(api_key="k")

    with pytest.raises(ValueError):
        c.enrich_lead("lea_1")

    assert captured == {}


def test_bulk_enrich_posts_a_json_array(monkeypatch):
    captured = _capture(monkeypatch, body=[{"id": "enr_1"}])
    c = lm.LemlistClient(api_key="k")

    items = [{"input": {"email": "a@acme.fr"}, "enrichmentRequests": ["verify"]}]
    out = c.bulk_enrich(items)

    assert captured["url"].endswith("/v2/enrichments/bulk")
    assert captured["json"] == items
    assert out == [{"id": "enr_1"}]


def test_bulk_action_vocabulary_is_not_a_snake_case_of_the_v1_flags():
    # v2 dit `verify`, v1 dit `verifyEmail` : une conversion mécanique enverrait
    # une action que lemlist ne connaît pas.
    assert lm.LemlistClient.ENRICH_BULK_ACTIONS["verify_email"] == "verify"
    assert lm.LemlistClient.ENRICH_FLAGS["verify_email"] == "verifyEmail"


def test_requests_carry_a_timeout(monkeypatch):
    captured = _capture(monkeypatch, body={"id": "enr_1"})
    c = lm.LemlistClient(api_key="k")

    c.enrich(email="a@acme.fr", find_phone=True)

    assert captured["timeout"] == lm._HTTP_TIMEOUT


# --- Campaign management -----------------------------------------------------
#
# Same bench as above (mocking `requests.request`): what is asserted is the
# OUTGOING call — verb, path, and which of body/query each parameter lands in —
# because that is what the lemlist contract fixes and what a silent mismatch
# breaks. Not replayed against a live key.


def test_list_campaigns_asks_for_v2_and_passes_filters(monkeypatch):
    captured = _capture(monkeypatch, body=[
        {"_id": "cam_1", "name": "Q3", "status": "running", "emoji": "🚀",
         "hasError": True, "errors": ["no sender"], "createdAt": "2026-01-01"},
    ])
    c = lm.LemlistClient(api_key="k")
    out = c.list_campaigns(limit=50, offset=100, status="running", sort_order="desc")

    assert captured["method"] == "GET"
    assert captured["url"].endswith("/campaigns")
    # `version=v2` is not optional decoration: the v1 shape is what comes back
    # without it.
    assert captured["params"]["version"] == "v2"
    assert captured["params"]["limit"] == 50
    assert captured["params"]["offset"] == 100
    assert captured["params"]["status"] == "running"
    assert captured["params"] == {
        "version": "v2", "limit": 50, "offset": 100, "status": "running",
        "sortBy": "createdAt", "sortOrder": "desc",
    }
    assert out[0].has_error is True
    assert out[0].errors == ["no sender"]
    assert out[0].created_at == "2026-01-01"


def test_list_campaigns_refuses_an_unknown_status(monkeypatch):
    _capture(monkeypatch, body=[])
    c = lm.LemlistClient(api_key="k")
    with pytest.raises(ValueError, match="status must be one of"):
        c.list_campaigns(status="finished")


def test_list_campaigns_accepte_les_DEUX_formes_de_reponse(monkeypatch):
    """La doc annonce un TABLEAU ; la v2 rend un objet. Relevé en live le
    31/08 : le code qui itérait le tableau parcourait les CLÉS du dict et
    cassait sur `c["_id"]`, ce qui faisait tomber `status()` d'entrée."""
    captured = _capture(monkeypatch, body={
        "campaigns": [{"_id": "cam_1", "name": "Q3", "status": "running"}],
        "pagination": {"totalRecords": 1, "currentPage": 1, "nextPage": 0},
    })
    c = lm.LemlistClient(api_key="k")
    out = c.list_campaigns()
    assert [x.id for x in out] == ["cam_1"]

    # Et la forme v1 (tableau nu) continue de passer.
    _capture(monkeypatch, body=[{"_id": "cam_2", "name": "Q4"}])
    assert [x.id for x in c.list_campaigns()] == ["cam_2"]
    assert captured  # le recorder a bien servi


def test_list_all_campaigns_se_fie_a_la_pagination_de_la_v2(monkeypatch):
    """`nextPage` dit où on en est ; le comptage de page courte se trompe sur
    une dernière page pleine."""
    pages = []

    def fake_request(method, url, headers=None, **kwargs):
        pages.append(kwargs["params"]["page"])
        # Page pleine à chaque fois, mais la v2 annonce la fin au 2e tour.
        next_page = 0 if len(pages) == 2 else len(pages) + 1
        return _Resp(200, {
            "campaigns": [{"_id": f"cam_{i}"} for i in range(100)],
            "pagination": {"nextPage": next_page},
        })

    monkeypatch.setattr(lm.requests, "request", fake_request)
    c = lm.LemlistClient(api_key="k")
    campaigns, truncated = c.list_all_campaigns(max_pages=5)

    assert truncated is False
    assert len(campaigns) == 200
    assert pages == [1, 2]


def test_list_all_campaigns_reports_truncation_rather_than_hiding_it(monkeypatch):
    """A capped list that looks complete is the failure mode worth a test."""
    pages = []

    def fake_request(method, url, headers=None, **kwargs):
        pages.append(kwargs["params"]["page"])
        return _Resp(200, {
            "campaigns": [{"_id": f"cam_{i}"} for i in range(100)],
            "pagination": {"nextPage": len(pages) + 1},
        })

    monkeypatch.setattr(lm.requests, "request", fake_request)
    c = lm.LemlistClient(api_key="k")
    campaigns, truncated = c.list_all_campaigns(max_pages=3)

    assert truncated is True
    assert len(campaigns) == 300
    # The fourth page is only a look past the ceiling: read, never returned.
    assert pages == [1, 2, 3, 4]


def _walk(monkeypatch, sizes, pagination=lambda page: {"nextPage": page + 1}):
    """Serve `sizes[page - 1]` campaigns per page (0 past the end) and record
    the pages asked for."""
    pages = []

    def fake_request(method, url, headers=None, **kwargs):
        page = kwargs["params"]["page"]
        pages.append(page)
        n = sizes[page - 1] if page <= len(sizes) else 0
        return _Resp(200, {
            "campaigns": [{"_id": f"cam_{page}_{i}"} for i in range(n)],
            "pagination": pagination(page),
        })

    monkeypatch.setattr(lm.requests, "request", fake_request)
    return pages


def test_list_all_campaigns_a_short_page_ends_the_walk_whatever_nextPage_says(
        monkeypatch):
    """lemlist announces a `nextPage` even on its last page
    (and on the empty pages after it), so the walk spent every allowed page
    and answered `truncated: true` for a 26-campaign workspace."""
    pages = _walk(monkeypatch, [26])
    c = lm.LemlistClient(api_key="k")
    campaigns, truncated = c.list_all_campaigns(max_pages=10)

    assert truncated is False
    assert len(campaigns) == 26
    assert pages == [1]


def test_list_all_campaigns_a_full_last_page_then_an_empty_one_is_complete(
        monkeypatch):
    pages = _walk(monkeypatch, [100, 100])
    c = lm.LemlistClient(api_key="k")
    campaigns, truncated = c.list_all_campaigns(max_pages=5)

    assert truncated is False
    assert len(campaigns) == 200
    assert pages == [1, 2, 3]


def test_list_all_campaigns_exactly_at_the_ceiling_is_not_truncated(monkeypatch):
    """A workspace of exactly `max_pages` full pages: the look past the ceiling
    finds nothing, so nothing was cut."""
    pages = _walk(monkeypatch, [100, 100])
    c = lm.LemlistClient(api_key="k")
    campaigns, truncated = c.list_all_campaigns(max_pages=2)

    assert truncated is False
    assert len(campaigns) == 200
    assert pages == [1, 2, 3]


def test_list_all_campaigns_truncated_when_the_ceiling_cuts_the_list(monkeypatch):
    pages = _walk(monkeypatch, [100, 100, 19])
    c = lm.LemlistClient(api_key="k")
    campaigns, truncated = c.list_all_campaigns(max_pages=2)

    assert truncated is True
    assert len(campaigns) == 200
    assert pages == [1, 2, 3]


@pytest.mark.parametrize("total, truncated", [(200, False), (300, True)])
def test_list_all_campaigns_trusts_totalRecords_when_lemlist_gives_it(
        monkeypatch, total, truncated):
    """With a total in the answer, no look past the ceiling is needed."""
    pages = _walk(monkeypatch, [100, 100, 100],
                  pagination=lambda page: {"nextPage": page + 1,
                                           "totalRecords": total})
    c = lm.LemlistClient(api_key="k")
    campaigns, was_truncated = c.list_all_campaigns(max_pages=2)

    assert was_truncated is truncated
    assert len(campaigns) == 200
    assert pages == [1, 2]


def test_list_all_campaigns_status_filter_does_not_set_truncated(monkeypatch):
    """The status filter is sent to lemlist, which pages the FILTERED list: a
    short page of running campaigns is the end of them, not a gap."""
    seen = []

    def fake_request(method, url, headers=None, **kwargs):
        seen.append(dict(kwargs["params"]))
        return _Resp(200, {
            "campaigns": [{"_id": f"cam_{i}", "status": "running"}
                          for i in range(10)],
            "pagination": {"nextPage": kwargs["params"]["page"] + 1},
        })

    monkeypatch.setattr(lm.requests, "request", fake_request)
    c = lm.LemlistClient(api_key="k")
    campaigns, truncated = c.list_all_campaigns(max_pages=5, status="running")

    assert truncated is False
    assert len(campaigns) == 10
    assert [p["status"] for p in seen] == ["running"]


def test_list_all_campaigns_stops_on_a_short_page(monkeypatch):
    """Repli quand la réponse ne porte PAS de pagination (forme v1)."""
    calls = {"n": 0}

    def fake_request(method, url, headers=None, **kwargs):
        calls["n"] += 1
        n = 100 if calls["n"] == 1 else 7
        return _Resp(200, [{"_id": f"cam_{i}"} for i in range(n)])

    monkeypatch.setattr(lm.requests, "request", fake_request)
    c = lm.LemlistClient(api_key="k")
    campaigns, truncated = c.list_all_campaigns()

    assert truncated is False
    assert len(campaigns) == 107
    assert calls["n"] == 2


def test_status_says_when_its_count_is_only_a_floor(monkeypatch):
    _capture(monkeypatch, body=[{"_id": f"cam_{i}"} for i in range(100)])
    c = lm.LemlistClient(api_key="k")
    assert c.status() == {
        "connected": True, "campaigns_count": 100, "campaigns_capped": True,
    }


def test_create_campaign_sends_only_what_was_asked(monkeypatch):
    captured = _capture(monkeypatch, body={"_id": "cam_1", "sequenceId": "seq_1"})
    c = lm.LemlistClient(api_key="k")
    c.create_campaign("Q3 outbound")

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/campaigns")
    assert captured["json"] == {"name": "Q3 outbound"}


def test_create_campaign_carries_timezone_and_auto_review(monkeypatch):
    captured = _capture(monkeypatch, body={"_id": "cam_1"})
    c = lm.LemlistClient(api_key="k")
    c.create_campaign(
        "Q3", timezone="America/New_York", auto_review=True,
        auto_review_conditions=["deliverable"])

    assert captured["json"] == {
        "name": "Q3", "timezone": "America/New_York",
        "autoReview": True, "autoReviewConditions": ["deliverable"],
    }


def test_auto_review_conditions_are_checked_locally(monkeypatch):
    _capture(monkeypatch)
    c = lm.LemlistClient(api_key="k")
    with pytest.raises(ValueError, match="unknown autoReviewConditions"):
        c.create_campaign("Q3", auto_review_conditions=["deliverable", "maybe"])
    with pytest.raises(ValueError, match="unknown autoReviewConditions"):
        c.update_campaign("cam_1", {"autoReviewConditions": ["nope"]})


def test_start_and_pause_are_posts_on_their_own_paths(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")

    c.start_campaign("cam_1")
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/campaigns/cam_1/start")

    c.pause_campaign("cam_1")
    assert captured["url"].endswith("/campaigns/cam_1/pause")


def test_duplicate_campaign_omits_the_name_when_not_given(monkeypatch):
    captured = _capture(monkeypatch, body={"_id": "cam_2"})
    c = lm.LemlistClient(api_key="k")

    c.duplicate_campaign("cam_1")
    assert captured["url"].endswith("/campaigns/cam_1/duplicate")
    assert captured["json"] == {}

    c.duplicate_campaign("cam_1", name="Q4 outbound")
    assert captured["json"] == {"name": "Q4 outbound"}


def test_statutes_is_a_get(monkeypatch):
    captured = _capture(monkeypatch, body={"name": "Q3", "statutes": []})
    c = lm.LemlistClient(api_key="k")
    c.get_campaign_statutes("cam_1")

    assert captured["method"] == "GET"
    assert captured["url"].endswith("/campaigns/cam_1/statutes")


# --- Sequence steps & A/B ----------------------------------------------------


def test_add_step_refuses_a_type_outside_the_vocabulary(monkeypatch):
    _capture(monkeypatch)
    c = lm.LemlistClient(api_key="k")
    with pytest.raises(ValueError, match="unknown step type"):
        c.add_step("seq_1", {"type": "carrierPigeon"})
    with pytest.raises(ValueError, match="needs a 'type'"):
        c.add_step("seq_1", {"subject": "hi"})
    with pytest.raises(ValueError, match="unknown conditionKey"):
        c.add_step("seq_1", {"type": "conditional", "conditionKey": "hasVibes"})


def test_add_step_posts_the_step_verbatim(monkeypatch):
    captured = _capture(monkeypatch, body={"_id": "stp_1"})
    c = lm.LemlistClient(api_key="k")
    step = {"type": "email", "subject": "hi {{firstName}}", "message": "…", "delay": 2}
    c.add_step("seq_1", step)

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/sequences/seq_1/steps")
    assert captured["json"] == step


def test_delete_step_is_a_delete_on_the_step_path(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")
    c.delete_step("seq_1", "stp_1")

    assert captured["method"] == "DELETE"
    assert captured["url"].endswith("/sequences/seq_1/steps/stp_1")


def test_ab_variant_verbs_share_one_path(monkeypatch):
    captured = _capture(monkeypatch, body={"stepId": "stp_1"})
    c = lm.LemlistClient(api_key="k")
    path = "/sequences/seq_1/steps/stp_1/ab-test"

    c.create_ab_variant("seq_1", "stp_1")
    assert (captured["method"], captured["url"].endswith(path)) == ("POST", True)

    c.get_ab_variant("seq_1", "stp_1")
    assert captured["method"] == "GET"

    c.update_ab_variant("seq_1", "stp_1", {"subject": "v2"})
    assert captured["method"] == "PATCH"
    assert captured["json"] == {"subject": "v2"}


def test_delete_ab_variant_puts_the_variant_in_the_QUERY(monkeypatch):
    """Body vs query is the whole contract here — a `json=` variant is ignored."""
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")

    c.delete_ab_variant("seq_1", "stp_1")
    assert captured["method"] == "DELETE"
    assert captured["params"] == {"variant": "B"}
    assert "json" not in captured

    c.delete_ab_variant("seq_1", "stp_1", variant="A")
    assert captured["params"] == {"variant": "A"}

    with pytest.raises(ValueError, match="variant must be"):
        c.delete_ab_variant("seq_1", "stp_1", variant="C")


def test_select_ab_winner_puts_the_variant_in_the_BODY(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")
    c.select_ab_winner("seq_1", "stp_1", "B")

    assert captured["url"].endswith("/sequences/seq_1/steps/stp_1/ab-test/winner")
    assert captured["json"] == {"variant": "B"}


# --- Schedules ---------------------------------------------------------------


def test_create_schedule_fills_every_required_field(monkeypatch):
    captured = _capture(monkeypatch, body={"_id": "skd_1"})
    c = lm.LemlistClient(api_key="k")
    c.create_schedule("Matinées")

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/schedules")
    assert captured["json"] == {
        "name": "Matinées", "timezone": "Europe/Paris",
        "start": "09:00", "end": "18:00", "weekdays": [1, 2, 3, 4, 5],
    }


def test_schedule_rejects_a_malformed_window(monkeypatch):
    _capture(monkeypatch)
    c = lm.LemlistClient(api_key="k")
    with pytest.raises(ValueError, match="start must be HH:mm"):
        c.create_schedule("x", start="9am")
    with pytest.raises(ValueError, match="end must be HH:mm"):
        c.create_schedule("x", end="25:00")
    with pytest.raises(ValueError, match="weekdays must be ints"):
        c.create_schedule("x", weekdays=[0, 1])
    with pytest.raises(ValueError, match="IANA"):
        c.create_schedule("x", timezone="CET+1")
    # The same checks apply on the PATCH path, on the keys actually sent.
    with pytest.raises(ValueError, match="start must be HH:mm"):
        c.update_schedule("skd_1", {"start": "nine"})


def test_campaign_schedules_keeps_the_documented_trailing_slash(monkeypatch):
    captured = _capture(monkeypatch, body=[])
    c = lm.LemlistClient(api_key="k")
    c.get_campaign_schedules("cam_1")

    assert captured["url"].endswith("/campaigns/cam_1/schedules/")

    c.associate_schedule("cam_1", "skd_1")
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/campaigns/cam_1/schedules/skd_1")


# --- Stats -------------------------------------------------------------------


def test_stats_v2_goes_through_the_v2_prefix(monkeypatch):
    captured = _capture(monkeypatch, body={"nbLeads": 12})
    c = lm.LemlistClient(api_key="k")
    c.get_campaign_stats_v2(
        "cam_1", start_date="2026-01-01T00:00:00.000Z",
        end_date="2026-02-01T00:00:00.000Z", channels=["email", "linkedin"])

    assert captured["url"].endswith("/api/v2/campaigns/cam_1/stats")
    assert captured["params"]["startDate"] == "2026-01-01T00:00:00.000Z"
    # Query side: a JSON array STRING, not repeated keys.
    assert captured["params"]["channels"] == '["email", "linkedin"]'


def test_batch_stats_sends_channels_as_a_real_array(monkeypatch):
    """Same vocabulary, two encodings — this is the one that is easy to get wrong."""
    captured = _capture(monkeypatch, body={"results": []})
    c = lm.LemlistClient(api_key="k")
    c.get_batch_campaign_stats(
        ["cam_1", "cam_2"], start_date="a", end_date="b", channels=["email"])

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v2/campaigns/stats/batch")
    assert captured["json"]["channels"] == ["email"]
    assert captured["json"]["campaignIds"] == ["cam_1", "cam_2"]


def test_stats_reject_bad_channels_ab_and_send_user(monkeypatch):
    _capture(monkeypatch, body={})
    c = lm.LemlistClient(api_key="k")
    with pytest.raises(ValueError, match="unknown channels"):
        c.get_campaign_stats_v2("cam_1", start_date="a", end_date="b",
                                channels=["carrier-pigeon"])
    with pytest.raises(ValueError, match="ab_selected must be"):
        c.get_campaign_stats_v2("cam_1", start_date="a", end_date="b", ab_selected="C")
    with pytest.raises(ValueError, match="both halves"):
        c.get_campaign_stats_v2("cam_1", start_date="a", end_date="b",
                                send_user="usr_1")
    with pytest.raises(ValueError, match="at most 100"):
        c.get_batch_campaign_stats([f"cam_{i}" for i in range(101)],
                                   start_date="a", end_date="b")
    with pytest.raises(ValueError, match="nothing to fetch"):
        c.get_batch_campaign_stats([], start_date="a", end_date="b")


def test_reports_join_the_ids_into_one_query_parameter(monkeypatch):
    captured = _capture(monkeypatch, body=[])
    c = lm.LemlistClient(api_key="k")
    c.get_campaign_reports(["cam_1", "cam_2"])

    assert captured["url"].endswith("/campaigns/reports")
    assert captured["params"] == {"campaignIds": "cam_1,cam_2"}
    with pytest.raises(ValueError, match="nothing to report on"):
        c.get_campaign_reports([])


# --- Couverture intégrale : les encodages qui ne se devinent pas ---------------
#
# 105 méthodes s'ajoutent d'un coup ; les tester une à une dirait surtout que le
# code fait ce qu'il fait. Ce qui est verrouillé ici est la POIGNÉE de contrats
# qu'une réécriture innocente casse sans bruit : où voyage l'identifiant (chemin,
# query ou corps — lemlist utilise les trois pour la MÊME ressource), ce qu'un
# paramètre omis vaut par défaut, et quelles routes rendent du texte et non du
# JSON.


def test_delete_lead_desinscrit_par_defaut_et_supprime_sur_demande(monkeypatch):
    """Le défaut est le geste DOUX, et le nom de la méthode dit l'autre."""
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")

    c.delete_lead("cam_1", "a@acme.fr")
    assert captured["method"] == "DELETE"
    assert captured["url"].endswith("/campaigns/cam_1/leads/a@acme.fr")
    assert captured["params"] is None  # aucun `action` ⇒ désinscription

    c.delete_lead("cam_1", "a@acme.fr", action="remove")
    assert captured["params"] == {"action": "remove"}

    c.unsubscribe_lead("cam_1", "a@acme.fr")
    assert captured["params"] is None


def test_les_variables_de_lead_voyagent_en_QUERY_pas_en_corps(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")

    c.update_lead_variables("lea_1", {"industry": "SaaS"})
    assert captured["method"] == "PATCH"
    assert captured["params"] == "industry=SaaS"
    assert "json" not in captured

    # À la suppression, c'est la PRÉSENCE de la clé qui est l'instruction.
    c.delete_lead_variables("lea_1", ["industry", "icp"])
    assert captured["method"] == "DELETE"
    assert captured["params"] == "industry=&icp="
    with pytest.raises(ValueError, match="nothing to erase"):
        c.delete_lead_variables("lea_1", [])


def test_pause_lead_sans_campagne_vise_TOUTES_les_campagnes(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")

    c.pause_lead("lea_1")
    assert captured["url"].endswith("/leads/pause/lea_1")
    assert captured["params"] is None  # portée LARGE, pas étroite

    c.pause_lead("lea_1", campaign_id="cam_1")
    assert captured["params"] == {"campaignId": "cam_1"}


def test_marquage_dinteret_change_de_route_selon_la_portee(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")

    c.mark_lead_interested("a@acme.fr")
    assert captured["url"].endswith("/leads/interested/a@acme.fr")

    c.mark_lead_interested("a@acme.fr", campaign_id="cam_1")
    assert captured["url"].endswith("/campaigns/cam_1/leads/a@acme.fr/interested")

    c.mark_lead_not_interested("a@acme.fr", campaign_id="cam_1")
    assert captured["url"].endswith("/campaigns/cam_1/leads/a@acme.fr/notinterested")


def test_upload_audio_est_la_seule_route_multipart(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")
    c.upload_lead_audio("lea_1", "stp_1", b"\x00\x01", filename="voix.mp3")

    assert captured["url"].endswith("/leads/audio")
    assert captured["params"] == {"leadId": "lea_1", "stepId": "stp_1"}
    assert captured["files"] == {"file": ("voix.mp3", b"\x00\x01")}


def test_les_exports_rendent_du_TEXTE_et_non_du_json(monkeypatch):
    """`.json()` sur un CSV lèverait sur une réponse pourtant parfaite."""
    captured = {}

    class _Csv(_Resp):
        def json(self):
            raise AssertionError("un export CSV ne doit pas être parsé en JSON")

    def fake_request(method, url, headers=None, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return _Csv(200, None)

    monkeypatch.setattr(lm.requests, "request", fake_request)
    c = lm.LemlistClient(api_key="k")

    assert c.export_unsubscribes() == "None"
    # Abréviation de lemlist : /unsubs/, pas /unsubscribes/.
    assert captured["url"].endswith("/unsubs/export")

    c.export_unsubscribed_variables()
    assert captured["url"].endswith("/v2/unsubscribes/exports/variables")
    c.export_unsubscribed_contacts()
    assert captured["url"].endswith("/v2/unsubscribes/exports/contacts")
    c.export_contact_list("lst_1", entity="company")
    assert captured["params"] == {"listId": "lst_1", "entity": "company"}


def test_export_campaign_leads_suit_le_format_demande(monkeypatch):
    captured = _capture(monkeypatch, body={"leads": []})
    c = lm.LemlistClient(api_key="k")

    out = c.export_campaign_leads("cam_1", format="json")
    assert out == {"leads": []}          # JSON demandé ⇒ JSON rendu
    # `state=all` par DÉFAUT : sans lui, l'API filtre tout et rend une liste
    # vide qui se lit « pas de leads » (relevé en live sur une campagne d'un
    # lead). `state=None` restaure le comportement brut de lemlist.
    assert captured["params"] == {"format": "json", "state": "all"}
    c.export_campaign_leads("cam_1", state=None)
    assert captured["params"] == {}
    with pytest.raises(ValueError, match="format is"):
        c.export_campaign_leads("cam_1", format="xlsx")


def test_bulk_unsubscribe_borne_les_10000(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")

    c.bulk_unsubscribe_variables(["a@x.fr", "b@x.fr"])
    assert captured["json"] == {"values": ["a@x.fr", "b@x.fr"]}
    with pytest.raises(ValueError, match="at most 10 000"):
        c.bulk_unsubscribe_variables([f"{i}@x.fr" for i in range(10001)])
    with pytest.raises(ValueError, match="nothing to unsubscribe"):
        c.bulk_unsubscribe_variables([])


def test_manage_contact_list_ajoute_par_defaut(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")

    c.manage_contact_list("lst_1", ["ctc_1"])
    assert captured["params"] is None    # pas d'action ⇒ AJOUT
    assert captured["json"] == {"contactIds": ["ctc_1"]}

    c.manage_contact_list("lst_1", ["ctc_1"], action="remove")
    assert captured["params"] == {"action": "remove"}
    with pytest.raises(ValueError, match='action is "remove"'):
        c.manage_contact_list("lst_1", ["ctc_1"], action="delete")


def test_le_meme_id_de_watchlist_voyage_a_trois_endroits(monkeypatch):
    """Corps au PATCH, query au DELETE, chemin sur les signaux — trois
    placements pour une seule ressource, tous documentés ainsi."""
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")

    c.update_watch_list("wl_1", {"name": "Levées FR"})
    assert captured["method"] == "PATCH"
    assert captured["url"].endswith("/watchlist")
    assert captured["json"] == {"name": "Levées FR", "watchListId": "wl_1"}

    c.delete_watch_list("wl_1")
    assert captured["method"] == "DELETE"
    assert captured["params"] == {"watchListId": "wl_1"}

    c.push_external_signals(
        "wl_1", contact={"linkedinUrl": "u"}, company={"domain": "x.fr", "name": "X"})
    assert captured["url"].endswith("/watchlist/wl_1/external-signals")


def test_update_task_met_lid_dans_le_corps(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")
    c.update_task("tsk_1", {"done": True})

    assert captured["url"].endswith("/tasks")   # aucun segment d'id
    assert captured["json"] == {"done": True, "id": "tsk_1"}


def test_list_tasks_serialise_ses_filtres_en_chaine_json(monkeypatch):
    captured = _capture(monkeypatch, body=[])
    c = lm.LemlistClient(api_key="k")
    c.list_tasks(page=2, filters=[{"filterId": "fullName", "value": "Ada"}])

    assert captured["params"]["page"] == 2
    assert captured["params"]["filters"] == '[{"filterId": "fullName", "value": "Ada"}]'

    # Et SURTOUT sans filtre : `filters` est documenté optionnel mais ne l'est
    # pas (400 « Malformed filters » en live). Le tableau vide part toujours.
    c.list_tasks()
    assert captured["params"] == {"filters": "[]"}


def test_get_activities_envoie_toujours_version_v2(monkeypatch):
    """La doc marque `version` REQUIS sur cette route ; l'appel l'omettait."""
    captured = _capture(monkeypatch, body=[])
    c = lm.LemlistClient(api_key="k")
    c.get_activities(campaign_id="cam_1", type="emailsReplied", lead_id="lea_1")

    assert captured["params"]["version"] == "v2"
    assert captured["params"]["type"] == "emailsReplied"
    assert captured["params"]["leadId"] == "lea_1"


def test_attach_inbox_labels_dit_explicitement_sil_ajoute_ou_remplace(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")

    c.attach_inbox_labels("ctc_1", ["lbl_1"])
    assert captured["json"] == {"labelIds": ["lbl_1"], "appendLabels": True}

    c.attach_inbox_labels("ctc_1", ["lbl_1"], append=False)
    assert captured["json"]["appendLabels"] is False


def test_les_brouillons_exigent_leur_proprietaire(monkeypatch):
    captured = _capture(monkeypatch, body={"_id": "drf_1"})
    c = lm.LemlistClient(api_key="k")

    c.create_draft("ctc_1", "usr_1", channel="email", content="hello", subject="hi")
    assert captured["url"].endswith("/inbox/ctc_1/drafts")
    assert captured["params"] == {"draftOwner": "usr_1"}
    assert captured["json"] == {"channel": "email", "content": "hello", "subject": "hi"}

    with pytest.raises(ValueError, match="channel must be one of"):
        c.create_draft("ctc_1", "usr_1", channel="pigeon", content="x")


def test_les_trois_envois_dinbox_portent_leurs_champs_obligatoires(monkeypatch):
    captured = _capture(monkeypatch, body={"ok": True})
    c = lm.LemlistClient(api_key="k")

    c.send_inbox_email(
        send_user_id="usr_1", send_user_email="me@x.fr",
        send_user_mailbox_id="mbx_1", message="hello", contact_id="ctc_1")
    assert captured["url"].endswith("/inbox/email")
    assert captured["json"]["sendUserMailboxId"] == "mbx_1"

    c.send_linkedin_message(
        send_user_id="usr_1", lead_id="lea_1", contact_id="ctc_1", message="hi")
    assert captured["url"].endswith("/inbox/linkedin")

    c.send_whatsapp_message(
        send_user_id="usr_1", send_user_whatsapp_account_id="wa_1",
        lead_id="lea_1", contact_id="ctc_1", message="hi")
    assert captured["json"]["sendUserWhatsappAccountId"] == "wa_1"


def test_une_alerte_de_delivrabilite_refuse_un_vocabulaire_inconnu(monkeypatch):
    _capture(monkeypatch, body={"_id": "alr_1"})
    c = lm.LemlistClient(api_key="k")
    base = dict(widget="warmup", metric="inboxRate", severity="critical",
                scope="global", threshold=90, comparison_operator="below",
                period_days=7, period_mode="rolling")

    c.create_deliverability_alert(**base)
    for bad in ({"metric": "vibes"}, {"scope": "planet"},
                {"comparison_operator": "近"}, {"period_mode": "sometimes"}):
        with pytest.raises(ValueError, match="must be one of"):
            c.create_deliverability_alert(**{**base, **bad})


def test_create_persona_et_watch_list_bornent_leurs_enums(monkeypatch):
    _capture(monkeypatch, body={"_id": "x"})
    c = lm.LemlistClient(api_key="k")
    with pytest.raises(ValueError, match="mode is"):
        c.create_persona("ICP", filters=[], mode="humans")
    with pytest.raises(ValueError, match="type must be one of"):
        c.create_watch_list("Levées", type="companyDidSomething")
    with pytest.raises(ValueError, match="signal_processing_type"):
        c.create_watch_list("Levées", type="companyRaisedFunds",
                            signal_processing_type="send_now")


def test_un_corps_non_json_annonce_json_ne_casse_pas_lappel(monkeypatch):
    """Relevé en live : `POST /v2/unsubscribes/contacts/{id}` répond 200 avec
    `Content-Type: application/json` et le TEXTE NU « Contact subscription
    updated ». `.json()` levait sur une réponse réussie."""
    class _Texte(_Resp):
        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    def fake_request(method, url, headers=None, **kwargs):
        return _Texte(200, None)

    monkeypatch.setattr(lm.requests, "request", fake_request)
    c = lm.LemlistClient(api_key="k")
    assert c.unsubscribe_contact("ctc_1") == {"message": "None"}


# --- Écarts doc↔API relevés EN LIVE le 2026-08-31 ------------------------------
#
# Chacun de ces cas est un champ que la doc lemlist annonce optionnel et que
# l'API exige, ou un défaut qui ne fait pas ce qu'il annonce. Aucun n'était
# visible sans clé réelle : c'est la raison d'être de ce bloc.


def test_create_task_refuse_localement_un_record_id_manquant(monkeypatch):
    """L'API répond `400 recordId is required` là où la doc le dit optionnel.
    Refusé au bord pour que le message dise QUOI fournir."""
    _capture(monkeypatch, body={})
    c = lm.LemlistClient(api_key="k")
    with pytest.raises(ValueError, match="record_id is required"):
        c.create_task(task_type="manual", assigned_to="usr_1", due_date="2026-09-05")


def test_create_watch_list_envoie_les_quatre_champs_faussement_optionnels(monkeypatch):
    """`filters`, `segmentType`, `signalProcessingType` et `activate` sont tous
    exigés. `activate` surtout : l'omettre rend une erreur qui accuse les deux
    autres champs, pourtant présents."""
    captured = _capture(monkeypatch, body={"data": {"_id": "wat_1"}})
    c = lm.LemlistClient(api_key="k")
    c.create_watch_list("Levées", type="companyRaisedFunds")

    assert captured["json"] == {
        "name": "Levées", "type": "companyRaisedFunds",
        "filters": [], "segmentType": "all",
        "signalProcessingType": "manual", "activate": False,
    }
    # `activate` part MÊME à False : c'est l'absence de la clé qui est refusée.
    assert "activate" in captured["json"]


def test_create_watch_list_visiteurs_pousse_vers_une_campagne_avec_persona(monkeypatch):
    """Une liste « visiteurs du site » rend surtout une ENTREPRISE, sans
    contact : pour alimenter une campagne, lemlist veut le persona à chercher
    (`personaId`) et la campagne (`campaignId`) dès la création."""
    captured = _capture(monkeypatch, body={"data": {"_id": "wat_1"}})
    c = lm.LemlistClient(api_key="k")
    c.create_watch_list("Visiteurs", type="companyEmployeeVisitedMyWebsite",
                        signal_processing_type="push_to_campaign",
                        persona_id="pdp_1", campaign_id="cam_1")

    assert captured["json"]["personaId"] == "pdp_1"
    assert captured["json"]["campaignId"] == "cam_1"
    assert captured["json"]["activate"] is False


def test_create_watch_list_refuse_persona_ou_campagne_hors_push_to_campaign(monkeypatch):
    _capture(monkeypatch, body={})
    c = lm.LemlistClient(api_key="k")
    for extra in ({"persona_id": "pdp_1"}, {"campaign_id": "cam_1"}):
        with pytest.raises(ValueError, match="push_to_campaign"):
            c.create_watch_list("Visiteurs", type="companyEmployeeVisitedMyWebsite",
                                **extra)


def test_list_tasks_envoie_toujours_filters(monkeypatch):
    captured = _capture(monkeypatch, body={"results": []})
    c = lm.LemlistClient(api_key="k")
    c.list_tasks()
    assert captured["params"] == {"filters": "[]"}


# ----------------------------------------------------------------------
# Booléens en query : ce que lit lemlist, c'est l'URL que `requests` prépare,
# pas le dict passé à `_request`. D'où une doublure au niveau du TRANSPORT
# (`HTTPAdapter.send`), qui voit la requête préparée telle qu'elle partirait.
# ----------------------------------------------------------------------

def _sent_query(call):
    """Fait un appel du client avec un transport doublé ; rend la query envoyée."""
    from unittest.mock import patch
    from urllib.parse import parse_qsl, urlsplit

    import requests

    sent = []

    def send(adapter, request, **kw):
        sent.append(request)
        r = requests.Response()
        r.status_code, r._content, r.request, r.url = 200, b"{}", request, request.url
        return r

    with patch.object(requests.adapters.HTTPAdapter, "send", send):
        call(lm.LemlistClient(api_key="k"))
    assert len(sent) == 1
    return parse_qsl(urlsplit(sent[0].url).query, keep_blank_values=True)


@pytest.mark.parametrize("value, sent", [(True, "true"), (False, "false")])
def test_activities_is_first_leaves_lowercase_in_the_url(value, sent):
    """`True`/`False` (majuscule Python) ne sont pas des booléens pour lemlist :
    un filtre qu'on croit poser, ou lever, ne serait ni l'un ni l'autre."""
    query = _sent_query(lambda c: c.get_activities(is_first=value))
    assert ("isFirst", sent) in query
    assert not [v for _, v in query if v in ("True", "False")]


def test_contacts_not_in_any_campaign_leaves_lowercase_in_the_url():
    query = _sent_query(lambda c: c.list_contacts(not_in_any_campaign=True))
    assert query == [("notInAnyCampaign", "true")]


def test_add_webhook_is_first_leaves_lowercase_in_the_url():
    query = _sent_query(lambda c: c.add_webhook(
        "https://example.com/hook", campaign_id="cam_1", is_first=True))
    assert query == [("campaignId", "cam_1"), ("isFirst", "true")]


# --- La liste v1 des désinscriptions ne se sert plus (404) ; le pool d'expéditeurs --


def _calls_of(monkeypatch, body=None):
    calls = []

    def fake_request(method, url, headers=None, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return _Resp(200, body if body is not None else {})

    monkeypatch.setattr(lm.requests, "request", fake_request)
    return calls


def test_list_unsubscribes_reads_the_v2_list_not_the_withdrawn_v1_route(monkeypatch):
    rows = [{"_id": "uns_1", "value": "example.test", "source": "api"}]
    calls = _calls_of(monkeypatch, rows)
    out = lm.LemlistClient(api_key="k").list_unsubscribes(limit=100, offset=200)
    assert out == rows
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == f"{lm.LemlistClient.BASE_URL}/v2/unsubscribes/variables"
    assert calls[0]["params"] == {"limit": 100, "offset": 200}


def test_list_unsubscribes_and_list_unsubscribed_variables_are_the_same_request(monkeypatch):
    calls = _calls_of(monkeypatch, [])
    c = lm.LemlistClient(api_key="k")
    c.list_unsubscribes(limit=10)
    c.list_unsubscribed_variables(limit=10)
    assert (calls[0]["method"], calls[0]["url"], calls[0]["params"]) == (
        calls[1]["method"], calls[1]["url"], calls[1]["params"])


SIX_MAILBOXES = [{"userId": "usr_1", "channels": [
    {"type": "email", "id": f"usm_{i}"} for i in range(1, 7)]}]


def test_replace_campaign_senders_puts_every_mailbox(monkeypatch):
    calls = _calls_of(monkeypatch, {"data": {"senders": []}})
    lm.LemlistClient(api_key="k").replace_campaign_senders("cam_1", SIX_MAILBOXES)
    assert calls[0]["method"] == "PUT"
    assert calls[0]["url"].endswith("/campaigns/cam_1/senders")
    assert calls[0]["json"] == {"senders": SIX_MAILBOXES}
    assert len(calls[0]["json"]["senders"][0]["channels"]) == 6


def test_add_campaign_senders_posts_and_get_reads_the_pool(monkeypatch):
    calls = _calls_of(monkeypatch, {"data": {"senders": []}})
    c = lm.LemlistClient(api_key="k")
    c.add_campaign_senders("cam_1", [{"userId": "usr_1"}])
    c.get_campaign_senders("cam_1")
    assert [(k["method"], k["url"].rsplit("/api/", 1)[1]) for k in calls] == [
        ("POST", "campaigns/cam_1/senders"), ("GET", "campaigns/cam_1/senders")]
    assert calls[0]["json"] == {"senders": [{"userId": "usr_1"}]}


def test_replace_with_an_empty_list_clears_the_pool_but_add_refuses_it(monkeypatch):
    calls = _calls_of(monkeypatch)
    c = lm.LemlistClient(api_key="k")
    c.replace_campaign_senders("cam_1", [])
    assert calls[0]["json"] == {"senders": []}
    with pytest.raises(ValueError, match="empty"):
        c.add_campaign_senders("cam_1", [])
    assert len(calls) == 1


@pytest.mark.parametrize("senders, match", [
    ([{"channels": [{"type": "email"}]}], "userId"),
    ([{"userId": "usr_1", "mailboxes": ["usm_1"]}], "unknown sender keys"),
    ([{"userId": "usr_1", "channels": []}], "non-empty"),
    ([{"userId": "usr_1", "channels": [{"type": "mail", "id": "usm_1"}]}], "channel type"),
    ([{"userId": "usr_1", "channels": [{"type": "email", "sendUserMailboxId": "usm_1"}]}], "unknown channel keys"),
    ([{"userId": "usr_1", "channels": [{"type": "email", "alias": "a@example.test"}]}], "alias"),
])
def test_the_pool_body_is_checked_before_any_call(monkeypatch, senders, match):
    calls = _calls_of(monkeypatch)
    with pytest.raises(ValueError, match=match):
        lm.LemlistClient(api_key="k").replace_campaign_senders("cam_1", senders)
    assert calls == []
