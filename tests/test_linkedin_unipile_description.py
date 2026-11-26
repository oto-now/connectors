"""La description `linkedin_unipile` 1.4.0 : la recherche, le fil d'actualité, les produits premium du client et les
publications d'un membre ou d'une entreprise.

Le client `oto.tools.unipile` n'est pas tiré de la description (il prend l'identité du compte en paramètre) ; ces
gardes lisent le client lui-même pour les trois fichiers que 1.2.0 rattache (`_api/search.py`, `_api/premium.py`,
`feed.py`) : chaque méthode publique y est une fonction sous son nom, avec les arguments qu'elle nomme et la requête
qu'elle envoie, ou une méthode que le format ne sait pas dire, nommée dans l'en-tête de la description (`NOT_DESCRIBED`).

Écart assumé à la règle « une fonction par méthode, sous son nom », décidé par le mainteneur le 2026-10-24 : la méthode
`search` est décrite par huit fonctions (`SEARCH`), une par couple produit et cible que sert Unipile (cinq : Recruiter
n'a pas de recherche d'entreprises) et une par produit pour la recherche par adresse collée (trois). Chacune prend des
identifiants de facettes déjà résolus par `resolve_facet`, dans la forme de son produit, et envoie ce qu'envoie
`search` pour les mêmes identifiants ; la page suivante est le curseur seul, corps vide, comme le client l'envoie.

Écarts nommés, gardés ici : `resolve_facet` sans `limit` laisse Unipile choisir, là où le client envoie 100
(`CLIENT_DEFAULTS`) ; `skill_endorsement_id` est un texte de chiffres, comme Unipile le prend, là où la méthode prend un
entier qu'elle écrit en texte ; les `limit` sont bornés à 100, comme le dit la référence v2 ; une facette est un
identifiant en chiffres, là où `search` résout aussi un nom ; `list_member_posts` prend l'identifiant d'un membre
(`ACoA…`) ou d'une entreprise (des chiffres), là où la méthode résout aussi un slug par `get_profile`.
Rien ne revient à l'agent d'une adresse ouverte sans connexion : les photos d'un membre, d'une entreprise, d'une école,
d'une organisation ou d'une conversation, et le pixel de suivi d'une offre, sont retirés par la description et par le
client (`oto.tools.unipile.open_addresses`), mêmes chemins.
"""
from __future__ import annotations

import copy
import inspect
import re
from unittest import mock

import jsonschema
import pytest
import yaml

from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.tools.unipile import client as unipile_client
from oto.tools.unipile import feed, open_addresses
from oto.tools.unipile._api.premium import _PremiumMixin
from oto.tools.unipile._api.search import SEARCH_BY_URL_FUNCTIONS, SEARCH_FUNCTIONS, _SearchMixin
from oto.tools.unipile.client import UnipileClient, UnipileError

LINKEDIN = CONNECTORS / "linkedin_unipile" / "connector.yaml"
ACCOUNT = "acc_0"

# Ce que le format ne sait pas dire, et pourquoi (en tête de la description).
NOT_DESCRIBED = {
    "member_action": "member_action: the route and the body depend on the pair",
    "get_feed": "get_feed and parse_feed (the home feed)",
}
# L'écart décidé par le mainteneur : huit fonctions décrivent `search`.
STRUCTURED = {f: pair for pair, f in SEARCH_FUNCTIONS.items()}
BY_URL = {f: api for api, f in SEARCH_BY_URL_FUNCTIONS.items()}
SEARCH = {"search": set(STRUCTURED) | set(BY_URL)}
# Les fonctions décrites depuis 1.4.0 hors des trois fichiers rattachés : une méthode de `_api/content.py`, sous son nom.
CONTENT = {"list_member_posts"}
METHODS = {"resolve_facet", "list_contracts", "select_contract", "inmail_balance", "endorse_profile",
           "list_job_postings", "get_job_posting", "list_job_applicants", "get_job_applicant", "list_hiring_projects"}
