"""
LightOn API client (v3, api.lighton.ai) — sovereign document indexing
platform: ingestion (upload/parse/extract) + retrieval (hybrid search,
grounded RAG ask).

⚠️ v3 = the target platform (post-Paradigm). The old v2 API
`paradigm.lighton.ai/api/v2` (Paradigm app: chat alfred, query,
ask-question) is being deprecated — this client no longer covers it.
The same Console key (console.lighton.ai) works for both.

Auth = Bearer. Default base = `https://api.lighton.ai`; a private/on-prem
instance is targeted via `base_url`.

Endpoints covered (spec 3.12.0, developers.lighton.ai):
- POST /api/v3/search           — hybrid retrieval (dense + BM25 + multivector
                                  rerank), vision mode, facets
- POST /api/v3/ask              — full RAG: search + grounded LLM answer
- POST /api/v3/parse (+GET {id})— document → Markdown (sync/async)
- POST /api/v3/extract (+GET)   — structured extraction by JSON Schema
- GET/POST /api/v3/files        — list (filters + semantic search) / upload
- GET/DELETE /api/v3/files/{id} — record / deletion
- GET /api/v3/workspaces        — accessible workspaces (manual or synced
                                  SharePoint/Google Drive)

LightOn billing: ingestion per page, retrieval per query (search/ask),
vector storage per GB — see lighton.ai/pricing.

Requires: requests
"""

from __future__ import annotations

import json as _json
from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require
from ..common.errors import UpstreamHTTPError


