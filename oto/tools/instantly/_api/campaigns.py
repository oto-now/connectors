"""Campaigns, their analytics, and activating or pausing one."""
from __future__ import annotations

from typing import Any, List, Optional

from .. import checks as c

#: Campaign status: 0 draft, 1 active, 2 paused, 3 completed, 4 running subsequences,
#: -1 accounts unhealthy, -2 bounce protect, -99 account suspended.
CAMPAIGN_STATUSES = (0, 1, 2, 3, 4, -1, -2, -99)
SORT_ORDERS = ("asc", "desc")


class _CampaignsMixin:
    def list_campaigns(self, *, search: Optional[str] = None, status: Optional[int] = None,
                       tag_ids: Optional[str] = None, limit: Optional[int] = None,
                       starting_after: Optional[str] = None) -> Any:
        """GET /campaigns — campaigns, one page (`next_starting_after` for the next)."""
        return self._get("/campaigns", {
            "search": c.text(search, "search"),
            "status": c.choice(status, "status", CAMPAIGN_STATUSES),
            "tag_ids": c.text(tag_ids, "tag_ids"),
            "limit": c.limit(limit),
            "starting_after": c.text(starting_after, "starting_after"),
        })

    def get_campaign(self, campaign_id: str) -> Any:
        """GET /campaigns/{id} — one campaign, sequences included."""
        return self._get(f"/campaigns/{c.uuid(campaign_id, 'campaign_id')}")

    def search_campaigns_by_lead(self, search: str, *, sort_column: Optional[str] = None,
                                 sort_order: Optional[str] = None) -> Any:
        """GET /campaigns/search-by-contact — the campaigns a lead (by email) is in."""
        return self._get("/campaigns/search-by-contact", {
            "search": c.email(search, "search"),
            "sort_column": c.choice(sort_column, "sort_column", ("timestamp_created", "name")),
            "sort_order": c.choice(sort_order, "sort_order", SORT_ORDERS),
        })

    def get_campaign_analytics(self, *, campaign_id: Optional[str] = None,
                               campaign_ids: Optional[List[str]] = None,
                               start_date: Optional[str] = None, end_date: Optional[str] = None,
                               exclude_total_leads_count: Optional[bool] = None) -> Any:
        """GET /campaigns/analytics — per-campaign totals (one, several, or all)."""
        c.not_both("campaign_id", campaign_id, "campaign_ids", campaign_ids)
        return self._get("/campaigns/analytics", {
            "id": c.optional_uuid(campaign_id, "campaign_id"),
            "ids": c.uuids(campaign_ids, "campaign_ids"),
            "start_date": c.text(start_date, "start_date"),
            "end_date": c.text(end_date, "end_date"),
            "exclude_total_leads_count": exclude_total_leads_count,
        })

    def get_campaign_analytics_overview(self, *, campaign_id: Optional[str] = None,
                                        campaign_ids: Optional[List[str]] = None,
                                        start_date: Optional[str] = None,
                                        end_date: Optional[str] = None,
                                        campaign_status: Optional[int] = None,
                                        expand_crm_events: Optional[bool] = None) -> Any:
        """GET /campaigns/analytics/overview — the totals summed over the campaigns."""
        c.not_both("campaign_id", campaign_id, "campaign_ids", campaign_ids)
        return self._get("/campaigns/analytics/overview", {
            "id": c.optional_uuid(campaign_id, "campaign_id"),
            "ids": c.uuids(campaign_ids, "campaign_ids"),
            "start_date": c.text(start_date, "start_date"),
            "end_date": c.text(end_date, "end_date"),
            "campaign_status": c.choice(campaign_status, "campaign_status", CAMPAIGN_STATUSES),
            "expand_crm_events": expand_crm_events,
        })

    def get_campaign_daily_analytics(self, *, campaign_id: Optional[str] = None,
                                     start_date: Optional[str] = None,
                                     end_date: Optional[str] = None,
                                     campaign_status: Optional[int] = None) -> Any:
        """GET /campaigns/analytics/daily — day by day."""
        return self._get("/campaigns/analytics/daily", {
            "campaign_id": c.optional_uuid(campaign_id, "campaign_id"),
            "start_date": c.text(start_date, "start_date"),
            "end_date": c.text(end_date, "end_date"),
            "campaign_status": c.choice(campaign_status, "campaign_status", CAMPAIGN_STATUSES),
        })

    def get_campaign_steps_analytics(self, *, campaign_id: Optional[str] = None,
                                     start_date: Optional[str] = None,
                                     end_date: Optional[str] = None,
                                     include_opportunities_count: Optional[bool] = None) -> Any:
        """GET /campaigns/analytics/steps — step by step and variant by variant."""
        return self._get("/campaigns/analytics/steps", {
            "campaign_id": c.optional_uuid(campaign_id, "campaign_id"),
            "start_date": c.text(start_date, "start_date"),
            "end_date": c.text(end_date, "end_date"),
            "include_opportunities_count": include_opportunities_count,
        })

    def activate_campaign(self, campaign_id: str) -> Any:
        """POST /campaigns/{id}/activate — the campaign starts (or resumes) sending. No body."""
        return self._send("POST", f"/campaigns/{c.uuid(campaign_id, 'campaign_id')}/activate")

    def pause_campaign(self, campaign_id: str) -> Any:
        """POST /campaigns/{id}/pause — the campaign stops sending. No body."""
        return self._send("POST", f"/campaigns/{c.uuid(campaign_id, 'campaign_id')}/pause")