CLASSES = {
    "read": {"resolve_facet", "list_contracts", "inmail_balance", "list_job_postings", "get_job_posting",
             "list_job_applicants", "get_job_applicant", "list_hiring_projects"} | SEARCH["search"] | CONTENT,
    # Un réglage de la session premium du compte, envoyé à personne : du côté lecture, comme chez oto 1.
    "write": {"select_contract"},
    # Le membre voit la recommandation aussitôt, comme une invitation.
    "sensitive": {"endorse_profile"},
}
PAGINATED = {"list_job_postings", "list_job_applicants", "list_hiring_projects", "list_member_posts"}
PERSONAL = {
    "resolve_facet": {"keywords"}, "endorse_profile": {"profile_id"}, "get_job_applicant": {"applicant_id"},
    "search_people": {"keywords", "advanced_keywords"}, "search_sales_navigator_people": {"keywords"},
    "search_recruiter_people": {"keywords"}, "search_by_url": {"url"}, "search_sales_navigator_by_url": {"url"},
    "search_recruiter_by_url": {"url"}, "list_member_posts": {"identifier"},
}
# Les fonctions décrites avant 1.2.0, et l'appel du client qui fait leur requête.
EARLIER = {
    "get_me": lambda c: c.get_own_profile(),
    "get_profile": lambda c: c.get_profile("u-0"),
    "get_company": lambda c: c.get_company("c-0"),
    "list_relations": lambda c: c.list_relations(),
    "list_invitations": lambda c: c.list_invitations(direction="received"),
    "send_invitation": lambda c: c.send_invitation("ACoAAB1x2y3"),
    "get_post": lambda c: c.get_post("post-0"),
    "list_chats": lambda c: c.list_chats(),
    "list_chat_messages": lambda c: c.list_messages("ch-0"),
}
# Ce que le client envoie quand l'agent ne dit rien, et que la description laisse au service.
CLIENT_DEFAULTS = {"resolve_facet": {"limit": 100}}
VALUES = {
    "facet_type": "SKILL", "keywords": "Microsoft Excel", "limit": 25, "contract_id": "123456789",
    "profile_id": "ACoAAB1x2y3", "skill_endorsement_id": "7", "cursor": "eyJsaW1pdCI6MjV9", "job_id": "4012345678",
    "applicant_id": "98765", "identifier": "ACoAAB1x2y3",
}
# Chaque argument d'une recherche, pour chaque fonction : tous ensemble, puis le curseur seul.
SEARCH_EVERY = {
    "search_people": {"keywords": "data engineer", "advanced_keywords": {"first_name": "Jane", "title": "CTO"},
                      "location": ["105015875"], "current_company": ["1441"], "industry": ["4", "6"],
                      "network_distance": [1, 2]},
    "search_companies": {"keywords": "software", "location": ["105015875"], "industry": ["4"]},
    "search_sales_navigator_people": {"keywords": "head of sales", "location": {"include": ["105015875"]},
                                      "current_company": {"include": ["1441"]},
                                      "industry": {"include": ["4"], "exclude": ["6"]}, "network_distance": [2]},
    "search_sales_navigator_companies": {"keywords": "logistics", "location": {"include": ["105015875"]},
                                         "industry": {"exclude": ["6"]}},
    "search_recruiter_people": {"keywords": "data engineer", "location": [{"id": "105015875"}],
                                "current_company": [{"id": "1441"}], "industry": {"include": ["4"]},
                                "network_distance": [1, 3],
                                "skills": [{"name": "1234"}, {"name": "5678", "priority": "DOESNT_HAVE"}]},
    "search_by_url": {"url": "https://www.linkedin.com/search/results/people/?keywords=x"},
    "search_sales_navigator_by_url": {"url": "https://www.linkedin.com/sales/search/people?query=x"},
    "search_recruiter_by_url": {"url": "https://www.linkedin.com/talent/search?searchContextId=x"},
}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(LINKEDIN.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _mixin_methods(mixin) -> set[str]:
    return {n for n, v in vars(mixin).items() if inspect.isfunction(v) and not n.startswith("_")}


def _covered() -> set[str]:
    """Les méthodes publiques des trois fichiers rattachés, `get_feed` compris (il vit dans `_api/content.py` mais
    lit le fil par `feed.py`)."""
    return _mixin_methods(_SearchMixin) | _mixin_methods(_PremiumMixin) | {"get_feed"}


def _named_parameters(method: str) -> set[str]:
    return {p for p in inspect.signature(getattr(UnipileClient, method)).parameters if p != "self"}


class _Answer:
    status_code = 200
    headers: dict = {}

    def __init__(self, body=None):
        self._body = {} if body is None else body
        self.text = "{}"

    def json(self):
        return copy.deepcopy(self._body)


def _for_the_client(name: str, arguments: dict) -> dict:
    """Les arguments de la description tels que la méthode les prend : l'id de compétence en entier."""
    if name == "endorse_profile" and "skill_endorsement_id" in arguments:
        return dict(arguments, skill_endorsement_id=int(arguments["skill_endorsement_id"]))
    return arguments


def _facet_ids(value, product: str) -> list[str]:
    """Les identifiants d'une facette d'inclusion, dans la forme de son produit."""
    if product == "sales_navigator":
        return value["include"]
    if product == "recruiter":
        return [v["id"] for v in value]
    return value


def _search_arguments(name: str, arguments: dict) -> dict:
    """Les arguments d'une fonction de recherche tels que `search` les prend, pour la même requête."""
    if name in BY_URL:
        return {"api": BY_URL[name], "url": arguments["url"]}
    product, category = STRUCTURED[name]
    out = {"api": product, "category": category}
    for argument, value in arguments.items():
        if argument in ("location", "current_company"):
            out["location" if argument == "location" else "company"] = _facet_ids(value, product)
        elif argument == "industry":
            out["industry"] = {"include": value} if product == "classic" else value
        elif argument == "skills":
            out["skills"] = {"include": [s["name"] for s in value if "priority" not in s],
                             "exclude": [s["name"] for s in value if s.get("priority") == "DOESNT_HAVE"]}
        else:
            out[argument] = value
    return out


def _client_call(name: str, arguments: dict):
    if name in SEARCH["search"]:
        return lambda c: c.search(**_search_arguments(name, arguments))
    return lambda c: getattr(c, name)(**_for_the_client(name, arguments))


def _sent(call, answer=None) -> tuple[list[dict], object]:
    sent = []

    def request(self, method, url, **kwargs):
        sent.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {}),
                     "json": kwargs.get("json"), "headers": dict(self.headers), "timeout": kwargs.get("timeout")})
        return _Answer(answer)

    with mock.patch.object(unipile_client.requests.Session, "request", request):
        returned = call(UnipileClient(api_key="k", account_id=ACCOUNT))
    return sent, returned


