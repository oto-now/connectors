"""La description `brevo` 2.0.0 : celle du client existant (`oto.tools.brevo`).

Une fonction par requête : une méthode du client qui envoie une seule requête à une seule route est la fonction de son
nom ; une méthode qui choisit sa route par un argument est une fonction par route, l'argument fixé par la fonction
(`ROUTES`). Les noms d'arguments et les clés que le client envoie à Brevo (chemin, query, corps) sont les siens. Écarts
nommés, gardés ici :
- les filtres du CRM, que le client écrit d'une table (`filters`), sont des arguments nommés, un par filtre que la
  référence documente (`CRM_FILTERS`) ; les entreprises se paginent par numéro de page, que le client calcule d'`offset` :
  la fonction prend `page` ;
- `update_campaign` prend `fields`, le corps entier, là où le client prend des arguments nommés libres (`**fields`) ;
- les valeurs que le client pose quand l'appel ne les donne pas : défauts de Brevo, non déclarés (`CLIENT_FILLS`), ou
  valeurs dont l'absence change ce que fait Brevo, exigées (`REQUIRED_WHERE_THE_CLIENT_FILLS`) ;
- `limit` que le client abaisse (`CLIENT_CAPS`), et ce que la référence borne ou ferme et que le client laisse passer
  (`NARROWER`), dont les attributs à choix multiple (une liste de textes) que les clés libres à valeur simple refusent ;
- ce que le client garde hors de portée et que la description garde de même (`ARGUMENTS_LEFT_OUT`) : la date d'envoi
  d'une campagne, les pièces jointes en base64 ;
- le `limit` des listes, dossiers et segments, des emails transactionnels et de leurs événements, que le client pose
  à 50 quand Brevo en rend 10, 500 ou 2500 sans lui (`BREVO_DEFAULT_DIFFERS`) : la description de l'argument le dit ;
- ce que `get_account` retire de la réponse, le login du relais SMTP et la clé du suivi d'automatisation
  (`ACCOUNT_STRIPPED`), retiré de même par le client.
Les gardes ci-dessous lisent le client lui-même : une route, un argument ou une clé qu'il ajoute sans la description
fait échouer le test ; une écriture servie du côté lecture aussi.
"""
from __future__ import annotations

import copy
import inspect
import json
import re
from unittest import mock

import jsonschema
import pytest
import requests
import yaml

from fabrique import python
from fabrique.descriptions import CONNECTORS, check
from fabrique.typescript import connector_module
from oto.connectors.declared import DECLARED
from oto.tools.brevo import _base as brevo_base
from oto.tools.brevo.client import BrevoClient

BREVO = CONNECTORS / "brevo" / "connector.yaml"

