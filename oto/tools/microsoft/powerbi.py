"""Power BI REST API client — the workspaces, reports and semantic models the
signed-in person can reach, READ ONLY (delegated access, scope `scopes.POWERBI`).

Power BI is its own Entra resource (`https://analysis.windows.net/powerbi/api`): a
Graph access token is refused there. The consumer asks for a Power BI token from the
same refresh token (`auth.refresh(..., scopes=scopes.POWERBI_REFRESH)`); the person's
consent to `scopes.POWERBI` must have been given (or an administrator's, for the
organization).

## Protocol facts that shape a caller

- **Workspaces and "My workspace"**: `list_workspaces` returns the shared ones; the
  person's own is addressed with `workspace_id=None` (`/myorg/reports`, `/myorg/datasets`).
- **A DAX query** (`execute_query`) needs Read AND Build on the model, and the tenant
  setting « Semantic model Execute Queries REST API » on. One query, one table per
  call; at most 100,000 rows or 1,000,000 values (15 MB) — beyond, Power BI answers
  200 with the rows cut and an `error` in the result, surfaced here as `truncated`.
  120 queries per minute per person. A DAX error answers 400.
- **A measure's DEFINITION** (`list_measures`, `INFO.VIEW.MEASURES()`): name, table,
  description, display folder, format are served to any reader; the DAX `Expression`
  ONLY to a person with WRITE permission on the model — Power BI returns it empty
  otherwise. `expression_visible` says which case the answer is in.
- Models hosted in Azure Analysis Services (or live-connected to one) cannot be queried.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._transport import GraphBase, segment

BASE_URL = "https://api.powerbi.com/v1.0/myorg"

#: The DAX that lists a model's measures, readable without write permission.
MEASURES_DAX = "EVALUATE INFO.VIEW.MEASURES()"
#: What a measure row keeps (INFO.VIEW.MEASURES columns, brackets stripped).
MEASURE_FIELDS = ("Name", "Table", "Description", "Expression", "DisplayFolder",
                  "FormatString", "DataType", "IsHidden", "State")


def _root(workspace_id: Optional[str]) -> str:
    return (f"{BASE_URL}/groups/{segment(workspace_id, 'workspace_id')}"
            if workspace_id else BASE_URL)


def _column(name: str) -> str:
    """`[Name]` or `Table[Name]` (executeQueries' column keys) → `Name`."""
    return name[name.rfind("[") + 1:].rstrip("]") if "[" in name else name


class PowerBIClient(GraphBase):
    """Power BI on behalf of one person, read only."""

    def _value(self, url: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        page = self._json("GET", url, params=params or None)
        value = (page or {}).get("value")
        if not isinstance(value, list):
            raise ValueError("unexpected Power BI answer: no `value` list.")
        return value

    def list_workspaces(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        """GET /groups — the shared workspaces the person belongs to."""
        if not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        return self._value(f"{BASE_URL}/groups", {"$top": limit})

    def list_reports(self, workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """GET [/groups/{id}]/reports — `workspace_id=None`: "My workspace"."""
        return self._value(f"{_root(workspace_id)}/reports")

    def get_report(self, report_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        """GET [/groups/{id}]/reports/{report_id}."""
        return self._json("GET", f"{_root(workspace_id)}/reports/{segment(report_id, 'report_id')}")

    def list_report_pages(self, report_id: str,
                          workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """GET [/groups/{id}]/reports/{report_id}/pages — name and display name."""
        return self._value(
            f"{_root(workspace_id)}/reports/{segment(report_id, 'report_id')}/pages")

    def list_datasets(self, workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """GET [/groups/{id}]/datasets — the semantic models."""
        return self._value(f"{_root(workspace_id)}/datasets")

    def execute_query(self, dataset_id: str, dax: str, *,
                      workspace_id: Optional[str] = None) -> Dict[str, Any]:
        """POST [/groups/{id}]/datasets/{id}/executeQueries — ONE DAX query.

        Returns `{"columns": [...], "rows": [{column: value}], "truncated": bool,
        "error": str|None}`: column keys without their `Table[...]` wrapping when the
        short name is unique, `truncated` when Power BI cut the result (its limits)."""
        if not isinstance(dax, str) or not dax.strip():
            raise ValueError("dax is empty: give one DAX query (EVALUATE …)")
        body = {"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}}
        out = self._json(
            "POST",
            f"{_root(workspace_id)}/datasets/{segment(dataset_id, 'dataset_id')}/executeQueries",
            json=body)
        results = (out or {}).get("results")
        if not isinstance(results, list) or not results:
            raise ValueError("unexpected Power BI answer: no `results`.")
        first = results[0] or {}
        error = first.get("error")
        tables = first.get("tables") or []
        raw = (tables[0] or {}).get("rows") or [] if tables else []
        keys = list(raw[0]) if raw else []
        shorts = [_column(k) for k in keys]
        names = {k: (s if shorts.count(s) == 1 else k) for k, s in zip(keys, shorts)}
        rows = [{names.get(k, k): v for k, v in r.items()} for r in raw]
        message = None
        if error:
            message = (error.get("message") if isinstance(error, dict) else str(error))
        return {"columns": [names[k] for k in keys], "rows": rows,
                "truncated": bool(error) and bool(rows), "error": message}

    def list_measures(self, dataset_id: str, *,
                      workspace_id: Optional[str] = None) -> Dict[str, Any]:
        """The measures of a model and their definition (`INFO.VIEW.MEASURES()`).

        `expression_visible` is False when no measure carries its DAX: Power BI only
        returns it to a person with write permission on the model."""
        res = self.execute_query(dataset_id, MEASURES_DAX, workspace_id=workspace_id)
        if res["error"] and not res["rows"]:
            raise ValueError(f"Power BI refused to list the measures: {res['error']}")
        measures = [{k: r.get(k) for k in MEASURE_FIELDS} for r in res["rows"]]
        return {"measures": measures,
                "expression_visible": any((m.get("Expression") or "").strip()
                                          for m in measures),
                "truncated": res["truncated"]}