class LightOnClient:
    """Client for the LightOn v3 API (document indexing + retrieval)."""

    DEFAULT_BASE_URL = "https://api.lighton.ai"
    TIMEOUT = 60

    def __init__(self, api_key: str | None = None, base_url: str | None = None, *,
                 session: requests.Session | None = None):
        """
        Args:
            api_key: LightOn API key (Bearer, created on console.lighton.ai).
            base_url: API base for a private/on-prem instance
                (default = SaaS `https://api.lighton.ai`).
            session: the `requests.Session` every request goes out through, when the
                consumer supplies one (e.g. a session whose transport checks each
                destination at connection time). Absent, module-level `requests`,
                as before.
        """
        self.api_key = require(api_key, "LIGHTON_API_KEY")
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._http = session if session is not None else requests

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        files: Optional[dict] = None,
        data: Optional[dict] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        url = f"{self.base_url}/api/v3/{endpoint.lstrip('/')}"
        headers = self._headers()
        if json is not None:
            headers["Content-Type"] = "application/json"
        resp = self._http.request(
            method, url, headers=headers, json=json, params=params,
            files=files, data=data, timeout=timeout or self.TIMEOUT,
        )
        if not resp.ok:
            # LightOn returns {"code", "error", "detail"} (or a DRF
            # validation dict) — surface the most telling one.
            try:
                body = resp.json()
                if isinstance(body, dict):
                    msg = body.get("detail") or body.get("error") or _json.dumps(body)
                else:
                    msg = resp.text
            except Exception:
                msg = resp.text
            raise UpstreamHTTPError.from_response(resp, service="lighton", body=msg)
        if not resp.content:
            return None
        return resp.json()

    # ---- retrieval ----------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        workspace_ids: Optional[List[int]] = None,
        tag_ids: Optional[List[int]] = None,
        file_ids: Optional[List[int]] = None,
        max_results: Optional[int] = None,
        mode: Optional[str] = None,
        relevance_scoring: Optional[str] = None,
        content_type: Optional[List[str]] = None,
        attribute: Optional[List[str]] = None,
        include_image: bool = False,
        include_bboxes: bool = False,
    ) -> Dict[str, Any]:
        """Chunk retrieval (hybrid dense + BM25, multivector rerank).
        1 retrieval credit per query. No LLM generation.

        Args:
            query: natural-language query (max 1500 characters).
            workspace_ids / tag_ids / file_ids: scoping (file_ids exclusive
                with the other two). No filter = the whole authorized corpus.
            max_results: number of chunks after rerank (1-50).
            mode: `text` (default) or `vision` (VLM page images).
            relevance_scoring: `scoring_and_filtering` (default) /
                `scoring_only` / `none`.
            content_type / attribute: facet filters (see LightOn docs).
            include_image: attaches the page image in base64 per result.
            include_bboxes: attaches the PDF bounding boxes per result.
        """
        payload: Dict[str, Any] = {"query": query}
        if workspace_ids:
            payload["workspace_id"] = workspace_ids
        if tag_ids:
            payload["tag_id"] = tag_ids
        if file_ids:
            payload["file_id"] = file_ids
        if max_results is not None:
            payload["max_results"] = max_results
        if mode:
            payload["mode"] = mode
        if relevance_scoring:
            payload["relevance_scoring"] = relevance_scoring
        if content_type:
            payload["content_type"] = content_type
        if attribute:
            payload["attribute"] = attribute
        if include_image:
            payload["include_image"] = True
        if include_bboxes:
            payload["include_bboxes"] = True
        return self._request("POST", "search", json=payload)

    def ask(
        self,
        query: str,
        *,
        workspace_ids: Optional[List[int]] = None,
        tag_ids: Optional[List[int]] = None,
        file_ids: Optional[List[int]] = None,
        max_results: Optional[int] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Full RAG: search over the corpus then an LLM answer grounded in
        the retrieved passages (with provenance). Synchronous mode (no SSE).

        Args:
            query: natural-language question (max 1500 characters).
            workspace_ids / tag_ids / file_ids: scoping (same rules as
                `search`).
            max_results: number of context chunks (1-50).
            model: generation LLM (e.g. `mistral-large-latest`) — platform
                default if omitted.
        """
        payload: Dict[str, Any] = {"query": query, "stream": False}
        if workspace_ids:
            payload["workspace_id"] = workspace_ids
        if tag_ids:
            payload["tag_id"] = tag_ids
        if file_ids:
            payload["file_id"] = file_ids
        if max_results is not None:
            payload["max_results"] = max_results
        if model:
            payload["model"] = model
        return self._request("POST", "ask", json=payload, timeout=120)

    # ---- parse / extract (document processing, outside the index) -----------

    def parse_bytes(
        self, data: bytes, filename: str, *, async_: bool = False,
    ) -> Dict[str, Any]:
        """Parse a document (direct upload) → structured Markdown.

        Args:
            async_: True = async job (large files, 202 + job id to poll
                via `parse_job`). Sync: ~20 MB / 15 pages max.
        """
        form = {"options": _json.dumps({"async": True})} if async_ else None
        return self._request(
            "POST", "parse", files={"file": (filename, data)}, data=form,
            timeout=300,
        )

    def parse_url(self, document_url: str, *, async_: bool = False) -> Dict[str, Any]:
        """Parse a document reachable by public URL → Markdown."""
        payload: Dict[str, Any] = {"document": document_url}
        if async_:
            payload["options"] = {"async": True}
        return self._request("POST", "parse", json=payload, timeout=300)

    def parse_job(self, job_id: str) -> Dict[str, Any]:
        """Status/result of an async parse job."""
        return self._request("GET", f"parse/{job_id}")

    def extract_bytes(
        self, data: bytes, filename: str, schema: dict, *, async_: bool = False,
    ) -> Dict[str, Any]:
        """Structured extraction: pulls the fields described by a JSON Schema
        out of a document (direct upload).

        Args:
            schema: JSON Schema object of the fields to extract.
            async_: True = async job (poll via `extract_job`).
        """
        form: Dict[str, Any] = {"schema": _json.dumps(schema)}
        if async_:
            form["options"] = _json.dumps({"async": True})
        return self._request(
            "POST", "extract", files={"file": (filename, data)}, data=form,
            timeout=300,
        )

    def extract_url(
        self, document_url: str, schema: dict, *, async_: bool = False,
    ) -> Dict[str, Any]:
        """Structured extraction from a document reachable by URL."""
        payload: Dict[str, Any] = {"document": document_url, "schema": schema}
        if async_:
            payload["options"] = {"async": True}
        return self._request("POST", "extract", json=payload, timeout=300)

    def extract_job(self, job_id: str) -> Dict[str, Any]:
        """Status/result of an async extract job (`ext_…`)."""
        return self._request("GET", f"extract/{job_id}")

    # ---- files (the index) --------------------------------------------------

    def list_files(
        self,
        *,
        workspace_ids: Optional[List[int]] = None,
        tag_ids: Optional[List[int]] = None,
        search: Optional[str] = None,
        status: Optional[str] = None,
        filename: Optional[str] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Indexed documents accessible to the key (paginated).

        Args:
            workspace_ids / tag_ids: filters.
            search: semantic search — results are ordered by
                relevance (a light "find my doc" without going through `search`).
            status: ingestion status filter (e.g. `pending,embedded`).
            filename: filter by name (partial, case-insensitive).
        """
        params: Dict[str, Any] = {}
        if workspace_ids:
            params["workspace_id"] = ",".join(str(w) for w in workspace_ids)
        if tag_ids:
            params["tag_id"] = ",".join(str(t) for t in tag_ids)
        if search:
            params["search"] = search
        if status:
            params["status"] = status
        if filename:
            params["filename"] = filename
        if page is not None:
            params["page"] = page
        if page_size is not None:
            params["page_size"] = page_size
        return self._request("GET", "files", params=params or None)

    def get_file(self, file_id: int) -> Dict[str, Any]:
        """Record of a document (metadata, ingestion status)."""
        return self._request("GET", f"files/{file_id}")

    def upload_file_bytes(
        self,
        data: bytes,
        filename: str,
        workspace_id: int,
        *,
        title: Optional[str] = None,
        tag_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Upload + indexing of a document into a workspace (multipart).
        Billed per ingested page.

        Args:
            workspace_id: destination workspace (REQUIRED in v3).
            title: displayed title (default = filename without extension).
            tag_ids: tags to set on creation.
        """
        form: Dict[str, Any] = {"workspace_id": str(workspace_id)}
        if title:
            form["title"] = title
        if tag_ids:
            form["tags"] = [str(t) for t in tag_ids]
        return self._request(
            "POST", "files", files={"file": (filename, data)}, data=form,
            timeout=300,
        )

    def delete_file(self, file_id: int) -> None:
        """Permanently deletes a document and its index."""
        return self._request("DELETE", f"files/{file_id}")

    # ---- workspaces ---------------------------------------------------------

    def list_workspaces(
        self,
        *,
        name: Optional[str] = None,
        workspace_type: Optional[str] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Workspaces accessible to the key (⚠️ endpoint marked alpha by
        LightOn). `workspace_type`: shared | personal | public."""
        params: Dict[str, Any] = {}
        if name:
            params["name"] = name
        if workspace_type:
            params["workspace_type"] = workspace_type
        if page is not None:
            params["page"] = page
        if page_size is not None:
            params["page_size"] = page_size
        return self._request("GET", "workspaces", params=params or None)