# Les fonctions qui ne portent pas le nom de leur méthode : la méthode, et les arguments qui choisissent la route.
ROUTES = {
    "list_lists": ("list_lists", {}),
    "list_folder_lists": ("list_lists", {}),
    "list_templates": ("list_templates", {}),
    "get_template": ("list_templates", {}),
    "transactional_report": ("transactional_report", {"by_day": False}),
    "transactional_report_by_day": ("transactional_report", {"by_day": True}),
    "list_blocked": ("list_blocked", {"domains": False}),
    "list_blocked_domains": ("list_blocked", {"domains": True}),
    **{f"crm_{verb}_{one}": (f"crm_{verb}", {"entity": many})
       for verb in ("list", "get", "create", "update")
       for one, many in (("deal", "deals"), ("company", "companies"), ("task", "tasks"), ("note", "notes"))
       if verb != "list"},
    **{f"crm_list_{many}": ("crm_list", {"entity": many}) for many in ("deals", "companies", "tasks", "notes")},
    "crm_link_deal": ("crm_link", {"entity": "deals"}),
    "crm_link_company": ("crm_link", {"entity": "companies"}),
}
# L'argument de la méthode qui choisit la route quand il est donné ou absent (et non fixé par `ROUTES`).
SELECTED_BY_PRESENCE = {"list_lists": "folder_id", "list_templates": "template_id"}
# Les filtres du CRM : argument de la fonction -> clé de la table `filters` du client (sans son préfixe).
CRM_FILTERS = {
    "crm_list_deals": {
        "deal_name": "attributes.deal_name", "deal_owner": "attributes.deal_owner",
        "deal_stage": "attributes.deal_stage", "pipeline": "attributes.pipeline",
        "linked_companies_ids": "linkedCompaniesIds", "linked_contacts_ids": "linkedContactsIds",
    },
    "crm_list_companies": {"name": "attributes.name", "owner": "attributes.owner"},
    "crm_list_tasks": {
        "type": "type", "status": "status", "date": "date", "assign_to": "assignTo", "contacts": "contacts",
        "deals": "deals", "companies": "companies",
    },
    "crm_list_notes": {"entity": "entity", "entity_ids": "entityIds", "date_from": "dateFrom", "date_to": "dateTo"},
}
_NOT_ON_THIS_ROUTE = "not sent by the client on this route"
_NOT_A_ROUTE_PARAMETER = "sent by the client, not a parameter of this route in Brevo's reference"
# Les arguments d'une méthode que sa fonction ne prend pas, et pourquoi.
ARGUMENTS_LEFT_OUT = {
    "send_email": {"attachment": "the bytes of a file go base64 inside the JSON body; the format takes a file only "
                                 "as a transit file sent in multipart"},
    "create_campaign": {"scheduled_at": "schedules the mass send that the client and oto 1 leave to the Brevo app"},
    "transactional_report": {"limit": _NOT_ON_THIS_ROUTE, "offset": _NOT_ON_THIS_ROUTE},
    "list_blocked_domains": {a: _NOT_ON_THIS_ROUTE for a in (
        "start_date", "end_date", "limit", "offset", "senders", "sort")},
    "get_template": {a: _NOT_ON_THIS_ROUTE for a in ("active_only", "limit", "offset", "sort")},
    "crm_list_tasks": {"modified_since": _NOT_A_ROUTE_PARAMETER, "created_since": _NOT_A_ROUTE_PARAMETER},
    "crm_list_notes": {"sort_by": _NOT_A_ROUTE_PARAMETER, "modified_since": _NOT_A_ROUTE_PARAMETER,
                       "created_since": _NOT_A_ROUTE_PARAMETER},
}
# Les arguments d'une fonction que sa méthode n'a pas sous ce nom, et ce qu'ils deviennent chez le client : la table
# `filters` du CRM devient un argument nommé par filtre de la référence ; les entreprises se paginent par numéro de page.
ARGUMENTS_ADDED = {
    **{name: {a: "filters" for a in filters} for name, filters in CRM_FILTERS.items()},
    "crm_list_companies": {**{a: "filters" for a in CRM_FILTERS["crm_list_companies"]}, "page": "offset"},
    "update_campaign": {"fields": "**fields"},
}
# Ce que le client pose quand l'appel ne le donne pas, et que Brevo prend de même par défaut : pas un défaut déclaré.
_PAGE = {"limit": 50, "offset": 0}
CLIENT_FILLS = {
    **{n: _PAGE for n in ("list_contacts", "list_segments", "list_lists", "list_folder_lists", "list_folders",
                          "list_contacts_of_list", "list_transactional_emails", "transactional_events",
                          "list_blocked", "list_templates", "list_campaigns", "crm_list_deals", "crm_list_tasks",
                          "crm_list_notes")},
    "transactional_report_by_day": {"limit": 10, "offset": 0},
    "crm_list_companies": {"limit": 50, "page": 1},
    "import_contacts": {"update_existing_contacts": True, "empty_contacts_attributes": False},
}
# Écart nommé : le client pose 50, Brevo rend 10 sans `limit` (référence, relue le 2026-11-26). L'argument reste
# facultatif ; sa description dit ce que Brevo rend sans lui.
BREVO_DEFAULT_DIFFERS = {
    **{n: {"limit": 10} for n in ("list_segments", "list_lists", "list_folder_lists", "list_folders")},
    "list_transactional_emails": {"limit": 500},
    "transactional_events": {"limit": 2500},
}
# Ce que la réponse de `get_account` perd avant l'agent : un identifiant de connexion ou une clé.
ACCOUNT_STRIPPED = ["relay.data.userName", "marketingAutomation.key"]
# Les suppressions : sensibles, `destroy`, et rien d'autre ne part en DELETE.
DELETES = {"delete_list", "delete_contact"}
# Ce que le client pose toujours et dont l'absence change ce que fait Brevo : exigé.
REQUIRED_WHERE_THE_CLIENT_FILLS = {
    "upsert_contact": {"update_enabled": True},
    "create_template": {"is_active": True},
    "list_campaigns": {"exclude_html_content": True},
    "get_campaign": {"exclude_html_content": True},
    "import_contacts": {"disable_notification": True},
    "export_contacts": {"disable_notification": True, "contact_filter": {"emailBlacklisted": False}},
}
# Le client abaisse `limit` au plafond de Brevo (ou au sien) ; le schéma refuse au-delà.
CLIENT_CAPS = {
    "list_contacts": 1000, "list_contacts_of_list": 500, "list_transactional_emails": 500,
    "transactional_events": 100, "list_templates": 1000, "list_campaigns": 100,
}
# Plus étroit que le client, qui laisse passer : la référence borne, ferme ou exige.
NARROWER = {
    "list_segments": [{"limit": 51}], "list_lists": [{"limit": 51}], "list_folders": [{"limit": 51}],
    "list_folder_lists": [{"limit": 51}], "list_blocked": [{"limit": 101}],
    "transactional_report_by_day": [{"limit": 31}, {"days": 31}], "transactional_events": [{"days": 91}],
    "add_to_list": [{"emails": [f"c{i}@example.test" for i in range(151)]}],
    # Un attribut à choix multiple : Brevo et le client prennent une liste de textes, les clés libres à valeur simple
    # la refusent.
    "upsert_contact": [{"attributes": {"COUNTRIES": ["FR", "BE"]}}],
    "update_contact": [{"attributes": {"COUNTRIES": ["FR", "BE"]}}],
    # Les formes de filtre que le client documente, absentes de la référence.
    "export_contacts": [{"contact_filter": {"listIds": [1]}}, {"contact_filter": {"emailBlacklisted": True}}],
    "update_campaign": [{"fields": {"scheduledAt": "2026-11-01T09:00:00Z"}}, {"fields": {"sendAtBestTime": True}}],
    "list_transactional_emails": [{}],
    "remove_from_list": [{"emails": ["a@example.test"], "all_contacts": True}],
}
# Ce que le client refuse avant d'envoyer (ValueError) : le schéma le refuse aussi.
CLIENT_REFUSES = {
    "add_to_list": [{}, {"emails": ["a@example.test"], "ids": [1]}],
    "remove_from_list": [{}, {"emails": ["a@example.test"], "ids": [1]}],
    "crm_create_deal": [{"payload": {"attributes": {"amount": 1}}}, {"payload": {"name": ""}}],
    "crm_create_company": [{"payload": {"countryCode": 33}}],
    "crm_create_task": [{"payload": {"name": "Call", "taskTypeId": "t"}}],
    "crm_create_note": [{"payload": {"dealIds": ["d1"]}}, {"payload": {"text": ""}}],
}
# Les drapeaux qui ne partent que vrais, comme le client les envoie (`const: true`).
ONLY_TRUE = {"remove_from_list": {"all_contacts": "all"}}
# Ce que fait chaque fonction : une écriture mal classée est un défaut grave.
CLASSES = {
    "read": {
        "get_account", "list_senders", "list_contacts", "get_contact", "contact_campaign_stats", "list_attributes",
        "list_segments", "list_lists", "list_folder_lists", "get_list", "list_contacts_of_list", "list_folders",
        "list_transactional_emails", "get_transactional_email_content", "transactional_events",
        "transactional_report", "transactional_report_by_day", "list_blocked", "list_blocked_domains",
        "list_templates", "get_template", "list_campaigns", "get_campaign", "campaign_ab_test_result",
        "campaign_shared_url", "crm_list_deals", "crm_list_companies", "crm_list_tasks", "crm_list_notes",
        "crm_get_deal", "crm_get_company", "crm_get_task", "crm_get_note", "pipelines", "task_types",
        "crm_attributes",
    },
    "write": {
        "create_list", "update_list", "create_template", "update_template", "create_campaign", "update_campaign",
        "crm_create_deal", "crm_create_company", "crm_create_task", "crm_create_note", "crm_update_deal",
        "crm_update_company", "crm_update_task", "crm_update_note", "crm_link_deal", "crm_link_company",
    },
    "sensitive": {
        # Envoie un courriel réel.
        "send_email", "send_campaign_test",
        # L'appartenance à une liste (qui peut lancer ses automatisations) et le consentement d'un contact.
        "upsert_contact", "update_contact", "add_to_list", "remove_from_list",
        # Des milliers de contacts écrits d'un coup ; un fichier de contacts dont le lien part à une adresse.
        "import_contacts", "export_contacts",
        # Détruit pour de bon.
        "delete_list", "delete_contact",
    },
}
# Une écriture qui ne fait que préparer : servie du côté lecture. Aucune : un brouillon de campagne est un
# enregistrement du compte, servi du côté act.
NOT_ACT: set = set()
# La donnée personnelle : où elle est marquée.
PERSONAL = {
    "list_contacts": [("filter",)],
    "get_contact": [("identifier",)],
    "delete_contact": [("identifier",)],
    "update_contact": [("identifier",), ("attributes", "additionalProperties")],
    "contact_campaign_stats": [("identifier",)],
    "upsert_contact": [("email",), ("attributes", "additionalProperties")],
    "import_contacts": [("json_body", "items", "properties", "email"),
                        ("json_body", "items", "properties", "attributes", "additionalProperties"), ("file_body",)],
    "add_to_list": [("emails", "items")],
    "remove_from_list": [("emails", "items")],
    "send_email": [("to", "items", "properties", "email"), ("to", "items", "properties", "name"),
                   ("sender", "properties", "email"), ("subject",), ("html_content",), ("text_content",),
                   ("params", "additionalProperties"), ("reply_to", "properties", "email")],
    "list_transactional_emails": [("email",)],
    "transactional_events": [("email",)],
    "list_blocked": [("senders", "items")],
    "create_template": [("sender", "properties", "email"), ("reply_to",)],
    "update_template": [("sender", "properties", "email"), ("reply_to",)],
    "create_campaign": [("sender", "properties", "email"), ("reply_to",), ("params", "additionalProperties")],
    "update_campaign": [("fields", "properties", "sender", "properties", "email"), ("fields", "properties", "replyTo")],
    "send_campaign_test": [("email_to", "items")],
    "crm_list_deals": [("deal_owner",)],
    "crm_list_companies": [("owner",)],
    "crm_list_tasks": [("assign_to",)],
    **{f"crm_{verb}_{one}": [("payload", "properties", "attributes", "additionalProperties")]
       for verb in ("create", "update") for one in ("deal", "company")},
    **{f"crm_{verb}_task": [("payload", "properties", "assignToId"), ("payload", "properties", "notes")]
       for verb in ("create", "update")},
    **{f"crm_{verb}_note": [("payload", "properties", "text")] for verb in ("create", "update")},
}
# Les clés libres à valeur simple, la forme exacte de la règle 20, sur un objet imbriqué du corps.
SIMPLE_FREE_KEYS = {"type": ["string", "number", "boolean"], "personal": True}
FREE_ATTRIBUTES = {
    "upsert_contact": ("attributes",), "update_contact": ("attributes",),
    "import_contacts": ("json_body", "items", "properties", "attributes"),
    **{f"crm_{verb}_{one}": ("payload", "properties", "attributes")
       for verb in ("create", "update") for one in ("deal", "company")},
}
WRITING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
REQUEST = {"per": "request"}

