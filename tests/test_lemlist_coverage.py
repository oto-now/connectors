"""Inventaire : CHAQUE endpoint documenté de lemlist a un chemin dans le client.

Le connecteur doit refléter l'API entière, sans exception — une exigence qui ne
tient pas en prose : 141 routes, et celle qu'on oublie ne se signale jamais. Le
garde-fou est donc un INVENTAIRE, à la manière de `docs/conventions.md` côté
oto-backend : la liste ci-dessous est relevée de la doc lemlist
(developer.lemlist.com/llms.txt, 142 pages → 141 routes distinctes, le 2026-08-31),
et confrontée aux chemins que `client.py` construit RÉELLEMENT.

Ce qu'il attrape, et que rien d'autre n'attrape : une route jamais implémentée,
une faute de frappe dans un chemin (`/unsubs/export` vs `/unsubscribes/export`),
et une route qu'un refactor déplace. Ce qu'il n'attrape PAS, et qu'il ne prétend
pas attraper : que la charge envoyée soit la bonne — ça, c'est le travail des
tests de payload dans `test_lemlist_client.py`.

⚠️ Deux routes documentées SÉPARÉMENT partagent un (verbe, chemin) :
`DELETE /campaigns/{id}/leads/{id}` sert la suppression ET la désinscription,
départagées par le paramètre `action`. D'où 141 lignes pour 142 pages.

Retouche du 2026-11-15 : `GET /unsubscribes` (la liste v1) sort de la doc et
répond 404 — `list_unsubscribes` lit la liste v2 ; trois routes du pool
d'expéditeurs d'une campagne (`GET`/`POST`/`PUT /campaigns/{id}/senders`)
entrent. 143 lignes.
"""
from __future__ import annotations

import inspect
import re

from oto.tools.lemlist import client as lm


def _paths_built_by_the_client() -> set[tuple[str, str]]:
    """Les (verbe, chemin) que `client.py` construit, relevés dans sa SOURCE.

    Lecture statique volontaire : exécuter le client demanderait une clé et un
    réseau, et l'inventaire porte sur ce que le code SAIT atteindre, pas sur ce
    qu'un run particulier atteint. Les f-strings sont normalisées
    (`f"campaigns/{cid}/statutes"` → `campaigns/{}/statutes`) pour se comparer
    aux chemins de la doc.
    """
    src = inspect.getsource(lm)
    found = set()
    # `self._request("VERB", f"chemin"…)` — la voie normale.
    for verb, path in re.findall(
        r'_request\(\s*"(GET|POST|PATCH|PUT|DELETE)",\s*f?"([^"]*)"', src,
    ):
        found.add((verb, re.sub(r"\{[^}]*\}", "{}", path).strip("/")))
    # `requests.get(f"{self.BASE_URL}/chemin"…)` — l'export CSV, qui rend du
    # texte et court-circuite donc `_request`.
    for path in re.findall(r'requests\.get\(\s*\n?\s*f"\{self\.BASE_URL\}/([^"]*)"', src):
        found.add(("GET", re.sub(r"\{[^}]*\}", "{}", path).strip("/")))
    return found


