"""La surface de `BrevoClient` est un CONTRAT, pas un détail.

Un consommateur épingle la lib par tag et importe `oto.tools.brevo` : renommer une méthode, changer un défaut ou
déplacer un symbole casse un consommateur au bump du pin, loin d'ici. Ce test fige les membres AVEC leurs signatures,
les noms importables, la composition des mixins et la correspondance entre les fonctions de la description et les
méthodes du client.

⚠️ Ce test échoue si tu ajoutes une méthode : c'est voulu — mets à jour la constante EN CONNAISSANCE DE CAUSE, jamais
par réflexe pour faire passer le vert.
"""
import inspect
import pathlib

import yaml

import oto.tools.brevo as pkg
import oto.tools.brevo.client as mod
from oto.tools.brevo._base import _BrevoBase
from oto.tools.brevo.campaigns import CampaignsMixin
from oto.tools.brevo.contacts import ContactsMixin
from oto.tools.brevo.crm import CrmMixin
from oto.tools.brevo.email import TransactionalEmailMixin

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = ROOT / "connectors" / "brevo" / "connector.yaml"

EXPECTED_MEMBERS = {
    '_clean': "(params: 'Dict[str, Any]') -> 'Dict[str, Any]'",
    '_entity': "(entity: 'str') -> 'tuple'",
    '_list_membership': "(self, list_id: 'int', action: 'str', emails, ids, ext_ids, all_)",
    '_request': "(self, method: 'str', path: 'str', **kwargs) -> 'Any'",
    'add_to_list': "(self, list_id: 'int', emails: 'Optional[List[str]]' = None, ids: 'Optional[List[int]]' = None, ext_ids: 'Optional[List[str]]' = None) -> 'Dict[str, Any]'",
    'campaign_ab_test_result': "(self, campaign_id: 'int') -> 'Dict[str, Any]'",
    'campaign_shared_url': "(self, campaign_id: 'int') -> 'Dict[str, Any]'",
    'contact_campaign_stats': "(self, identifier: 'str', start_date: 'Optional[str]' = None, end_date: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'create_campaign': "(self, name: 'str', sender: 'Dict[str, str]', subject: 'Optional[str]' = None, html_content: 'Optional[str]' = None, html_url: 'Optional[str]' = None, template_id: 'Optional[int]' = None, recipients: 'Optional[Dict[str, Any]]' = None, scheduled_at: 'Optional[str]' = None, reply_to: 'Optional[str]' = None, preview_text: 'Optional[str]' = None, tag: 'Optional[str]' = None, params: 'Optional[Dict[str, Any]]' = None) -> 'Dict[str, Any]'",
    'create_list': "(self, name: 'str', folder_id: 'int') -> 'Dict[str, Any]'",
    'create_template': "(self, template_name: 'str', subject: 'str', sender: 'Dict[str, str]', html_content: 'Optional[str]' = None, html_url: 'Optional[str]' = None, reply_to: 'Optional[str]' = None, to_field: 'Optional[str]' = None, tag: 'Optional[str]' = None, is_active: 'bool' = True, attachment_url: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'crm_attributes': "(self, entity: 'str') -> 'Dict[str, Any]'",
    'crm_create': "(self, entity: 'str', payload: 'Dict[str, Any]') -> 'Dict[str, Any]'",
    'crm_get': "(self, entity: 'str', object_id: 'str') -> 'Dict[str, Any]'",
    'crm_link': "(self, entity: 'str', object_id: 'str', link_contact_ids: 'Optional[List[int]]' = None, unlink_contact_ids: 'Optional[List[int]]' = None, link_ids: 'Optional[List[str]]' = None, unlink_ids: 'Optional[List[str]]' = None) -> 'Dict[str, Any]'",
    'crm_list': "(self, entity: 'str', limit: 'int' = 50, offset: 'int' = 0, filters: 'Optional[Dict[str, Any]]' = None, sort: 'Optional[str]' = None, sort_by: 'Optional[str]' = None, modified_since: 'Optional[str]' = None, created_since: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'crm_update': "(self, entity: 'str', object_id: 'str', payload: 'Dict[str, Any]') -> 'Dict[str, Any]'",
    'delete_contact': "(self, identifier: 'str', identifier_type: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'delete_list': "(self, list_id: 'int') -> 'Dict[str, Any]'",
    'export_contacts': "(self, contact_filter: 'Optional[Dict[str, Any]]' = None, export_attributes: 'Optional[List[str]]' = None, notify_url: 'Optional[str]' = None, disable_notification: 'bool' = True) -> 'Dict[str, Any]'",
    'get_account': "(self) -> 'Dict[str, Any]'",
    'get_campaign': "(self, campaign_id: 'int', statistics: 'Optional[str]' = None, exclude_html_content: 'bool' = True) -> 'Dict[str, Any]'",
    'get_contact': "(self, identifier: 'str', identifier_type: 'Optional[str]' = None, start_date: 'Optional[str]' = None, end_date: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'get_list': "(self, list_id: 'int') -> 'Dict[str, Any]'",
    'get_transactional_email_content': "(self, uuid: 'str') -> 'Dict[str, Any]'",
    'import_contacts': "(self, list_ids: 'Optional[List[int]]' = None, json_body: 'Optional[List[Dict[str, Any]]]' = None, file_url: 'Optional[str]' = None, file_body: 'Optional[str]' = None, update_existing_contacts: 'bool' = True, empty_contacts_attributes: 'bool' = False, new_list: 'Optional[Dict[str, Any]]' = None, notify_url: 'Optional[str]' = None, disable_notification: 'bool' = True) -> 'Dict[str, Any]'",
    'list_attributes': "(self) -> 'Dict[str, Any]'",
    'list_blocked': "(self, domains: 'bool' = False, start_date: 'Optional[str]' = None, end_date: 'Optional[str]' = None, limit: 'int' = 50, offset: 'int' = 0, senders: 'Optional[List[str]]' = None, sort: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'list_campaigns': "(self, type: 'Optional[str]' = None, status: 'Optional[str]' = None, statistics: 'Optional[str]' = None, start_date: 'Optional[str]' = None, end_date: 'Optional[str]' = None, limit: 'int' = 50, offset: 'int' = 0, sort: 'Optional[str]' = None, exclude_html_content: 'bool' = True) -> 'Dict[str, Any]'",
    'list_contacts': "(self, limit: 'int' = 50, offset: 'int' = 0, modified_since: 'Optional[str]' = None, created_since: 'Optional[str]' = None, sort: 'Optional[str]' = None, segment_id: 'Optional[int]' = None, list_ids: 'Optional[List[int]]' = None, ids: 'Optional[List[int]]' = None, filter: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'list_contacts_of_list': "(self, list_id: 'int', limit: 'int' = 50, offset: 'int' = 0, modified_since: 'Optional[str]' = None, sort: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'list_folders': "(self, limit: 'int' = 50, offset: 'int' = 0, sort: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'list_lists': "(self, limit: 'int' = 50, offset: 'int' = 0, sort: 'Optional[str]' = None, folder_id: 'Optional[int]' = None) -> 'Dict[str, Any]'",
    'list_segments': "(self, limit: 'int' = 50, offset: 'int' = 0, sort: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'list_senders': "(self, ip: 'Optional[str]' = None, domain: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'list_templates': "(self, template_id: 'Optional[int]' = None, active_only: 'Optional[bool]' = None, limit: 'int' = 50, offset: 'int' = 0, sort: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'list_transactional_emails': "(self, email: 'Optional[str]' = None, template_id: 'Optional[int]' = None, message_id: 'Optional[str]' = None, start_date: 'Optional[str]' = None, end_date: 'Optional[str]' = None, limit: 'int' = 50, offset: 'int' = 0, sort: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'pipelines': "(self) -> 'Dict[str, Any]'",
    'remove_from_list': "(self, list_id: 'int', emails: 'Optional[List[str]]' = None, ids: 'Optional[List[int]]' = None, ext_ids: 'Optional[List[str]]' = None, all_contacts: 'bool' = False) -> 'Dict[str, Any]'",
    'send_campaign_test': "(self, campaign_id: 'int', email_to: 'List[str]') -> 'Dict[str, Any]'",
    'send_email': "(self, to: 'List[Dict[str, str]]', subject: 'Optional[str]' = None, html_content: 'Optional[str]' = None, text_content: 'Optional[str]' = None, sender: 'Optional[Dict[str, str]]' = None, template_id: 'Optional[int]' = None, params: 'Optional[Dict[str, Any]]' = None, cc: 'Optional[List[Dict[str, str]]]' = None, bcc: 'Optional[List[Dict[str, str]]]' = None, reply_to: 'Optional[Dict[str, str]]' = None, attachment: 'Optional[List[Dict[str, str]]]' = None, headers: 'Optional[Dict[str, str]]' = None, tags: 'Optional[List[str]]' = None, scheduled_at: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'task_types': "(self) -> 'Dict[str, Any]'",
    'transactional_events': "(self, limit: 'int' = 50, offset: 'int' = 0, start_date: 'Optional[str]' = None, end_date: 'Optional[str]' = None, days: 'Optional[int]' = None, email: 'Optional[str]' = None, event: 'Optional[str]' = None, tags: 'Optional[str]' = None, message_id: 'Optional[str]' = None, template_id: 'Optional[int]' = None, sort: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'transactional_report': "(self, by_day: 'bool' = False, start_date: 'Optional[str]' = None, end_date: 'Optional[str]' = None, days: 'Optional[int]' = None, tag: 'Optional[str]' = None, limit: 'int' = 10, offset: 'int' = 0) -> 'Dict[str, Any]'",
    'update_campaign': "(self, campaign_id: 'int', **fields: 'Any') -> 'Dict[str, Any]'",
    'update_contact': "(self, identifier: 'str', attributes: 'Optional[Dict[str, Any]]' = None, list_ids: 'Optional[List[int]]' = None, unlink_list_ids: 'Optional[List[int]]' = None, identifier_type: 'Optional[str]' = None, email_blacklisted: 'Optional[bool]' = None, sms_blacklisted: 'Optional[bool]' = None, ext_id: 'Optional[str]' = None) -> 'Dict[str, Any]'",
    'update_list': "(self, list_id: 'int', name: 'Optional[str]' = None, folder_id: 'Optional[int]' = None) -> 'Dict[str, Any]'",
    'update_template': "(self, template_id: 'int', template_name: 'Optional[str]' = None, subject: 'Optional[str]' = None, sender: 'Optional[Dict[str, str]]' = None, html_content: 'Optional[str]' = None, html_url: 'Optional[str]' = None, reply_to: 'Optional[str]' = None, to_field: 'Optional[str]' = None, tag: 'Optional[str]' = None, is_active: 'Optional[bool]' = None) -> 'Dict[str, Any]'",
    'upsert_contact': "(self, email: 'Optional[str]' = None, attributes: 'Optional[Dict[str, Any]]' = None, list_ids: 'Optional[List[int]]' = None, update_enabled: 'bool' = True, ext_id: 'Optional[str]' = None, email_blacklisted: 'Optional[bool]' = None, sms_blacklisted: 'Optional[bool]' = None) -> 'Dict[str, Any]'",
}

#: Les méthodes qui choisissent leur route par un argument : une fonction par route (`ROUTES` de
#: `tests/test_brevo_description.py`), sous un autre nom que la méthode.
SPLIT = {
    "list_lists": {"list_lists", "list_folder_lists"},
    "list_templates": {"list_templates", "get_template"},
    "transactional_report": {"transactional_report", "transactional_report_by_day"},
    "list_blocked": {"list_blocked", "list_blocked_domains"},
    "crm_list": {"crm_list_deals", "crm_list_companies", "crm_list_tasks", "crm_list_notes"},
    "crm_get": {"crm_get_deal", "crm_get_company", "crm_get_task", "crm_get_note"},
    "crm_create": {"crm_create_deal", "crm_create_company", "crm_create_task", "crm_create_note"},
    "crm_update": {"crm_update_deal", "crm_update_company", "crm_update_task", "crm_update_note"},
    "crm_link": {"crm_link_deal", "crm_link_company"},
}


def _members():
    return {n: str(inspect.signature(getattr(mod.BrevoClient, n)))
            for n in dir(mod.BrevoClient)
            if not n.startswith("__") and callable(getattr(mod.BrevoClient, n))}


def _functions():
    return {f["name"]: f for f in yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))["functions"]}


def test_membres_et_signatures_figes():
    assert _members() == EXPECTED_MEMBERS


def test_constructeur_fige():
    assert str(inspect.signature(mod.BrevoClient.__init__)) == "(self, api_key: 'Optional[str]' = None)"


def test_noms_importables_et_composition():
    assert pkg.__all__ == ["BrevoClient"]
    assert pkg.BrevoClient is mod.BrevoClient
    assert mod.BrevoClient.BASE_URL == "https://api.brevo.com/v3"
    assert mod.BrevoClient.__mro__[1:6] == (
        ContactsMixin, TransactionalEmailMixin, CampaignsMixin, CrmMixin, _BrevoBase)


def test_une_fonction_decrite_est_une_methode_ou_une_route_d_une_methode():
    public = {n for n in EXPECTED_MEMBERS if not n.startswith("_")}
    expected = (public - set(SPLIT)) | set().union(*SPLIT.values())
    assert set(_functions()) == expected