DEAL = "0000000000000000000000d1"
COMPANY = "0000000000000000000000c1"
# Une valeur par argument, pour un appel qui les donne tous.
VALUES = {
    "ip": "192.0.2.1", "domain": "acme.example", "limit": 20, "offset": 40,
    "modified_since": "2026-01-31T00:00:00.000Z", "created_since": "2026-01-01T00:00:00.000Z", "sort": "asc",
    "segment_id": 7, "list_ids": [3, 7], "ids": [1, 2], "filter": 'equals(FIRSTNAME,"Alex")',
    "identifier": "marie@example.test", "identifier_type": "email_id", "start_date": "2026-10-01",
    "end_date": "2026-10-15", "email": "marie@example.test",
    "attributes": {"FIRSTNAME": "Marie", "SCORE": 3.5, "OPTIN": True, "BIRTHDAY": "1990-02-11"},
    "update_enabled": False, "ext_id": "ext-1", "email_blacklisted": True, "sms_blacklisted": False,
    "unlink_list_ids": [5],
    "json_body": [{"email": "marie@example.test", "attributes": {"FIRSTNAME": "Marie"}}],
    "file_url": "https://files.example.test/contacts.csv", "file_body": "EMAIL;FIRSTNAME\nmarie@example.test;Marie",
    "update_existing_contacts": False, "empty_contacts_attributes": True,
    "new_list": {"listName": "Imported", "folderId": 1}, "notify_url": "https://hooks.example.test/brevo",
    "disable_notification": False, "contact_filter": {"actionForContacts": "subscribed", "listId": 3},
    "export_attributes": ["EMAIL", "FIRSTNAME"], "folder_id": 1, "list_id": 3, "name": "Newsletter",
    "emails": ["marie@example.test"], "ext_ids": ["ext-1"], "all_contacts": True,
    "to": [{"email": "marie@example.test", "name": "Marie"}], "subject": "Hello", "html_content": "<p>Hello</p>",
    "text_content": "Hello", "sender": {"email": "sales@acme.example", "name": "Acme"}, "template_id": 12,
    "params": {"FIRSTNAME": "Marie"}, "cc": [{"email": "paul@example.test"}], "bcc": [{"email": "anne@example.test"}],
    "reply_to": "reply@acme.example", "headers": {"X-Campaign": "q4"}, "tags": ["onboarding"],
    "scheduled_at": "2026-11-01T09:00:00Z", "message_id": "<m-1@smtp.example.test>",
    "uuid": "00000000-0000-4000-8000-000000000001", "days": 7, "event": "delivered", "tag": "onboarding",
    "senders": ["sales@acme.example"], "active_only": False, "template_name": "Follow-up",
    "html_url": "https://pages.example.test/t.html", "to_field": "{{contact.FIRSTNAME}}", "is_active": False,
    "attachment_url": "https://files.example.test/a.pdf", "type": "classic", "status": "sent",
    "statistics": "globalStats", "exclude_html_content": False, "campaign_id": 9,
    "recipients": {"listIds": [3], "exclusionListIds": [4]}, "preview_text": "News",
    "fields": {"subject": "Hello", "recipients": {"listIds": [3]}, "mirrorActive": False},
    "email_to": ["me@acme.example"], "object_id": DEAL, "deal_name": "Acme", "deal_owner": "owner@acme.example",
    "deal_stage": "stage-1", "pipeline": "pipe-1", "linked_companies_ids": COMPANY, "linked_contacts_ids": "12",
    "sort_by": "created_at", "page": 3, "owner": "owner@acme.example", "date": "today",
    "assign_to": "owner@acme.example", "contacts": "12", "deals": DEAL, "companies": COMPANY, "entity": "deals",
    "entity_ids": DEAL, "date_from": 1767225600000, "date_to": 1767312000000, "link_contact_ids": [12],
    "unlink_contact_ids": [13], "link_ids": [COMPANY], "unlink_ids": ["0000000000000000000000c2"],
}
VALUES_OF = {
    "send_email": {"reply_to": {"email": "reply@acme.example", "name": "Acme"}},
    "transactional_events": {"tags": '["onboarding"]'},
    "crm_list_tasks": {"type": "type-0", "status": "done"},
    "crm_create_deal": {"payload": {"name": "Pilot", "attributes": {"amount": 10000, "deal_stage": "stage-0"},
                                    "linkedContactsIds": [12], "linkedCompaniesIds": [COMPANY]}},
    "crm_update_deal": {"payload": {"attributes": {"deal_stage": "stage-1"}}},
    "crm_create_company": {"payload": {"name": "Acme", "attributes": {"domain": "acme.example"}, "countryCode": 33,
                                       "linkedContactsIds": [12], "linkedDealsIds": [DEAL]}},
    "crm_update_company": {"payload": {"name": "Acme SAS"}},
    "crm_create_task": {"payload": {"name": "Call", "taskTypeId": "type-0", "date": "2026-11-02T09:00:00Z",
                                    "duration": 900000, "done": False, "assignToId": "owner@acme.example",
                                    "contactsIds": [12], "reminder": {"value": 1, "unit": "hours",
                                                                      "types": ["email"]}}},
    "crm_update_task": {"payload": {"done": True}},
    "crm_create_note": {"payload": {"text": "<p>Call back.</p>", "dealIds": [DEAL]}},
    "crm_update_note": {"payload": {"text": "<p>Called back.</p>"}},
    "crm_get_company": {"object_id": COMPANY}, "crm_link_company": {"object_id": COMPANY, "link_ids": [DEAL]},
    "crm_attributes": {"entity": "companies"},
}


