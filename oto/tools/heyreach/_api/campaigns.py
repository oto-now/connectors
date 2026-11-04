"""Campaigns, the leads they work on, and the outreach statistics.

A mixin composed into `HeyReachClient`, which provides the transport (`_call`).
"""

from __future__ import annotations

from typing import Any, List, Optional

from . import checks as c


class CampaignsMixin:

    # --- reading ------------------------------------------------------------

    def list_campaigns(self, limit: int, offset: Optional[int] = None, *,
                       keyword: Optional[str] = None, statuses: Optional[List[str]] = None,
                       account_ids: Optional[List[int]] = None) -> Any:
        """POST /campaign/GetAll — one page of campaigns; next page: offset + limit."""
        return self._call("POST", "/campaign/GetAll", body={
            "limit": c.limit(limit), "offset": c.offset(offset),
            "keyword": c.text(keyword, "keyword"),
            "statuses": c.choices(statuses, "statuses", c.CAMPAIGN_STATUSES),
            "accountIds": c.idents(account_ids, "account_ids")})

    def get_campaign(self, campaign_id: int) -> Any:
        """GET /campaign/GetById — one campaign."""
        return self._call("GET", "/campaign/GetById",
                          query={"campaignId": c.ident(campaign_id, "campaign_id")})

    def list_campaign_leads(self, campaign_id: int, limit: int, offset: Optional[int] = None, *,
                            time_from: Optional[str] = None, time_to: Optional[str] = None,
                            time_filter: Optional[str] = None) -> Any:
        """POST /campaign/GetLeadsFromCampaign — one page of the leads a campaign works on."""
        return self._call("POST", "/campaign/GetLeadsFromCampaign", body={
            "campaignId": c.ident(campaign_id, "campaign_id"), "limit": c.limit(limit),
            "offset": c.offset(offset), "timeFrom": c.date_time(time_from, "time_from"),
            "timeTo": c.date_time(time_to, "time_to"),
            "timeFilter": c.choice(time_filter, "time_filter", c.TIME_FILTERS)},
            strip="list_campaign_leads")

    def list_lead_campaigns(self, limit: int, offset: Optional[int] = None, *,
                            profile_url: Optional[str] = None, linkedin_id: Optional[str] = None,
                            email: Optional[str] = None) -> Any:
        """POST /campaign/GetCampaignsForLead — the campaigns a lead is in."""
        c.one_lead_key(profile_url=profile_url, linkedin_id=linkedin_id, email=email)
        return self._call("POST", "/campaign/GetCampaignsForLead", body={
            "profileUrl": c.text(profile_url, "profile_url"),
            "linkedinId": c.text(linkedin_id, "linkedin_id"), "email": c.text(email, "email"),
            "limit": c.limit(limit), "offset": c.offset(offset)})

    # --- acting -------------------------------------------------------------

    def add_leads_to_campaign(self, campaign_id: int, account_lead_pairs: List[dict], *,
                              resume_paused_campaign: Optional[bool] = None,
                              resume_finished_campaign: Optional[bool] = None) -> Any:
        """POST /campaign/AddLeadsToCampaignV2 — up to 100 leads, each {lead,
        linkedInAccountId?}; a running campaign starts acting on them. Answers the added,
        updated and failed counts."""
        return self._call("POST", "/campaign/AddLeadsToCampaignV2", body={
            "campaignId": c.ident(campaign_id, "campaign_id"),
            "accountLeadPairs": c.account_lead_pairs(account_lead_pairs),
            "resumePausedCampaign": c.flag(resume_paused_campaign, "resume_paused_campaign"),
            "resumeFinishedCampaign": c.flag(resume_finished_campaign, "resume_finished_campaign")})

    def stop_lead_in_campaign(self, campaign_id: int, *, lead_member_id: Optional[str] = None,
                              lead_url: Optional[str] = None) -> Any:
        """POST /campaign/StopLeadInCampaign — no further step runs for this lead."""
        c.one_lead_key(lead_member_id=lead_member_id, lead_url=lead_url)
        return self._call("POST", "/campaign/StopLeadInCampaign", body={
            "campaignId": c.ident(campaign_id, "campaign_id"),
            "leadMemberId": c.text(lead_member_id, "lead_member_id"),
            "leadUrl": c.text(lead_url, "lead_url")})

    def pause_campaign(self, campaign_id: int) -> Any:
        """POST /campaign/Pause — the campaign stops acting until it is resumed."""
        return self._call("POST", "/campaign/Pause",
                          query={"campaignId": c.ident(campaign_id, "campaign_id")})

    def resume_campaign(self, campaign_id: int) -> Any:
        """POST /campaign/Resume — the campaign acts on its leads again."""
        return self._call("POST", "/campaign/Resume",
                          query={"campaignId": c.ident(campaign_id, "campaign_id")})

    # --- statistics ---------------------------------------------------------

    @staticmethod
    def _stats_body(start_date, end_date, account_ids, campaign_ids) -> dict:
        return {"startDate": c.date_time(start_date, "start_date", required=True),
                "endDate": c.date_time(end_date, "end_date", required=True),
                "accountIds": c.idents(account_ids, "account_ids", allow_empty=True),
                "campaignIds": c.idents(campaign_ids, "campaign_ids", allow_empty=True)}

    def get_overall_stats(self, start_date: str, end_date: str, *,
                          account_ids: Optional[List[int]] = None,
                          campaign_ids: Optional[List[int]] = None) -> Any:
        """POST /stats/GetOverallStats — totals and per-day counters over a period; an
        empty list of senders or campaigns means all of them."""
        return self._call("POST", "/stats/GetOverallStats",
                          body=self._stats_body(start_date, end_date, account_ids, campaign_ids))

    def get_stats_by_campaign(self, start_date: str, end_date: str, *,
                              account_ids: Optional[List[int]] = None,
                              campaign_ids: Optional[List[int]] = None) -> Any:
        """POST /stats/GetOverallStatsByCampaign — the same, broken down per campaign."""
        return self._call("POST", "/stats/GetOverallStatsByCampaign",
                          body=self._stats_body(start_date, end_date, account_ids, campaign_ids))