def _sent_by_the_client(name: str, arguments: dict, answer=None) -> tuple[dict, object]:
    sent, returned = _sent(_client_call(name, arguments), answer)
    assert len(sent) == 1, name
    return sent[0], returned


def _described(function: dict, arguments: dict) -> tuple:
    call = function["call"]
    path = call["path"].format(**{a: arguments[a] for a in re.findall(r"{(\w+)}", call["path"])})
    query = {api: arguments[a] for api, a in call.get("query", {}).items() if a in arguments}
    query |= {k: v for k, v in CLIENT_DEFAULTS.get(function["name"], {}).items() if k not in query}
    body = {api: arguments[a] for api, a in call["body"].items() if a in arguments} if "body" in call else None
    base = _description()["connector"]["base_url"].format(account_id=ACCOUNT)
    return call["method"], base + path, query, body


def _client_view(sent: dict) -> tuple:
    return sent["method"], sent["url"], sent["params"], sent["json"]


def _shaped(answer, paths: list[str], items: str | None = None):
    answer = copy.deepcopy(answer)
    target = answer[items] if items else answer
    for path in paths:
        open_addresses.drop(target, path.split("."))
    return answer


def test_the_description_is_version_1_4_0_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.4.0"
    check(description, LINKEDIN)


def test_each_method_of_the_three_files_is_described_or_named_as_not_described():
    """Une fonction par méthode, sous son nom, sauf l'écart nommé : `search`, décrite par huit fonctions."""
    functions = set(_functions())
    covered = _covered()
    assert covered - functions == set(NOT_DESCRIBED) | set(SEARCH)
    assert SEARCH["search"] <= functions
    header = LINKEDIN.read_text(encoding="utf-8").split("\nconnector:")[0]
    for name, says in NOT_DESCRIBED.items():
        assert says in header, name
    assert "parse_feed" in header and callable(feed.parse_feed)
    flat = header.replace("\n# ", " ")
    assert "decided by Alexis on 2026-10-24" in flat and "eight search functions all describe search" in flat
    assert set().union(*CLASSES.values()) == (covered - set(NOT_DESCRIBED) - set(SEARCH)) | SEARCH["search"] | CONTENT
    assert METHODS == covered - set(NOT_DESCRIBED) - set(SEARCH)


