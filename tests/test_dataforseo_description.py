"""La description `dataforseo` 1.0.4 : celle du client existant (`oto.tools.dataforseo`), sans rien y changer, et huit
fonctions que le client n'a pas (résultats organiques en direct ou en file standard, Maps, fiche d'établissement, avis
Google en dépôt puis relecture, résolution des liens goto de Google).

Une fonction par méthode publique du client, sous son nom, avec ses noms d'arguments et les clés que le client envoie,
sur le même endpoint. Les gardes ci-dessous font passer le client et le moteur par le même faux tiers : un endpoint, un
argument ou une clé que le client ajoute sans la description fait échouer le test. Écarts nommés : `options` (une table
libre fusionnée dans la tâche) n'est pas décrit, et `backlinks_intersection` prend ses cibles déjà numérotées
(`{"1": …}`), que le client numérote lui-même depuis une liste.
"""
from __future__ import annotations

import copy
import inspect

import pytest
import yaml
from jsonschema import Draft202012Validator

import engine_fake
from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors import cost as connector_cost
from oto.connectors import serve
from oto.connectors.declared import DECLARED
from oto.connectors.engine.errors import Refused
from oto.tools.dataforseo import client as dataforseo_client
from oto.tools.dataforseo.client import DataForSeoClient

DATAFORSEO = CONNECTORS / "dataforseo" / "connector.yaml"
BASE_URL = DataForSeoClient.BASE_URL

# Les fonctions sans méthode du client, ajoutées par la description.
ADDED = {"serp_organic", "serp_maps", "my_business_info", "google_reviews_post", "google_reviews_get",
         "serp_organic_post", "serp_organic_get", "goto_url_resolve"}
# Les fonctions gratuites : ni pire cas ni règlement.
FREE = {"user_data", "labs_locations_and_languages", "llm_models", "onpage_summary", "onpage_pages",
        "onpage_non_indexable", "onpage_duplicate_tags", "onpage_links", "onpage_redirect_chains", "google_reviews_get",
        "serp_organic_get", "goto_url_resolve"}
