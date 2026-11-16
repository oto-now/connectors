"""Les vues d'une page de la base partagée de lemlist (`fields`, `compact`) : appliquées par le client à chaque élément
de `results`, jamais envoyées à lemlist ; sans vue, la page de lemlist telle quelle.

Les enregistrements ci-dessous sont inventés, à la forme de la référence de lemlist (developer.lemlist.com,
« Search people database » et « Search companies database »), sauf la liste des offres d'une société (`job_posts`,
`posted_at`), un nom d'exemple : la vue compacte lit ainsi toute liste sous une clé qui contient `job`.
"""
from __future__ import annotations

import copy
import json

import pytest

from oto.tools.lemlist import client as lm
from oto.tools.lemlist import database_views as dv


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.content = b"x"
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _person(i: int) -> dict:
    position = {
        "title": "Head of Sales", "title_normalized": "head of sales", "date_from": "2024-01", "date_to": None,
        "description": "x" * 2000, "company_name": f"Example {i}", "company_domain": f"example{i}.test",
        "company_linkedin_url": f"https://www.example.test/company/e{i}", "company_employee_count": 120,
        "company_description": "y" * 3000, "technologies": [{"name": f"t{n}", "first_seen_at": "2025-01-01"}
                                                            for n in range(100)],
        "active_job_post": {"total": "3", "count_by_department": {"sales": "2", "engineering": "1"}},
    }
    past = {**position, "title": "Account Executive", "date_from": "2020-01", "date_to": "2023-12",
            "company_name": "Old Example"}
    return {
        "lead_id": i, "_id": f"p-{i}", "_score": 1.0, "full_name": f"Marie Martin {i}", "title": "Head of Sales",
        "country": "France", "location": "Paris", "seniority": "Director",
        "lead_linkedin_url": f"https://www.example.test/in/marie{i}", "summary": "z" * 3000,
        "skills": [{"skill": f"s{n}", "skill_id": n} for n in range(50)],
        "education": [{"title": "School", "description": "w" * 1000}],
        "experiences": [position, past, copy.deepcopy(past)],
    }


def _company(i: int) -> dict:
    return {
        "company_id": f"c-{i}", "_id": f"c-{i}", "_score": 1.0, "company_name": f"Example {i}",
        "company_domain": f"example{i}.test", "company_website_url": f"https://example{i}.test",
        "company_linkedin_url": f"https://www.example.test/company/e{i}", "company_employee_count": "300",
        "company_size": "201-500", "company_headquarters_country": "France", "company_industry": "Software",
        "company_description": "d" * 3000, "company_logo_url": "https://img.example.test/l.png",
        "techno_used_array": [f"t{n}" for n in range(200)],
        "technologies": [{"name": f"t{n}", "scoring": "CONFIRMED", "first_seen_at": "2025-01-01"}
                         for n in range(200)],
        "job_posts": [{"title": f"Job {n}", "posted_at": f"2026-{1 + n % 9:02d}-{1 + n % 28:02d}",
                       "description": "j" * 800, "location": "Paris", "skills": ["a", "b"]}
                      for n in range(200)],
    }


def _page(records: list) -> dict:
    return {"results": records, "total": 1234, "took": 12, "page": 1, "size": len(records),
            "search": "lsh_00000000000000000", "limitation": 1998, "team": "tea_0"}


@pytest.fixture
def calls(monkeypatch):
    sent: list = []
    answer: dict = {}

    def fake_request(method, url, headers=None, **kwargs):
        sent.append({"method": method, "url": url, **kwargs})
        return _Resp(answer["body"])

    monkeypatch.setattr(lm.requests, "request", fake_request)
    monkeypatch.setattr(lm.LemlistClient, "_rate_limit", lambda self: None)
    return sent, answer


# --- without a view: lemlist's page, untouched ------------------------------------------------------------------------

def test_sans_vue_la_page_est_celle_de_lemlist(calls):
    sent, answer = calls
    answer["body"] = _page([_person(1)])
    out = lm.LemlistClient(api_key="k").search_people_database(search="sales", size=1)
    assert out == _page([_person(1)])
    assert sent[0]["json"] == {"search": "sales", "size": 1}


def test_sans_vue_le_meme_objet():
    page = _page([_company(1)])
    assert dv.shape_page(page, dv.view_of(), "companies") is page


def test_la_vue_ne_part_jamais_chez_lemlist(calls):
    sent, answer = calls
    answer["body"] = _page([])
    c = lm.LemlistClient(api_key="k")
    c.search_people_database(filters=[], size=3, fields=["full_name"], compact=True)
    c.search_companies_database(filters=[], size=50, fields=["company_name"], compact=True)
    assert sent[0]["json"] == {"filters": [], "size": 3}
    assert sent[1]["json"] == {"filters": [], "size": 50}


# --- fields ---------------------------------------------------------------------------------------------------------

def test_fields_garde_les_cles_nommees_et_l_enveloppe(calls):
    sent, answer = calls
    answer["body"] = _page([_company(1), _company(2)])
    out = lm.LemlistClient(api_key="k").search_companies_database(
        size=2, fields=["company_name", "company_domain", "absent_key"])
    assert out["results"] == [{"company_name": "Example 1", "company_domain": "example1.test"},
                              {"company_name": "Example 2", "company_domain": "example2.test"}]
    assert {k: out[k] for k in ("total", "page", "size", "search")} == {
        "total": 1234, "page": 1, "size": 2, "search": "lsh_00000000000000000"}
    assert "omitted_fields" not in out


