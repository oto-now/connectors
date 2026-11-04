"""La surface d'`InstantlyClient` est un CONTRAT, pas un détail.

oto-backend épingle oto-core par tag et importe `oto.tools.instantly` : renommer une méthode, changer un défaut ou
déplacer un symbole hors de `client.py` casse un consommateur au bump du pin, loin d'ici. Ce test fige les membres
AVEC leurs signatures, les noms importables depuis `oto.tools.instantly[.client]`, la taille des modules du paquet
et la correspondance entre les fonctions de la description (`connectors/instantly/connector.yaml`) et les méthodes
publiques du client : une fonction décrite, une méthode du même nom, et rien d'autre.

⚠️ Ce test échoue si tu ajoutes une méthode : c'est voulu — mets à jour la constante EN CONNAISSANCE DE CAUSE,
jamais par réflexe pour faire passer le vert.
"""
import inspect
import pathlib

import yaml

import oto.tools.instantly as pkg
import oto.tools.instantly.client as mod

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = ROOT / "connectors" / "instantly" / "connector.yaml"

EXPECTED_MEMBERS = {
    '_call': "(self, method: 'str', path: 'str', *, params: 'Optional[Dict[str, Any]]' = None, json: 'Optional[Dict[str, Any]]' = None) -> 'Any'",
    '_get': "(self, path: 'str', params: 'Optional[Dict[str, Any]]' = None) -> 'Any'",
    '_send': "(self, method: 'str', path: 'str', body: 'Optional[Dict[str, Any]]' = None) -> 'Any'",
    'activate_campaign': "(self, campaign_id: 'str') -> 'Any'",
    'add_block_list_entry': "(self, bl_value: 'str') -> 'Any'",
    'add_leads': "(self, leads: 'List[dict]', *, campaign_id: 'Optional[str]' = None, list_id: 'Optional[str]' = None, blocklist_id: 'Optional[str]' = None, skip_if_in_workspace: 'Optional[bool]' = None, skip_if_in_campaign: 'Optional[bool]' = None, skip_if_in_list: 'Optional[bool]' = None) -> 'Any'",
    'count_unread_emails': "(self) -> 'Any'",
    'create_lead': "(self, email: 'str', *, campaign: 'Optional[str]' = None, list_id: 'Optional[str]' = None, first_name: 'Optional[str]' = None, last_name: 'Optional[str]' = None, company_name: 'Optional[str]' = None, job_title: 'Optional[str]' = None, phone: 'Optional[str]' = None, website: 'Optional[str]' = None, personalization: 'Optional[str]' = None, custom_variables: 'Variables' = None, blocklist_id: 'Optional[str]' = None, skip_if_in_workspace: 'Optional[bool]' = None, skip_if_in_campaign: 'Optional[bool]' = None, skip_if_in_list: 'Optional[bool]' = None) -> 'Any'",
    'create_lead_list': "(self, name: 'str') -> 'Any'",
    'create_webhook': "(self, target_hook_url: 'str', *, event_type: 'Optional[str]' = None, custom_interest_value: 'Optional[int]' = None, campaign: 'Optional[str]' = None, name: 'Optional[str]' = None, headers: 'Optional[Dict[str, str]]' = None) -> 'Any'",
    'delete_webhook': "(self, webhook_id: 'str') -> 'Any'",
    'get_account': "(self, email: 'str') -> 'Any'",
    'get_background_job': "(self, job_id: 'str', *, data_fields: 'Optional[str]' = None) -> 'Any'",
    'get_campaign': "(self, campaign_id: 'str') -> 'Any'",
    'get_campaign_analytics': "(self, *, campaign_id: 'Optional[str]' = None, campaign_ids: 'Optional[List[str]]' = None, start_date: 'Optional[str]' = None, end_date: 'Optional[str]' = None, exclude_total_leads_count: 'Optional[bool]' = None) -> 'Any'",
    'get_campaign_analytics_overview': "(self, *, campaign_id: 'Optional[str]' = None, campaign_ids: 'Optional[List[str]]' = None, start_date: 'Optional[str]' = None, end_date: 'Optional[str]' = None, campaign_status: 'Optional[int]' = None, expand_crm_events: 'Optional[bool]' = None) -> 'Any'",
    'get_campaign_daily_analytics': "(self, *, campaign_id: 'Optional[str]' = None, start_date: 'Optional[str]' = None, end_date: 'Optional[str]' = None, campaign_status: 'Optional[int]' = None) -> 'Any'",
    'get_campaign_steps_analytics': "(self, *, campaign_id: 'Optional[str]' = None, start_date: 'Optional[str]' = None, end_date: 'Optional[str]' = None, include_opportunities_count: 'Optional[bool]' = None) -> 'Any'",
    'get_email': "(self, email_id: 'str') -> 'Any'",
    'get_lead': "(self, lead_id: 'str') -> 'Any'",
    'get_lead_list': "(self, list_id: 'str') -> 'Any'",
    'get_workspace': "(self) -> 'Any'",
    'list_accounts': "(self, *, search: 'Optional[str]' = None, status: 'Optional[int]' = None, provider_code: 'Optional[int]' = None, tag_ids: 'Optional[str]' = None, limit: 'Optional[int]' = None, starting_after: 'Optional[str]' = None) -> 'Any'",
    'list_campaigns': "(self, *, search: 'Optional[str]' = None, status: 'Optional[int]' = None, tag_ids: 'Optional[str]' = None, limit: 'Optional[int]' = None, starting_after: 'Optional[str]' = None) -> 'Any'",
    'list_emails': "(self, *, campaign_id: 'Optional[str]' = None, list_id: 'Optional[str]' = None, lead: 'Optional[str]' = None, search: 'Optional[str]' = None, eaccount: 'Optional[str]' = None, email_type: 'Optional[str]' = None, is_unread: 'Optional[bool]' = None, i_status: 'Optional[int]' = None, mode: 'Optional[str]' = None, latest_of_thread: 'Optional[bool]' = None, preview_only: 'Optional[bool]' = None, company_domain: 'Optional[str]' = None, min_timestamp_created: 'Optional[str]' = None, max_timestamp_created: 'Optional[str]' = None, sort_order: 'Optional[str]' = None, limit: 'Optional[int]' = None, starting_after: 'Optional[str]' = None) -> 'Any'",
    'list_lead_labels': "(self, *, search: 'Optional[str]' = None, interest_status: 'Optional[str]' = None, limit: 'Optional[int]' = None, starting_after: 'Optional[str]' = None) -> 'Any'",
    'list_lead_lists': "(self, *, search: 'Optional[str]' = None, has_enrichment_task: 'Optional[bool]' = None, limit: 'Optional[int]' = None, starting_after: 'Optional[str]' = None) -> 'Any'",
    'list_leads': "(self, *, campaign: 'Optional[str]' = None, list_id: 'Optional[str]' = None, search: 'Optional[str]' = None, contacts: 'Optional[List[str]]' = None, ids: 'Optional[List[str]]' = None, filter: 'Optional[str]' = None, in_campaign: 'Optional[bool]' = None, in_list: 'Optional[bool]' = None, distinct_contacts: 'Optional[bool]' = None, limit: 'Optional[int]' = None, starting_after: 'Optional[str]' = None) -> 'Any'",
    'list_webhook_event_types': "(self) -> 'Any'",
    'list_webhooks': "(self, *, campaign: 'Optional[str]' = None, event_type: 'Optional[str]' = None, limit: 'Optional[int]' = None, starting_after: 'Optional[str]' = None) -> 'Any'",
    'mark_thread_as_read': "(self, thread_id: 'str') -> 'Any'",
    'move_leads': "(self, *, campaign: 'Optional[str]' = None, list_id: 'Optional[str]' = None, to_campaign_id: 'Optional[str]' = None, to_list_id: 'Optional[str]' = None, ids: 'Optional[List[str]]' = None, contacts: 'Optional[List[str]]' = None, search: 'Optional[str]' = None, filter: 'Optional[str]' = None, limit: 'Optional[int]' = None, copy_leads: 'Optional[bool]' = None, check_duplicates: 'Optional[bool]' = None, check_duplicates_in_campaigns: 'Optional[bool]' = None, skip_invalid_emails: 'Optional[bool]' = None) -> 'Any'",
    'pause_campaign': "(self, campaign_id: 'str') -> 'Any'",
    'reply_to_email': "(self, reply_to_uuid: 'str', eaccount: 'str', subject: 'str', *, text: 'Optional[str]' = None, html: 'Optional[str]' = None, cc_address_email_list: 'Optional[str]' = None, bcc_address_email_list: 'Optional[str]' = None) -> 'Any'",
    'search_campaigns_by_lead': "(self, search: 'str', *, sort_column: 'Optional[str]' = None, sort_order: 'Optional[str]' = None) -> 'Any'",
    'update_lead': "(self, lead_id: 'str', *, first_name: 'Optional[str]' = None, last_name: 'Optional[str]' = None, company_name: 'Optional[str]' = None, job_title: 'Optional[str]' = None, phone: 'Optional[str]' = None, website: 'Optional[str]' = None, personalization: 'Optional[str]' = None, pl_value_lead: 'Optional[str]' = None, custom_variables: 'Variables' = None) -> 'Any'",
    'update_lead_interest_status': "(self, lead_email: 'str', interest_value: 'Optional[int]', *, campaign_id: 'Optional[str]' = None, list_id: 'Optional[str]' = None) -> 'Any'",
}