def _description() -> dict:
    return copy.deepcopy(yaml.safe_load(BREVO.read_text(encoding="utf-8")))


def _functions() -> dict[str, dict]:
    return {f["name"]: f for f in _description()["functions"]}


def _public_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(BrevoClient, inspect.isfunction) if not n.startswith("_")}


def _method(name: str) -> str:
    return ROUTES.get(name, (name, {}))[0]


def _parameters(method: str) -> dict[str, inspect.Parameter]:
    return {p: v for p, v in inspect.signature(getattr(BrevoClient, method)).parameters.items() if p != "self"}


class _Answer:
    status_code = 200
    ok = True
    headers: dict = {}
    content = b"{}"
    text = "{}"

    def json(self):
        return {}


def _to_client(name: str, arguments: dict) -> tuple[str, dict]:
    """La méthode du client et ses arguments pour un appel de la fonction : route fixée, filtres du CRM remis dans la
    table `filters`, `page` des entreprises remise en `offset`, `fields` déplié."""
    method, fixed = ROUTES.get(name, (name, {}))
    given = dict(arguments)
    kwargs = dict(fixed)
    filters = {key: given.pop(a) for a, key in CRM_FILTERS.get(name, {}).items() if a in given}
    if filters:
        kwargs["filters"] = filters
    if name == "crm_list_companies" and "page" in given:
        kwargs["offset"] = (given.pop("page") - 1) * given.get("limit", 50)
    if name == "update_campaign":
        kwargs.update(given.pop("fields"))
    kwargs.update(given)
    return method, kwargs