# Le pire cas de chaque fonction payante, en microdollars : le prix catalogue à ses plus grands arguments.
WORST = {
    **dict.fromkeys(["keyword_ideas", "keyword_suggestions", "related_keywords", "bulk_keyword_difficulty",
                     "search_intent", "domain_rank_overview", "ranked_keywords", "competitors_domain",
                     "domain_intersection", "relevant_pages", "keywords_for_site", "bulk_traffic_estimation",
                     "serp_competitors", "subdomains"], 132000),
    "keyword_overview": 96000, "historical_rank_overview": 240000,
    "google_ads_search_volume": 90000, "google_trends_explore": 20000,
    **dict.fromkeys(["backlinks_summary", "backlinks_list", "referring_domains", "anchors", "bulk_ranks",
                     "backlinks_competitors", "backlinks_intersection", "backlinks_domain_pages"], 60000),
    "backlinks_history": 100000, "backlinks_new_lost": 100000,
    "llm_mentions_search": 1100000, "llm_mentions_top_domains": 1100000, "llm_mentions_top_pages": 1100000,
    "llm_mentions_metrics": 200000, "llm_mentions_history": 200000, "llm_response": 200000,
    "google_ai_mode": 10000, "ai_keyword_search_volume": 110000,
    "content_search": 30000, "content_summary": 30000,
    "onpage_instant": 150, "onpage_content": 150, "lighthouse": 5000, "onpage_crawl_start": 300000,
    "domain_technologies": 12000, "domains_by_technology": 1212000, "technologies_summary": 24000,
    "serp_organic": 40000, "serp_maps": 14000, "my_business_info": 5400, "google_reviews_post": 37500,
    "serp_organic_post": 12000,
}
SETTLE = {"path": "cost", "scale": 1000000}
# Les codes de DataForSEO (GET /appendix/errors, lu le 11/11/2026), par refus : lus au niveau de la requête et de la tâche.
CLASSES = {
    "key_refused": [40100],
    "balance_exhausted": [40200, 40210],
    "account_not_verified": [40104],
    "access_denied": [40201, 40204, 40207, 40208],
    "cost_limit_reached": [40203],
    "rate_limited": [40202, 40209],
    "duplicate_limit": [40205, 40206],
    "upstream_failed": [40101, 40103],
    "no_results": [40102, 40405],
    "not_found": [40105, 40400, 40401, 40402, 40403, 40404, 40406],
    "task_not_ready": [40601, 40602],
    "invalid_request": [40000, 40001, 40002, 40003, 40004, 40005, 40006, 40407, 40408, 40501, 40502, 40503, 40504,
                        40505, 40506],
    "upstream_unavailable": [50000, 50001, 50100, 50301, 50302, 50303, 50304, 50401, 50402],
}
RETRYABLE = {"rate_limited", "upstream_failed", "task_not_ready", "upstream_unavailable"}
# Une valeur par argument, pour un appel qui les donne tous.
VALUES = {
    "keywords": ["alpha", "beta"], "keyword": "alpha", "location_code": 2250, "location_name": "France",
    "language_code": "fr", "language_name": "French", "limit": 20, "offset": 40,
    "filters": [["keyword_info.search_volume", ">", 100]], "order_by": ["keyword_info.search_volume,desc"],
    "depth": 2, "date_from": "2026-01-01", "date_to": "2026-06-30", "time_range": "past_12_months", "type": "web",
    "target": "example.com", "target1": "example.com", "target2": "example.org", "intersections": False,
    "targets": ["example.com", "example.org"], "mode": "one_per_domain", "exclude_targets": ["example.net"],
    "group_range": "week", "platform": "google", "engine": "claude", "user_prompt": "Which CRM?",
    "model_name": "model-0", "web_search": True, "web_search_country_iso_code": "FR", "max_output_tokens": 256,
    "device": "mobile", "page_type": ["news"], "url": "https://example.com/page", "for_mobile": True,
    "categories": ["seo"], "max_crawl_pages": 100, "task_id": "11111111-1111-1111-1111-111111111111",
    "technologies": ["Shopify"],
}
# Les valeurs propres à une fonction, quand le même nom n'y prend pas la même forme.
OWN_VALUES = {
    "llm_mentions_search": {"targets": [{"domain": "example.com"}, {"keyword": "crm"}]},
    "llm_mentions_metrics": {"targets": [{"domain": "example.com"}]},
    "llm_mentions_top_domains": {"targets": [{"keyword": "crm", "search_filter": "include"}]},
    "llm_mentions_top_pages": {"targets": [{"keyword": "crm"}]},
    "llm_mentions_history": {"targets": [{"domain": "example.com"}]},
    "onpage_duplicate_tags": {"type": "duplicate_description"},
    "domains_by_technology": {"keywords": ["bakery"]},
    "technologies_summary": {"keywords": ["bakery"]},
}
# La forme qu'une fonction prend et que sa méthode ne prend pas : le client numérote lui-même les cibles.
DESCRIBED_VALUES = {"backlinks_intersection": {"targets": {"1": "example.com", "2": "example.org"}}}
OK = {"status_code": 20000, "cost": 0.0126, "tasks": [{"id": "t-1", "status_code": 20000, "cost": 0.0126, "result": []}]}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(DATAFORSEO.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _client_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(DataForSeoClient, inspect.isfunction) if not n.startswith("_")}


def _arguments(name: str, function: dict) -> dict:
    """Une valeur pour chaque argument de la fonction."""
    own = OWN_VALUES.get(name, {})
    return {argument: own.get(argument, VALUES.get(argument)) for argument in function["input"]["properties"]}


def _sent_by_the_client(name: str, arguments: dict) -> dict:
    """La requête que la méthode du client envoie pour ces arguments, par le faux tiers."""
    vendor = engine_fake.Vendor(engine_fake.answer(200, OK))
    client = DataForSeoClient(login="login-0", password="test-key-not-real")
    client.session.mount("https://", vendor)
    getattr(client, name)(**arguments)
    (request,) = vendor.requests
    return _request_parts(engine_fake.sent(request))


def _sent_by_the_engine(description: dict, name: str, arguments: dict) -> dict:
    _, vendor = engine_fake.play(description, name, arguments, engine_fake.answer(200, OK),
                                 credential={"login": "login-0", "password": "test-key-not-real"})
    (request,) = vendor.requests
    return _request_parts(engine_fake.sent(request))


def _request_parts(parts: dict) -> dict:
    return {"method": parts["method"], "url": parts["url"], "query": parts["query"], "body": parts["body"]}


def test_the_description_is_version_1_0_2_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.4"
    check(description, DATAFORSEO)


