"""Views of a page of lemlist's shared database (`POST /database/people`, `POST /database/companies`).

A record of that base is large: a person carries a whole career (`experiences`, each position with its company's
firmographics, technologies and job counts), a company its technologies and its recent job posts. A page of 50
companies runs to megabytes, more than an agent can read. Two opt-in views shape each element of `results`, the rest
of the page (`total`, `page`, `size`, `search`…) kept as lemlist sends it; a page without a `results` list raises:

- `fields`: a keep-list. Each entry names a key of the record, or a nested key by a dotted path
  (`experiences.title`); a list met on the way is traversed, the rest of the path holding for each element, and a
  segment is never an index (the same paths as `output.strip`, `oto.connectors.serve`). A key the record lacks is
  left out, never an error; a null or scalar value where the path expects an object is kept as it is.
- `compact`: a preset of the fields prospecting reads (identity, title, LinkedIn, headcount, country, industry). A
  person keeps only its current positions (no end date), each reduced to its title and company. A company keeps a
  list under a key containing `job` as job posts: the five most recent, each reduced to its title and dates, with
  their total (`<key>_total`). Any other key is left out. The page says which keys of its records the preset left out (`omitted_fields`), so the call can ask for one by
  `fields`.

With both, the fields named are taken from the whole record and replace the compact key of the same first level.
Neither given: the page is returned untouched, the same object.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["DatabaseView", "PEOPLE_COMPACT", "POSITION_COMPACT", "COMPANY_COMPACT", "JOB_POSTS_KEPT", "view_of",
           "shape_page"]

# A person: who, where, which role, how to reach them.
PEOPLE_COMPACT = (
    "lead_id", "full_name", "title", "headline", "seniority", "department", "country", "location",
    "lead_linkedin_url", "potential_email", "current_exp_company_name", "experiences",
)
# A current position of a person: the role, and the company it is held at.
POSITION_COMPACT = (
    "title", "date_from", "company_name", "company_domain", "company_website_url", "company_linkedin_url",
    "company_employee_count", "company_size", "company_industry", "company_headquarters_country", "active_job_post",
)
# A company: identity, size, place, market.
COMPANY_COMPACT = (
    "company_id", "company_name", "company_domain", "company_website_url", "company_linkedin_url",
    "company_employee_count", "company_size", "employees_count_growth", "company_headquarters_country",
    "company_headquarters_city", "company_location", "company_industry", "company_subindustry",
    "company_subsubindustry", "company_type", "company_founded", "revenue_bucket", "last_funding_round_at",
    "business_business_customer",
)
# The job posts a compact company keeps, the most recent first.
JOB_POSTS_KEPT = 5

_KINDS = ("people", "companies")


class DatabaseView:
    """A checked view: the keep-list as a tree (`None` = the whole value) and the preset flag."""

    def __init__(self, fields: Optional[Dict[str, Any]], compact: bool):
        self.fields = fields
        self.compact = compact


def _tree(fields: List[str]) -> Dict[str, Any]:
    """The keep-list as a tree; a path under another one is already kept whole."""
    tree: Dict[str, Any] = {}
    for path in fields:
        node = tree
        segments = path.split(".")
        for i, segment in enumerate(segments):
            if segment in node and node[segment] is None:
                break  # an ancestor is kept whole
            if i == len(segments) - 1:
                node[segment] = None
            else:
                node = node.setdefault(segment, {})
    return tree


def view_of(fields: Optional[List[str]] = None, compact: bool = False) -> Optional[DatabaseView]:
    """The view the call asks for, checked before any request; None when it asks for none."""
    if not isinstance(compact, bool):
        raise ValueError(f"compact is true or false, got {compact!r}")
    if fields is None:
        return DatabaseView(None, True) if compact else None
    if isinstance(fields, str) or not isinstance(fields, (list, tuple)) or not fields:
        raise ValueError("fields is a non-empty list of field names (a nested one by a dotted path)")
    for path in fields:
        if not isinstance(path, str) or not path or any(not s for s in path.split(".")):
            raise ValueError(f"fields: {path!r} is not a field name nor a dotted path (a.b)")
    return DatabaseView(_tree(list(fields)), compact)


def _keep(value: Any, tree: Optional[Dict[str, Any]]) -> Any:
    if tree is None:
        return value
    if isinstance(value, list):
        return [_keep(element, tree) for element in value]
    if not isinstance(value, dict):
        return value
    return {key: _keep(value[key], sub) for key, sub in tree.items() if key in value}


def _is_date_key(key: str) -> bool:
    key = key.lower()
    return "date" in key or key.endswith("_at") or key in ("posted", "published", "created")


def _is_title_key(key: str) -> bool:
    key = key.lower()
    return "title" in key or key in ("name", "position", "role")


def _job_post(post: Any) -> Any:
    """A job post reduced to its title and dates (scalar values only)."""
    if not isinstance(post, dict):
        return post
    return {k: v for k, v in post.items()
            if (_is_title_key(k) or _is_date_key(k)) and not isinstance(v, (dict, list))}


def _latest(post: Any) -> str:
    """The latest date-looking text of a post, for ordering (ISO dates sort as texts); empty when none."""
    if not isinstance(post, dict):
        return ""
    dates = [v for k, v in post.items() if _is_date_key(k) and isinstance(v, str)]
    return max(dates, default="")


def _job_posts(posts: list) -> list:
    reduced = [_job_post(p) for p in posts]
    reduced.sort(key=_latest, reverse=True)
    return reduced[:JOB_POSTS_KEPT]


def _compact_person(record: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: record[key] for key in PEOPLE_COMPACT if key in record and key != "experiences"}
    experiences = record.get("experiences")
    if isinstance(experiences, list):
        out["experiences"] = [
            {key: exp[key] for key in POSITION_COMPACT if key in exp}
            for exp in experiences if isinstance(exp, dict) and not exp.get("date_to")]
    elif "experiences" in record:
        out["experiences"] = experiences
    return out


def _compact_company(record: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: record[key] for key in COMPANY_COMPACT if key in record}
    for key, value in record.items():
        if key not in out and "job" in key.lower() and isinstance(value, list):
            out[key] = _job_posts(value)
            out[f"{key}_total"] = len(value)
    return out


def _compact(record: Dict[str, Any], kind: str) -> Dict[str, Any]:
    return _compact_person(record) if kind == "people" else _compact_company(record)


def _shape(record: Any, view: DatabaseView, kind: str) -> Any:
    if not isinstance(record, dict):
        return record
    if not view.compact:
        return _keep(record, view.fields)
    out = _compact(record, kind)
    if view.fields is not None:
        out.update(_keep(record, view.fields))
    return out


def shape_page(page: Any, view: Optional[DatabaseView], kind: str) -> Any:
    """The page with each element of `results` shaped by `view`; untouched without a view. A view asked of a page
    that carries no `results` list raises: lemlist changed its answer, and returning it whole would hand the agent
    the megabytes the view was asked to avoid."""
    if kind not in _KINDS:
        raise ValueError(f"kind is one of {_KINDS}, got {kind!r}")
    if view is None:
        return page
    if not isinstance(page, dict) or not isinstance(page.get("results"), list):
        raise RuntimeError(f"lemlist {kind} database: the page has no results list, the view cannot apply "
                           "(call again without fields and compact to read it as lemlist sends it)")
    results = page["results"]
    shaped = [_shape(record, view, kind) for record in results]
    out = {**page, "results": shaped}
    if view.compact:
        kept = {key for record in shaped if isinstance(record, dict) for key in record}
        out["omitted_fields"] = sorted({key for record in results if isinstance(record, dict) for key in record}
                                       - kept)
    return out