def _sent_by_the_client(name: str, arguments: dict) -> dict:
    """Ce que le client envoie pour un appel de la fonction, sans réseau : méthode, adresse, query, corps, en-têtes."""
    sent = []

    def request(self, method, url, **kwargs):
        sent.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {}),
                     "json": kwargs.get("json"), "headers": dict(self.headers), "timeout": kwargs.get("timeout")})
        return _Answer()

    method, kwargs = _to_client(name, arguments)
    with mock.patch.object(brevo_base.requests.Session, "request", request):
        getattr(BrevoClient(api_key="xkeysib-k"), method)(**kwargs)
    assert len(sent) == 1, name
    return sent[0]


def _in_query(value):
    """Une valeur en query comme l'écrit l'hôte : booléen en minuscules, nombre en texte, liste élément par élément."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return [_in_query(v) for v in value]
    return str(value)


def _described(name: str, function: dict, arguments: dict) -> tuple:
    """La requête que dit la description, plus ce que le client pose quand l'appel ne le donne pas (`CLIENT_FILLS`)."""
    call = function["call"]
    given = {**CLIENT_FILLS.get(name, {}), **arguments}
    path = call["path"].format(**{a: given[a] for a in re.findall(r"{(\w+)}", call["path"])})
    query = {api: _in_query(given[a]) for api, a in call.get("query", {}).items() if a in given}
    body = None
    if isinstance(call.get("body"), str):
        body = given[call["body"]]
    elif "body" in call:
        body = {api: given[a] for api, a in call["body"].items() if a in given}
    return call["method"], BrevoClient.BASE_URL + path, query, body


def _client_view(sent: dict) -> tuple:
    return sent["method"], sent["url"], {k: _in_query(v) for k, v in sent["params"].items()}, sent["json"]


def _argument_sets(name: str, function: dict) -> list[dict]:
    """Un appel qui donne tous les arguments ; un par branche quand l'entrée en exige une seule (`oneOf`)."""
    values = {**VALUES, **VALUES_OF.get(name, {})}
    every = {a: values[a] for a in function["input"]["properties"]}
    branches = [b["required"][0] for b in function["input"].get("oneOf", [])]
    if not branches:
        return [every]
    return [{a: v for a, v in every.items() if a not in branches or a == kept} for kept in branches]


def _required(name: str) -> dict:
    function = _functions()[name]
    values = {**VALUES, **VALUES_OF.get(name, {})}
    return {a: values[a] for a in function["input"].get("required", [])}


def _valid(function: dict, arguments: dict) -> bool:
    return jsonschema.Draft202012Validator(function["input"]).is_valid(arguments)


def _node(function: dict, path: tuple) -> dict:
    node = function["input"]["properties"]
    for key in path:
        node = node[key]
    return node


def test_the_description_is_version_1_0_0_and_passes_the_fabrique():
    description = _description()
    assert description["connector"]["version"] == "2.0.0"
    check(description, BREVO)


def test_each_method_of_the_client_is_reached_by_its_functions_and_only_the_routes_are_renamed():
    functions = _functions()
    assert {_method(n) for n in functions} == _public_methods()
    assert set(ROUTES) <= set(functions)
    for name in set(functions) - set(ROUTES):
        assert name in _public_methods(), name