def test_a_maps_place_is_served_trimmed_with_the_cost_and_the_task_status():
    """Une grille de points reste petite : chaque lieu sans images, réservation, horaires ni copies de l'adresse ; le
    coût et le statut de la tâche restent."""
    place = {"type": "maps_search", "rank_group": 1, "rank_absolute": 1, "domain": "example.test", "title": "Alpha",
             "url": "https://example.test/", "rating": {"value": 4.5, "votes_count": 10}, "address": "1 rue Alpha",
             "place_id": "p-0", "cid": "0", "category": "Bakery", "book_online_url": "https://example.test/book",
             "work_hours": {"timetable": {}}, "main_image": "https://example.test/i.jpg",
             "rating_distribution": {"5": 10}, "contributor_url": "https://example.test/c", "feature_id": "f-0",
             "address_info": {"city": "Alpha"}}
    answer = {"status_code": 20000, "cost": 0.002, "time": "1 sec.",
              "tasks": [{"id": "t-0", "status_code": 20000, "status_message": "Ok.", "cost": 0.002, "time": "1 sec.",
                         "data": {"keyword": "alpha"}, "path": ["v3"],
                         "result": [{"check_url": "https://example.test/maps", "items": [place]}]}]}
    served = serve.result(serve.function(_description(), "serp_maps"), [answer])
    (task,) = served["tasks"]
    assert served["cost"] == 0.002 and task["status_code"] == 20000 and task["cost"] == 0.002
    (item,) = task["result"][0]["items"]
    assert set(item) == {"type", "rank_group", "rank_absolute", "domain", "title", "url", "rating", "address",
                         "place_id", "cid", "category"}
    assert "check_url" not in task["result"][0]


def test_each_method_of_the_client_is_a_function_and_the_rest_is_named():
    assert set(_functions()) - _client_methods() == ADDED
    assert _client_methods() <= set(_functions())


def test_the_connector_says_what_the_client_does():
    connector = _description()["connector"]
    assert connector["base_url"] == BASE_URL
    assert connector["auth"] == {"kind": "basic", "username": "login", "password": "password"}
    assert connector["credential"] == [{"name": "login", "label": "API login", "secret": False},
                                       {"name": "password", "label": "API password", "secret": True}]
    assert connector["timeout_s"] == dataforseo_client._HTTP_TIMEOUT[1]
    assert connector["modes"] == ["byo_user", "byo_org", "platform"]


def test_each_function_takes_the_arguments_of_its_client_method_but_options():
    for name, function in _functions().items():
        if name in ADDED:
            continue
        parameters = set(inspect.signature(getattr(DataForSeoClient, name)).parameters) - {"self"}
        properties = set(function["input"]["properties"])
        assert properties <= parameters, name
        assert parameters - properties == parameters & {"options"}, name


@pytest.mark.parametrize("name", sorted(set(_functions()) - ADDED))
def test_the_engine_sends_what_the_client_sends(name):
    """Tous les arguments donnés : même méthode, même adresse, même corps, une liste d'une tâche."""
    description = _description()
    function = _functions()[name]
    arguments = _arguments(name, function)
    described = {**arguments, **DESCRIBED_VALUES.get(name, {})}
    assert Draft202012Validator(function["input"]).is_valid(described), name
    assert _sent_by_the_engine(description, name, described) == _sent_by_the_client(name, arguments)


@pytest.mark.parametrize("name", sorted(set(_functions()) - ADDED))
def test_each_example_sends_what_the_client_sends_for_it(name):
    """Un argument que l'exemple ne donne pas n'est envoyé ni par l'un ni par l'autre."""
    description = _description()
    function = _functions()[name]
    for example in function["examples"]:
        given = example["input"]
        assert Draft202012Validator(function["input"]).is_valid(given), (name, example["title"])
        client_given = dict(given)
        if name == "backlinks_intersection":
            client_given["targets"] = [given["targets"][key] for key in sorted(given["targets"], key=int)]
        assert _sent_by_the_engine(description, name, given) == _sent_by_the_client(name, client_given), example["title"]


