"""Views of a page of lemlist activities (`GET /activities`).

An activity carries its whole lead: `lead.variables` holds every personalised copy of the sequence (HTML) and any
custom variable, next to the message ids, the sender's mailbox and lemlist's own metadata. A page of 100 activities
runs to hundreds of thousands of characters, more than an agent can read. Two opt-in views shape each activity, the
same two as the shared-database search (`database_views`, whose checks and keep-list they reuse):

- `fields`: a keep-list, a nested key by a dotted path (`lead.variables.email`); a list met on the way is traversed.
  A key the activity lacks is left out, never an error.
- `compact`: what happened, when, in which campaign and step, to whom: `ACTIVITY_COMPACT`, plus `excerpt`, the first
  `EXCERPT_CHARS` characters of the message body (a text body first, else lemlist's preview, else an HTML body with
  its tags removed), whitespace collapsed. No body is kept whole, HTML never.

With both, the fields named are taken from the whole activity and replace the compact key of the same first level.
Neither given: the list is returned untouched, the same object.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, Optional

from .database_views import DatabaseView, _keep, view_of

__all__ = ["ACTIVITY_COMPACT", "EXCERPT_CHARS", "view_of", "shape_activities"]

# Which event, when, where in the sequence, who it is about, who sent or answered it.
ACTIVITY_COMPACT = (
    "_id", "type", "createdAt", "campaignId", "campaignName", "sequenceStep", "leadId", "contactId", "leadEmail",
    "leadFirstName", "leadLastName", "leadCompanyName", "fromEmail", "sendUserEmail", "subject",
)
# The text bodies an activity may carry, read in this order; lemlist's preview comes after them.
TEXT_BODIES = ("text", "extractedText", "body", "message", "content", "comment", "note")
PREVIEW = "messagePreview"
EXCERPT_CHARS = 280

_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def _from_html(text: str) -> str:
    return _clean(html.unescape(_TAG.sub(" ", text)))


def _excerpt(activity: Dict[str, Any]) -> Optional[str]:
    candidates = [(key, False) for key in (*TEXT_BODIES, PREVIEW)]
    candidates += [(key, True) for key in activity if "html" in key.lower()]
    for key, is_html in candidates:
        value = activity.get(key)
        if not isinstance(value, str):
            continue
        text = _from_html(value) if is_html else _clean(value)
        if text:
            return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS] + "…"
    return None


def _compact(activity: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: activity[key] for key in ACTIVITY_COMPACT if key in activity}
    excerpt = _excerpt(activity)
    if excerpt is not None:
        out["excerpt"] = excerpt
    return out


def _shape(activity: Any, view: DatabaseView) -> Any:
    if not isinstance(activity, dict):
        return activity
    if not view.compact:
        return _keep(activity, view.fields)
    out = _compact(activity)
    if view.fields is not None:
        out.update(_keep(activity, view.fields))
    return out


def shape_activities(activities: Any, view: Optional[DatabaseView]) -> Any:
    """The activities shaped by `view`; untouched without a view. A view asked of an answer that is not a list raises:
    returning it whole would hand the agent what the view was asked to avoid."""
    if view is None:
        return activities
    if not isinstance(activities, list):
        raise RuntimeError("lemlist activities: the answer is not a list, the view cannot apply "
                           "(call again without fields and compact to read it as lemlist sends it)")
    return [_shape(activity, view) for activity in activities]
