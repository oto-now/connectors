"""La surface de `HeyReachClient` est un CONTRAT, pas un détail.

Un consommateur épingle la lib par tag et importe `oto.tools.heyreach` : renommer une méthode, changer un défaut ou
déplacer un symbole casse un consommateur au bump du pin, loin d'ici. Ce test fige les membres AVEC leurs signatures
(ceux des mixins `_api/*` compris), les noms importables et la correspondance entre les fonctions de la description et
les méthodes du client : toute méthode publique est une fonction décrite.

⚠️ Ce test échoue si tu ajoutes une méthode : c'est voulu — mets à jour la constante EN CONNAISSANCE DE CAUSE, jamais
par réflexe pour faire passer le vert.
"""
import inspect
import pathlib

import yaml

import oto.tools.heyreach as pkg
import oto.tools.heyreach.client as mod

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = ROOT / "connectors" / "heyreach" / "connector.yaml"

EXPECTED_MEMBERS = {
    '_call': "(self, method: 'str', path: 'str', *, query: 'Optional[Dict[str, Any]]' = None, body: 'Optional[Dict[str, Any]]' = None, strip: 'Optional[str]' = None) -> 'Any'",
    '_stats_body': "(start_date, end_date, account_ids, campaign_ids) -> 'dict'",
    '_tag_body': "(lead_profile_url, lead_linkedin_id, tags, create_tag_if_not_existing) -> 'dict'",
    'add_lead_tags': "(self, tags: 'List[str]', *, lead_profile_url: 'Optional[str]' = None, lead_linkedin_id: 'Optional[str]' = None, create_tag_if_not_existing: 'Optional[bool]' = None) -> 'Any'",
    'add_leads_to_campaign': "(self, campaign_id: 'int', account_lead_pairs: 'List[dict]', *, resume_paused_campaign: 'Optional[bool]' = None, resume_finished_campaign: 'Optional[bool]' = None) -> 'Any'",
    'add_leads_to_list': "(self, list_id: 'int', leads: 'List[dict]') -> 'Any'",
    'check_api_key': "(self) -> 'Any'",
    'create_empty_list': "(self, name: 'str', type: 'Optional[str]' = None) -> 'Any'",
    'create_webhook': "(self, webhook_name: 'str', webhook_url: 'str', event_type: 'str', *, campaign_ids: 'Optional[List[int]]' = None) -> 'Any'",
    'delete_webhook': "(self, webhook_id: 'int') -> 'Any'",
    'get_campaign': "(self, campaign_id: 'int') -> 'Any'",
    'get_conversation': "(self, linkedin_account_id: 'int', conversation_id: 'str') -> 'Any'",
    'get_lead': "(self, profile_url: 'str') -> 'Any'",
    'get_lead_tags': "(self, profile_url: 'str') -> 'Any'",
    'get_linkedin_account': "(self, account_id: 'int') -> 'Any'",
    'get_list': "(self, list_id: 'int') -> 'Any'",
    'get_overall_stats': "(self, start_date: 'str', end_date: 'str', *, account_ids: 'Optional[List[int]]' = None, campaign_ids: 'Optional[List[int]]' = None) -> 'Any'",
    'get_stats_by_campaign': "(self, start_date: 'str', end_date: 'str', *, account_ids: 'Optional[List[int]]' = None, campaign_ids: 'Optional[List[int]]' = None) -> 'Any'",
    'get_webhook': "(self, webhook_id: 'int') -> 'Any'",
    'check_connection': "(self, sender_account_id: 'int', *, lead_profile_url: 'Optional[str]' = None, lead_linkedin_id: 'Optional[str]' = None) -> 'Any'",
    'list_campaign_leads': "(self, campaign_id: 'int', limit: 'int', offset: 'Optional[int]' = None, *, time_from: 'Optional[str]' = None, time_to: 'Optional[str]' = None, time_filter: 'Optional[str]' = None) -> 'Any'",
    'list_campaigns': "(self, limit: 'int', offset: 'Optional[int]' = None, *, keyword: 'Optional[str]' = None, statuses: 'Optional[List[str]]' = None, account_ids: 'Optional[List[int]]' = None) -> 'Any'",
    'list_conversations': "(self, limit: 'Optional[int]' = None, cursor: 'Optional[str]' = None, *, last_message_from: 'Optional[str]' = None, last_message_to: 'Optional[str]' = None, linkedin_account_ids: 'Optional[List[int]]' = None, campaign_ids: 'Optional[List[int]]' = None, search: 'Optional[str]' = None, lead_linkedin_id: 'Optional[str]' = None, lead_profile_url: 'Optional[str]' = None, tags: 'Optional[List[str]]' = None, latest_auto_tag_names: 'Optional[List[str]]' = None, seen: 'Optional[bool]' = None) -> 'Any'",
    'list_lead_campaigns': "(self, limit: 'int', offset: 'Optional[int]' = None, *, profile_url: 'Optional[str]' = None, linkedin_id: 'Optional[str]' = None, email: 'Optional[str]' = None) -> 'Any'",
    'list_lead_lists': "(self, limit: 'int', offset: 'Optional[int]' = None, *, profile_url: 'Optional[str]' = None, linkedin_id: 'Optional[str]' = None, email: 'Optional[str]' = None) -> 'Any'",
    'list_linkedin_accounts': "(self, limit: 'int', offset: 'Optional[int]' = None, *, keyword: 'Optional[str]' = None) -> 'Any'",
    'list_list_leads': "(self, list_id: 'int', limit: 'int', offset: 'Optional[int]' = None, *, keyword: 'Optional[str]' = None, lead_profile_url: 'Optional[str]' = None, lead_linkedin_id: 'Optional[str]' = None, created_from: 'Optional[str]' = None, created_to: 'Optional[str]' = None) -> 'Any'",
    'list_lists': "(self, limit: 'int', offset: 'Optional[int]' = None, *, keyword: 'Optional[str]' = None, list_type: 'Optional[str]' = None, campaign_ids: 'Optional[List[int]]' = None) -> 'Any'",
    'list_webhooks': "(self, limit: 'int', offset: 'Optional[int]' = None) -> 'Any'",
    'pause_campaign': "(self, campaign_id: 'int') -> 'Any'",
    'replace_lead_tags': "(self, tags: 'List[str]', *, lead_profile_url: 'Optional[str]' = None, lead_linkedin_id: 'Optional[str]' = None, create_tag_if_not_existing: 'Optional[bool]' = None) -> 'Any'",
    'resume_campaign': "(self, campaign_id: 'int') -> 'Any'",
    'send_message': "(self, linkedin_account_id: 'int', conversation_id: 'str', message: 'str', *, subject: 'Optional[str]' = None) -> 'Any'",
    'set_seen_status': "(self, linkedin_account_id: 'int', conversation_id: 'str', seen: 'bool') -> 'Any'",
    'stop_lead_in_campaign': "(self, campaign_id: 'int', *, lead_member_id: 'Optional[str]' = None, lead_url: 'Optional[str]' = None) -> 'Any'",
}