def test_the_connector_says_what_the_client_does():
    """Clé v3 en en-tête `api-key` de la session ; délai de 30 s, celui du connecteur par défaut ; aucun rythme
    déclaré (Brevo limite par route et par offre) ; sonde par la fiche du compte, comme oto 1 ; listes répétées en
    query, comme les écrit `requests`."""
    connector = _description()["connector"]
    assert connector["base_url"] == BrevoClient.BASE_URL
    assert connector["auth"] == {"kind": "api_key", "in": "header", "name": "api-key", "key": "api_key"}
    assert connector["credential"] == [{"name": "api_key", "label": "API key (v3)", "secret": True}]
    sent = _sent_by_the_client("get_account", {})
    assert sent["headers"]["api-key"] == "xkeysib-k"
    assert sent["timeout"] == 30 and "timeout_s" not in connector
    assert "rate_limit" not in connector, "Brevo limits per route and per plan"
    assert "platform" not in connector["modes"]
    assert connector["probe"] == {"function": "get_account"}
    assert connector["query_arrays"] == "repeat"
    prepared = requests.Request("GET", BrevoClient.BASE_URL, params=_sent_by_the_client(
        "list_contacts", {"list_ids": [3, 7]})["params"]).prepare()
    assert "listIds=3&listIds=7" in prepared.url
    assert "headers" not in connector, "accept and content-type are set by the host for a JSON call"


def test_each_function_takes_the_arguments_of_its_client_method():
    for name, function in _functions().items():
        method, fixed = ROUTES.get(name, (name, {}))
        parameters = {p for p, v in _parameters(method).items() if v.kind is not inspect.Parameter.VAR_KEYWORD}
        added = ARGUMENTS_ADDED.get(name, {})
        properties = set(function["input"]["properties"])
        taken = {added.get(a, a) for a in properties} - {"**fields"}
        assert taken <= parameters, name
        selector = {SELECTED_BY_PRESENCE[name]} if name in SELECTED_BY_PRESENCE else set()
        assert parameters - taken - set(fixed) - selector == set(ARGUMENTS_LEFT_OUT.get(name, {})), name
    assert set(ARGUMENTS_LEFT_OUT) <= set(_functions())


def test_each_argument_goes_where_the_client_sends_it():
    for name, function in _functions().items():
        for arguments in _argument_sets(name, function):
            jsonschema.validate(arguments, function["input"])
            assert _client_view(_sent_by_the_client(name, arguments)) == _described(name, function, arguments), name


def test_each_example_sends_what_the_client_sends_for_it():
    for name, function in _functions().items():
        for example in function["examples"]:
            jsonschema.validate(example["input"], function["input"])
            sent = _sent_by_the_client(name, example["input"])
            assert _client_view(sent) == _described(name, function, example["input"]), (name, example["title"])


def test_the_route_picked_by_an_argument_is_fixed_by_the_function():
    assert _sent_by_the_client("list_lists", {})["url"].endswith("/contacts/lists")
    assert _sent_by_the_client("list_folder_lists", {"folder_id": 4})["url"].endswith("/contacts/folders/4/lists")
    assert _sent_by_the_client("get_template", {"template_id": 12})["url"].endswith("/smtp/templates/12")
    assert _sent_by_the_client("transactional_report", {})["url"].endswith("/smtp/statistics/aggregatedReport")
    assert _sent_by_the_client("list_blocked_domains", {})["url"].endswith("/smtp/blockedDomains")
    companies = _sent_by_the_client("crm_list_companies", {"page": 3, "limit": 20})
    assert companies["url"].endswith("/companies") and companies["params"]["page"] == 3
    assert "offset" not in companies["params"]


def test_what_the_client_fills_is_never_a_declared_default_and_what_changes_brevo_is_required():
    functions = _functions()
    for name, fills in CLIENT_FILLS.items():
        properties = functions[name]["input"]["properties"]
        assert all("default" not in properties[a] for a in fills), name
        assert not set(fills) & set(functions[name]["input"].get("required", [])), name
        sent = _sent_by_the_client(name, _required(name))
        assert _client_view(sent) == _described(name, functions[name], _required(name)), name
    for name, fills in REQUIRED_WHERE_THE_CLIENT_FILLS.items():
        assert set(fills) <= set(functions[name]["input"]["required"]), name
        assert all("default" not in functions[name]["input"]["properties"][a] for a in fills), name
        defaults = {a: _parameters(_method(name))[a].default for a in fills}
        sent = _sent_by_the_client(name, {a: v for a, v in _required(name).items() if a not in fills})
        call = functions[name]["call"]
        places = {**call.get("query", {}), **(call.get("body") or {})}
        api = {a: next(k for k, v in places.items() if v == a) for a in fills}
        on_the_wire = {**sent["params"], **(sent["json"] or {})}
        assert {a: on_the_wire[api[a]] for a in fills} == fills, name
        assert {a: d for a, d in defaults.items() if d is not None} == {
            a: v for a, v in fills.items() if a != "contact_filter"}, name


def test_the_limit_the_client_lowers_the_schema_refuses():
    functions = _functions()
    for name, cap in CLIENT_CAPS.items():
        assert functions[name]["input"]["properties"]["limit"]["maximum"] == cap, name
        arguments = {**_required(name), "limit": cap + 1}
        assert not _valid(functions[name], arguments), name
        assert _sent_by_the_client(name, arguments)["params"]["limit"] == cap, name


def test_where_the_schema_is_narrower_than_the_client_it_is_named():
    functions = _functions()
    for name, cases in NARROWER.items():
        for case in cases:
            arguments = {**_required(name), **case}
            assert not _valid(functions[name], arguments), (name, case)
            _sent_by_the_client(name, arguments)


def test_what_the_client_refuses_the_schema_refuses():
    functions = _functions()
    for name, cases in CLIENT_REFUSES.items():
        for case in cases:
            arguments = {**{a: v for a, v in _required(name).items() if a != "payload"}, "list_id": 3, **case}
            arguments = {a: v for a, v in arguments.items() if a in functions[name]["input"]["properties"]}
            assert not _valid(functions[name], arguments), (name, case)
            with pytest.raises(ValueError):
                _sent_by_the_client(name, arguments)