def test_the_search_functions_are_the_pairs_unipile_serves():
    """Cinq couples produit et cible (Recruiter n'a pas de recherche d'entreprises), et un par produit par adresse."""
    assert set(STRUCTURED.values()) == {("classic", "people"), ("classic", "companies"),
                                        ("sales_navigator", "people"), ("sales_navigator", "companies"),
                                        ("recruiter", "people")}
    assert set(BY_URL.values()) == {"classic", "sales_navigator", "recruiter"}
    sent, _ = _sent(lambda c: pytest.raises(UnipileError, c.search, api="recruiter", category="companies",
                                            keywords="x"))
    assert sent == []


def test_each_function_takes_the_arguments_its_client_method_names():
    functions = _functions()
    for name in METHODS | CONTENT:
        function = functions[name]
        assert set(function["input"]["properties"]) == _named_parameters(name), name
        required = {p for p, v in inspect.signature(getattr(UnipileClient, name)).parameters.items()
                    if p != "self" and v.default is inspect.Parameter.empty}
        assert set(function["input"].get("required", [])) == required, name
    searched = _named_parameters("search")
    for name in SEARCH["search"]:
        renamed = {"company" if a == "current_company" else a for a in functions[name]["input"]["properties"]}
        assert renamed <= searched, name


def test_each_argument_goes_where_the_client_sends_it():
    functions = _functions()
    for name in METHODS | CONTENT:
        function = functions[name]
        every = {a: VALUES[a] for a in function["input"]["properties"]}
        jsonschema.validate(every, function["input"])
        sent, _ = _sent_by_the_client(name, every)
        assert _client_view(sent) == _described(function, every), name
        assert sent["headers"]["X-API-KEY"] == "k"
        fewest = {a: VALUES[a] for a in function["input"].get("required", [])}
        sent, _ = _sent_by_the_client(name, fewest)
        method, url, query, body = _described(function, fewest)
        # Une table de corps sans argument : l'hôte envoie un objet vide, comme le client.
        assert _client_view(sent) == (method, url, query, {} if body == {} else body), name


def test_each_search_sends_what_search_sends_for_the_same_ids():
    functions = _functions()
    for name in SEARCH["search"]:
        function = functions[name]
        every = SEARCH_EVERY[name]
        assert set(every) == set(function["input"]["properties"]) - {"cursor"}, name
        jsonschema.validate(every, function["input"])
        sent, _ = _sent_by_the_client(name, every)
        assert _client_view(sent) == _described(function, every), name
        assert sent["timeout"][1] == function["timeout_s"], name