@pytest.mark.parametrize("name", sorted(ADDED))
def test_each_added_function_sends_one_task_of_its_arguments(name):
    function = _functions()[name]
    for example in function["examples"]:
        assert Draft202012Validator(function["input"]).is_valid(example["input"]), example["title"]
        sent = _sent_by_the_engine(_description(), name, example["input"])
        if function["call"]["method"] == "GET":
            assert sent["body"] is None
        else:
            assert sent["body"] == [{key: example["input"][argument] for key, argument in function["call"]["body"].items()
                                     if argument in example["input"]}]


def test_the_added_functions_reach_their_endpoints():
    functions = _functions()
    assert {name: (functions[name]["call"]["method"], functions[name]["call"]["path"]) for name in ADDED} == {
        "serp_organic": ("POST", "/serp/google/organic/live/advanced"),
        "serp_maps": ("POST", "/serp/google/maps/live/advanced"),
        "my_business_info": ("POST", "/business_data/google/my_business_info/live"),
        "google_reviews_post": ("POST", "/business_data/google/reviews/task_post"),
        "google_reviews_get": ("GET", "/business_data/google/reviews/task_get/{task_id}"),
        "serp_organic_post": ("POST", "/serp/google/organic/task_post"),
        "serp_organic_get": ("GET", "/serp/google/organic/task_get/advanced/{task_id}"),
        "goto_url_resolve": ("POST", "/appendix/goto_url_converter"),
    }


def test_a_location_and_a_language_are_required_where_dataforseo_requires_them():
    serp_maps = _functions()["serp_maps"]["input"]
    valid = Draft202012Validator(serp_maps).is_valid
    assert valid({"keyword": "alpha", "location_coordinate": "45.764,4.835,15z", "language_code": "fr"})
    assert not valid({"keyword": "alpha", "language_code": "fr"})
    assert not valid({"keyword": "alpha", "location_code": 2250})
    assert not valid({"keyword": "alpha", "location_coordinate": "45.764,4.835,25z", "language_code": "fr"})
    reviews = Draft202012Validator(_functions()["google_reviews_post"]["input"]).is_valid
    assert not reviews({"location_code": 2250, "language_code": "fr"})
    assert reviews({"cid": "123", "location_code": 2250, "language_code": "fr"})


def test_each_paid_call_is_settled_on_the_cost_dataforseo_reports():
    for name, function in _functions().items():
        if name in FREE:
            assert "cost" not in function, name
            continue
        assert function["cost"] == {"per": "request", "quantity": WORST[name], "unit": "microdollars",
                                    "settle": SETTLE}, name
    assert set(WORST) | FREE == set(_functions())


def test_a_decimal_cost_is_settled_in_microdollars():
    settled = connector_cost.settled_quantity("dataforseo", "my_business_info", {}, [{"cost": 0.0054}])
    assert settled == {"quantity": 5400, "unit": "microdollars", "from": "response", "exceeded": False}
    above = connector_cost.settled_quantity("dataforseo", "serp_maps", {}, [{"cost": 0.016}])
    assert above["quantity"] == 16000 and above["exceeded"] is True


