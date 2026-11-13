"""
Lemlist API Client for email campaign management.

Requires: requests
"""

import json
import re
import time
import base64
from urllib.parse import quote, urlencode
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests

from ..common.credentials import require
from ..common import raise_for_upstream
from . import activity_views, database_views

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never wait indefinitely


def _query(params: Dict[str, Any]) -> Dict[str, Any]:
    """Query parameters with booleans written `true`/`false`: `requests` would
    write `True`/`False` (Python capital), which lemlist does not read as a
    boolean — a filter believed set, or cleared, would silently be neither.
    Other values pass unchanged."""
    return {k: ("true" if v else "false") if isinstance(v, bool) else v
            for k, v in params.items()}


@dataclass
class Campaign:
    """Campaign data.

    `senders` is kept first-class because it is what an operator asks for
    ("who is sending this campaign?"), but the v2 sample payload of
    `GET /campaigns` does NOT advertise it — on that shape it would land `[]`.

    ⚠️ Replayed live on 2026-08-31 WITHOUT A CONCLUSION: all the campaigns of
    the test account had zero senders, so an empty `senders` does not say whether
    the field is absent from the payload or simply empty. The question remains
    open, and is settled by a single read on a campaign that has a sender.
    """
    id: str
    name: str
    status: str
    senders: List[str]
    emoji: str = ""
    labels: List[str] = field(default_factory=list)
    timezone: str = ""
    created_at: str = ""
    created_by: str = ""
    has_error: bool = False
    errors: List[str] = field(default_factory=list)


@dataclass
class Lead:
    """Lead data for campaign. Uses camelCase to match Lemlist API."""
    email: str
    firstName: str = None
    lastName: str = None
    companyName: str = None
    phone: str = None
    picture: str = None
    linkedinUrl: str = None