def test_the_next_page_is_the_cursor_alone_with_an_empty_body():
    functions = _functions()
    for name in STRUCTURED:
        function = functions[name]
        assert "pagination" not in function, name
        page = {"cursor": VALUES["cursor"]}
        jsonschema.validate(page, function["input"])
        assert not jsonschema.Draft202012Validator(function["input"]).is_valid(dict(page, keywords="x")), name
        sent, _ = _sent_by_the_client(name, page)
        assert _client_view(sent) == _described(function, page), name
        assert sent["json"] == {} and sent["params"] == page, name
    for name in BY_URL:
        assert "cursor" not in functions[name]["input"]["properties"], name


def test_a_search_takes_resolved_ids_and_says_so_first():
    functions = _functions()
    for name in STRUCTURED:
        function = functions[name]
        assert "call resolve_facet first" in function["description"][:200], name
        facets = [a for a in ("location", "industry") if a in function["input"]["properties"]]
        for argument in facets:
            named = copy.deepcopy(SEARCH_EVERY[name][argument])
            text = str(named).replace("105015875", "Paris").replace("'4'", "'Software'").replace("'6'", "'Retail'")
            assert not jsonschema.Draft202012Validator(function["input"]).is_valid(
                {argument: yaml.safe_load(text)}), (name, argument)


def test_each_example_sends_what_the_client_sends_for_it():
    functions = _functions()
    for name in METHODS | CONTENT | SEARCH["search"]:
        function = functions[name]
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent, _ = _sent_by_the_client(name, example["input"])
            assert _client_view(sent) == _described(function, example["input"]), (name, example["title"])


def test_where_the_schema_is_narrower_than_the_client_it_is_named():
    functions = _functions()
    for name, case in [("resolve_facet", {"facet_type": "skill", "keywords": "Excel"}),
                       ("resolve_facet", {"facet_type": "SKILL", "keywords": "Excel", "limit": 101}),
                       ("list_job_postings", {"limit": 101}),
                       ("endorse_profile", {"profile_id": "ACoAAB1x2y3", "skill_endorsement_id": "seven"}),
                       # Ce qu'Unipile refuse et que `search` enverrait : compétences hors Recruiter, mots-clés par
                       # champ hors la recherche classique de personnes, exclusion d'un lieu ou d'un employeur.
                       ("search_people", {"skills": ["1234"]}),
                       ("search_sales_navigator_people", {"advanced_keywords": {"first_name": "Jane"}}),
                       ("search_companies", {"advanced_keywords": {"first_name": "Jane"}}),
                       ("search_sales_navigator_people", {"location": {"exclude": ["105015875"]}}),
                       ("search_recruiter_people", {"location": [{"id": "1", "priority": "DOESNT_HAVE"}]})]:
        assert not jsonschema.Draft202012Validator(functions[name]["input"]).is_valid(case), (name, case)


def test_classes_side_and_confirmation():
    functions = _functions()
    named = set().union(*CLASSES.values())
    assert {c: {n for n in named if functions[n]["class"] == c} for c in CLASSES} == CLASSES
    assert functions["select_contract"]["act"] is False
    for name in named - {"select_contract"}:
        assert "act" not in functions[name], name
    for name in named:
        assert ("confirm" in functions[name]) == (functions[name]["class"] == "sensitive"), name
    summary = functions["endorse_profile"]["confirm"]["summary"]
    assert "{profile_id}" in summary and "{skill_endorsement_id}" in summary


def test_the_lists_page_by_cursor_as_the_client_does():
    functions = _functions()
    for name in METHODS | CONTENT:
        function = functions[name]
        if name not in PAGINATED:
            assert "pagination" not in function, name
            continue
        assert function["pagination"] == {"kind": "cursor", "request_param": "cursor", "next": "next_cursor",
                                          "max_pages": 10}, name
        assert function["output"]["items"] == "data", name