#: Relevé de developer.lemlist.com le 2026-08-31. Une ligne = une route.
DOCUMENTED: tuple[tuple[str, str], ...] = (
    ("GET", "activities"),  # activities_get-many-activities
    ("DELETE", "activities/{}/recording-transcript"),  # activities_delete-activity-recording-transcript
    ("GET", "campaigns"),  # campaigns_get-many-campaigns
    ("POST", "campaigns"),  # campaigns_create-campaign
    ("GET", "campaigns/reports"),  # campaigns_get-campaign-reports
    ("GET", "campaigns/{}"),  # campaigns_get-campaign
    ("PATCH", "campaigns/{}"),  # campaigns_update-campaign
    ("GET", "campaigns/{}/senders"),  # senders_get-campaign-senders (2026-11-15)
    ("POST", "campaigns/{}/senders"),  # senders_add-campaign-senders (2026-11-15)
    ("PUT", "campaigns/{}/senders"),  # senders_replace-campaign-senders (2026-11-15)
    ("POST", "campaigns/{}/duplicate"),  # campaigns_duplicate-campaign
    ("GET", "campaigns/{}/export/leads"),  # campaigns_export-campaign-leads
    ("GET", "campaigns/{}/export/start"),  # campaigns_start-campaign-export
    ("PUT", "campaigns/{}/export/{}/email/{}"),  # campaigns_set-export-email-notification
    ("GET", "campaigns/{}/export/{}/status"),  # campaigns_get-campaign-export-status
    ("GET", "campaigns/{}/leads"),  # leads_get-campaign-leads
    ("POST", "campaigns/{}/leads"),  # leads_create-lead-in-campaign
    ("POST", "campaigns/{}/leads/import"),  # leads_import-leads-from-crm
    ("DELETE", "campaigns/{}/leads/{}"),  # leads_delete-lead  # ALSO: leads_unsubscribe-lead-from-campaign
    ("PATCH", "campaigns/{}/leads/{}"),  # leads_update-lead
    ("POST", "campaigns/{}/leads/{}/interested"),  # leads_mark-lead-as-interested-in-campaign
    ("POST", "campaigns/{}/leads/{}/notinterested"),  # leads_mark-lead-as-not-interested-in-campaign
    ("POST", "campaigns/{}/pause"),  # campaigns_pause-campaign
    ("GET", "campaigns/{}/schedules"),  # schedules_get-campaign-schedules
    ("POST", "campaigns/{}/schedules/{}"),  # schedules_associate-schedule-with-campaign
    ("GET", "campaigns/{}/sequences"),  # sequences_get-campaign-sequences
    ("POST", "campaigns/{}/start"),  # campaigns_start-campaign
    ("GET", "campaigns/{}/statutes"),  # campaigns_get-campaign-statutes
    ("GET", "companies"),  # companies_get-many-companies
    ("POST", "companies"),  # companies_upsert-company
    ("DELETE", "companies/{}"),  # companies_delete-company
    ("GET", "companies/{}/notes"),  # companies_get-company-notes
    ("POST", "companies/{}/notes"),  # companies_create-company-note
    ("GET", "contacts"),  # contacts_get-many-contacts
    ("POST", "contacts"),  # contacts_upsert-contact
    ("GET", "contacts/export"),  # contacts_export-contact-list
    ("GET", "contacts/lists"),  # contacts_get-contact-lists
    ("POST", "contacts/lists"),  # contacts_create-contact-list
    ("POST", "contacts/lists/{}/entities"),  # contacts_manage-contact-list-entities
    ("DELETE", "contacts/{}"),  # contacts_delete-contact
    ("GET", "contacts/{}"),  # contacts_get-contact
    ("GET", "crm/filters"),  # crm_get-crm-filters
    ("POST", "database/companies"),  # people-database_search-companies-database
    ("GET", "database/filters"),  # people-database_get-database-filters
    ("POST", "database/people"),  # people-database_search-people-database
    ("GET", "database/personas"),  # people-database_list-personas
    ("POST", "database/personas"),  # people-database_create-persona
    ("DELETE", "database/personas/{}"),  # people-database_delete-persona
    ("GET", "deliverability/alerts"),  # deliverability-alerts_list-alerts
    ("POST", "deliverability/alerts"),  # deliverability-alerts_create-alert
    ("DELETE", "deliverability/alerts/{}"),  # deliverability-alerts_delete-alert
    ("GET", "deliverability/alerts/{}"),  # deliverability-alerts_get-alert
    ("PATCH", "deliverability/alerts/{}"),  # deliverability-alerts_update-alert
    ("POST", "enrich"),  # enrich_enrich-data
    ("GET", "enrich/{}"),  # enrich_get-enrichment-result
    ("GET", "fields"),  # fields_list-fields
    ("GET", "hooks"),  # webhooks_get-many-webhooks
    ("POST", "hooks"),  # webhooks_add-webhook
    ("DELETE", "hooks/{}"),  # webhooks_delete-webhook
    ("GET", "inbox"),  # inbox_get-many-inboxes
    ("DELETE", "inbox/conversations/labels/{}"),  # inbox_remove-labels-from-conversation
    ("POST", "inbox/conversations/labels/{}"),  # inbox_attach-labels-to-conversations
    ("POST", "inbox/email"),  # inbox_send-email
    ("GET", "inbox/labels"),  # inbox_get-many-labels
    ("POST", "inbox/labels"),  # inbox_create-label
    ("GET", "inbox/labels/{}"),  # inbox_get-label
    ("POST", "inbox/linkedin"),  # inbox_send-linkedin-message
    ("POST", "inbox/whatsapp"),  # inbox_send-whatsapp-message
    ("GET", "inbox/{}"),  # inbox_get-contact-messages
    ("GET", "inbox/{}/drafts"),  # inbox_list-drafts
    ("POST", "inbox/{}/drafts"),  # inbox_create-draft
    ("DELETE", "inbox/{}/drafts/{}"),  # inbox_delete-draft
    ("GET", "inbox/{}/drafts/{}"),  # inbox_get-draft
    ("PATCH", "inbox/{}/drafts/{}"),  # inbox_update-draft
    ("GET", "leads"),  # leads_get-lead-by-email-or-id
    ("POST", "leads/audio"),  # leads_upload-audio-for-voice-message-step
    ("POST", "leads/interested/{}"),  # leads_mark-lead-as-interested
    ("POST", "leads/notinterested/{}"),  # leads_mark-lead-as-not-interested
    ("POST", "leads/pause/{}"),  # leads_pause-lead
    ("POST", "leads/review/{}"),  # leads_launch-lead
    ("POST", "leads/start/{}"),  # leads_resume-paused-lead
    ("GET", "leads/{}"),  # leads_get-lead-by-email
    ("POST", "leads/{}/enrich"),  # enrich_enrich-lead
    ("DELETE", "leads/{}/variables"),  # leads_delete-lead-variables
    ("PATCH", "leads/{}/variables"),  # leads_update-lead-variables
    ("POST", "leads/{}/variables"),  # leads_add-lead-variables
    ("POST", "lemwarm/{}/pause"),  # lemwarm_pause-lemwarm
    ("GET", "lemwarm/{}/settings"),  # lemwarm_get-lemwarm-settings
    ("PATCH", "lemwarm/{}/settings"),  # lemwarm_update-lemwarm-settings
    ("POST", "lemwarm/{}/start"),  # lemwarm_start-lemwarm
    ("GET", "schedules"),  # schedules_get-many-schedules
    ("POST", "schedules"),  # schedules_create-schedule
    ("DELETE", "schedules/{}"),  # schedules_delete-schedule
    ("GET", "schedules/{}"),  # schedules_get-schedule
    ("PATCH", "schedules/{}"),  # schedules_update-schedule
    ("POST", "sequences/{}/steps"),  # sequences_add-step-to-sequence
    ("DELETE", "sequences/{}/steps/{}"),  # sequences_delete-sequence-step
    ("PATCH", "sequences/{}/steps/{}"),  # sequences_update-sequence-step
    ("DELETE", "sequences/{}/steps/{}/ab-test"),  # sequences_delete-ab-test-variant
    ("GET", "sequences/{}/steps/{}/ab-test"),  # sequences_get-ab-test-variant
    ("PATCH", "sequences/{}/steps/{}/ab-test"),  # sequences_update-ab-test-variant
    ("POST", "sequences/{}/steps/{}/ab-test"),  # sequences_create-ab-test-variant
    ("POST", "sequences/{}/steps/{}/ab-test/winner"),  # sequences_select-ab-test-winner
    ("GET", "tasks"),  # tasks_get-many-tasks
    ("PATCH", "tasks"),  # tasks_update-task
    ("POST", "tasks"),  # tasks_create-task
    ("POST", "tasks/ignore"),  # tasks_ignore-tasks
    ("GET", "team"),  # team_get-team
    ("GET", "team/credits"),  # team_get-team-credits
    ("GET", "team/crmUsers"),  # crm_get-team-crm-users
    ("GET", "team/senders"),  # team_get-team-senders
    ("GET", "unsubs/export"),  # unsubscribes_export-unsubscribes
    ("DELETE", "unsubscribes/{}"),  # unsubscribes_delete-unsubscribe-email
    ("GET", "unsubscribes/{}"),  # unsubscribes_get-unsubscribe-by-email
    ("POST", "unsubscribes/{}"),  # unsubscribes_add-unsubscribe-email-or-domain
    ("GET", "user/channels"),  # users_get-user-channels
    ("POST", "user/email-accounts"),  # email-accounts_connect-email-account
    ("DELETE", "user/email-accounts/{}"),  # email-accounts_disconnect-email-account
    ("POST", "user/email-accounts/{}/test"),  # email-accounts_test-email-account
    ("GET", "users/{}"),  # users_get-user
    ("POST", "v2/campaigns/stats/batch"),  # campaigns_get-batch-campaign-stats
    ("GET", "v2/campaigns/{}/stats"),  # campaigns_get-campaign-stats
    ("POST", "v2/enrichments/bulk"),  # enrich_bulk-enrich-data
    ("DELETE", "v2/unsubscribes/contacts/{}"),  # unsubscribes_resubscribe-contact
    ("GET", "v2/unsubscribes/contacts/{}"),  # unsubscribes_get-contact-subscription-status
    ("POST", "v2/unsubscribes/contacts/{}"),  # unsubscribes_unsubscribe-contact
    ("GET", "v2/unsubscribes/exports/contacts"),  # unsubscribes_export-unsubscribed-contacts
    ("GET", "v2/unsubscribes/exports/variables"),  # unsubscribes_export-unsubscribed-variables
    ("GET", "v2/unsubscribes/variables"),  # unsubscribes_list-unsubscribed-variables
    ("POST", "v2/unsubscribes/variables"),  # unsubscribes_bulk-unsubscribe-variables
    ("DELETE", "v2/unsubscribes/variables/{}"),  # unsubscribes_resubscribe-variable
    ("GET", "v2/unsubscribes/variables/{}"),  # unsubscribes_get-unsubscribed-variable
    ("POST", "v2/unsubscribes/variables/{}"),  # unsubscribes_unsubscribe-variable
    ("DELETE", "watchlist"),  # watch-list_delete-watch-list
    ("GET", "watchlist"),  # watch-list_list-watch-lists
    ("PATCH", "watchlist"),  # watch-list_update-watch-list
    ("POST", "watchlist"),  # watch-list_create-watch-list
    ("GET", "watchlist/filter-values"),  # watch-list_get-filter-values
    ("GET", "watchlist/filters"),  # watch-list_get-filters
    ("GET", "watchlist/history"),  # watch-list_get-history
    ("GET", "watchlist/library"),  # watch-list_get-library
    ("GET", "watchlist/signals"),  # watch-list_get-signals
    ("POST", "watchlist/{}/external-signals"),  # watch-list_push-external-signals
)