def test_the_probe_reads_the_balance_for_free():
    description = _description()
    assert description["connector"]["probe"] == {
        "function": "user_data", "balance": {"path": "tasks.0.result.0.money.balance", "min": 1, "unit": "USD"}}
    answer = {"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"money": {"balance": 48.97}}]}]}
    assert serve.probe_health(description, answer)["healthy"] is True
    empty = {"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"money": {"balance": 0.4}}]}]}
    assert serve.probe_health(description, empty)["healthy"] is False


def test_the_error_table_reads_both_levels_of_the_body_under_200():
    errors = _description()["connector"]["errors"]
    for path in ("status_code", "tasks.0.status_code"):
        table = {e["code"]: e["body"]["equals"] for e in errors if e["status"] == 200 and e["body"]["path"] == path}
        assert table == CLASSES, path
    assert {e["code"] for e in errors if e["retryable"]} == RETRYABLE
    marked = {(e["code"], e["marks_account"]) for e in errors if "marks_account" in e}
    assert marked == {("key_refused", "credential_rejected"), ("balance_exhausted", "quota_exhausted")}


@pytest.mark.parametrize("code,refusal", [(c, r) for r, codes in CLASSES.items() for c in codes])
def test_a_task_error_under_200_is_refused_by_name(code, refusal):
    description = _description()
    task_level = {"status_code": 20000, "cost": 0,
                  "tasks": [{"status_code": code, "status_message": "x", "cost": 0, "result": None}]}
    request_level = {"status_code": code, "status_message": "x", "cost": 0, "tasks": None}
    for body in (task_level, request_level):
        with pytest.raises(Refused) as refused:
            engine_fake.play(description, "keyword_overview",
                             {"keywords": ["alpha"], "location_code": 2250, "language_code": "fr"},
                             engine_fake.answer(200, body))
        assert refused.value.code == refusal
        assert refused.value.retryable is (refusal in RETRYABLE)


@pytest.mark.parametrize("code", [20000, 20100, 40106, 40007])
def test_a_success_a_queued_task_a_partial_result_and_a_notice_are_served(code):
    body = {"status_code": 20000, "cost": 0.001,
            "tasks": [{"id": "t-1", "status_code": code, "cost": 0.001, "path": ["v3"], "data": {"api": "x"},
                       "time": "0.1 sec.", "result": [{"items": []}]}]}
    result, _ = engine_fake.play(_description(), "onpage_crawl_start", {"target": "example.com", "max_crawl_pages": 10},
                                 engine_fake.answer(200, body))
    assert result == {"cost": 0.001, "tasks": [{"id": "t-1", "status_code": code, "cost": 0.001, "result": [{"items": []}]}]}


def test_a_refused_login_is_a_401_that_marks_the_account():
    body = {"status_code": 40100, "status_message": "You are not authorized.", "tasks": None}
    with pytest.raises(Refused) as refused:
        engine_fake.play(_description(), "user_data", {}, engine_fake.answer(401, body))
    assert (refused.value.code, refused.value.marks_account) == ("key_refused", "credential_rejected")


def test_the_organic_results_lose_their_layout():
    answer = {"status_code": 20000, "cost": 0.002, "tasks": [{"id": "t-1", "status_code": 20000, "cost": 0.002,
              "result": [{"items": [{"type": "organic", "rank_group": 1, "xpath": "/html", "rectangle": None,
                                     "about_this_result": {"url": "https://example.test"}, "domain": "google.com",
                                     "url": "https://google.com/goto?url=x", "breadcrumb": "https://example.test › a",
                                     "website_name": "Example"}]}]}]}
    result, _ = engine_fake.play(_description(), "serp_organic",
                                 {"keyword": "alpha", "location_code": 2250, "language_code": "fr"},
                                 engine_fake.answer(200, answer))
    # A google.com/goto URL hides the site: its breadcrumb and name stay, to tell it.
    assert result["tasks"][0]["result"][0]["items"] == [
        {"type": "organic", "rank_group": 1, "domain": "google.com", "url": "https://google.com/goto?url=x",
         "breadcrumb": "https://example.test › a", "website_name": "Example"}]


def test_the_account_is_served_without_its_per_endpoint_tables():
    answer = {"status_code": 20000, "cost": 0, "tasks": [{"id": "t-1", "status_code": 20000, "cost": 0,
              "result": [{"login": "login-0", "money": {"total": 50, "balance": 10.5, "limits": {"day": {}},
                                                        "statistics": {"day": {}}},
                          "rates": {"limits": {"day": {}}}, "price": {"serp": {}}}]}]}
    result, _ = engine_fake.play(_description(), "user_data", {}, engine_fake.answer(200, answer))
    assert result["tasks"][0]["result"] == [{"login": "login-0", "money": {"total": 50, "balance": 10.5}}]


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), DATAFORSEO)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "dataforseo.{name}",' in module.source, name
    assert module.source.count("bodyList: true") == sum(1 for f in _functions().values() if f["call"].get("body_list"))
    table = python.declared([checked])["dataforseo"]
    assert DECLARED.get("dataforseo") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"


def test_the_queued_search_takes_the_live_search_arguments_and_returns_its_shape():
    functions = _functions()
    assert functions["serp_organic_post"]["input"] == functions["serp_organic"]["input"]
    assert functions["serp_organic_post"]["call"]["body"] == functions["serp_organic"]["call"]["body"]
    assert functions["serp_organic_get"]["output"] == functions["serp_organic"]["output"]


def test_only_goto_urls_are_sent_to_the_resolver():
    valid = Draft202012Validator(_functions()["goto_url_resolve"]["input"]).is_valid
    assert valid({"goto_urls": ["https://google.com/goto?url=CAESUgHr&gsessionid=x"]})
    assert not valid({"goto_urls": ["https://example.test/"]})
    assert not valid({"goto_urls": []})