def test_what_names_a_person_is_personal():
    functions = _functions()
    for name in set().union(*CLASSES.values()):
        properties = functions[name]["input"]["properties"]
        assert {p for p, s in properties.items() if s.get("personal")} == PERSONAL.get(name, set()), name


def test_open_addresses_are_stripped_description_and_client():
    """Les photos et le pixel de suivi s'ouvrent sans connexion : retirés par la description (`output.strip`) et par
    le client, mêmes chemins ; l'adresse d'un profil, derrière la connexion LinkedIn, reste."""
    functions = _functions()
    stripped = {n for n, f in functions.items() if "strip" in f.get("output", {})}
    assert stripped == set(open_addresses.OPEN_ADDRESS_FIELDS)
    for name, paths in open_addresses.OPEN_ADDRESS_FIELDS.items():
        function = functions[name]
        assert tuple(function["output"]["strip"]) == paths, name
        for path in paths:
            assert not path.endswith("profile_url"), (name, path)
        items = function["output"].get("items")
        sample = function["output"].get("sample")
        if sample is None:
            # Des clés de premier niveau seulement : un exemple qui les porte toutes.
            assert all("." not in p for p in paths), name
            sample = {items: [{p: "https://media.example.test/x.png" for p in paths} | {"id": "x-0"}]}
        assert "media.example.test" in str(sample), name
        shaped = _shaped(sample, list(paths), items)
        assert "media.example.test" not in str(shaped), name
        if name in EARLIER:
            call = EARLIER[name]
        elif name in SEARCH["search"]:
            call = _client_call(name, {"url": "https://x.test"} if name in BY_URL else {"keywords": "x"})
        else:
            call = _client_call(name, {a: VALUES[a] for a in function["input"].get("required", [])})
        sent, returned = _sent(call, sample)
        assert len(sent) == 1, name
        assert "media.example.test" not in str(returned), name
        for key in (k for k in shaped if k in returned):
            assert returned[key] == shaped[key], (name, key)


def test_the_errors_are_the_connector_table():
    """Ni erreur, ni rythme, ni quota nouveaux : la table commune vaut ; les refus ajoutés disent un 404. Une
    recherche ne consomme aucun quota : oto 1 la sert en lecture, sous la seule discipline du 429."""
    functions = _functions()
    for name in set().union(*CLASSES.values()):
        for refusal in functions[name].get("refusals", []):
            assert refusal["when"] == 404, name
    assert "rate_limit" not in _description()["connector"] and "quota" not in _description()["connector"]
    for name in SEARCH["search"]:
        assert "cost" not in functions[name], name


def test_the_typescript_output():
    module = connector_module(check(_description(), LINKEDIN))
    for name in set().union(*CLASSES.values()):
        assert f'name: "linkedin_unipile.{name}",' in module.source, name
    assert not module.skipped


def test_the_posts_of_a_member_or_a_company_take_their_ids_never_a_slug_nor_an_address():
    """Un membre par son identifiant `ACoA…`, une entreprise par ses chiffres, un seul appel ; jamais un slug, une
    adresse ni un chemin, que la route refuse ou qui en sortiraient."""
    function = _functions()["list_member_posts"]
    validator = jsonschema.Draft202012Validator(function["input"])
    for identifier in ("ACoAAB1x2y3", "1441"):
        assert validator.is_valid({"identifier": identifier}), identifier
        sent, _ = _sent_by_the_client("list_member_posts", {"identifier": identifier})
        assert sent["url"].endswith(f"/users/{identifier}/posts"), identifier
    for identifier in ("jane-doe-4b2a1c", "unipile", "https://www.linkedin.com/in/jane-doe", "ACoA../me", "me",
                       "1441/posts", "ACoA", ""):
        assert not validator.is_valid({"identifier": identifier}), identifier
    assert function["class"] == "read" and "act" not in function and "cost" not in function
    assert "get_profile" in function["description"] and "get_company" in function["description"]
