"""La description `amplemarket` 1.0.0 : ce que l'agent voit et ce que l'hôte fait, figé ici.

Le rejeu des exemples et de chaque argument contre le client est dans `tests/test_amplemarket_client.py`. Ici, les gardes
qu'une relecture ne doit pas voir glisser en silence :
- la classe de chaque fonction : une écriture classée lecture, ou une inscription à une séquence sans confirmation, est
  un défaut grave ; toute fonction qui dépense des crédits sur une personne est une action ; terminer ou passer une
  tâche fait avancer le lead dans sa séquence, qui peut envoyer un email automatique : sensible ;
- aucune adresse d'enregistrement ne revient à l'agent : `recording_url` est retiré (`output.strip`) des réponses de
  `list_calls` et `log_call`, les seules de l'OpenAPI qui la portent ; la route de l'audio n'est pas décrite ;
- la donnée personnelle (`personal: true`) : chaque argument qui désigne une personne ou porte ce qu'elle a dit, que
  l'hôte ne journalise jamais ;
- la table d'erreurs : seul le 402 `insufficient_credits` marque le compte ; le 401 ne marque rien (le code
  `unauthorized` de la référence couvre aussi un droit manquant) ; un 5xx ne se rejoue pas ;
- les valeurs que l'API exige et que le client pose : requises, jamais des défauts déclarés ;
- l'identification que le client exige avant l'appel : exigée par le schéma.
"""
from __future__ import annotations

import copy
import inspect

import jsonschema
import pytest
import yaml

from fabrique.descriptions import CONNECTORS, check
from test_strip_cases import shaped
from oto.tools.amplemarket import AmplemarketClient

AMPLEMARKET = CONNECTORS / "amplemarket" / "connector.yaml"

# Les réponses qui portent l'adresse d'un enregistrement (OpenAPI lue le 2026-10-23 : GET /calls, chaque appel de
# `calls` ; POST /calls, l'appel journalisé), et le chemin retiré de chacune.
RECORDING_STRIPS = {"list_calls": ["calls.recording_url"], "log_call": ["recording_url"]}

CLASSES = {
    "read": {
        "get_account_info", "list_users", "list_personas", "list_territories", "list_accounts", "get_account",
        "list_mailboxes", "search_people", "search_companies", "get_person_enrichment", "find_company",
        "get_company_enrichment", "get_email_validation", "list_job_openings", "get_job_opening", "list_lead_lists",
        "get_lead_list", "list_sequences", "get_contact", "get_contact_by_email", "list_contacts", "list_tasks",
        "list_task_statuses", "list_task_types", "list_calls", "list_call_dispositions", "list_excluded_emails",
        "list_excluded_domains",
    },
    "write": {
        "update_mailbox_daily_limit", "find_person", "start_person_enrichment", "cancel_person_enrichment",
        "start_company_enrichment", "cancel_company_enrichment", "start_email_validation", "cancel_email_validation",
        "create_lead_list", "add_leads_to_lead_list", "create_contact", "log_call",
        "add_excluded_emails", "add_excluded_domains", "review_phone_number",
    },
    # Une séquence active contacte les leads ; lever une exclusion rouvre des adresses à la prospection ; terminer ou
    # passer une tâche fait avancer le lead à l'étape suivante, qui peut être un email automatique.
    "sensitive": {"add_leads_to_sequence", "remove_excluded_emails", "remove_excluded_domains", "complete_task",
                  "skip_task"},
}

# Chaque chemin d'argument qui désigne une personne, `[]` pour l'élément d'une liste.
PERSONAL = {
    "list_users": {"email"},
    "list_accounts": {"owner_email"},
    "list_mailboxes": {"user_email"},
    "search_people": {"person_name", "linkedin_networks", "exclude_linkedin_networks", "crm_account_owner"},
    "search_companies": {"crm_account_owner"},
    "find_person": {"email", "linkedin_url", "name"},
    "start_person_enrichment": {"leads[].email", "leads[].linkedin_url", "leads[].name"},
    "start_email_validation": {"emails[].email"},
    "list_lead_lists": {"owner_email"},
    "create_lead_list": {"owner", "leads[].linkedin_url", "leads[].email", "leads[].phone_numbers", "leads[].context"},
    "add_leads_to_lead_list": {"leads[].linkedin_url", "leads[].email", "leads[].phone_numbers", "leads[].context"},
    "list_sequences": {"created_by_user_email"},
    "add_leads_to_sequence": {"leads[].email", "leads[].linkedin_url", "leads[].data", "settings.mailboxes"},
    "get_contact_by_email": {"email"},
    "list_contacts": {"name"},
    "create_contact": {"email", "first_name", "last_name", "linkedin_url", "owner_email", "phone_numbers"},
    "list_tasks": {"keyword"},
    "list_calls": {"from_number", "to_number"},
    "log_call": {"from_number", "to_number", "transcription", "recording_url"},
    "list_excluded_emails": {"email"},
    "add_excluded_emails": {"emails[].email"},
    "remove_excluded_emails": {"emails[].email"},
}

# Ce que l'API exige et que le client pose quand l'appel ne le donne pas : requis par le schéma.
REQUIRED_CLIENT_VALUES = {"create_lead_list": {"shared": False}, "review_phone_number": {"reason": "wrong_number"}}

