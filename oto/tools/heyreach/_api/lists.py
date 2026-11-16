"""Lead and company lists, leads and their tags.

A mixin composed into `HeyReachClient`, which provides the transport (`_call`).
"""

from __future__ import annotations

from typing import Any, List, Optional

from . import checks as c


class ListsMixin:

    # --- lists --------------------------------------------------------------

    def list_lists(self, limit: int, offset: Optional[int] = None, *,
                   keyword: Optional[str] = None, list_type: Optional[str] = None,
                   campaign_ids: Optional[List[int]] = None) -> Any:
        """POST /list/GetAll — one page of lead and company lists."""
        return self._call("POST", "/list/GetAll", body={
            "limit": c.limit(limit), "offset": c.offset(offset),
            "keyword": c.text(keyword, "keyword"),
            "listType": c.choice(list_type, "list_type", c.LIST_TYPES),
            "campaignIds": c.idents(campaign_ids, "campaign_ids")})

    def get_list(self, list_id: int) -> Any:
        """GET /list/GetById — one list."""
        return self._call("GET", "/list/GetById", query={"listId": c.ident(list_id, "list_id")})

    def list_list_leads(self, list_id: int, limit: int, offset: Optional[int] = None, *,
                        keyword: Optional[str] = None, lead_profile_url: Optional[str] = None,
                        lead_linkedin_id: Optional[str] = None,
                        created_from: Optional[str] = None,
                        created_to: Optional[str] = None) -> Any:
        """POST /list/GetLeadsFromList — one page (up to 1000) of the leads of a list."""
        return self._call("POST", "/list/GetLeadsFromList", body={
            "listId": c.ident(list_id, "list_id"),
            "limit": c.limit(limit, maximum=c.MAX_LIST_LEADS_PAGE), "offset": c.offset(offset),
            "keyword": c.text(keyword, "keyword"),
            "leadProfileUrl": c.text(lead_profile_url, "lead_profile_url"),
            "leadLinkedInId": c.text(lead_linkedin_id, "lead_linkedin_id"),
            "createdFrom": c.date_time(created_from, "created_from"),
            "createdTo": c.date_time(created_to, "created_to")}, strip="list_list_leads")

    def list_lead_lists(self, limit: int, offset: Optional[int] = None, *,
                        profile_url: Optional[str] = None, linkedin_id: Optional[str] = None,
                        email: Optional[str] = None) -> Any:
        """POST /list/GetListsForLead — the lists a lead is in."""
        c.one_lead_key(profile_url=profile_url, linkedin_id=linkedin_id, email=email)
        return self._call("POST", "/list/GetListsForLead", body={
            "profileUrl": c.text(profile_url, "profile_url"),
            "linkedinId": c.text(linkedin_id, "linkedin_id"), "email": c.text(email, "email"),
            "limit": c.limit(limit), "offset": c.offset(offset)})

    def add_leads_to_list(self, list_id: int, leads: List[dict]) -> Any:
        """POST /list/AddLeadsToListV2 — up to 100 leads; answers the added, updated and
        failed counts. A running campaign that uses the list may pick them up."""
        return self._call("POST", "/list/AddLeadsToListV2", body={
            "listId": c.ident(list_id, "list_id"), "leads": c.leads(leads, "leads")})

    def create_empty_list(self, name: str, type: Optional[str] = None) -> Any:
        """POST /list/CreateEmptyList — a lead list (USER_LIST, the default) or a company list."""
        return self._call("POST", "/list/CreateEmptyList", body={
            "name": c.text(name, "name", required=True),
            "type": c.choice(type, "type", c.LIST_TYPES)})

    # --- leads and tags -----------------------------------------------------

    def get_lead(self, profile_url: str) -> Any:
        """POST /lead/GetLead — one lead by its LinkedIn profile URL."""
        return self._call("POST", "/lead/GetLead",
                          body={"profileUrl": c.text(profile_url, "profile_url", required=True)},
                          strip="get_lead")

    def get_lead_tags(self, profile_url: str) -> Any:
        """POST /lead/GetTags — the tags of a lead, sorted alphabetically."""
        return self._call("POST", "/lead/GetTags",
                          body={"profileUrl": c.text(profile_url, "profile_url", required=True)})

    @staticmethod
    def _tag_body(lead_profile_url, lead_linkedin_id, tags, create_tag_if_not_existing) -> dict:
        c.one_lead_key(lead_profile_url=lead_profile_url, lead_linkedin_id=lead_linkedin_id)
        return {"leadProfileUrl": c.text(lead_profile_url, "lead_profile_url"),
                "leadLinkedInId": c.text(lead_linkedin_id, "lead_linkedin_id"),
                "tags": c.texts([] if tags is None else tags, "tags"),
                "createTagIfNotExisting": c.flag(create_tag_if_not_existing,
                                                 "create_tag_if_not_existing")}

    def add_lead_tags(self, tags: List[str], *, lead_profile_url: Optional[str] = None,
                      lead_linkedin_id: Optional[str] = None,
                      create_tag_if_not_existing: Optional[bool] = None) -> Any:
        """POST /lead/AddTags — add tags to a lead, its other tags kept."""
        return self._call("POST", "/lead/AddTags", body=self._tag_body(
            lead_profile_url, lead_linkedin_id, tags, create_tag_if_not_existing))

    def replace_lead_tags(self, tags: List[str], *, lead_profile_url: Optional[str] = None,
                          lead_linkedin_id: Optional[str] = None,
                          create_tag_if_not_existing: Optional[bool] = None) -> Any:
        """POST /lead/ReplaceTags — the lead's tags become these; the others are removed."""
        return self._call("POST", "/lead/ReplaceTags", body=self._tag_body(
            lead_profile_url, lead_linkedin_id, tags, create_tag_if_not_existing))