def _members():
    return {n: str(inspect.signature(getattr(mod.HeyReachClient, n)))
            for n in dir(mod.HeyReachClient)
            if not n.startswith("__") and callable(getattr(mod.HeyReachClient, n))}


def _functions():
    return {f["name"]: f for f in yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))["functions"]}


def test_membres_et_signatures_figes():
    assert _members() == EXPECTED_MEMBERS


def test_constructeur_fige():
    assert str(inspect.signature(mod.HeyReachClient.__init__)) == "(self, api_key: 'Optional[str]' = None)"


def test_noms_importables():
    assert pkg.__all__ == ["HeyReachClient", "HeyReachUnreadable"]
    assert pkg.HeyReachClient is mod.HeyReachClient
    assert pkg.HeyReachUnreadable is mod.HeyReachUnreadable
    assert mod.__all__ == ["HeyReachClient", "HeyReachUnreadable", "BASE_URL", "STRIP", "without_hidden"]
    assert mod.BASE_URL == "https://api.heyreach.io/api/public"


def test_une_fonction_decrite_est_une_methode_du_meme_nom():
    public = {n for n in EXPECTED_MEMBERS if not n.startswith("_")}
    assert set(_functions()) == public


def test_modules_sous_500_lignes():
    for path in (ROOT / "oto" / "tools" / "heyreach").rglob("*.py"):
        assert len(path.read_text(encoding="utf-8").splitlines()) < 500, path.name