class LemlistClient:
    """
    Covers the WHOLE documented lemlist API — 141 routes as of 2026-08-31,
    held by the inventory in `tests/test_lemlist_coverage.py` rather than by
    this list, which will drift:

    - Campaigns: list, get, create, update, start, pause, duplicate, statutes,
      stats (v2 + batch), reports, and the asynchronous export (start/status/
      email) — plus the campaign tree, a local view over sequences
    - Sequences: steps (get/add/update/delete) and A/B tests
    - Schedules: sending windows, team-owned, shared by campaigns
    - Leads: create, launch, read, update, delete/unsubscribe, pause/resume,
      mark (not) interested, variables, CRM import, voice-note audio
    - Contacts & companies: the lemlist CRM, lists, notes, fields
    - Inbox: conversations, drafts, labels — and the three direct sends
    - Unsubscribes: three separate lists (v1 emails/domains, v2 variables,
      v2 contact do-not-contact flag)
    - Watch lists & signals, tasks, people/companies database & personas
    - Team, users, CRM link, email accounts, lemwarm, deliverability alerts,
      webhooks, activities, enrichment

    A CONTACT is not a LEAD: the lead is a person's copy INSIDE a campaign (its
    sending state, its variables), the contact is the person in the lemlist CRM,
    campaign-independent. Most confusions here start there.

    Five calls put messages in front of a real person: `start_campaign`,
    `launch_lead`, `resume_lead`, and the three inbox sends
    (`send_inbox_email`, `send_linkedin_message`, `send_whatsapp_message`) —
    the last three with neither campaign nor review in front of them. Two more
    can send indirectly: a campaign carrying `autoReview`, and a watch list set
    to `push_to_campaign`. `start_lemwarm` sends only inside the warm-up
    network. Everything else reads, edits a draft, or enriches.
    """

    BASE_URL = "https://api.lemlist.com/api"

    def __init__(self, api_key: str = None):
        """
        Initialize Lemlist client.

        Args:
            api_key: Lemlist API key
        """
        self.api_key = require(api_key, "LEMLIST_API_KEY")
        self._last_request = 0.0

    @property
    def headers(self) -> dict:
        """Get auth headers dict."""
        return {
            "Authorization": self._get_auth_header(),
            "Content-Type": "application/json",
        }

    def _rate_limit(self):
        """Enforce minimum 100ms between requests."""
        elapsed = time.time() - self._last_request
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)
        self._last_request = time.time()

    def _get_auth_header(self) -> str:
        """Get Basic auth header."""
        credentials = f":{self.api_key}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    def _request(
        self, method: str, endpoint: str, *,
        tolerate: tuple = (), as_text: bool = False, **kwargs,
    ) -> Any:
        """Make API request with rate limiting.

        `tolerate` lists status codes whose body is a legitimate payload rather
        than an error — used by the enrichment poll, where lemlist answers 404
        with `{"enrichmentStatus": "not-found", ...}`.

        `as_text` returns the raw body instead of parsing it: the export routes
        answer CSV, and calling `.json()` on those would raise on a perfectly
        good response.
        """
        self._rate_limit()

        url = f"{self.BASE_URL}/{endpoint}"
        headers = {"Authorization": self._get_auth_header()}

        kwargs.setdefault("timeout", _HTTP_TIMEOUT)
        # A dict query has its booleans written `true`/`false` (`_query`); a
        # query already encoded as a string (`_vars_query`) passes as is.
        if isinstance(kwargs.get("params"), dict):
            kwargs["params"] = _query(kwargs["params"])
        response = requests.request(method, url, headers=headers, **kwargs)
        if response.status_code not in tolerate:
            raise_for_upstream(response, service="lemlist")

        if as_text:
            return response.text
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            # lemlist announces `Content-Type: application/json` and sometimes
            # returns BARE TEXT — observed live on 2026-08-31 on
            # `POST/DELETE /v2/unsubscribes/contacts/{id}`, which answers 200
            # "Contact subscription updated". `.json()` then raised on an
            # otherwise successful response, and the call surfaced as a failure.
            # We return the text rather than lose it: the caller sees what
            # lemlist said, and nothing breaks.
            return {"message": response.text}

    @staticmethod
    def _flag_params(**flags: bool) -> Dict[str, str]:
        """Opt-in flags: only the enabled ones are sent, as `"true"` — a
        disabled flag is left out rather than sent as `false`."""
        return {k: "true" for k, v in flags.items() if v}

    # --- Campaigns ---
    #
    # `GET /campaigns` has two shapes and the doc carries an explicit warning to
    # ask for the recent one: we always send `version=v2` rather than trust the
    # server default. It is ALSO paginated (limit max 100) — `list_campaigns`
    # returns ONE page, `list_all_campaigns` walks them.

    #: Campaign statuses accepted as a `status=` filter. A campaign can hold
    #: several at once (a paused campaign WITH errors), so this filters, it does
    #: not partition.
    CAMPAIGN_STATUSES = ("running", "draft", "archived", "ended", "paused", "errors")

    #: Max page size accepted by `GET /campaigns` and `GET /schedules`.
    PAGE_MAX = 100

    @staticmethod
    def _campaign(c: dict) -> Campaign:
        return Campaign(
            id=c["_id"],
            name=c.get("name", ""),
            status=c.get("status", ""),
            senders=c.get("senders", []),
            emoji=c.get("emoji", ""),
            labels=c.get("labels", []) or [],
            timezone=c.get("timezone", ""),
            created_at=c.get("createdAt", ""),
            created_by=c.get("createdBy", ""),
            has_error=bool(c.get("hasError", False)),
            errors=c.get("errors", []) or [],
        )

    def list_campaigns(
        self,
        *,
        limit: int = None,
        offset: int = None,
        page: int = None,
        status: str = None,
        created_by: str = None,
        sort_order: str = None,
    ) -> List[Campaign]:
        """List ONE page of campaigns (up to 100 — the API maximum).

        Args:
            limit: Page size, 1..100. Default (server-side) 100.
            offset: Records to skip. Used when `page` is not given.
            page: 1-based page number.
            status: One of `CAMPAIGN_STATUSES`.
            created_by: Filter on a creator user id (`usr_…`).
            sort_order: `asc` (default) or `desc`, on `createdAt`.

        A caller that needs the WHOLE workspace must use `list_all_campaigns` —
        this one truncates at the page size, and says nothing about it.
        """
        params = self._campaign_params(
            limit=limit, offset=offset, page=page, status=status,
            created_by=created_by, sort_order=sort_order)
        rows, _ = self._campaign_page(
            self._request("GET", "campaigns", params=params))
        return rows

    @classmethod
    def _campaign_params(
        cls, *, limit=None, offset=None, page=None, status=None,
        created_by=None, sort_order=None,
    ) -> Dict[str, Any]:
        """Query of `GET /campaigns`, shared by the page and the walk."""
        if status is not None and status not in cls.CAMPAIGN_STATUSES:
            raise ValueError(
                f"status must be one of {cls.CAMPAIGN_STATUSES}, got {status!r}")
        params: Dict[str, Any] = {"version": "v2"}
        for key, value in (("limit", limit), ("offset", offset), ("page", page),
                           ("status", status), ("createdBy", created_by)):
            if value is not None:
                params[key] = value
        if sort_order is not None:
            params["sortBy"] = "createdAt"
            params["sortOrder"] = sort_order
        return params

    @classmethod
    def _campaign_page(cls, data: Any) -> tuple[List[Campaign], Optional[dict]]:
        """Split a page of campaigns into `(campaigns, pagination)`.

        ⚠️ TWO shapes, and the doc only announces one. The OpenAPI schema of
        `GET /campaigns` describes an ARRAY; that is true in v1, false in v2,
        which returns `{"campaigns": [...], "pagination": {...}}`. Verified live
        on 2026-08-31: the code that iterated over the array therefore walked the
        dict's KEYS and broke on `c["_id"]` ("string indices must be integers") —
        `status()` fell over on the very first call. We accept both rather than
        betting on the doc.
        """
        if isinstance(data, dict):
            rows = data.get("campaigns") or []
            return [cls._campaign(c) for c in rows], data.get("pagination")
        return [cls._campaign(c) for c in (data or [])], None

    def list_all_campaigns(
        self, *, max_pages: int = 20, **filters,
    ) -> tuple[List[Campaign], bool]:
        """Walk `GET /campaigns` page by page.

        Returns `(campaigns, truncated)` — `truncated` is True when `max_pages`
        was reached with a full page still coming, i.e. the list is INCOMPLETE.
        Returning the flag rather than silently stopping is the whole point: a
        capped list that looks complete is how "this campaign does not exist"
        gets answered wrongly.
        """
        out: List[Campaign] = []
        params = dict(filters)
        for page in range(1, max_pages + 1):
            data = self._request("GET", "campaigns", params={
                **self._campaign_params(limit=self.PAGE_MAX, page=page, **params),
            })
            batch, pagination = self._campaign_page(data)
            out.extend(batch)
            # v2 says itself where it stands; trusting that beats counting short
            # pages, which gets a full last page wrong.
            if pagination is not None:
                if not pagination.get("nextPage"):
                    return out, False
            elif len(batch) < self.PAGE_MAX:
                return out, False
        return out, True

    def get_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """Get campaign details."""
        return self._request("GET", f"campaigns/{campaign_id}")

    def create_campaign(
        self,
        name: str,
        *,
        timezone: str = None,
        auto_review: bool = None,
        auto_review_conditions: List[str] = None,
    ) -> Dict[str, Any]:
        """Create a campaign — empty sequence + default schedule come with it.

        Args:
            name: Campaign name (required by the API).
            timezone: IANA zone for the auto-created schedule (e.g.
                `America/New_York`). Defaults server-side to `Europe/Paris`.
            auto_review: Launch every lead as soon as it is added, instead of
                leaving it paused for manual review. ⚠️ This is the switch that
                turns "add a lead" into "send to a real person" — see
                `AUTO_REVIEW_CONDITIONS` and the caller's own gating.
            auto_review_conditions: Restrict auto-launch to leads whose email
                verification matches these statuses.

        Returns the campaign, including `sequenceId` and `scheduleIds` — the two
        ids every other management call needs.

        ⚠️ **A campaign created by the API is born `state="running"`, not as a
        draft** (verified live on 2026-08-31; `duplicate_campaign`, for its part,
        does return `paused`). The `status` shown by `get_campaign` and
        `list_campaigns` is "draft", but that is a DISPLAY status derived from
        the absence of a step and of a lead, not the execution state.
        Consequences: `start_campaign` answers `400 "already running"` on a
        fresh campaign, and nothing "starts" sending — it is adding a sender, a
        step and a LAUNCHED lead that does so. The lock that remains is the
        per-lead review (`launch_lead`). To build a campaign calmly:
        `pause_campaign` right after creation.
        """
        body: Dict[str, Any] = {"name": name}
        if timezone is not None:
            body["timezone"] = timezone
        if auto_review is not None:
            body["autoReview"] = auto_review
        if auto_review_conditions is not None:
            self._check_auto_review_conditions(auto_review_conditions)
            body["autoReviewConditions"] = auto_review_conditions
        return self._request("POST", "campaigns", json=body)

    #: Deliverability statuses accepted by `autoReviewConditions`. Checked here
    #: because lemlist answers an invalid one with a bare 400.
    AUTO_REVIEW_CONDITIONS = ("deliverable", "risky", "undeliverable", "unverified")

    @classmethod
    def _check_auto_review_conditions(cls, conditions: List[str]) -> None:
        bad = [c for c in conditions if c not in cls.AUTO_REVIEW_CONDITIONS]
        if bad:
            raise ValueError(
                f"unknown autoReviewConditions {bad} — "
                f"expected any of {cls.AUTO_REVIEW_CONDITIONS}")

    def start_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """Start (or resume) a campaign.

        ⚠️ This is the call that puts real messages on the wire: from here
        lemlist walks the sequence for every launched lead.

        ⚠️ The doc announces "no-op if already started"; that is FALSE — the API
        answers `400 "You can't start campaigns that are already running."`
        (verified live on 2026-08-31). And since a campaign created by the API is
        ALREADY `running` (see `create_campaign`), this call is in practice only
        useful to RESUME a paused campaign.
        """
        return self._request("POST", f"campaigns/{campaign_id}/start")

    def pause_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """Pause a campaign — LE vrai interrupteur.

        Already-scheduled leads are untouched (lemlist's own wording): pausing
        stops the campaign advancing, it does not recall what is queued.

        ⚠️ Here too the doc announces a no-op and the API answers
        `400 "You can't pause campaigns that are not running."`. Since a
        campaign created by the API is born `running`, it is THIS action — not
        `start_campaign` — that decides whether it can send.
        """
        return self._request("POST", f"campaigns/{campaign_id}/pause")

    def duplicate_campaign(self, campaign_id: str, name: str = None) -> Dict[str, Any]:
        """Copy a campaign — sequence steps, schedules and AI templates included.

        The copy lands in DRAFT with lead counts at zero (CRM settings are not
        copied). Without `name` lemlist appends " Copy" to the original.
        """
        body = {"name": name} if name is not None else {}
        return self._request("POST", f"campaigns/{campaign_id}/duplicate", json=body)

    def update_campaign(self, campaign_id: str, data: dict) -> Dict[str, Any]:
        """Update campaign settings (PATCH — only the keys you send change).

        Notable keys: `name`, `sendUserIds` (senders, `usr_…`), the stop rules
        (`stopOnEmailReplied`, `stopOnMeetingBooked`, `stopOnLinkClicked`),
        tracking toggles, `aiFeatures`, `onReplied`, and `autoReview` /
        `autoReviewConditions`.
        """
        if "autoReviewConditions" in data:
            self._check_auto_review_conditions(data["autoReviewConditions"])
        return self._request("PATCH", f"campaigns/{campaign_id}", json=data)

    def get_campaign_statutes(self, campaign_id: str) -> Dict[str, Any]:
        """Validation statutes — the same engine the lemlist UI runs.

        Returns `{name, status, statutes: [{type, level, message, category}]}`
        where `level` 3 BLOCKS a launch (no sender, broken DNS…), 2 is an
        actionable warning (daily limit, missing schedule) and 1 is info. This
        is what to read BEFORE `start_campaign`, rather than starting and
        discovering the campaign errors out.
        """
        return self._request("GET", f"campaigns/{campaign_id}/statutes")

    # --- Sequences & Steps ---

    def get_sequences(self, campaign_id: str) -> Dict[str, Any]:
        """Get sequences for a campaign.

        Returns:
            Dict mapping sequence_id -> sequence data with steps and level.
        """
        return self._request("GET", f"campaigns/{campaign_id}/sequences")

    def get_sequence_steps(self, campaign_id: str, sequence_id: str) -> List[Dict]:
        """Get steps for a specific sequence."""
        sequences = self.get_sequences(campaign_id)
        if sequence_id in sequences:
            return sequences[sequence_id].get('steps', [])
        return []

    #: Step types accepted by `POST/PATCH /sequences/{id}/steps`. Checked
    #: locally: a typo lands as a bare 400 that says nothing about the vocabulary.
    STEP_TYPES = (
        "email", "manual", "phone", "api",
        "linkedinVisit", "linkedinInvite", "linkedinSend", "linkedinVoiceNote",
        "linkedinFollow", "linkedinLikeLastPost", "linkedinCommentLastPost",
        "linkedinEndorse", "linkedinWithdrawInvitation",
        "sendToAnotherCampaign", "conditional", "whatsappMessage", "sms",
    )

    #: Condition keys for a `conditional` step (the branch test).
    CONDITION_KEYS = (
        "hasEmailAddress", "hasLinkedinUrl", "hasPhoneNumber", "hasScore",
        "emailsOpened", "emailsClicked", "emailsUnsubscribed", "meetingBooked",
        "linkedinInviteAccepted", "linkedinOpened", "aircallDone",
        "linkedinNetworkCheck", "hasWhatsappAccount",
    )

    def add_step(self, sequence_id: str, step: dict) -> Dict[str, Any]:
        """Add a step (or a condition) to a sequence.

        Args:
            sequence_id: Sequence ID (e.g., 'seq_abc123')
            step: Step data. `type` is REQUIRED and must be one of
                  `STEP_TYPES`. Common fields: `index` (insert position, ≥ -1,
                  appended when omitted or past the end), `delay` (days before
                  the step — server default 0 for the first, 1 after),
                  `subject` (email), `message` (email/linkedin/manual/phone/
                  whatsapp/sms), `title` (manual), `method` + `url` (api),
                  `conditionKey` + `delayType` (conditional), `campaignId`
                  (sendToAnotherCampaign), `images`/`videos` (linkedinInvite /
                  linkedinSend, public HTTPS URLs lemlist re-hosts).
                  For email: {'type': 'email', 'subject': '...', 'message': '...', 'delay': 0}
        """
        self._check_step(step, require_type=True)
        return self._request("POST", f"sequences/{sequence_id}/steps", json=step)

    @classmethod
    def _check_step(cls, step: dict, *, require_type: bool) -> None:
        """Reject a step whose `type`/`conditionKey` is outside the vocabulary."""
        step_type = step.get("type")
        if step_type is None:
            if require_type:
                raise ValueError(
                    f"step needs a 'type' — one of {cls.STEP_TYPES}")
        elif step_type not in cls.STEP_TYPES:
            raise ValueError(
                f"unknown step type {step_type!r} — expected one of {cls.STEP_TYPES}")
        key = step.get("conditionKey")
        if key is not None and key not in cls.CONDITION_KEYS:
            raise ValueError(
                f"unknown conditionKey {key!r} — expected one of {cls.CONDITION_KEYS}")

    def update_step(self, sequence_id: str, step_id: str, data: dict) -> Dict[str, Any]:
        """Update a step.

        Args:
            sequence_id: Sequence ID
            step_id: Step ID
            data: Update data. `type` is REQUIRED by the API and must MATCH the
                existing step — it identifies the step's shape, it does not
                convert it. Same field vocabulary as `add_step`; `images` /
                `videos` REPLACE what the step carries (`[]` clears them).
        """
        self._check_step(data, require_type=True)
        return self._request("PATCH", f"sequences/{sequence_id}/steps/{step_id}", json=data)

    def delete_step(self, sequence_id: str, step_id: str) -> Dict[str, Any]:
        """Delete a step from a sequence.

        Refused by lemlist (400) while the campaign is RUNNING — pause it first.
        """
        return self._request("DELETE", f"sequences/{sequence_id}/steps/{step_id}")

    # --- A/B tests (email steps, Email Pro plan) ---
    #
    # An A/B test is variant B hanging off an email step: creating it starts the
    # split, deleting it ends the test, and picking a winner applies one template
    # to every remaining lead. All four live on the SAME path, one verb each.

    def create_ab_variant(self, sequence_id: str, step_id: str) -> Dict[str, Any]:
        """Create variant B on an email step, prefilled from A, and START the
        split (leads are shared between A and B from here on)."""
        return self._request(
            "POST", f"sequences/{sequence_id}/steps/{step_id}/ab-test")

    def get_ab_variant(self, sequence_id: str, step_id: str) -> Dict[str, Any]:
        """Read variant B (subject, message, config) of an email step's A/B test."""
        return self._request(
            "GET", f"sequences/{sequence_id}/steps/{step_id}/ab-test")

    def update_ab_variant(
        self, sequence_id: str, step_id: str, data: dict,
    ) -> Dict[str, Any]:
        """Edit variant B — only the keys sent change.

        Accepts `subject` (≤ 400 chars), `message` (HTML), `altMessage`, `cc`,
        `plainText`.
        """
        return self._request(
            "PATCH", f"sequences/{sequence_id}/steps/{step_id}/ab-test", json=data)

    def delete_ab_variant(
        self, sequence_id: str, step_id: str, variant: str = "B",
    ) -> Dict[str, Any]:
        """End the A/B test by dropping one variant.

        `variant="B"` (default) drops B; `variant="A"` PROMOTES B to A — the
        step keeps B's content. Sent as a query parameter, not a body.
        """
        if variant not in ("A", "B"):
            raise ValueError(f"variant must be 'A' or 'B', got {variant!r}")
        return self._request(
            "DELETE", f"sequences/{sequence_id}/steps/{step_id}/ab-test",
            params={"variant": variant})

    def select_ab_winner(
        self, sequence_id: str, step_id: str, variant: str,
    ) -> Dict[str, Any]:
        """Pick the winning variant — its template is then sent to every
        remaining lead going through the campaign."""
        if variant not in ("A", "B"):
            raise ValueError(f"variant must be 'A' or 'B', got {variant!r}")
        return self._request(
            "POST", f"sequences/{sequence_id}/steps/{step_id}/ab-test/winner",
            json={"variant": variant})

    # --- Schedules ---
    #
    # A schedule is a sending WINDOW (days, hours, timezone, pacing) owned by the
    # team, not by a campaign: several campaigns can share one, and a campaign can
    # carry several. Creating a campaign auto-creates one and returns its id in
    # `scheduleIds`.

    def list_schedules(
        self,
        *,
        limit: int = None,
        offset: int = None,
        page: int = None,
        sort_order: str = None,
    ) -> Dict[str, Any]:
        """List the team's schedules (paginated). Returns `{schedules: [...], ...}`."""
        params: Dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if page is not None:
            params["page"] = page
        if sort_order is not None:
            params["sortBy"] = "createdAt"
            params["sortOrder"] = sort_order
        return self._request("GET", "schedules", params=params)

    def get_schedule(self, schedule_id: str) -> Dict[str, Any]:
        """Get one schedule."""
        return self._request("GET", f"schedules/{schedule_id}")

    def create_schedule(
        self,
        name: str,
        *,
        timezone: str = "Europe/Paris",
        start: str = "09:00",
        end: str = "18:00",
        weekdays: List[int] = None,
        seconds_to_wait: int = None,
        public: bool = None,
    ) -> Dict[str, Any]:
        """Create a sending window.

        Args:
            name: Schedule name.
            timezone: IANA zone the hours are read in.
            start / end: `HH:mm`, 24h.
            weekdays: Active days, 1 = Monday … 7 = Sunday. Defaults to Mon-Fri.
            seconds_to_wait: Pacing between two sends (server default 1200).
            public: Expose it as a template to the team.

        `name`, `timezone`, `start`, `end` and `weekdays` are all REQUIRED by the
        API — the defaults here fill them in rather than let a partial body 400.
        """
        weekdays = [1, 2, 3, 4, 5] if weekdays is None else weekdays
        self._check_schedule(
            start=start, end=end, weekdays=weekdays, timezone=timezone)
        body: Dict[str, Any] = {
            "name": name, "timezone": timezone,
            "start": start, "end": end, "weekdays": weekdays,
        }
        if seconds_to_wait is not None:
            body["secondsToWait"] = seconds_to_wait
        if public is not None:
            body["public"] = public
        return self._request("POST", "schedules", json=body)

    def update_schedule(self, schedule_id: str, data: dict) -> Dict[str, Any]:
        """Update a schedule (PATCH — only the keys sent change).

        Same vocabulary as `create_schedule`: `name`, `timezone`, `start`,
        `end`, `weekdays`, `secondsToWait`, `public`.
        """
        self._check_schedule(
            start=data.get("start"), end=data.get("end"),
            weekdays=data.get("weekdays"), timezone=data.get("timezone"))
        return self._request("PATCH", f"schedules/{schedule_id}", json=data)

    def delete_schedule(self, schedule_id: str) -> Dict[str, Any]:
        """Delete a schedule."""
        return self._request("DELETE", f"schedules/{schedule_id}")

    def get_campaign_schedules(self, campaign_id: str) -> List[Dict[str, Any]]:
        """List the schedules attached to a campaign.

        ⚠️ The documented path carries a TRAILING SLASH
        (`/campaigns/{id}/schedules/`) — kept verbatim.
        """
        return self._request("GET", f"campaigns/{campaign_id}/schedules/")

    def associate_schedule(self, campaign_id: str, schedule_id: str) -> Dict[str, Any]:
        """Attach an existing schedule to a campaign."""
        return self._request(
            "POST", f"campaigns/{campaign_id}/schedules/{schedule_id}")

    _TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

    @classmethod
    def _check_schedule(cls, *, start=None, end=None, weekdays=None, timezone=None) -> None:
        """Reject a malformed window locally — `start`/`end` are `HH:mm` and
        `weekdays` are 1..7, both patterns lemlist only answers with a bare 400."""
        for label, value in (("start", start), ("end", end)):
            if value is not None and not cls._TIME_RE.match(value):
                raise ValueError(f"{label} must be HH:mm (24h), got {value!r}")
        if weekdays is not None:
            bad = [d for d in weekdays if not isinstance(d, int) or not 1 <= d <= 7]
            if bad:
                raise ValueError(
                    f"weekdays must be ints 1 (Monday) to 7 (Sunday), got {bad!r}")
        if timezone is not None and "/" not in timezone and timezone != "UTC":
            raise ValueError(
                f"timezone must be an IANA name (e.g. 'Europe/Paris'), got {timezone!r}")

    # --- Campaign Tree ---

    def get_campaign_tree(self, campaign_id: str) -> Dict[str, Any]:
        """Get full campaign structure with sequences organized by level.

        Returns a tree structure with sequences dict, steps_flat list (depth-first),
        and branch information for conditionals.
        """
        campaign = self.get_campaign(campaign_id)
        sequences_raw = self.get_sequences(campaign_id)

        sequences = {}
        root_sequence = None

        for seq_id, seq_data in sequences_raw.items():
            level = seq_data.get('level', 0)
            if level == 0:
                root_sequence = seq_id

            steps = []
            for step in seq_data.get('steps', []):
                step_clean = {
                    'id': step.get('_id'),
                    'type': step.get('type'),
                    'delay': step.get('delay'),
                }
                if step.get('type') == 'email':
                    step_clean['subject'] = step.get('subject', '')
                    step_clean['message'] = step.get('message', '')
                elif step.get('type') in ('linkedinInvite', 'linkedinMessage', 'linkedinSend'):
                    step_clean['message'] = step.get('message', '')
                elif step.get('type') == 'conditional':
                    conditions = step.get('conditions', [])
                    step_clean['branches'] = []
                    for cond in conditions:
                        branch = {
                            'sequence_id': cond.get('sequenceId'),
                            'label': cond.get('label', 'Fallback' if cond.get('fallback') else 'Unknown'),
                            'fallback': cond.get('fallback', False),
                        }
                        if cond.get('key'):
                            branch['key'] = cond['key']
                        step_clean['branches'].append(branch)

                steps.append(step_clean)

            sequences[seq_id] = {
                'id': seq_id,
                'level': level,
                'steps': steps,
            }

        # Build flat list in execution order (depth-first traversal)
        steps_flat = []

        def traverse(seq_id: str, path: str = 'root'):
            if seq_id not in sequences:
                return
            seq = sequences[seq_id]
            for step in seq['steps']:
                steps_flat.append({
                    'sequence': seq_id,
                    'step': step,
                    'path': path,
                    'level': seq['level'],
                })
                if step.get('type') == 'conditional' and step.get('branches'):
                    for branch in step['branches']:
                        child_seq = branch.get('sequence_id')
                        if child_seq:
                            branch_label = branch.get('label', 'branch')
                            child_path = f"{path} > {branch_label}"
                            traverse(child_seq, child_path)

        if root_sequence:
            traverse(root_sequence)

        return {
            'id': campaign_id,
            'name': campaign.get('name', ''),
            'status': campaign.get('status', 'unknown'),
            'root_sequence': root_sequence,
            'sequences': sequences,
            'steps_flat': steps_flat,
        }

    def save_campaign_tree(self, campaign_id: str, directory: Path = None, tree: dict = None) -> Path:
        """Save campaign tree to local JSON file.

        Args:
            campaign_id: Campaign ID
            directory: Directory to save to (default: current directory)
            tree: Optional pre-fetched tree (will fetch if not provided)

        Returns:
            Path to saved file
        """
        if tree is None:
            tree = self.get_campaign_tree(campaign_id)

        save_dir = Path(directory) if directory else Path.cwd()
        save_dir.mkdir(parents=True, exist_ok=True)
        tree['synced_at'] = datetime.now().isoformat()

        filepath = save_dir / f"{campaign_id}.json"
        filepath.write_text(json.dumps(tree, indent=2, ensure_ascii=False))
        return filepath

    @staticmethod
    def load_campaign_tree(campaign_id: str, directory: Path = None) -> Optional[dict]:
        """Load campaign tree from local cache.

        Args:
            campaign_id: Campaign ID
            directory: Directory to load from (default: current directory)

        Returns:
            Campaign tree dict or None if not cached
        """
        load_dir = Path(directory) if directory else Path.cwd()
        filepath = load_dir / f"{campaign_id}.json"
        if filepath.exists():
            return json.loads(filepath.read_text())
        return None

    def sync_campaign(self, campaign_id: str, directory: Path = None) -> dict:
        """Fetch campaign tree from API and save locally.

        Returns:
            Campaign tree dict
        """
        tree = self.get_campaign_tree(campaign_id)
        self.save_campaign_tree(campaign_id, directory=directory, tree=tree)
        return tree

    # --- Tree helpers (work on tree dicts from get_campaign_tree) ---

    @staticmethod
    def find_step(tree: dict, step_id: str) -> Optional[dict]:
        """Find a step by ID in a campaign tree."""
        for item in tree.get('steps_flat', []):
            if item['step'].get('id') == step_id:
                return item
        return None

    @staticmethod
    def get_first_email(tree: dict) -> Optional[dict]:
        """Get the first email step in execution order."""
        for item in tree.get('steps_flat', []):
            if item['step'].get('type') == 'email':
                return item
        return None

    @staticmethod
    def get_emails(tree: dict) -> List[dict]:
        """Get all email steps from a campaign tree."""
        return [item for item in tree.get('steps_flat', []) if item['step'].get('type') == 'email']

    @staticmethod
    def print_tree(tree: dict):
        """Print campaign tree in a readable format."""
        print(f"Campaign: {tree['name']}")
        print(f"Status: {tree['status']}")
        if tree.get('synced_at'):
            print(f"Synced: {tree['synced_at'][:16].replace('T', ' ')}")
        print()
        print("Sequence:")
        for item in tree['steps_flat']:
            step = item['step']
            indent = '  ' * item['level']
            delay = f"J+{step['delay']}" if step['delay'] else 'J+0'

            if step['type'] == 'email':
                label = step.get('subject', '')[:45]
                print(f"{indent}[{delay}] 📧 {label}")
            elif step['type'] == 'conditional':
                branches = [b['label'][:20] for b in step.get('branches', [])]
                print(f"{indent}[{delay}] ❓ {' | '.join(branches)}")
            elif step['type'] == 'linkedinVisit':
                print(f"{indent}[{delay}] 👁️  LinkedIn visit")
            elif step['type'] == 'linkedinInvite':
                print(f"{indent}[{delay}] 🤝 LinkedIn invite")
            elif step['type'] in ('linkedinSend', 'linkedinMessage'):
                print(f"{indent}[{delay}] 💬 LinkedIn message")
            elif step['type'] == 'phone':
                print(f"{indent}[{delay}] 📞 Phone call")
            else:
                print(f"{indent}[{delay}] {step['type']}")

    # --- Leads ---

    def add_lead(self, campaign_id: str, lead) -> Dict[str, Any]:
        """Add lead to campaign.

        Args:
            campaign_id: Campaign ID
            lead: Lead dataclass or dict with email + optional fields
        """
        if isinstance(lead, Lead):
            data = {}
            if lead.firstName:
                data["firstName"] = lead.firstName
            if lead.lastName:
                data["lastName"] = lead.lastName
            if lead.companyName:
                data["companyName"] = lead.companyName
            if lead.phone:
                data["phone"] = lead.phone
            if lead.linkedinUrl:
                data["linkedinUrl"] = lead.linkedinUrl
            email = lead.email
        elif isinstance(lead, dict):
            lead = dict(lead)  # copy to avoid mutating
            email = lead.pop("email")
            data = lead
        else:
            raise TypeError(f"lead must be Lead or dict, got {type(lead)}")

        return self._request("POST", f"campaigns/{campaign_id}/leads/{email}", json=data)

    def get_all_leads(self, campaign_id: str) -> List[Dict[str, Any]]:
        """Get all leads from a campaign via CSV export."""
        import csv
        import io

        csv_text = self.export_leads(campaign_id)
        if not csv_text.strip():
            return []
        reader = csv.DictReader(io.StringIO(csv_text))
        return list(reader)

    def delete_lead(
        self, campaign_id: str, email: str, *, action: str = None,
    ) -> Dict[str, Any]:
        """Remove a lead from a campaign — or unsubscribe it.

        ONE route serves both gestures, and the default is the SOFT one:
        without `action="remove"` lemlist UNSUBSCRIBES the lead (it stays on the
        campaign, marked unsubscribed) rather than deleting it. This method
        predates the parameter and therefore always took the soft path while its
        name said otherwise — pass `action="remove"` for a real delete.

        Args:
            campaign_id: Campaign the lead sits in.
            email: Lead email — or its id, both are accepted on this route.
            action: `"remove"` to force the delete. Anything else (or nothing)
                unsubscribes.
        """
        params = {"action": action} if action is not None else None
        return self._request(
            "DELETE", f"campaigns/{campaign_id}/leads/{email}", params=params)

    def unsubscribe_lead(self, campaign_id: str, email: str) -> Dict[str, Any]:
        """Unsubscribe a lead from a campaign (the soft half of `delete_lead`)."""
        return self.delete_lead(campaign_id, email)

    def get_lead(
        self, *, lead_id: str = None, email: str = None, version: str = None,
    ) -> Dict[str, Any]:
        """Look a lead up by id or by email (`GET /leads`).

        One of `lead_id` / `email` is required. Distinct from
        `get_lead_by_email`, which puts the address IN THE PATH — same object,
        two routes lemlist documents separately.
        """
        if not (lead_id or email):
            raise ValueError("get_lead needs lead_id or email")
        params = {k: v for k, v in
                  {"id": lead_id, "email": email, "version": version}.items()
                  if v is not None}
        return self._request("GET", "leads", params=params)

    def get_lead_by_email(self, email: str, *, version: str = None) -> Dict[str, Any]:
        """Look a lead up by email, address in the path (`GET /leads/{email}`)."""
        params = {"version": version} if version is not None else None
        return self._request("GET", f"leads/{email}", params=params)

    def get_campaign_leads(
        self, campaign_id: str, *, state: str = None, limit: int = None,
    ) -> Any:
        """Leads of a campaign with their state (`GET /campaigns/{id}/leads/`).

        The JSON route, as opposed to `get_all_leads`, which goes through the
        CSV export. ⚠️ Trailing slash kept verbatim, as documented.
        """
        params = {k: v for k, v in {"state": state, "limit": limit}.items()
                  if v is not None}
        return self._request("GET", f"campaigns/{campaign_id}/leads/", params=params)

    def update_lead(
        self, campaign_id: str, lead_id: str, data: dict,
    ) -> Dict[str, Any]:
        """Update a lead inside a campaign.

        Accepts `firstName`, `lastName`, `companyName`, `jobTitle`,
        `preferredContactMethod`. Custom variables go through
        `update_lead_variables`, not here.
        """
        return self._request(
            "PATCH", f"campaigns/{campaign_id}/leads/{lead_id}", json=data)

    def update_lead_variables(
        self, lead_id: str, variables: Dict[str, str],
    ) -> Dict[str, Any]:
        """Update custom variables on a lead.

        Like `add_lead_variables`, the variables travel as QUERY parameters
        (arbitrary keys) — not as a JSON body. Spaces travel as `%20` and not as
        `+`, see `_vars_query`: lemlist stored the `+` literally.
        """
        return self._request(
            "PATCH", f"leads/{lead_id}/variables",
            params=self._vars_query(variables))

    def delete_lead_variables(
        self, lead_id: str, names: List[str],
    ) -> Dict[str, Any]:
        """Erase custom variables on a lead.

        `names` are the variable NAMES; lemlist reads them as query keys, so
        each is sent with an empty value — the key's presence is the instruction.
        """
        if not names:
            raise ValueError("names is empty — nothing to erase")
        # Same encoding as the two setters: a variable NAME carrying a space
        # would otherwise designate a different key than the one we think we erase.
        return self._request(
            "DELETE", f"leads/{lead_id}/variables",
            params=self._vars_query({n: "" for n in names}))

    def pause_lead(self, lead_id: str, *, campaign_id: str = None) -> Dict[str, Any]:
        """Pause a lead — in ONE campaign with `campaign_id`, in ALL of them
        without it. The scope is the parameter; omitting it is not the narrow
        case, it is the wide one."""
        params = {"campaignId": campaign_id} if campaign_id is not None else None
        return self._request("POST", f"leads/pause/{lead_id}", params=params)

    def resume_lead(self, lead_id: str) -> Dict[str, Any]:
        """Resume a paused lead (`POST /leads/start/{leadId}`).

        ⚠️ Puts the lead back in a live sequence: from here lemlist resumes
        sending to it.
        """
        return self._request("POST", f"leads/start/{lead_id}")

    def mark_lead_interested(
        self, lead_id_or_email: str, *, campaign_id: str = None,
    ) -> Dict[str, Any]:
        """Mark a lead interested — in one campaign with `campaign_id`, across
        ALL campaigns without it (two distinct documented routes)."""
        if campaign_id:
            return self._request(
                "POST", f"campaigns/{campaign_id}/leads/{lead_id_or_email}/interested")
        return self._request("POST", f"leads/interested/{lead_id_or_email}")

    def mark_lead_not_interested(
        self, lead_id_or_email: str, *, campaign_id: str = None,
    ) -> Dict[str, Any]:
        """Mark a lead not interested — one campaign, or all of them."""
        if campaign_id:
            return self._request(
                "POST",
                f"campaigns/{campaign_id}/leads/{lead_id_or_email}/notinterested")
        return self._request("POST", f"leads/notinterested/{lead_id_or_email}")

    def import_leads_from_crm(
        self,
        campaign_id: str,
        *,
        crm: str,
        user_id: str,
        filter_id: str,
        filter_type: str = None,
        deduplicate: bool = None,
    ) -> Dict[str, Any]:
        """Import leads from a connected CRM into a campaign.

        `crm`, `user_id` and `filter_id` are all required by the API — the
        filter is the CRM-side selection (list `crm_filters` to pick one).
        """
        body: Dict[str, Any] = {
            "crm": crm, "userId": user_id, "filterId": filter_id,
        }
        if filter_type is not None:
            body["filterType"] = filter_type
        if deduplicate is not None:
            body["deduplicate"] = deduplicate
        return self._request(
            "POST", f"campaigns/{campaign_id}/leads/import", json=body)

    def upload_lead_audio(
        self, lead_id: str, step_id: str, audio: Any, *, filename: str = "audio.mp3",
    ) -> Dict[str, Any]:
        """Upload the audio of a `linkedinVoiceNote` step for one lead.

        The ONLY multipart route of the API: the file goes in `files=`, the two
        ids in the query. `audio` is bytes or an open binary file.
        """
        return self._request(
            "POST", "leads/audio",
            params={"leadId": lead_id, "stepId": step_id},
            files={"file": (filename, audio)})

    def create_lead(
        self,
        campaign_id: str,
        lead: Dict[str, Any],
        *,
        deduplicate: bool = False,
        linkedin_enrichment: bool = False,
        find_email: bool = False,
        verify_email: bool = False,
        find_phone: bool = False,
    ) -> Dict[str, Any]:
        """Create a lead in a campaign (current API: POST /campaigns/{id}/leads/,
        email in the body — distinct from the legacy `add_lead`/{email}-in-path
        endpoint above).

        Args:
            campaign_id: Campaign ID.
            lead: Lead fields — email, firstName, lastName, companyName, jobTitle,
                linkedinUrl, picture, phone, companyDomain, icebreaker, timezone
                (IANA, e.g. "Europe/Paris"), contactOwner (user ID or login
                email). All optional per the API; any other key is stored as a
                custom variable.
            deduplicate: Skip the insert if the email already exists in another
                campaign.
            linkedin_enrichment: Run LinkedIn enrichment on the lead.
            find_email: Find a verified email for the lead.
            verify_email: Verify the lead's existing email (debounce).
            find_phone: Find a phone number for the lead.

        Returns the created lead, including `_id` (used by `launch_lead` and
        `add_lead_variables`).
        """
        params = self._flag_params(
            deduplicate=deduplicate,
            linkedinEnrichment=linkedin_enrichment,
            findEmail=find_email,
            verifyEmail=verify_email,
            findPhone=find_phone,
        )
        return self._request(
            "POST", f"campaigns/{campaign_id}/leads/", json=lead, params=params,
        )

    def launch_lead(self, lead_id: str) -> Dict[str, Any]:
        """Launch a lead pending manual review (POST /leads/review/{leadId}).

        Only relevant for a campaign with review-before-send enabled — such a
        campaign leaves a newly created lead paused for review until launched.
        Returns `{"ok": true}` on success; a 400 raises `UpstreamHTTPError` whose
        body carries a lemlist error code, e.g.
        `CAMPAIGN_LEAD_REVIEW_LEAD_ALREADY_LAUNCHED`,
        `CAMPAIGN_LEAD_REVIEW_LEAD_PAUSED`,
        `CAMPAIGN_LEAD_REVIEW_LEAD_AI_VARIABLE_INVALID`,
        `CAMPAIGN_LEAD_REVIEW_LEAD_NO_SENDER_AVAILABLE`,
        `CAMPAIGN_LEAD_REVIEW_CAMPAIGN_STEP_ERRORS`.
        """
        return self._request("POST", f"leads/review/{lead_id}")

    @staticmethod
    def _vars_query(variables: Dict[str, str]) -> str:
        """The lead-variables query string, spaces as `%20` and not `+`.

        ⚠️ **lemlist decodes `%XX` but NOT `+`.** The three variable routes
        take their values as URL parameters (arbitrary keys, no JSON body);
        `requests` encodes the space as `+` there by default, and lemlist stores
        that `+` LITERALLY. Measured 4 times out of 4 on 10/09/2026:
        `{"posteRecrute": "un Traffic Manager"}` is read back as
        `"un+Traffic+Manager"`, and goes out as is in the message to the prospect.

        The signal that found it carried its own proof: *"the slash, for its
        part, survives"* — `/` travels as `%2F` and comes back intact, so lemlist
        does decode percent-encoding. It is the `+` alone that it ignores.

        ⚠️ **No error was raised**: the response was `{"ok": true}`.
        The workaround found on the caller's side was to delete the lead and
        recreate it — creation goes through a JSON body and never had the
        defect.

        Returned as an already-encoded STRING: `requests` does not touch a query
        string passed as is, whereas it would re-encode a dict.
        """
        return urlencode(variables, quote_via=quote)

    def add_lead_variables(self, lead_id: str, variables: Dict[str, str]) -> Dict[str, Any]:
        """Add/set custom variables on a lead (POST /leads/{leadId}/variables).

        `variables` is sent as query parameters — the lemlist API contract for
        this endpoint (arbitrary keys, e.g. `{"customField1": "..."}`), not a
        JSON body. Spaces travel as `%20`, cf. `_vars_query`.
        """
        return self._request("POST", f"leads/{lead_id}/variables",
                             params=self._vars_query(variables))

    def export_leads(self, campaign_id: str, state: str = None) -> str:
        """Export leads from campaign as CSV."""
        self._rate_limit()
        params = {}
        if state:
            params["state"] = state

        response = requests.get(
            f"{self.BASE_URL}/campaigns/{campaign_id}/export",
            headers={"Authorization": self._get_auth_header()},
            params=params
        , timeout=_HTTP_TIMEOUT)
        response.raise_for_status()
        return response.text

    # --- Enrichment ---
    #
    # Standalone enrichment, distinct from the flags on `create_lead` (which only
    # enrich a lead on its way into a campaign). Every call is ASYNC: the POST
    # returns an id in ~1s, the work runs server-side, and `get_enrichment` polls
    # it. Each action spends lemlist enrichment credits.

    #: Enrichment actions, as the v1 query flags (`/enrich`, `/leads/{id}/enrich`).
    ENRICH_FLAGS = {
        "find_email": "findEmail",
        "verify_email": "verifyEmail",
        "linkedin_enrichment": "linkedinEnrichment",
        "find_phone": "findPhone",
    }
    #: The same actions as the v2 bulk vocabulary — deliberately NOT a snake_case
    #: of the v1 names: lemlist calls email verification `verify`, not `verify_email`.
    ENRICH_BULK_ACTIONS = {
        "find_email": "find_email",
        "verify_email": "verify",
        "linkedin_enrichment": "linkedin_enrichment",
        "find_phone": "find_phone",
    }

    def enrich(
        self,
        *,
        email: str = None,
        linkedin_url: str = None,
        first_name: str = None,
        last_name: str = None,
        company_name: str = None,
        company_domain: str = None,
        find_email: bool = False,
        verify_email: bool = False,
        linkedin_enrichment: bool = False,
        find_phone: bool = False,
        webhook_url: str = None,
    ) -> Dict[str, Any]:
        """Submit a standalone enrichment (POST /enrich) — no campaign, no lead.

        Args:
            email, linkedin_url, first_name, last_name, company_name,
                company_domain: the identity to enrich. All optional on paper;
                lemlist matches on whatever it gets, so a LinkedIn URL or a
                name + company domain is what actually resolves.
            find_email: find a verified email.
            verify_email: verify the email passed in (debounce).
            linkedin_enrichment: run the LinkedIn enrichment.
            find_phone: find a phone number.
            webhook_url: notified when the enrichment completes, instead of polling.

        At least one action is required — lemlist answers 400 otherwise.

        Returns `{"id": "enr_..."}`; pass that id to `get_enrichment`.
        """
        params = self._flag_params(
            findEmail=find_email,
            verifyEmail=verify_email,
            linkedinEnrichment=linkedin_enrichment,
            findPhone=find_phone,
        )
        if not params:
            raise ValueError(
                "no enrichment requested — set at least one of find_email, "
                "verify_email, linkedin_enrichment, find_phone"
            )
        params.update({
            k: v for k, v in {
                "email": email,
                "linkedinUrl": linkedin_url,
                "firstName": first_name,
                "lastName": last_name,
                "companyName": company_name,
                "companyDomain": company_domain,
                "webhookUrl": webhook_url,
            }.items() if v is not None
        })
        return self._request("POST", "enrich", params=params)

    def get_enrichment(self, enrich_id: str) -> Dict[str, Any]:
        """Poll an enrichment (GET /enrich/{enrichId}).

        Returns `{enrichmentId, enrichmentStatus, input, data}`. `enrichmentStatus`
        is the field to branch on — `in-progress` (HTTP 202), `done` (200) or
        `not-found` (404, a legitimate payload rather than an error, so it is
        returned instead of raised). `data` holds the found fields once done —
        shapes observed live, beyond the published schema: `email` carries
        `email` plus a verification `status` (`deliverable`/`undeliverable`),
        `phone` carries `phone`, `linkedin` carries a full profile, or `{}`
        when it could not be resolved.

        Two live caveats. `notFound` is not reliable — seen `false` on a
        payload with no number. And `done` does not guarantee the payload has
        landed: lemlist sometimes flips the status first and fills `data` on a
        later poll, so re-read once before concluding nothing was found.
        """
        return self._request("GET", f"enrich/{enrich_id}", tolerate=(404,))

    def enrich_lead(
        self,
        lead_id: str,
        *,
        find_email: bool = False,
        verify_email: bool = False,
        linkedin_enrichment: bool = False,
        find_phone: bool = False,
        webhook_url: str = None,
    ) -> Dict[str, Any]:
        """Enrich a lead already in a campaign (POST /leads/{leadId}/enrich).

        Same actions as `enrich`, but the identity comes from the existing lead
        and the result is written back onto it. Returns `{"id": "enr_..."}`,
        pollable with `get_enrichment`.

        Only accepted for a lead still AWAITING REVIEW: a reviewed lead — every
        lead in a campaign without review-before-send — gets
        `400 {"error": "lemrich is not available for lead reviewed"}`.
        """
        params = self._flag_params(
            findEmail=find_email,
            verifyEmail=verify_email,
            linkedinEnrichment=linkedin_enrichment,
            findPhone=find_phone,
        )
        if not params:
            raise ValueError(
                "no enrichment requested — set at least one of find_email, "
                "verify_email, linkedin_enrichment, find_phone"
            )
        if webhook_url:
            params["webhookUrl"] = webhook_url
        return self._request("POST", f"leads/{lead_id}/enrich", params=params)

    def bulk_enrich(
        self, items: List[Dict[str, Any]], webhook_url: str = None,
    ) -> List[Dict[str, Any]]:
        """Submit several enrichments in one call (POST /v2/enrichments/bulk).

        Args:
            items: one entry per person, each
                `{"input": {linkedinUrl|email|firstName|lastName|companyName|
                companyDomain}, "enrichmentRequests": [...], "metadata": {...}}`.
                `enrichmentRequests` uses the v2 vocabulary — `find_email`,
                `find_phone`, `verify`, `linkedin_enrichment` (see
                `ENRICH_BULK_ACTIONS`). `metadata` is echoed back, use it to
                match ids to your own rows. ⚠️ Its VALUES must be
                STRINGS: `{"row": 1}` is rejected (`WRONG_METADATA_FORMAT`),
                `{"row": "1"}` passes — verified live on 2026-08-31, where the
                doc only says "string or object".
            webhook_url: notified as each enrichment completes.

        Returns one entry per item, in order — `{"id": "enr_...", "metadata": ...}`
        or `{"error": "MISSING_INPUTS", "metadata": ...}`. Unlike a FullEnrich
        job, a bulk submit yields N ids, not one: poll each with `get_enrichment`.
        """
        params = {"webhookUrl": webhook_url} if webhook_url else None
        return self._request("POST", "v2/enrichments/bulk", json=items, params=params)

    # --- Activities & Stats ---

    def get_activities(
        self,
        campaign_id: str = None,
        limit: int = 100,
        offset: int = 0,
        *,
        type: str = None,
        is_first: bool = None,
        lead_id: str = None,
        min_date: str = None,
        max_date: str = None,
        start_date: str = None,
        end_date: str = None,
        fields: List[str] = None,
        compact: bool = False,
    ) -> List[Dict]:
        """Get activities (lead interactions).

        `version=v2` is sent unconditionally — the doc marks it REQUIRED on this
        route, and this method used to omit it.

        Args:
            campaign_id: Optional campaign filter
            limit: Max results (default 100)
            offset: Pagination offset
            type: Activity type (`emailsSent`, `emailsOpened`, `paused`…)
            is_first: Keep only the first activity of its kind per lead
            lead_id: Restrict to one lead
            min_date / max_date: Bounds on the activity date
            start_date / end_date: The other documented pair of bounds — lemlist
                exposes both; kept distinct rather than merged into a guess.
            fields: Keep only these keys of each activity (names, dotted paths
                such as `lead.variables.email`).
            compact: Keep a preset (type, date, campaign, step, lead, sender,
                subject) and a short excerpt of the body, never HTML. `fields`
                and `compact` are applied here, never sent to lemlist; neither
                given, the list is lemlist's own (`activity_views`).
        """
        view = activity_views.view_of(fields, compact)
        params = {'limit': limit, 'offset': offset, 'version': 'v2'}
        if campaign_id:
            params['campaignId'] = campaign_id
        for key, value in (
            ("type", type), ("isFirst", is_first), ("leadId", lead_id),
            ("minDate", min_date), ("maxDate", max_date),
            ("startDate", start_date), ("endDate", end_date),
        ):
            if value is not None:
                params[key] = value
        return activity_views.shape_activities(
            self._request("GET", "activities", params=params), view)

    def delete_activity_recording_transcript(self, activity_id: str) -> Dict[str, Any]:
        """Delete the recording transcript attached to a call activity."""
        return self._request(
            "DELETE", f"activities/{activity_id}/recording-transcript")

    def sync_activities(self, campaign_id: str = None, since: str = None, max_pages: int = 50,
                        *, fields: List[str] = None, compact: bool = False) -> List[Dict]:
        """Fetch all activities with pagination.

        Args:
            campaign_id: Optional campaign filter
            since: Optional ISO date string to filter activities after this date
            max_pages: Maximum number of pages to fetch (default 50 = 5000 activities)
            fields / compact: The view of `get_activities`, applied once the
                pages are read and filtered by `since`.
        """
        view = activity_views.view_of(fields, compact)
        all_activities = []
        offset = 0
        limit = 100
        pages = 0
        while pages < max_pages:
            batch = self.get_activities(campaign_id, limit, offset)
            if not batch:
                break
            if since:
                batch = [a for a in batch if a.get('createdAt', '') >= since]
            all_activities.extend(batch)
            offset += limit
            pages += 1
            if len(batch) < limit:
                break
            if since and batch and all(a.get('createdAt', '') < since for a in batch):
                break
        return activity_views.shape_activities(all_activities, view)

    #: Channels accepted by the v2 stats endpoints.
    STATS_CHANNELS = ("email", "linkedin", "others")

    def get_campaign_stats_v2(
        self,
        campaign_id: str,
        *,
        start_date: str,
        end_date: str,
        send_user: str = None,
        ab_selected: str = None,
        channels: List[str] = None,
    ) -> Dict[str, Any]:
        """Campaign stats from lemlist's OWN counters (`GET /v2/campaigns/{id}/stats`).

        Distinct from `get_campaign_stats` below, which derives numbers from a
        page of activities and therefore under-counts any campaign bigger than
        that page. Prefer this one.

        Args:
            start_date / end_date: ISO 8601, both REQUIRED by the API.
            send_user: `usr_…|sender@email` — BOTH halves are mandatory when set.
            ab_selected: `A` or `B`, to read one side of a running A/B test.
            channels: Subset of `STATS_CHANNELS`. Sent as a JSON array STRING —
                the endpoint reads it as a query parameter, not as repeated keys.

        Returns lead-level counters (`nbLeads`, `nbLeadsReached`,
        `nbLeadsAnswered`…), message-level counters (`messagesSent`, `opened`,
        `clicked`, `replied`, `messagesBounced`…), plus `perChannel` and a
        per-step `steps` array.
        """
        params: Dict[str, Any] = {"startDate": start_date, "endDate": end_date}
        if send_user is not None:
            if "|" not in send_user:
                raise ValueError(
                    "send_user must be 'usr_xxx|sender@email' — both halves are "
                    f"required by the API, got {send_user!r}")
            params["sendUser"] = send_user
        if ab_selected is not None:
            if ab_selected not in ("A", "B"):
                raise ValueError(
                    f"ab_selected must be 'A' or 'B', got {ab_selected!r}")
            params["ABSelected"] = ab_selected
        if channels is not None:
            self._check_channels(channels)
            params["channels"] = json.dumps(channels)
        return self._request(
            "GET", f"v2/campaigns/{campaign_id}/stats", params=params)

    def get_batch_campaign_stats(
        self,
        campaign_ids: List[str],
        *,
        start_date: str,
        end_date: str,
        send_user: str = None,
        ab_selected: str = None,
        channels: List[str] = None,
    ) -> Dict[str, Any]:
        """Same counters as `get_campaign_stats_v2`, for up to 100 campaigns in
        ONE call (`POST /v2/campaigns/stats/batch`).

        Here `channels` is a real JSON ARRAY in the body — not the stringified
        one the single-campaign query parameter wants. Same vocabulary, two
        encodings; sending the wrong one is silently ignored upstream.
        """
        if not campaign_ids:
            raise ValueError("campaign_ids is empty — nothing to fetch")
        if len(campaign_ids) > 100:
            raise ValueError(
                f"at most 100 campaigns per batch, got {len(campaign_ids)}")
        body: Dict[str, Any] = {
            "campaignIds": campaign_ids,
            "startDate": start_date,
            "endDate": end_date,
        }
        if send_user is not None:
            if "|" not in send_user:
                raise ValueError(
                    "send_user must be 'usr_xxx|sender@email' — both halves are "
                    f"required by the API, got {send_user!r}")
            body["sendUser"] = send_user
        if ab_selected is not None:
            if ab_selected not in ("A", "B"):
                raise ValueError(
                    f"ab_selected must be 'A' or 'B', got {ab_selected!r}")
            body["ABSelected"] = ab_selected
        if channels is not None:
            self._check_channels(channels)
            body["channels"] = channels
        return self._request("POST", "v2/campaigns/stats/batch", json=body)

    @classmethod
    def _check_channels(cls, channels: List[str]) -> None:
        bad = [c for c in channels if c not in cls.STATS_CHANNELS]
        if bad:
            raise ValueError(
                f"unknown channels {bad} — expected any of {cls.STATS_CHANNELS}")

    def get_campaign_reports(self, campaign_ids: List[str]) -> List[Dict[str, Any]]:
        """Cross-campaign report rows (`GET /campaigns/reports`).

        One row per campaign, in the operator's vocabulary (`emailsSent`,
        `emailsOpened`, `emailsReplied`, `senderNames`, `state`…) rather than
        the v2 stats one — the right shape for "compare mes campagnes".
        Campaign ids travel as ONE comma-joined query parameter.
        """
        if not campaign_ids:
            raise ValueError("campaign_ids is empty — nothing to report on")
        return self._request(
            "GET", "campaigns/reports",
            params={"campaignIds": ",".join(campaign_ids)})

    def get_campaign_stats(self, campaign_id: str) -> Dict[str, Any]:
        """Get stats for a campaign from activities.

        ⚠️ Derived from ONE page of activities (1000 max) — it under-counts any
        campaign bigger than that. `get_campaign_stats_v2` reads lemlist's own
        counters and has no such ceiling; prefer it.
        """
        activities = self.get_activities(campaign_id=campaign_id, limit=1000)
        counts = Counter(a.get('type') for a in activities)
        return {
            'total_activities': len(activities),
            'emails_sent': counts.get('emailsSent', 0),
            'emails_opened': counts.get('emailsOpened', 0),
            'emails_replied': counts.get('emailsReplied', 0),
            'emails_bounced': counts.get('emailsBounced', 0),
            'linkedin_visits': counts.get('linkedinVisitDone', 0),
            'linkedin_invites': counts.get('linkedinInviteDone', 0),
            'linkedin_messages': counts.get('linkedinSent', 0),
            'linkedin_accepted': counts.get('linkedinInviteAccepted', 0),
            'by_type': dict(counts),
        }

    # --- Status ---

    def status(self) -> Dict[str, Any]:
        """Check API connection status.

        Deliberately ONE page: this is a connection probe, and it is called on
        paths where a multi-page walk would be paid for nothing. When the page
        comes back full the count is a floor, and says so (`campaigns_capped`)
        rather than passing 100 off as the total.
        """
        try:
            campaigns = self.list_campaigns(limit=self.PAGE_MAX)
            return {
                "connected": True,
                "campaigns_count": len(campaigns),
                "campaigns_capped": len(campaigns) >= self.PAGE_MAX,
            }
        except Exception as e:
            return {"connected": False, "error": str(e)}

    # --- Unsubscribes ---------------------------------------------------------
    #
    # THREE lists, not one, and lemlist documents them separately because they
    # do not carry the same objects:
    #   • v1 "unsubscribes" — emails and DOMAINS, flat;
    #   • v2 "variables"    — any identifying value (email, domain, LinkedIn
    #     URL, phone), with a bulk import;
    #   • v2 "contacts"     — the do-not-contact flag set on a CONTACT of the
    #     lemlist CRM, not on a value.
    # Unsubscribing an address in v1 does not set the contact's flag, and
    # vice versa. The three families are therefore exposed as they are rather
    # than merged into a single one, which would lie about what was done.

    def list_unsubscribes(self, *, offset: int = None, limit: int = None) -> Any:
        """List unsubscribed emails and domains (v1)."""
        params = {k: v for k, v in {"offset": offset, "limit": limit}.items()
                  if v is not None}
        return self._request("GET", "unsubscribes", params=params)

    def get_unsubscribe(self, email: str) -> Dict[str, Any]:
        """Read one unsubscribed email or domain (v1)."""
        return self._request("GET", f"unsubscribes/{email}")

    def add_unsubscribe(self, email: str) -> Dict[str, Any]:
        """Add an email — or a whole DOMAIN — to the unsubscribe list (v1)."""
        return self._request("POST", f"unsubscribes/{email}")

    def delete_unsubscribe(self, email: str) -> Dict[str, Any]:
        """Remove an email or domain from the unsubscribe list (v1)."""
        return self._request("DELETE", f"unsubscribes/{email}")

    def export_unsubscribes(self) -> str:
        """Export the v1 unsubscribe list as CSV.

        ⚠️ The path is `/unsubs/export`, NOT `/unsubscribes/export` — lemlist's
        own abbreviation, and the kind of detail a "tidy-up" would break.
        """
        return self._request("GET", "unsubs/export", as_text=True)

    def list_unsubscribed_variables(
        self, *, offset: int = None, limit: int = None,
    ) -> Any:
        """List unsubscribed variables (v2): emails, domains, LinkedIn URLs,
        phone numbers — anything that identifies someone."""
        params = {k: v for k, v in {"offset": offset, "limit": limit}.items()
                  if v is not None}
        return self._request("GET", "v2/unsubscribes/variables", params=params)

    def get_unsubscribed_variable(self, value: str) -> Dict[str, Any]:
        """Read one unsubscribed variable by its value (v2)."""
        return self._request("GET", f"v2/unsubscribes/variables/{value}")

    def unsubscribe_variable(self, value: str) -> Dict[str, Any]:
        """Unsubscribe one variable (v2). Idempotent — an already-unsubscribed
        value returns its existing record rather than erroring."""
        return self._request("POST", f"v2/unsubscribes/variables/{value}")

    def resubscribe_variable(self, value: str) -> Dict[str, Any]:
        """Re-subscribe a variable, removing it from the v2 list."""
        return self._request("DELETE", f"v2/unsubscribes/variables/{value}")

    def bulk_unsubscribe_variables(self, values: List[str]) -> Dict[str, Any]:
        """Unsubscribe up to 10 000 variables in one call (v2)."""
        if not values:
            raise ValueError("values is empty — nothing to unsubscribe")
        if len(values) > 10000:
            raise ValueError(
                f"at most 10 000 values per call, got {len(values)}")
        return self._request(
            "POST", "v2/unsubscribes/variables", json={"values": values})

    def export_unsubscribed_variables(self) -> str:
        """Export every unsubscribed variable as CSV (v2)."""
        return self._request(
            "GET", "v2/unsubscribes/exports/variables", as_text=True)

    def get_contact_subscription(self, contact_id: str) -> Dict[str, Any]:
        """Is this CRM contact flagged do-not-contact? (v2)"""
        return self._request("GET", f"v2/unsubscribes/contacts/{contact_id}")

    def unsubscribe_contact(self, contact_id: str) -> Dict[str, Any]:
        """Flag a CRM contact do-not-contact (v2). Distinct from unsubscribing
        its email: the flag rides the CONTACT, not one of its values."""
        return self._request("POST", f"v2/unsubscribes/contacts/{contact_id}")

    def resubscribe_contact(self, contact_id: str) -> Dict[str, Any]:
        """Clear the do-not-contact flag on a CRM contact (v2)."""
        return self._request("DELETE", f"v2/unsubscribes/contacts/{contact_id}")

    def export_unsubscribed_contacts(self) -> str:
        """Export every contact with its subscription status as CSV (v2)."""
        return self._request(
            "GET", "v2/unsubscribes/exports/contacts", as_text=True)

    # --- Contacts (the lemlist CRM) -------------------------------------------
    #
    # A CONTACT is not a LEAD: the lead is a person's copy INSIDE a campaign
    # (its sending state, its variables), the contact is the person themselves
    # in the lemlist CRM, independent of campaigns. lemlist says so in its own
    # doc, and it is the most costly confusion here.

    def list_contacts(
        self,
        *,
        ids_or_emails: List[str] = None,
        search: str = None,
        email: str = None,
        list_id: str = None,
        not_in_any_campaign: bool = None,
        company_id: str = None,
        company_domain: str = None,
        company_linkedin_url: str = None,
        company_salesnav_url: str = None,
        field_rejection_reason: str = None,
        limit: int = None,
        offset: int = None,
    ) -> Any:
        """List or look up CRM contacts.

        `ids_or_emails` fetches specific contacts (id or email, max 100 per
        call); omit it to search. Comma-joined, as the API expects one value.
        """
        if ids_or_emails is not None and len(ids_or_emails) > 100:
            raise ValueError(
                f"at most 100 ids or emails per call, got {len(ids_or_emails)}")
        params = {k: v for k, v in {
            "idsOrEmails": ",".join(ids_or_emails) if ids_or_emails else None,
            "search": search, "email": email, "listId": list_id,
            "notInAnyCampaign": not_in_any_campaign, "companyId": company_id,
            "companyDomain": company_domain,
            "companyLinkedinUrl": company_linkedin_url,
            "companySalesnavUrl": company_salesnav_url,
            "fieldRejectionReason": field_rejection_reason,
            "limit": limit, "offset": offset,
        }.items() if v is not None}
        return self._request("GET", "contacts", params=params)

    def get_contact(self, id_or_email: str) -> Dict[str, Any]:
        """Read one CRM contact by id or email."""
        return self._request("GET", f"contacts/{id_or_email}")

    def upsert_contact(self, contact: dict) -> Dict[str, Any]:
        """Create or update a CRM contact (ONE route for both).

        Identity keys: `contactId`, `email`, `linkedinUrl`. Other fields:
        `additionalEmails`, `firstName`, `lastName`, `phone`, `jobTitle`,
        `jobDescription`, `picture`, `timezone`, `industry`, `languages`,
        `location`, `skills`, `summary`, `tagline`, `contactOwner`, `source`,
        and the company link (`companyId` / `companyDomain` /
        `companyLinkedinUrl`).
        """
        return self._request("POST", "contacts", json=contact)

    def delete_contact(self, id_or_email: str) -> Dict[str, Any]:
        """Delete a CRM contact."""
        return self._request("DELETE", f"contacts/{id_or_email}")

    def list_contact_lists(self, *, search: str = None) -> Any:
        """List the contact lists of the team."""
        params = {"search": search} if search is not None else None
        return self._request("GET", "contacts/lists", params=params)

    def create_contact_list(self, name: str) -> Dict[str, Any]:
        """Create a contact list."""
        return self._request("POST", "contacts/lists", json={"name": name})

    def manage_contact_list(
        self, list_id: str, contact_ids: List[str], *, action: str = None,
    ) -> Dict[str, Any]:
        """Add contacts to a list — or remove them.

        ONE route, and the default is ADD: `action="remove"` is what takes them
        out. Same shape as `delete_lead`, and the same trap.
        """
        if not contact_ids:
            raise ValueError("contact_ids is empty — nothing to move")
        if action is not None and action != "remove":
            raise ValueError(
                f'action is "remove" or nothing (= add), got {action!r}')
        params = {"action": action} if action is not None else None
        return self._request(
            "POST", f"contacts/lists/{list_id}/entities",
            params=params, json={"contactIds": contact_ids})

    def export_contact_list(self, list_id: str, *, entity: str = None) -> str:
        """Export a contact list as CSV.

        `entity` is `contact` (default) or `company` — the same list can be read
        through either side.
        """
        if entity is not None and entity not in ("contact", "company"):
            raise ValueError(
                f"entity is 'contact' or 'company', got {entity!r}")
        params = {"listId": list_id}
        if entity is not None:
            params["entity"] = entity
        return self._request(
            "GET", "contacts/export", params=params, as_text=True)

    # --- Companies -------------------------------------------------------------

    def list_companies(
        self,
        *,
        ids_or_domains: List[str] = None,
        search: str = None,
        fields: List[str] = None,
        offset: int = None,
        limit: int = None,
        sort_by: str = None,
        sort_order: str = None,
        crm_sync_status: str = None,
        field_rejection_reason: str = None,
    ) -> Any:
        """List or look up companies of the lemlist CRM."""
        params = {k: v for k, v in {
            "idsOrDomains": ",".join(ids_or_domains) if ids_or_domains else None,
            "search": search,
            "fields": ",".join(fields) if fields else None,
            "offset": offset, "limit": limit,
            "sortBy": sort_by, "sortOrder": sort_order,
            "crmSyncStatus": crm_sync_status,
            "fieldRejectionReason": field_rejection_reason,
        }.items() if v is not None}
        return self._request("GET", "companies", params=params)

    def upsert_company(self, company: dict) -> Dict[str, Any]:
        """Create or update a company (ONE route for both).

        Identity keys: `companyId`, `domain`, `linkedinUrl`. Other fields:
        `name`, `linkedinUrlSalesNav`, `companyOwner`, `industry`, `location`,
        `size`, `specialties`, `tagline`, `type`, `description`, `foundedOn`,
        `headquarters`, `picture`, `source`.
        """
        return self._request("POST", "companies", json=company)

    def delete_company(self, company_id: str, *, force: bool = None) -> Dict[str, Any]:
        """Delete a company. `force=True` deletes it even when contacts hang
        off it (lemlist refuses otherwise)."""
        params = {"force": "true"} if force else None
        return self._request("DELETE", f"companies/{company_id}", params=params)

    def get_company_notes(
        self, company_id: str, *,
        limit: int = None, page: int = None,
        sort_by: str = None, sort_order: str = None,
    ) -> Any:
        """List the notes attached to a company."""
        params = {k: v for k, v in {
            "limit": limit, "page": page,
            "sortBy": sort_by, "sortOrder": sort_order,
        }.items() if v is not None}
        return self._request("GET", f"companies/{company_id}/notes", params=params)

    def create_company_note(self, company_id: str, note: str) -> Dict[str, Any]:
        """Attach a note to a company."""
        return self._request(
            "POST", f"companies/{company_id}/notes", json={"note": note})

    # --- Inbox ------------------------------------------------------------------
    #
    # The unified inbox: conversations per CONTACT (not per lead), all
    # channels combined, with drafts and labels. Three send routes live
    # there (`/inbox/email`, `/inbox/linkedin`, `/inbox/whatsapp`): they are
    # the only sends of this client that go through NEITHER a campaign NOR a
    # review — a message goes out directly, to a real person.

    #: Canaux d'un brouillon.
    DRAFT_CHANNELS = ("email", "linkedin", "whatsapp", "sms")

    def list_inboxes(self, user_id: str, *, page: int = None, limit: int = None) -> Any:
        """List the inbox conversations of ONE user (`userId` is required)."""
        params = {k: v for k, v in
                  {"userId": user_id, "page": page, "limit": limit}.items()
                  if v is not None}
        return self._request("GET", "inbox", params=params)

    def get_contact_messages(
        self, contact_id: str, *,
        user_id: str = None, limit: int = None, skip: int = None,
        mark_as_read: bool = None,
    ) -> Any:
        """Read the message history of a conversation, by CONTACT.

        ⚠️ `mark_as_read=True` MUTATES on a read call — lemlist's design, named
        here so it is not discovered by surprise.
        """
        params = {k: v for k, v in {
            "userId": user_id, "limit": limit, "skip": skip,
            "markAsRead": mark_as_read,
        }.items() if v is not None}
        return self._request("GET", f"inbox/{contact_id}", params=params)

    def list_inbox_labels(self) -> Any:
        """List the inbox labels of the team."""
        return self._request("GET", "inbox/labels")

    def get_inbox_label(self, label_id: str) -> Dict[str, Any]:
        """Read one inbox label."""
        return self._request("GET", f"inbox/labels/{label_id}")

    def create_inbox_label(self, label_name: str) -> Dict[str, Any]:
        """Create an inbox label."""
        return self._request(
            "POST", "inbox/labels", json={"labelName": label_name})

    def attach_inbox_labels(
        self, contact_id: str, label_ids: List[str], *, append: bool = True,
    ) -> Dict[str, Any]:
        """Put labels on a conversation.

        `append=False` REPLACES the conversation's labels with `label_ids` —
        both are sent explicitly because the API requires `appendLabels` and
        the destructive branch should never be the one you fall into.
        """
        return self._request(
            "POST", f"inbox/conversations/labels/{contact_id}",
            json={"labelIds": label_ids, "appendLabels": append})

    def remove_inbox_labels(
        self, contact_id: str, label_ids: List[str],
    ) -> Dict[str, Any]:
        """Take labels off a conversation."""
        return self._request(
            "DELETE", f"inbox/conversations/labels/{contact_id}",
            json={"labelIds": label_ids})

    def list_drafts(self, contact_id: str, draft_owner: str) -> Any:
        """List the drafts of a conversation. `draft_owner` (a user id) is
        REQUIRED — a draft belongs to a person, not to the team."""
        return self._request(
            "GET", f"inbox/{contact_id}/drafts", params={"draftOwner": draft_owner})

    def get_draft(self, contact_id: str, draft_id: str, draft_owner: str) -> Dict[str, Any]:
        """Read one draft."""
        return self._request(
            "GET", f"inbox/{contact_id}/drafts/{draft_id}",
            params={"draftOwner": draft_owner})

    def create_draft(
        self,
        contact_id: str,
        draft_owner: str,
        *,
        channel: str,
        content: str,
        subject: str = None,
        cc: List[str] = None,
        attachments: List[dict] = None,
        reply_to_activity_id: str = None,
        source_metadata: dict = None,
    ) -> Dict[str, Any]:
        """Write a draft in a conversation. A draft SENDS NOTHING."""
        if channel not in self.DRAFT_CHANNELS:
            raise ValueError(
                f"channel must be one of {self.DRAFT_CHANNELS}, got {channel!r}")
        body: Dict[str, Any] = {"channel": channel, "content": content}
        for key, value in (
            ("subject", subject), ("cc", cc), ("attachments", attachments),
            ("replyToActivityId", reply_to_activity_id),
            ("sourceMetadata", source_metadata),
        ):
            if value is not None:
                body[key] = value
        return self._request(
            "POST", f"inbox/{contact_id}/drafts",
            params={"draftOwner": draft_owner}, json=body)

    def update_draft(
        self, contact_id: str, draft_id: str, draft_owner: str, data: dict,
    ) -> Dict[str, Any]:
        """Edit a draft (`subject`, `cc`, `content`, `attachments`,
        `replyToActivityId`)."""
        return self._request(
            "PATCH", f"inbox/{contact_id}/drafts/{draft_id}",
            params={"draftOwner": draft_owner}, json=data)

    def delete_draft(self, contact_id: str, draft_id: str, draft_owner: str) -> Dict[str, Any]:
        """Delete a draft."""
        return self._request(
            "DELETE", f"inbox/{contact_id}/drafts/{draft_id}",
            params={"draftOwner": draft_owner})

    def send_inbox_email(
        self,
        *,
        send_user_id: str,
        send_user_email: str,
        send_user_mailbox_id: str,
        message: str,
        contact_id: str = None,
        lead_id: str = None,
        subject: str = None,
        cc: List[str] = None,
        reply_to_activity_id: str = None,
    ) -> Dict[str, Any]:
        """Send an email from the inbox — DIRECTLY, to a real person.

        No campaign, no sequence, no review in front of it: the most immediate
        send in this client. The three `send_user_*` are all required — lemlist
        will not guess the mailbox.
        """
        body: Dict[str, Any] = {
            "sendUserId": send_user_id, "sendUserEmail": send_user_email,
            "sendUserMailboxId": send_user_mailbox_id, "message": message,
        }
        for key, value in (
            ("contactId", contact_id), ("leadId", lead_id),
            ("subject", subject), ("cc", cc),
            ("replyToActivityId", reply_to_activity_id),
        ):
            if value is not None:
                body[key] = value
        return self._request("POST", "inbox/email", json=body)

    def send_linkedin_message(
        self, *, send_user_id: str, lead_id: str, contact_id: str, message: str,
    ) -> Dict[str, Any]:
        """Send a LinkedIn message from the inbox — directly, to a real person."""
        return self._request("POST", "inbox/linkedin", json={
            "sendUserId": send_user_id, "leadId": lead_id,
            "contactId": contact_id, "message": message,
        })

    def send_whatsapp_message(
        self, *, send_user_id: str, send_user_whatsapp_account_id: str,
        lead_id: str, contact_id: str, message: str,
    ) -> Dict[str, Any]:
        """Send a WhatsApp message from the inbox — directly, to a real person."""
        return self._request("POST", "inbox/whatsapp", json={
            "sendUserId": send_user_id,
            "sendUserWhatsappAccountId": send_user_whatsapp_account_id,
            "leadId": lead_id, "contactId": contact_id, "message": message,
        })

    # --- Tasks -----------------------------------------------------------------

    #: Task types, and priorities (0 = high … 2 = low, "" = none).
    TASK_TYPES = ("email", "manual", "phone", "linkedin")

    def list_tasks(self, *, page: int = None, filters: List[dict] = None) -> Any:
        """List tasks. `filters` is a list of `{filterId, …}` objects, sent as a
        JSON array STRING in the query — the same encoding trap as the stats
        `channels`.

        ⚠️ `filters` is documented OPTIONAL and is not: without it the API
        answers `400 {"error": "Malformed filters"}`. Verified live on
        2026-08-31 — an EMPTY array is enough, so we always send it. This is
        exactly the kind of gap that no load test sees.
        """
        params: Dict[str, Any] = {"filters": json.dumps(filters or [])}
        if page is not None:
            params["page"] = page
        return self._request("GET", "tasks", params=params)

    def create_task(
        self,
        *,
        task_type: str,
        assigned_to: str,
        due_date: str,
        record_id: str = None,
        title: str = None,
        message: str = None,
        priority: str = None,
        images: List[str] = None,
        videos: List[str] = None,
    ) -> Dict[str, Any]:
        """Create a manual task assigned to a user. Sends nothing by itself.

        ⚠️ `record_id` is documented OPTIONAL and is not: without it the API
        answers `400 {"error": "recordId is required"}` (verified live on
        2026-08-31). It is the id of the contact or lead the task attaches
        to — a lemlist task does not exist outside a record.
        Refused here so that the message says WHAT to provide.
        """
        if not record_id:
            raise ValueError(
                "record_id is required by the API despite being documented "
                "optional — pass the contact (ctc_…) or lead (lea_…) the task "
                "hangs off")
        if task_type not in self.TASK_TYPES:
            raise ValueError(
                f"task_type must be one of {self.TASK_TYPES}, got {task_type!r}")
        body: Dict[str, Any] = {
            "type": task_type, "assignedTo": assigned_to, "dueDate": due_date,
        }
        for key, value in (
            ("recordId", record_id), ("title", title), ("message", message),
            ("priority", priority), ("images", images), ("videos", videos),
        ):
            if value is not None:
                body[key] = value
        return self._request("POST", "tasks", json=body)

    def update_task(self, task_id: str, data: dict) -> Dict[str, Any]:
        """Update a task. ⚠️ The id travels in the BODY (`id`), not in the path —
        `PATCH /tasks` has no path parameter."""
        return self._request("PATCH", "tasks", json={**data, "id": task_id})

    def ignore_tasks(self, ids: List[str]) -> Dict[str, Any]:
        """Dismiss tasks without completing them."""
        if not ids:
            raise ValueError("ids is empty — nothing to ignore")
        return self._request("POST", "tasks/ignore", json={"ids": ids})

    # --- Watch lists (signals) --------------------------------------------------
    #
    # A watch list monitors a SIGNAL (a company hiring, a fundraise, a job
    # change…) and can, on its own, create an opportunity or push the people
    # found INTO a campaign (`signalProcessingType`) — it is the only
    # non-campaign surface of this client that can feed a send.

    #: Signal types monitored by a watch list.
    WATCH_LIST_TYPES = (
        "companyIsHiring", "companyRaisedFunds", "recruitmentCampaign",
        "jobChange", "newHire", "companyEmployeeVisitedMyWebsite",
        "customSignals", "competitorConnections", "competitorReactions",
        "companyFollowers", "technologyChange", "linkedinPeopleProfile",
        "linkedinCompanyProfile", "mergersAcquisitions", "promotion",
        "linkedinKeywords", "externalSignalContact", "externalSignalCompany",
        "buyingIntent",
    )
    #: What the watch list does with a signal it catches.
    SIGNAL_PROCESSING = ("manual", "create_opportunity", "push_to_campaign")

    def list_watch_lists(
        self, *, page: int = None, limit: int = None,
        type: str = None, status: str = None,
    ) -> Any:
        """List the team's watch lists."""
        params = {k: v for k, v in
                  {"page": page, "limit": limit, "type": type, "status": status}.items()
                  if v is not None}
        return self._request("GET", "watchlist", params=params)

    def create_watch_list(
        self,
        name: str,
        *,
        type: str,
        filters: List[Any] = None,
        emoji: str = None,
        segment_type: str = "all",
        signal_processing_type: str = "manual",
        signal_opportunity_template: dict = None,
        persona_id: str = None,
        campaign_id: str = None,
        activate: bool = False,
    ) -> Dict[str, Any]:
        """Create a watch list.

        ⚠️ `signal_processing_type="push_to_campaign"` + `activate=True` makes
        this list feed a campaign on its own — the one configuration here that
        can end in messages being sent without a further call. Hence
        `activate=False` by default: a list is born as a draft.

        ⚠️ FOUR fields documented as optional are MANDATORY — `filters`,
        `segmentType`, `signalProcessingType`, `activate` (verified live on
        2026-08-31); the last three therefore have a default here. The worst is
        `activate`: omitting it triggers `400 "activate requires both
        segmentType and signalProcessingType"`, a message that blames the TWO
        OTHER fields, which are in fact present.

        `filters` remains to be provided and depends on the `type`: each type has
        its required filters (`companyIsHiring` requires `title`, `location` and
        `maxIdentificationsPerDay`). The VALUES must be canonical, as returned
        by `get_watch_list_filter_values` — a free string is rejected
        (`INVALID_FILTER_VALUE`) — and numeric values travel as STRINGS
        (`{"filterId": "maxIdentificationsPerDay", "in": ["5"]}`). Read
        `get_watch_list_filters(type=…)` before building the payload.

        `persona_id` (`pdp_…`) and `campaign_id` (`cam_…`) only go with
        `signal_processing_type="push_to_campaign"`. The campaign is the one the
        list feeds; the persona says WHO to push when the signal carries a
        company and no contact, as `companyEmployeeVisitedMyWebsite` mostly
        does (lemlist names the visitor only for some US traffic).
        """
        if (persona_id is not None or campaign_id is not None) and \
                signal_processing_type != "push_to_campaign":
            raise ValueError(
                "persona_id and campaign_id need "
                "signal_processing_type='push_to_campaign'")
        if type not in self.WATCH_LIST_TYPES:
            raise ValueError(
                f"type must be one of {self.WATCH_LIST_TYPES}, got {type!r}")
        if (signal_processing_type is not None
                and signal_processing_type not in self.SIGNAL_PROCESSING):
            raise ValueError(
                f"signal_processing_type must be one of {self.SIGNAL_PROCESSING}, "
                f"got {signal_processing_type!r}")
        body: Dict[str, Any] = {
            "name": name, "type": type,
            "filters": filters or [],
            "segmentType": segment_type,
            "signalProcessingType": signal_processing_type,
            # Sent even as False: it is the ABSENCE of the key that the API refuses.
            "activate": bool(activate),
        }
        for key, value in (
            ("emoji", emoji),
            ("signalOpportunityTemplate", signal_opportunity_template),
            ("personaId", persona_id),
            ("campaignId", campaign_id),
        ):
            if value is not None:
                body[key] = value
        return self._request("POST", "watchlist", json=body)

    def update_watch_list(self, watch_list_id: str, data: dict) -> Dict[str, Any]:
        """Update a watch list. ⚠️ The id travels in the BODY (`watchListId`),
        and `PATCH /watchlist` carries no path parameter."""
        return self._request(
            "PATCH", "watchlist", json={**data, "watchListId": watch_list_id})

    def delete_watch_list(self, watch_list_id: str) -> Dict[str, Any]:
        """Delete a watch list. ⚠️ The id is a QUERY parameter here — a third
        placement for the same id, on the same resource."""
        return self._request(
            "DELETE", "watchlist", params={"watchListId": watch_list_id})

    def get_watch_list_filters(self, *, type: str = None) -> Any:
        """The filters available for a watch list type."""
        params = {"type": type} if type is not None else None
        return self._request("GET", "watchlist/filters", params=params)

    def get_watch_list_filter_values(self, filter_id: str, *, query: str = None) -> Any:
        """The values a given filter accepts (typeahead)."""
        params = {"filterId": filter_id}
        if query is not None:
            params["query"] = query
        return self._request("GET", "watchlist/filter-values", params=params)

    def get_watch_list_library(self) -> Any:
        """The library of ready-made watch lists."""
        return self._request("GET", "watchlist/library")

    def get_watch_list_history(
        self, watch_list_id: str, *, page: int = None, limit: int = None,
    ) -> Any:
        """The history of a watch list."""
        params = {k: v for k, v in
                  {"watchListId": watch_list_id, "page": page, "limit": limit}.items()
                  if v is not None}
        return self._request("GET", "watchlist/history", params=params)

    def get_signals(
        self,
        *,
        page: int = None,
        limit: int = None,
        sort_by: str = None,
        sort_order: str = None,
        type: str = None,
        status: str = None,
        received_at_from: str = None,
        received_at_to: str = None,
        watch_list_id: str = None,
    ) -> Any:
        """The signals caught by the watch lists."""
        params = {k: v for k, v in {
            "page": page, "limit": limit, "sortBy": sort_by, "sortOrder": sort_order,
            "type": type, "status": status,
            "receivedAtFrom": received_at_from, "receivedAtTo": received_at_to,
            "watchListId": watch_list_id,
        }.items() if v is not None}
        return self._request("GET", "watchlist/signals", params=params)

    def push_external_signals(
        self, watch_list_id: str, *, contact: dict, company: dict,
        custom_fields: dict = None,
    ) -> Dict[str, Any]:
        """Push a signal detected OUTSIDE lemlist into a watch list.

        `contact` needs `linkedinUrl`; `company` needs `domain` and `name`.
        """
        body: Dict[str, Any] = {"contact": contact, "company": company}
        if custom_fields is not None:
            body["customFields"] = custom_fields
        return self._request(
            "POST", f"watchlist/{watch_list_id}/external-signals", json=body)

    # --- Campaign exports (asynchronous) ----------------------------------------
    #
    # Three routes for ONE export: you open it (`start`), you poll its state
    # (`status`), and you can ask to be notified by email at the end. Not to be
    # confused with `export_leads`, the historical and SYNCHRONOUS CSV export, nor
    # with `export_campaign_leads`, which returns the leads directly.

    def start_campaign_export(self, campaign_id: str) -> Dict[str, Any]:
        """Open an asynchronous export of a campaign's stats. Returns its id."""
        return self._request("GET", f"campaigns/{campaign_id}/export/start")

    def get_campaign_export_status(
        self, campaign_id: str, export_id: str,
    ) -> Dict[str, Any]:
        """Where an asynchronous campaign export stands."""
        return self._request(
            "GET", f"campaigns/{campaign_id}/export/{export_id}/status")

    def set_campaign_export_email(
        self, campaign_id: str, export_id: str, email: str,
    ) -> Dict[str, Any]:
        """Be emailed when an export completes. ⚠️ A PUT, and the address is a
        PATH segment — not a body field."""
        return self._request(
            "PUT", f"campaigns/{campaign_id}/export/{export_id}/email/{email}")

    _UNSET = object()

    def export_campaign_leads(
        self, campaign_id: str, *, state: Any = _UNSET, format: str = None,
    ) -> Any:
        """Export a campaign's leads (`GET /campaigns/{id}/export/leads`).

        `format` is `csv` (the API default) or `json`. CSV comes back as text,
        JSON as parsed data — the return type follows the format asked for.

        ⚠️ `state` defaults to `"all"` HERE, and that is NOT lemlist's
        default. Verified live on 2026-08-31: without `state`, a one-lead
        campaign returns an EMPTY list (and a CSV reduced to its header). The
        API's default therefore filters everything out, which reads as "no leads"
        rather than "wrong filter" — silent, and wrong. `state=None` restores the
        raw behaviour.
        """
        if format is not None and format not in ("json", "csv"):
            raise ValueError(f"format is 'json' or 'csv', got {format!r}")
        state = "all" if state is self._UNSET else state
        params = {k: v for k, v in {"state": state, "format": format}.items()
                  if v is not None}
        return self._request(
            "GET", f"campaigns/{campaign_id}/export/leads",
            params=params, as_text=(format != "json"))

    # --- People & companies database ---------------------------------------------
    #
    # lemlist's SHARED base (cold outreach), distinct from the
    # `contacts`/`companies` CRM which, for its part, contains ONLY your data.

    def search_people_database(
        self, *, filters: List[dict] = None, page: int = None,
        size: int = None, excludes: List[str] = None, search: str = None,
        fields: List[str] = None, compact: bool = False,
    ) -> Any:
        """Search the shared people database.

        `fields` (names, dotted paths such as `experiences.title`) keeps only
        those keys of each result; `compact` keeps a preset (identity, title,
        LinkedIn, current positions only). Both are applied here, never sent
        to lemlist; neither given, the page is lemlist's own
        (`database_views`)."""
        view = database_views.view_of(fields, compact)
        body = {k: v for k, v in {
            "filters": filters, "page": page, "size": size,
            "excludes": excludes, "search": search,
        }.items() if v is not None}
        return database_views.shape_page(
            self._request("POST", "database/people", json=body), view, "people")

    def search_companies_database(
        self, *, filters: List[dict] = None, page: int = None, size: int = None,
        fields: List[str] = None, compact: bool = False,
    ) -> Any:
        """Search the shared companies database.

        `fields` (names, dotted paths) keeps only those keys of each result;
        `compact` keeps a preset (name, domain, headcount, country, industry)
        and its job posts reduced to the latest few, title and dates. Both are
        applied here, never sent to lemlist; neither given, the page is
        lemlist's own (`database_views`)."""
        view = database_views.view_of(fields, compact)
        body = {k: v for k, v in
                {"filters": filters, "page": page, "size": size}.items()
                if v is not None}
        return database_views.shape_page(
            self._request("POST", "database/companies", json=body), view, "companies")

    def get_database_filters(self) -> Any:
        """The filters the shared database accepts — read this before building
        a `filters` payload, the vocabulary is server-side."""
        return self._request("GET", "database/filters")

    def list_personas(self, *, mode: str = None) -> Any:
        """List saved personas (a persona = a named set of database filters)."""
        params = {"mode": mode} if mode is not None else None
        return self._request("GET", "database/personas", params=params)

    def create_persona(self, name: str, *, filters: List[Any], mode: str) -> Dict[str, Any]:
        """Save a persona. `mode` is `leads` or `companies`."""
        if mode not in ("leads", "companies"):
            raise ValueError(f"mode is 'leads' or 'companies', got {mode!r}")
        return self._request("POST", "database/personas", json={
            "name": name, "filters": filters, "mode": mode})

    def delete_persona(self, persona_id: str) -> Dict[str, Any]:
        """Delete a persona."""
        return self._request("DELETE", f"database/personas/{persona_id}")

    # --- Team, users, CRM, fields --------------------------------------------------

    def get_team(self, *, version: str = None) -> Dict[str, Any]:
        """The team behind the key: plan, members, billing."""
        params = {"version": version} if version is not None else None
        return self._request("GET", "team", params=params)

    def get_team_credits(self) -> Dict[str, Any]:
        """Remaining credits of the team (enrichment spends these)."""
        return self._request("GET", "team/credits")

    def get_team_senders(self, *, state: str = None) -> Any:
        """Team members and the campaigns they send from. `state` filters on
        campaign status (`running`, `paused`, `draft`, `ended`, `archived`,
        `errors`)."""
        params = {"state": state} if state is not None else None
        return self._request("GET", "team/senders", params=params)

    def get_user(self, user_id: str) -> Dict[str, Any]:
        """Read one user."""
        return self._request("GET", f"users/{user_id}")

    def get_user_channels(self) -> Any:
        """The channels the key's user can send on (mailboxes, LinkedIn,
        WhatsApp) — the ids `send_inbox_email` & co require."""
        return self._request("GET", "user/channels")

    def get_team_crm_users(self) -> Any:
        """The team's users connected to a CRM."""
        return self._request("GET", "team/crmUsers")

    def get_crm_filters(self, *, crm: str, user_id: str, type: str = None) -> Any:
        """The CRM-side filters usable as an import selection.

        `crm` (hubspot, salesforce, pipedrive…) and `user_id` are both required;
        `type` narrows to `lead`, `contact` or `report`.
        """
        params = {"crm": crm, "userId": user_id}
        if type is not None:
            params["type"] = type
        return self._request("GET", "crm/filters", params=params)

    def list_fields(self, *, entity: str = None, source: str = None) -> Any:
        """The field schema of contacts/companies — `entity` is `contact` or
        `company`, `source` is `default`, `custom` or `crm_synced`.

        This is what tells you which custom fields exist before writing one.
        """
        params = {k: v for k, v in {"entity": entity, "source": source}.items()
                  if v is not None}
        return self._request("GET", "fields", params=params)

    # --- Email accounts & lemwarm ---------------------------------------------------

    def connect_email_account(
        self,
        *,
        sender_name: str,
        sender_email: str,
        smtp_host: str,
        smtp_port: int,
        smtp_login: str,
        smtp_password: str,
        imap_host: str,
        imap_port: int,
        imap_login: str,
        imap_password: str,
        smtp_secure: bool = None,
        imap_secure: bool = None,
        user_id: str = None,
    ) -> Dict[str, Any]:
        """Connect a mailbox over SMTP/IMAP.

        ⚠️ Takes MAILBOX CREDENTIALS in the body. Every field below is required
        by the API; the two `*_secure` and `user_id` are the only optional ones.
        """
        body: Dict[str, Any] = {
            "sender_name": sender_name, "sender_email": sender_email,
            "smtp_host": smtp_host, "smtp_port": smtp_port,
            "smtp_login": smtp_login, "smtp_password": smtp_password,
            "imap_host": imap_host, "imap_port": imap_port,
            "imap_login": imap_login, "imap_password": imap_password,
        }
        for key, value in (("smtp_secure", smtp_secure),
                           ("imap_secure", imap_secure), ("userId", user_id)):
            if value is not None:
                body[key] = value
        return self._request("POST", "user/email-accounts", json=body)

    def disconnect_email_account(self, email_account_id: str) -> Dict[str, Any]:
        """Disconnect a mailbox — campaigns sending from it stop."""
        return self._request("DELETE", f"user/email-accounts/{email_account_id}")

    def test_email_account(self, email_account_id: str) -> Dict[str, Any]:
        """Test a connected mailbox (SMTP/IMAP round-trip)."""
        return self._request(
            "POST", f"user/email-accounts/{email_account_id}/test")

    def get_lemwarm_settings(self, user_mailbox_id: str) -> Dict[str, Any]:
        """Read the lemwarm (deliverability warm-up) settings of a mailbox."""
        return self._request("GET", f"lemwarm/{user_mailbox_id}/settings")

    def update_lemwarm_settings(self, user_mailbox_id: str, data: dict) -> Dict[str, Any]:
        """Update lemwarm settings: `warmEmailMax`, `warmEmailRampup`,
        `internalCommunicationPercent`, `answerPercentage`, `warmUsePlaintext`,
        `warmDailyVarianceEnabled`."""
        return self._request(
            "PATCH", f"lemwarm/{user_mailbox_id}/settings", json=data)

    def start_lemwarm(self, user_mailbox_id: str) -> Dict[str, Any]:
        """Start warming a mailbox.

        This DOES send mail — but inside the lemwarm network (other lemlist
        mailboxes), never to a prospect. That distinction is why it does not sit
        with `start_campaign` and `launch_lead`.
        """
        return self._request("POST", f"lemwarm/{user_mailbox_id}/start")

    def pause_lemwarm(self, user_mailbox_id: str) -> Dict[str, Any]:
        """Pause warming a mailbox."""
        return self._request("POST", f"lemwarm/{user_mailbox_id}/pause")

    # --- Deliverability alerts --------------------------------------------------

    #: Vocabulary of the deliverability alerts.
    ALERT_WIDGETS = ("warmup", "outreach")
    ALERT_METRICS = ("inboxRate", "spamRate", "score", "deliveryRate", "bounceRate")
    ALERT_SEVERITIES = ("warning", "critical")
    ALERT_SCOPES = ("global", "mailbox", "domain")
    ALERT_OPERATORS = ("equal", "below", "above")
    ALERT_PERIOD_MODES = ("rolling", "consecutive")

    def list_deliverability_alerts(self) -> Any:
        """List the deliverability alerts of the team."""
        return self._request("GET", "deliverability/alerts")

    def get_deliverability_alert(self, alert_id: str) -> Dict[str, Any]:
        """Read one deliverability alert."""
        return self._request("GET", f"deliverability/alerts/{alert_id}")

    def create_deliverability_alert(
        self,
        *,
        widget: str,
        metric: str,
        severity: str,
        scope: str,
        threshold: float,
        comparison_operator: str,
        period_days: int,
        period_mode: str,
        scope_entities: List[str] = None,
        channel_config: dict = None,
        recheck_delay_hours: int = None,
    ) -> Dict[str, Any]:
        """Create a deliverability alert.

        Eight required fields, each from a closed vocabulary — checked here
        because lemlist answers an invalid one with a bare 400. Without
        `channel_config` the alert is in-app only; enabling its `email` channel
        requires at least one address.
        """
        for label, value, allowed in (
            ("widget", widget, self.ALERT_WIDGETS),
            ("metric", metric, self.ALERT_METRICS),
            ("severity", severity, self.ALERT_SEVERITIES),
            ("scope", scope, self.ALERT_SCOPES),
            ("comparison_operator", comparison_operator, self.ALERT_OPERATORS),
            ("period_mode", period_mode, self.ALERT_PERIOD_MODES),
        ):
            if value not in allowed:
                raise ValueError(
                    f"{label} must be one of {allowed}, got {value!r}")
        body: Dict[str, Any] = {
            "widget": widget, "metric": metric, "severity": severity,
            "scope": scope, "threshold": threshold,
            "comparisonOperator": comparison_operator,
            "periodDays": period_days, "periodMode": period_mode,
        }
        for key, value in (("scopeEntities", scope_entities),
                           ("channelConfig", channel_config),
                           ("recheckDelayHours", recheck_delay_hours)):
            if value is not None:
                body[key] = value
        return self._request("POST", "deliverability/alerts", json=body)

    def update_deliverability_alert(self, alert_id: str, data: dict) -> Dict[str, Any]:
        """Update an alert: `threshold`, `comparisonOperator`, `periodDays`,
        `periodMode`, `channelConfig`, `scopeEntities`, `enabled`."""
        return self._request(
            "PATCH", f"deliverability/alerts/{alert_id}", json=data)

    def delete_deliverability_alert(self, alert_id: str) -> Dict[str, Any]:
        """Delete a deliverability alert."""
        return self._request("DELETE", f"deliverability/alerts/{alert_id}")

    # --- Webhooks ---------------------------------------------------------------

    def list_webhooks(self) -> Any:
        """List the team's webhooks (`GET /hooks` — lemlist's own abbreviation)."""
        return self._request("GET", "hooks")

    def add_webhook(
        self,
        target_url: str,
        *,
        type: str = None,
        secret: str = None,
        campaign_id: str = None,
        is_first: bool = None,
        zap_id: str = None,
    ) -> Dict[str, Any]:
        """Subscribe a URL to lemlist events.

        `type` is ONE event name (`emailsReplied`, `linkedinInviteAccepted`,
        `signalRegistered`… ~70 of them); omitting it subscribes to all.
        `campaign_id` narrows to one campaign, `is_first` to the first
        occurrence per lead. The event vocabulary is not mirrored here on
        purpose: it changes with lemlist's features, and a stale local copy
        would refuse an event that works.
        """
        params = {k: v for k, v in {
            "campaignId": campaign_id, "isFirst": is_first, "zapId": zap_id,
        }.items() if v is not None}
        body: Dict[str, Any] = {"targetUrl": target_url}
        if type is not None:
            body["type"] = type
        if secret is not None:
            body["secret"] = secret
        return self._request("POST", "hooks", params=params, json=body)

    def delete_webhook(self, hook_id: str) -> Dict[str, Any]:
        """Unsubscribe a webhook."""
        return self._request("DELETE", f"hooks/{hook_id}")
