"""n8n public REST API client — workflows + executions.

n8n is a workflow automation platform (open source, self-hosted
OR n8n Cloud). The **public API** exposes workflows, their executions,
credentials and tags.

Auth = **API key** (`X-N8N-API-KEY` header) + the instance's **base URL** (self-hosting
requires its own URL — n8n Cloud: `https://<sub>.app.n8n.cloud`).
The key is created in n8n: Settings → n8n API → Create an API key.

Both passed to the constructor.

Writing a workflow: the API takes a whole workflow (`name`, `nodes`, `connections`,
`settings`) and refuses any key it does not accept in a body ("must NOT have additional
properties") — and a workflow as READ carries several (`id`, `active`, `tags`,
`createdAt`…). `workflow_body` keeps only the writable keys, so a workflow read with
`get_workflow`, changed, and sent back is accepted.

Docs: https://docs.n8n.io/api/

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import requests

from ..common.credentials import require
from ..common.errors import raise_for_upstream
from ..http.client import refuse_redirect

#: The keys of a workflow body the public API accepts, on update and on create.
#: `shared` is left out: a read workflow carries it with read-only fields, and who a
#: workflow is shared with is not set through this body.
WORKFLOW_FIELDS = ("name", "nodes", "connections", "settings", "staticData",
                   "pinData", "nodeGroups", "parentFolderId", "description")
#: Accepted on create only: the project the new workflow goes into.
CREATE_ONLY_FIELDS = ("projectId",)
#: The keys an update must carry: it replaces the whole workflow.
REQUIRED_FIELDS = ("name", "nodes", "connections", "settings")
#: The writable keys a body may set to null (`parentFolderId: null` = the project root).
#: Any other is refused null, though a read workflow carries `description: null`.
NULLABLE_FIELDS = ("parentFolderId", "pinData", "staticData")


def workflow_body(workflow: Dict[str, Any], *,
                  create: bool = False) -> Tuple[Dict[str, Any], List[str]]:
    """Split a workflow into the body the API accepts and the keys left out.

    Returns `(body, ignored)`: `ignored` names, sorted, every key of `workflow` that is
    not writable (read-only fields of a read workflow, `tags`, `shared`…) or is null
    where the API refuses null. A key left out of an update keeps its current value.
    """
    allowed = WORKFLOW_FIELDS + (CREATE_ONLY_FIELDS if create else ())

    def kept(k: str, v: Any) -> bool:
        return k in allowed and (v is not None or k in NULLABLE_FIELDS)

    body = {k: v for k, v in workflow.items() if kept(k, v)}
    ignored = sorted(k for k, v in workflow.items() if not kept(k, v))
    return body, ignored


class N8nClient:
    """n8n client — workflows, executions, tags (public API v1)."""

    def __init__(self, api_key: Optional[str] = None,
                 base_url: Optional[str] = None):
        """Initialize the client.

        Args:
            api_key: n8n API key.
            base_url: instance URL, e.g. `https://acme.app.n8n.cloud`.
                The `/api/v1` suffix is added.
        """
        self.api_key = require(api_key, "N8N_API_KEY")
        base = require(base_url, "N8N_BASE_URL").rstrip("/")
        # Tolerate being passed the URL already carrying /api/v1.
        if base.endswith("/api/v1"):
            base = base[: -len("/api/v1")]
        self.base_url = base
        self.session = requests.Session()
        self.session.headers.update({
            "X-N8N-API-KEY": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}/api/v1{path}"
        resp = self.session.request(method, url, timeout=30, allow_redirects=False,
                                    **kwargs)
        refuse_redirect(resp)
        raise_for_upstream(resp, service="n8n")
        return resp.json() if resp.content else {}

    # --- Workflows ----------------------------------------------------------

    def list_workflows(
        self,
        limit: int = 50,
        active: Optional[bool] = None,
        tags: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List workflows (paginated via `nextCursor`).

        Args:
            active: keep only active/inactive workflows.
            tags: comma-separated list of tags.
            cursor: pagination cursor (`nextCursor` from the previous page).
        """
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if active is not None:
            params["active"] = str(active).lower()
        if tags:
            params["tags"] = tags
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/workflows", params=params)

    def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Fetch a workflow (nodes, connections, settings)."""
        return self._request("GET", f"/workflows/{workflow_id}")

    def activate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Activate a workflow (triggers/cron started)."""
        return self._request("POST", f"/workflows/{workflow_id}/activate")

    def deactivate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Deactivate a workflow."""
        return self._request("POST", f"/workflows/{workflow_id}/deactivate")

    def create_workflow(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Create a workflow, inactive. `workflow` needs `name`, `nodes` and
        `connections`; `settings` defaults to `{}`. Keys the API does not accept in a
        body are left out (`workflow_body`)."""
        body, _ = workflow_body(workflow, create=True)
        body.setdefault("settings", {})
        return self._request("POST", "/workflows", json=body)

    def update_workflow(self, workflow_id: str, workflow: Dict[str, Any],
                        publish_if_active: Optional[bool] = None) -> Dict[str, Any]:
        """Replace a workflow. The API requires `name`, `nodes`, `connections` and
        `settings`: one of them that `workflow` omits keeps its current value (one
        read before the write). An optional key omitted (`staticData`, `pinData`,
        `description`…) is left untouched by n8n. Keys the API does not accept in a
        body are left out (`workflow_body`).

        A published (active) workflow is re-published with the change, unless
        `publish_if_active=False` (the change is then saved as a draft). The parameter
        is sent only when given: an instance older than it refuses it.
        """
        body, _ = workflow_body(workflow)
        missing = [k for k in REQUIRED_FIELDS if k not in body]
        if missing:
            current = self.get_workflow(workflow_id)
            for k in missing:
                if k in current:
                    body[k] = current[k]
        params = None
        if publish_if_active is not None:
            params = {"publishIfActive": str(publish_if_active).lower()}
        return self._request("PUT", f"/workflows/{workflow_id}", json=body,
                             params=params)

    def delete_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Delete a workflow, for good."""
        return self._request("DELETE", f"/workflows/{workflow_id}")

    def set_workflow_tags(self, workflow_id: str,
                          tag_ids: List[str]) -> Any:
        """Replace a workflow's tags with `tag_ids` (existing tags, see
        `list_tags`); `[]` removes them all."""
        return self._request("PUT", f"/workflows/{workflow_id}/tags",
                             json=[{"id": t} for t in tag_ids])

    # --- Executions ---------------------------------------------------------

    def list_executions(
        self,
        limit: int = 50,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List executions (paginated).

        Args:
            workflow_id: filter by workflow.
            status: `success` | `error` | `waiting`.
            cursor: pagination cursor.
        """
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if workflow_id:
            params["workflowId"] = workflow_id
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/executions", params=params)

    def get_execution(self, execution_id: int,
                      include_data: bool = False) -> Dict[str, Any]:
        """Fetch an execution. `include_data` includes the detailed data
        of the nodes (voluminous)."""
        params = {"includeData": "true"} if include_data else None
        return self._request("GET", f"/executions/{execution_id}", params=params)

    # --- Tags ---------------------------------------------------------------

    def list_tags(self, limit: int = 50,
                  cursor: Optional[str] = None) -> Dict[str, Any]:
        """List tags."""
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/tags", params=params)