def test_fields_a_points_traverse_les_listes():
    page = _page([_person(1)])
    out = dv.shape_page(page, dv.view_of(["full_name", "experiences.title", "experiences.active_job_post.total"]),
                        "people")
    assert out["results"] == [{"full_name": "Marie Martin 1", "experiences": [
        {"title": "Head of Sales", "active_job_post": {"total": "3"}},
        {"title": "Account Executive", "active_job_post": {"total": "3"}},
        {"title": "Account Executive", "active_job_post": {"total": "3"}},
    ]}]


def test_fields_un_chemin_sous_un_autre_garde_le_tout():
    out = dv.shape_page(_page([_person(1)]), dv.view_of(["experiences.title", "experiences"]), "people")
    assert out["results"][0]["experiences"] == _person(1)["experiences"]


def test_fields_une_valeur_nulle_ou_scalaire_reste_telle_quelle():
    page = _page([{"full_name": "A", "experiences": None}, {"full_name": "B", "experiences": [None, 3, {"x": 1}]}])
    out = dv.shape_page(page, dv.view_of(["experiences.title"]), "people")
    assert out["results"] == [{"experiences": None}, {"experiences": [None, 3, {}]}]


def test_la_page_recue_n_est_pas_modifiee():
    page = _page([_person(1)])
    before = copy.deepcopy(page)
    dv.shape_page(page, dv.view_of(["full_name"], compact=True), "people")
    assert page == before


@pytest.mark.parametrize("fields", [[], "full_name", [""], ["a..b"], [".a"], ["a."], [3], {"a": 1}])
def test_fields_mal_formes_refuses_avant_l_appel(calls, fields):
    sent, answer = calls
    answer["body"] = _page([])
    with pytest.raises(ValueError):
        lm.LemlistClient(api_key="k").search_people_database(fields=fields)
    assert sent == []


def test_compact_doit_etre_un_booleen(calls):
    sent, _answer = calls
    with pytest.raises(ValueError):
        lm.LemlistClient(api_key="k").search_companies_database(compact="yes")
    assert sent == []


# --- compact ----------------------------------------------------------------------------------------------------------

def test_compact_une_personne_garde_ses_postes_actuels():
    out = dv.shape_page(_page([_person(1)]), dv.view_of(compact=True), "people")
    person = out["results"][0]
    assert person == {
        "lead_id": 1, "full_name": "Marie Martin 1", "title": "Head of Sales", "seniority": "Director",
        "country": "France", "location": "Paris", "lead_linkedin_url": "https://www.example.test/in/marie1",
        "experiences": [{
            "title": "Head of Sales", "date_from": "2024-01", "company_name": "Example 1",
            "company_domain": "example1.test", "company_linkedin_url": "https://www.example.test/company/e1",
            "company_employee_count": 120,
            "active_job_post": {"total": "3", "count_by_department": {"sales": "2", "engineering": "1"}},
        }],
    }
    assert out["omitted_fields"] == ["_id", "_score", "education", "skills", "summary"]


def test_compact_une_societe_et_ses_offres_reduites():
    out = dv.shape_page(_page([_company(1)]), dv.view_of(compact=True), "companies")
    company = out["results"][0]
    assert company["company_name"] == "Example 1"
    assert company["company_domain"] == "example1.test"
    assert company["company_employee_count"] == "300"
    assert company["company_headquarters_country"] == "France"
    assert company["company_linkedin_url"] == "https://www.example.test/company/e1"
    assert "technologies" not in company and "company_description" not in company
    assert company["job_posts_total"] == 200
    posts = company["job_posts"]
    assert len(posts) == dv.JOB_POSTS_KEPT
    assert all(set(p) == {"title", "posted_at"} for p in posts)
    dates = [p["posted_at"] for p in posts]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == max(p["posted_at"] for p in _company(1)["job_posts"])
    assert "technologies" in out["omitted_fields"] and "job_posts" not in out["omitted_fields"]


def test_compact_et_fields_les_champs_nommes_s_ajoutent():
    view = dv.view_of(["technologies.name", "company_description"], compact=True)
    company = dv.shape_page(_page([_company(1)]), view, "companies")["results"][0]
    assert company["company_name"] == "Example 1"
    assert company["technologies"][0] == {"name": "t0"}
    assert company["company_description"] == "d" * 3000


def test_compact_une_page_de_50_societes_tient_dans_un_contexte():
    page = _page([_company(i) for i in range(50)])
    raw = len(json.dumps(page))
    compact = len(json.dumps(dv.shape_page(page, dv.view_of(compact=True), "companies")))
    assert raw > 7_000_000
    assert compact < 100_000


def test_compact_trois_personnes_tiennent_dans_un_contexte():
    page = _page([_person(i) for i in range(3)])
    assert len(json.dumps(dv.shape_page(page, dv.view_of(compact=True), "people"))) < 5_000


def test_une_vue_sur_une_page_sans_results_leve():
    """lemlist a changé sa réponse : la rendre entière donnerait à l'agent les mégaoctets que la vue évite."""
    with pytest.raises(RuntimeError, match="no results list"):
        dv.shape_page({"error": "nothing"}, dv.view_of(compact=True), "companies")
    with pytest.raises(RuntimeError, match="no results list"):
        dv.shape_page({"data": []}, dv.view_of(fields=["company_name"]), "companies")


def test_sans_vue_une_page_sans_results_reste_telle_quelle():
    page = {"error": "nothing"}
    assert dv.shape_page(page, None, "companies") is page


def test_compact_une_grosse_liste_hors_preset_est_laissee():
    """La taille tient par la liste des clés gardées, pas par la lecture des offres."""
    records = []
    for i in range(50):
        record = _company(i)
        record["open_positions"] = record.pop("job_posts")
        records.append(record)
    out = dv.shape_page(_page(records), dv.view_of(compact=True), "companies")
    assert all("open_positions" not in r for r in out["results"])
    assert "open_positions" in out["omitted_fields"]
    assert len(json.dumps(out)) < 100_000