def test_l_inventaire_documente_est_complet():
    """Le relevé lui-même : 143 routes distinctes, aucune ligne en double."""
    assert len(DOCUMENTED) == 143
    assert len(set(DOCUMENTED)) == 143


def test_chaque_endpoint_documente_a_un_chemin_dans_le_client():
    built = _paths_built_by_the_client()
    manquants = sorted(set(DOCUMENTED) - built)
    assert not manquants, (
        f"{len(manquants)} endpoint(s) lemlist sans chemin dans le client :\n  "
        + "\n  ".join(f"{v} /{p}" for v, p in manquants))


def test_le_client_ne_vise_aucun_chemin_hors_inventaire():
    """L'autre sens : un chemin construit mais absent de la doc est soit une
    faute de frappe, soit une route non documentée qu'il faut assumer ICI."""
    hors = sorted(_paths_built_by_the_client() - set(DOCUMENTED))
    assert hors == [
        # Export CSV historique, antérieur à `GET /campaigns/{id}/export/leads`
        # et toujours en service : `export_leads` rend le CSV directement, là où
        # la route documentée ouvre un export ASYNCHRONE (start → status).
        ("GET", "campaigns/{}/export"),
        # `add_lead` — l'ancienne création de lead, email DANS LE CHEMIN. lemlist
        # ne la documente plus (`create_lead` la remplace, email dans le corps)
        # mais elle répond toujours, et des appelants la tiennent. Assumée ici
        # plutôt que retirée : la retirer casserait du code qui marche, et la
        # laisser non déclarée ferait mentir l'inventaire.
        ("POST", "campaigns/{}/leads/{}"),
    ], f"chemins hors inventaire : {hors}"
