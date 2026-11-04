"""Leads, lead lists, lead labels, the background job of a move, and the block list."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from .. import checks as c

#: `list_leads` `filter`: a lead state, as Instantly names it.
LEAD_FILTERS = (
    "FILTER_VAL_CONTACTED", "FILTER_VAL_NOT_CONTACTED", "FILTER_VAL_COMPLETED",
    "FILTER_VAL_UNSUBSCRIBED", "FILTER_VAL_ACTIVE", "FILTER_LEAD_INTERESTED",
    "FILTER_LEAD_NOT_INTERESTED", "FILTER_LEAD_MEETING_BOOKED", "FILTER_LEAD_MEETING_COMPLETED",
    "FILTER_LEAD_CLOSED", "FILTER_LEAD_OUT_OF_OFFICE", "FILTER_LEAD_WRONG_PERSON",
    "FILTER_LEAD_LOST", "FILTER_LEAD_NO_SHOW", "FILTER_LEAD_CUSTOM_LABEL_POSITIVE",
    "FILTER_LEAD_CUSTOM_LABEL_NEGATIVE", "FILTER_VAL_BOUNCED", "FILTER_VAL_SKIPPED",
    "FILTER_VAL_RISKY", "FILTER_VAL_INVALID", "FILTER_VAL_VALID", "FILTER_VAL_CATCH_ALL",
    "FILTER_VAL_NOT_VERIFIED", "FILTER_VAL_IN_SUBSEQUENCE", "FILTER_VAL_OPENED_NO_REPLY",
    "FILTER_VAL_COMPLETED_NO_REPLY", "FILTER_VAL_NO_OPENS", "FILTER_VAL_REPLIED",
    "FILTER_VAL_LINK_CLICKED",
)
#: The keys one lead of `add_leads` takes.
LEAD_FIELDS = frozenset({"email", "first_name", "last_name", "company_name", "job_title", "phone",
                         "website", "personalization", "custom_variables"})
LABEL_INTERESTS = ("positive", "neutral", "negative")

Variables = Optional[Dict[str, Union[str, int, float, bool]]]


def _variables(value: Any) -> Variables:
    """Sequence variables: a table of text, number or true/false values."""
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
            isinstance(v, (str, int, float, bool)) for v in value.values()):
        raise ValueError("`custom_variables` must map names to text, number or true/false values.")
    return value


def _leads(leads: Any, in_campaign: bool) -> List[dict]:
    if not isinstance(leads, list) or not 1 <= len(leads) <= c.MAX_LEADS_PER_ADD:
        raise ValueError(f"`leads` must be a list of 1 to {c.MAX_LEADS_PER_ADD} leads.")
    for i, lead in enumerate(leads):
        if not isinstance(lead, dict) or not lead:
            raise ValueError(f"`leads[{i}]` must be a non-empty object.")
        stray = sorted(set(lead) - LEAD_FIELDS)
        if stray:
            raise ValueError(f"`leads[{i}]` does not take {', '.join(stray)} "
                             f"(takes {', '.join(sorted(LEAD_FIELDS))}).")
        if in_campaign:
            c.email(lead.get("email"), f"leads[{i}].email")
        _variables(lead.get("custom_variables"))
    return leads


class _LeadsMixin:
    def list_leads(self, *, campaign: Optional[str] = None, list_id: Optional[str] = None,
                   search: Optional[str] = None, contacts: Optional[List[str]] = None,
                   ids: Optional[List[str]] = None, filter: Optional[str] = None,
                   in_campaign: Optional[bool] = None, in_list: Optional[bool] = None,
                   distinct_contacts: Optional[bool] = None, limit: Optional[int] = None,
                   starting_after: Optional[str] = None) -> Any:
        """POST /leads/list — leads by filters, one page (a read sent as a POST)."""
        return self._send("POST", "/leads/list", c.present({
            "campaign": c.optional_uuid(campaign, "campaign"),
            "list_id": c.optional_uuid(list_id, "list_id"),
            "search": c.text(search, "search"),
            "contacts": c.emails(contacts, "contacts"),
            "ids": c.uuids(ids, "ids"),
            "filter": c.choice(filter, "filter", LEAD_FILTERS),
            "in_campaign": in_campaign, "in_list": in_list,
            "distinct_contacts": distinct_contacts,
            "limit": c.limit(limit),
            "starting_after": c.text(starting_after, "starting_after"),
        }))

    def get_lead(self, lead_id: str) -> Any:
        """GET /leads/{id} — one lead."""
        return self._get(f"/leads/{c.uuid(lead_id, 'lead_id')}")

    def add_leads(self, leads: List[dict], *, campaign_id: Optional[str] = None,
                  list_id: Optional[str] = None, blocklist_id: Optional[str] = None,
                  skip_if_in_workspace: Optional[bool] = None,
                  skip_if_in_campaign: Optional[bool] = None,
                  skip_if_in_list: Optional[bool] = None) -> Any:
        """POST /leads/add — up to 1000 leads into ONE campaign or ONE list. In an active
        campaign, its sequence starts emailing them."""
        c.one_of("campaign_id", campaign_id, "list_id", list_id)
        return self._send("POST", "/leads/add", c.present({
            "campaign_id": c.optional_uuid(campaign_id, "campaign_id"),
            "list_id": c.optional_uuid(list_id, "list_id"),
            "leads": _leads(leads, in_campaign=campaign_id is not None),
            "blocklist_id": c.optional_uuid(blocklist_id, "blocklist_id"),
            "skip_if_in_workspace": skip_if_in_workspace,
            "skip_if_in_campaign": skip_if_in_campaign,
            "skip_if_in_list": skip_if_in_list,
        }))

    def create_lead(self, email: str, *, campaign: Optional[str] = None,
                    list_id: Optional[str] = None, first_name: Optional[str] = None,
                    last_name: Optional[str] = None, company_name: Optional[str] = None,
                    job_title: Optional[str] = None, phone: Optional[str] = None,
                    website: Optional[str] = None, personalization: Optional[str] = None,
                    custom_variables: Variables = None, blocklist_id: Optional[str] = None,
                    skip_if_in_workspace: Optional[bool] = None,
                    skip_if_in_campaign: Optional[bool] = None,
                    skip_if_in_list: Optional[bool] = None) -> Any:
        """POST /leads — one lead, in a campaign or a list."""
        c.not_both("campaign", campaign, "list_id", list_id)
        return self._send("POST", "/leads", c.present({
            "campaign": c.optional_uuid(campaign, "campaign"),
            "list_id": c.optional_uuid(list_id, "list_id"),
            "email": c.email(email, "email"),
            "first_name": first_name, "last_name": last_name, "company_name": company_name,
            "job_title": job_title, "phone": phone, "website": website,
            "personalization": personalization,
            "custom_variables": _variables(custom_variables),
            "blocklist_id": c.optional_uuid(blocklist_id, "blocklist_id"),
            "skip_if_in_workspace": skip_if_in_workspace,
            "skip_if_in_campaign": skip_if_in_campaign,
            "skip_if_in_list": skip_if_in_list,
        }))

    def update_lead(self, lead_id: str, *, first_name: Optional[str] = None,
                    last_name: Optional[str] = None, company_name: Optional[str] = None,
                    job_title: Optional[str] = None, phone: Optional[str] = None,
                    website: Optional[str] = None, personalization: Optional[str] = None,
                    pl_value_lead: Optional[str] = None, custom_variables: Variables = None) -> Any:
        """PATCH /leads/{id} — the given fields of one lead; the others are kept."""
        body = c.present({
            "first_name": first_name, "last_name": last_name, "company_name": company_name,
            "job_title": job_title, "phone": phone, "website": website,
            "personalization": personalization, "pl_value_lead": pl_value_lead,
            "custom_variables": _variables(custom_variables),
        })
        if not body:
            raise ValueError("Give at least one field to change.")
        return self._send("PATCH", f"/leads/{c.uuid(lead_id, 'lead_id')}", body)

    def update_lead_interest_status(self, lead_email: str, interest_value: Optional[int], *,
                                    campaign_id: Optional[str] = None,
                                    list_id: Optional[str] = None) -> Any:
        """POST /leads/update-interest-status — `interest_value` is always sent: `None`
        goes as `null`, which resets the lead to « Lead ». Accepted at once (202)."""
        if interest_value is not None and (isinstance(interest_value, bool)
                                           or not isinstance(interest_value, int)):
            raise ValueError(f"`interest_value` must be an integer or None; got {interest_value!r}.")
        c.not_both("campaign_id", campaign_id, "list_id", list_id)
        return self._send("POST", "/leads/update-interest-status", {
            "lead_email": c.email(lead_email, "lead_email"),
            "interest_value": interest_value,
            **c.present({"campaign_id": c.optional_uuid(campaign_id, "campaign_id"),
                         "list_id": c.optional_uuid(list_id, "list_id")}),
        })

    def move_leads(self, *, campaign: Optional[str] = None, list_id: Optional[str] = None,
                   to_campaign_id: Optional[str] = None, to_list_id: Optional[str] = None,
                   ids: Optional[List[str]] = None, contacts: Optional[List[str]] = None,
                   search: Optional[str] = None, filter: Optional[str] = None,
                   limit: Optional[int] = None, copy_leads: Optional[bool] = None,
                   check_duplicates: Optional[bool] = None,
                   check_duplicates_in_campaigns: Optional[bool] = None,
                   skip_invalid_emails: Optional[bool] = None) -> Any:
        """POST /leads/move — from ONE source to ONE destination, in the background:
        answers the job, read with `get_background_job`. Never the whole workspace."""
        c.one_of("campaign", campaign, "list_id", list_id)
        c.one_of("to_campaign_id", to_campaign_id, "to_list_id", to_list_id)
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise ValueError(f"`limit` must be a positive integer; got {limit!r}.")
        if filter is not None and (not isinstance(filter, str) or not filter.startswith("FILTER_")):
            raise ValueError(f"`filter` must be a lead state such as FILTER_LEAD_INTERESTED; got {filter!r}.")
        return self._send("POST", "/leads/move", c.present({
            "campaign": c.optional_uuid(campaign, "campaign"),
            "list_id": c.optional_uuid(list_id, "list_id"),
            "to_campaign_id": c.optional_uuid(to_campaign_id, "to_campaign_id"),
            "to_list_id": c.optional_uuid(to_list_id, "to_list_id"),
            "ids": c.uuids(ids, "ids"),
            "contacts": c.emails(contacts, "contacts"),
            "search": c.text(search, "search"),
            "filter": filter, "limit": limit, "copy_leads": copy_leads,
            "check_duplicates": check_duplicates,
            "check_duplicates_in_campaigns": check_duplicates_in_campaigns,
            "skip_invalid_emails": skip_invalid_emails,
        }))

    def get_background_job(self, job_id: str, *, data_fields: Optional[str] = None) -> Any:
        """GET /background-jobs/{id} — the job a move answers, until success or failed."""
        return self._get(f"/background-jobs/{c.job_id(job_id)}",
                         {"data_fields": c.text(data_fields, "data_fields")})

    # --- lead lists and labels ----------------------------------------------------------------

    def list_lead_lists(self, *, search: Optional[str] = None,
                        has_enrichment_task: Optional[bool] = None, limit: Optional[int] = None,
                        starting_after: Optional[str] = None) -> Any:
        """GET /lead-lists — lead lists, one page."""
        return self._get("/lead-lists", {
            "search": c.text(search, "search"), "has_enrichment_task": has_enrichment_task,
            "limit": c.limit(limit), "starting_after": c.text(starting_after, "starting_after"),
        })

    def get_lead_list(self, list_id: str) -> Any:
        """GET /lead-lists/{id} — one lead list."""
        return self._get(f"/lead-lists/{c.uuid(list_id, 'list_id')}")

    def create_lead_list(self, name: str) -> Any:
        """POST /lead-lists — an empty list, without enrichment (it spends credits)."""
        return self._send("POST", "/lead-lists", {"name": c.required_text(name, "name")})

    def list_lead_labels(self, *, search: Optional[str] = None,
                         interest_status: Optional[str] = None, limit: Optional[int] = None,
                         starting_after: Optional[str] = None) -> Any:
        """GET /lead-labels — the custom interest statuses, one page."""
        return self._get("/lead-labels", {
            "search": c.text(search, "search"),
            "interest_status": c.choice(interest_status, "interest_status", LABEL_INTERESTS),
            "limit": c.limit(limit), "starting_after": c.text(starting_after, "starting_after"),
        })

    # --- block list ---------------------------------------------------------------------------

    def add_block_list_entry(self, bl_value: str) -> Any:
        """POST /block-lists-entries — block an email or a whole domain."""
        return self._send("POST", "/block-lists-entries",
                          {"bl_value": c.required_text(bl_value, "bl_value")})