def test_the_flags_the_client_sends_only_when_true_are_const_true():
    functions = _functions()
    for name, flags in ONLY_TRUE.items():
        for argument, api in flags.items():
            assert functions[name]["input"]["properties"][argument].get("const") is True, name
            assert api not in _sent_by_the_client(name, {"list_id": 3, "emails": ["a@example.test"],
                                                         argument: False})["json"], name
            assert _sent_by_the_client(name, {"list_id": 3, argument: True})["json"] == {api: True}, name


def test_a_boolean_in_the_query_is_written_true_by_the_host_and_True_by_the_client():
    """Écart nommé : `requests` écrit un booléen Python `True`/`False`, l'hôte `true`/`false`. À relire sur un vrai
    appel : oto 1 envoie `excludeHtmlContent=True` depuis toujours."""
    sent = _sent_by_the_client("list_campaigns", {"exclude_html_content": True})
    url = requests.Request("GET", sent["url"], params=sent["params"]).prepare().url
    assert "excludeHtmlContent=True" in url


def test_reads_and_writes_are_classified_and_every_write_is_served_on_the_act_side():
    functions = _functions()
    assert {c: {n for n, f in functions.items() if f["class"] == c} for c in CLASSES} == CLASSES
    for name, function in functions.items():
        method = function["call"]["method"]
        assert (method in WRITING_METHODS) == (function["class"] != "read"), name
        assert (method == "DELETE") == (name in DELETES), name
        if name in DELETES:
            assert function["class"] == "sensitive" and function["effect"] == "destroy", name
        assert ("confirm" in function) == (function["class"] == "sensitive"), name
        acts = function.get("act", function["class"] != "read")
        assert acts == (function["class"] != "read" and name not in NOT_ACT), name
    assert {n for n, f in functions.items() if "act" in f} == NOT_ACT


def test_what_the_client_keeps_out_stays_out():
    """Ni envoi de campagne, ni planification, ni suppression d'une campagne ou d'un modèle, ni purge : rien de ce que le
    client et oto 1 laissent à l'application Brevo."""
    for name, function in _functions().items():
        call = function["call"]
        assert not re.search(r"sendNow|status|hardbounces", call["path"], re.I), name
        body = call.get("body")
        if body is None:
            continue
        keys = set(body) if isinstance(body, dict) else set(function["input"]["properties"][body].get("properties", {}))
        assert not keys & {"scheduledAt", "sendAtBestTime", "attachment"} or name == "send_email", name
    assert "scheduledAt" in _functions()["send_email"]["call"]["body"], "a transactional email may be scheduled"


def test_no_pagination_is_declared_and_each_call_costs_one_request():
    """Brevo pagine par `limit` et `offset` (`page` pour les entreprises) et rend `count`, jamais la page suivante :
    l'appelant pagine lui-même, par les arguments du client."""
    for name, function in _functions().items():
        assert "pagination" not in function, name
        assert ("output" in function) == (name == "get_account"), name
        assert function["cost"] == REQUEST, name
        paged = {"limit", "offset"} & set(_parameters(_method(name))) - set(ARGUMENTS_LEFT_OUT.get(name, {}))
        taken = {ARGUMENTS_ADDED.get(name, {}).get(a, a) for a in function["input"]["properties"]}
        assert paged <= taken, name


def test_the_personal_values_are_marked():
    functions = _functions()
    for name, paths in PERSONAL.items():
        for path in paths:
            assert _node(functions[name], path).get("personal") is True, (name, path)
    marked = {n for n, f in functions.items() if '"personal": true' in json.dumps(f["input"])}
    assert marked == set(PERSONAL)


def _with_attributes(name: str, attributes: dict) -> dict:
    """Un appel de la fonction dont les attributs libres sont `attributes`, à leur place dans l'entrée."""
    arguments = copy.deepcopy(_argument_sets(name, _functions()[name])[0])
    node, path = arguments, [k for k in FREE_ATTRIBUTES[name] if k != "properties"]
    for key in path[:-1]:
        node = node[0] if key == "items" else node[key]
    node[path[-1]] = attributes
    return arguments


def test_the_free_attributes_are_simple_values_each_personal():
    """Texte, nombre ou booléen, chaque valeur personnelle ; ni null, ni objet, ni liste (un attribut à choix multiple
    est une liste : écart nommé dans `NARROWER`)."""
    functions = _functions()
    simple = {"TEXT": "a", "NUMBER": 2.5, "FLAG": False}
    for name, path in FREE_ATTRIBUTES.items():
        assert _node(functions[name], path)["additionalProperties"] == SIMPLE_FREE_KEYS, name
        assert _valid(functions[name], _with_attributes(name, simple)), name
        _sent_by_the_client(name, _with_attributes(name, simple))
        for bad in (None, {"a": 1}, ["FR"]):
            assert not _valid(functions[name], _with_attributes(name, {**simple, "BAD": bad})), (name, bad)
        assert not _valid(functions[name], _with_attributes(name, {})), name