# L'identification que le client exige avant tout appel : une entrée sans elle est refusée par le schéma.
UNIDENTIFIED = {
    "find_person": [{}, {"name": "Jane Doe"}, {"company_domain": "example.com"}, {"title": "CEO"}],
    "find_company": [{}],
    "list_job_openings": [{}, {"only_remote": True}],
    "list_contacts": [{}],
}
IDENTIFIED = {
    "find_person": [{"email": "jane@example.test"}, {"linkedin_url": "https://www.linkedin.com/in/jane-doe"},
                    {"name": "Jane Doe", "company_name": "Example"}, {"name": "Jane Doe", "company_domain": "example.com"}],
    "find_company": [{"domain": "example.com"}, {"linkedin_url": "https://www.linkedin.com/company/example"}],
    "list_job_openings": [{"company_id": "c1"}, {"domain": "example.com"}, {"linkedin_url": "https://www.linkedin.com/company/example"}],
    "list_contacts": [{"ids": ["c1"]}, {"name": "Jane"}, {"account_id": "acc_1"}],
}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(AMPLEMARKET.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _personal_paths(schema: dict, prefix: str = "") -> set[str]:
    found = set()
    for name, prop in (schema.get("properties") or {}).items():
        path = f"{prefix}{name}"
        if prop.get("personal") is True:
            found.add(path)
        if prop.get("type") == "object":
            found |= _personal_paths(prop, path + ".")
        if prop.get("type") == "array" and isinstance(prop.get("items"), dict):
            found |= _personal_paths(prop["items"], path + "[].")
    return found


def test_the_description_is_version_1_0_1_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "1.0.1"
    check(description, AMPLEMARKET)


def test_each_function_has_its_class_and_every_sensitive_one_asks_for_a_confirmation():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        assert ("confirm" in function) == (function["class"] == "sensitive"), name
        assert "act" not in function, name


def test_a_function_that_spends_credits_on_a_person_is_an_action():
    """`find_person` coûte 0,5 crédit dès qu'une personne est trouvée : une action, comme les lots."""
    functions = _functions()
    spending = {"find_person", "start_person_enrichment", "start_email_validation", "create_lead_list",
                "add_leads_to_lead_list"}
    assert all(functions[name]["class"] != "read" for name in spending)


def test_every_argument_that_names_a_person_is_personal():
    found = {name: _personal_paths(f["input"]) for name, f in _functions().items()}
    assert {name: paths for name, paths in found.items() if paths} == PERSONAL


def test_only_the_insufficient_credits_402_marks_the_account():
    errors = _description()["connector"]["errors"]
    marking = [e for e in errors if "marks_account" in e]
    assert marking == [{
        "status": 402, "body": {"path": "_errors.0.code", "equals": ["insufficient_credits"]},
        "code": "insufficient_credits", "retryable": False, "marks_account": "quota_exhausted",
        "message": marking[0]["message"]}]
    assert not any("marks_account" in r for f in _functions().values() for r in f.get("refusals", []))
    by_status = {(e["status"] if isinstance(e["status"], int) else tuple(e["status"])): e for e in errors if "body" not in e}
    assert "marks_account" not in by_status[401]
    assert by_status[(500, 502, 503, 504)]["retryable"] is False, "a write may have gone through"
    assert by_status[429]["retryable"] is True


@pytest.mark.parametrize("name,values", sorted(REQUIRED_CLIENT_VALUES.items()))
def test_what_the_api_requires_and_the_client_sets_is_required_never_a_default(name, values):
    function = _functions()[name]
    method = getattr(AmplemarketClient, name)
    for argument, value in values.items():
        assert argument in function["input"]["required"], (name, argument)
        assert "default" not in function["input"]["properties"][argument], (name, argument)
        assert inspect.signature(method).parameters[argument].default == value, (name, argument)
    assert not [(n, a) for n, f in _functions().items() for a, p in f["input"]["properties"].items() if "default" in p]


@pytest.mark.parametrize("name", sorted(UNIDENTIFIED))
def test_the_identification_the_client_requires_is_required_by_the_schema(name):
    schema = _functions()[name]["input"]
    validator = jsonschema.Draft202012Validator(schema)
    for value in UNIDENTIFIED[name]:
        assert not validator.is_valid(value), (name, value)
    for value in IDENTIFIED[name]:
        assert validator.is_valid(value), (name, value)


def test_a_task_that_moves_its_lead_on_asks_for_a_confirmation():
    functions = _functions()
    for name in ("complete_task", "skip_task"):
        assert functions[name]["class"] == "sensitive", name
        summary = functions[name]["confirm"]["summary"]
        assert "{task_id}" in summary and "next step" in summary and "automatic email" in summary, name


def test_no_recording_address_reaches_the_agent():
    functions = _functions()
    stripping = {n: f["output"]["strip"] for n, f in functions.items() if "strip" in f.get("output", {})}
    assert stripping == RECORDING_STRIPS
    sample = functions["list_calls"]["output"]["sample"]
    assert all("recording_url" in call for call in sample["calls"]), "the sample carries the address it proves stripped"
    answer = shaped(copy.deepcopy(sample), functions["list_calls"]["output"])
    assert [sorted(call) for call in answer["calls"]] == [["answered", "duration", "id"]]
    assert "_links" in answer, "the whole answer is kept, pagination included"
    logged = {"id": "c-1", "duration": 120, "recording_url": "https://files.example.test/c-1.mp3"}
    assert shaped(logged, functions["log_call"]["output"]) == {"id": "c-1", "duration": 120}


def test_the_recording_route_is_not_described_and_a_given_recording_url_is_personal():
    assert not [n for n, f in _functions().items() if "recording" in f["call"]["path"]]
    assert _functions()["log_call"]["input"]["properties"]["recording_url"]["personal"] is True