def _members():
    return {n: str(inspect.signature(getattr(mod.InstantlyClient, n)))
            for n in dir(mod.InstantlyClient)
            if not n.startswith("__") and callable(getattr(mod.InstantlyClient, n))}


def test_membres_et_signatures_figes():
    assert _members() == EXPECTED_MEMBERS


def test_constructeur_fige():
    assert str(inspect.signature(mod.InstantlyClient.__init__)) == "(self, api_key: 'Optional[str]' = None)"


def test_noms_importables():
    assert pkg.__all__ == ["InstantlyClient", "without_attachment_urls", "without_webhook_headers"]
    for name in mod.__all__:
        assert hasattr(mod, name), name
    assert mod.BASE_URL == "https://api.instantly.ai/api/v2"
    assert str(inspect.signature(pkg.without_webhook_headers)) == "(answer: 'Any') -> 'Any'"
    assert str(inspect.signature(pkg.without_attachment_urls)) == "(answer: 'Any') -> 'Any'"


def test_une_fonction_decrite_est_une_methode_du_meme_nom():
    functions = {f["name"] for f in yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))["functions"]}
    public = {n for n in EXPECTED_MEMBERS if not n.startswith("_")}
    assert functions == public


def test_aucun_module_de_500_lignes_ou_plus():
    package = pathlib.Path(mod.__file__).parent
    for path in package.rglob("*.py"):
        assert len(path.read_text(encoding="utf-8").splitlines()) < 500, path