def test_the_401_and_spent_credits_mark_the_account_and_a_blocked_ip_does_not():
    """Le client ne nomme aucun refus (`raise_for_upstream`). Un 401 marque `credential_rejected`, sauf quand son
    message dit une adresse IP non autorisée (la clé vaut) ; le code `not_enough_credits`, sous un 400 ou un 402,
    marque `quota_exhausted`. Seuls le 425 et le 429 se rejouent."""
    errors = _description()["connector"]["errors"]
    marking = [(e["status"], e["code"], e["marks_account"], e.get("body")) for e in errors if "marks_account" in e]
    credits = {"path": "code", "equals": ["not_enough_credits"]}
    assert marking == [
        ([400, 402], "credits_exhausted", "quota_exhausted", credits),
        (401, "key_refused", "credential_rejected", None),
    ]
    first = {}
    for entry in errors:
        first.setdefault(entry["code"], entry)
    assert first["credits_exhausted"] is errors[0], "before invalid_request (400) and payment_required (402)"
    ip = next(e for e in errors if e["code"] == "ip_not_authorized")
    assert ip["status"] == 401 and "marks_account" not in ip
    assert ip["body"] == {"path": "message", "contains": ["unrecognised ip address"]}
    assert errors.index(ip) < next(i for i, e in enumerate(errors) if e["code"] == "key_refused")
    assert {e["code"] for e in errors if e["retryable"]} == {"account_in_process", "rate_limited"}
    assert not [r for f in _functions().values() for r in f.get("refusals", [])]
    transport = inspect.getsource(BrevoClient._request)
    assert "raise_for_upstream(resp" in transport and "status_code" not in transport


def test_the_typescript_output_and_the_python_table():
    checked = check(_description(), BREVO)
    module = connector_module(checked)
    for name in _functions():
        assert f'name: "brevo.{name}",' in module.source, name
    assert not module.skipped
    table = python.declared([checked])["brevo"]
    assert set(table) == set(_functions())
    assert DECLARED.get("brevo") == table, "oto/connectors/declared.py is out of date: run `python -m fabrique`"


def test_a_refusal_is_listed_once():
    """Un lecteur qui liste les refus d'une fonction (code et message) le fait depuis la table : chaque code y est une
    seule entrée, ses statuts en liste."""
    codes = [e["code"] for e in _description()["connector"]["errors"]]
    assert len(codes) == len(set(codes)), codes


def test_get_account_strips_the_relay_login_and_the_automation_key():
    """Le login du relais SMTP et la clé du suivi d'automatisation n'atteignent jamais l'agent : retirés par la
    description (`output.strip`, appliqué par l'hôte) et par le client, au même endroit."""
    from oto.connectors.serve import shaped
    from oto.tools.brevo import client as brevo_client

    function = _functions()["get_account"]
    assert function["output"]["strip"] == ACCOUNT_STRIPPED
    assert [".".join(p) for p in brevo_client.ACCOUNT_STRIPPED] == ACCOUNT_STRIPPED
    answer = copy.deepcopy(function["output"]["sample"])
    served = shaped(answer, function["output"])
    text = json.dumps(served)
    assert "smtp-login@acme.example" not in text and "tracker-key-0" not in text
    assert served["relay"]["data"] == {"relay": "smtp.example.test", "port": 587}
    assert served["marketingAutomation"] == {"enabled": True}
    assert served["plan"] == answer["plan"]
    assert brevo_client._strip_account(copy.deepcopy(answer)) == served
    typescript = connector_module(check(_description(), BREVO)).source
    assert '"relay.data.userName"' in typescript and '"marketingAutomation.key"' in typescript


def test_the_limit_brevo_fills_otherwise_is_said_by_the_argument():
    functions = _functions()
    for name, defaults in BREVO_DEFAULT_DIFFERS.items():
        properties = functions[name]["input"]["properties"]
        for argument, value in defaults.items():
            assert "default" not in properties[argument], name
            assert argument not in functions[name]["input"].get("required", []), name
            said = properties[argument]["description"]
            assert any(f"Brevo answers {up}{value} when it is absent" in said for up in ("", "up to ")), name
            assert "50 by default" not in said, name
        assert CLIENT_FILLS[name]["limit"] == _parameters(_method(name))["limit"].default == 50, name


def test_a_delete_is_confirmed_and_sends_what_the_client_sends():
    functions = _functions()
    assert {n for n, f in functions.items() if f["call"]["method"] == "DELETE"} == DELETES
    assert _sent_by_the_client("delete_list", {"list_id": 3})["url"].endswith("/contacts/lists/3")
    sent = _sent_by_the_client("delete_contact", {"identifier": "42", "identifier_type": "contact_id"})
    assert sent["method"] == "DELETE" and sent["url"].endswith("/contacts/42")
    assert sent["params"] == {"identifierType": "contact_id"}
    for name in DELETES:
        assert "for good" in functions[name]["confirm"]["summary"], name


def test_the_emails_of_a_period_are_read_from_the_events_without_a_filter():
    """`/smtp/emails` exige une adresse, un modèle ou un identifiant de message ; la période sans filtre se lit dans
    les événements (`requests`, un par email parti), qui n'exigent rien."""
    functions = _functions()
    assert not _valid(functions["list_transactional_emails"], {"start_date": "2026-11-01", "end_date": "2026-11-25"})
    period = {"event": "requests", "start_date": "2026-11-01", "end_date": "2026-11-25"}
    assert _valid(functions["transactional_events"], period)
    assert "transactional_events with event requests" in " ".join(
        functions["list_transactional_emails"]["description"].split())
