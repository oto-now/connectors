"""Mailbox exports for `MailpoolClient` — a mixin, composed by `client.py`.

Mailpool hands mailboxes to a sending platform itself (`POST
/exports/mailboxes`), credentials included: the mailbox passwords go from
Mailpool to the platform and never through the caller. The platform's own
login must be saved in Mailpool first; this module only says WHETHER it is
saved (`export_credentials_status`), never its value — not even masked.
Saving it is not served: it is a secret, typed in the Mailpool dashboard.

What the caller needs to know, and cannot guess:

- **The export is asynchronous**: the call returns a batch `in_progress`;
  `get_export_batch` says when each mailbox is `completed` or `failed`.
- **A mailbox type a platform does not take is skipped**, listed in `errors`
  of the answer; the batch carries the rest (400 when nothing is left).
- **Only `active` mailboxes are sent**: a mailbox still `pending` (its
  provider is provisioning it) is refused here before anything is sent, so a
  half-provisioned inbox never reaches the platform.
- ⚠️ The export is never retried (a replay could connect a mailbox twice):
  on a timeout or a 5xx, list the batches before calling again.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

#: Platforms Mailpool exports to (request side of its reference).
EXPORT_PLATFORMS = ("instantly", "smartlead", "reachinbox", "reply", "saleshandy",
                    "warmbox", "emelia", "lemlist", "plusvibe")
MAX_EXPORT = 50
_BATCH_FIELDS = ("id", "createdAt", "status", "platform", "exports", "errors")
_ITEM_FIELDS = ("id", "mailboxId", "mailboxEmail", "status", "domainType", "error")


class MailpoolExportRefused(ValueError):
    """An export refused BEFORE anything was sent (`problems`)."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__("Export refused, nothing was sent: " + "; ".join(problems))


def _batch(b: Any) -> Any:
    if not isinstance(b, dict):
        return b
    out = {k: b[k] for k in _BATCH_FIELDS if k in b}
    if isinstance(out.get("exports"), list):
        out["exports"] = [{k: i[k] for k in _ITEM_FIELDS if k in i}
                          for i in out["exports"] if isinstance(i, dict)]
    if isinstance(out.get("errors"), list):
        out["errors"] = [{k: e[k] for k in ("mailboxId", "error") if k in e}
                         for e in out["errors"] if isinstance(e, dict)]
    return out


class MailboxExportMixin:
    """Exports on top of `MailpoolClient`'s transport and reads."""

    @staticmethod
    def _platform(platform: Any) -> str:
        if platform not in EXPORT_PLATFORMS:
            raise ValueError(f"`platform` must be one of {', '.join(EXPORT_PLATFORMS)}.")
        return platform

    def export_credentials_status(self, platform: str) -> Dict[str, Any]:
        """`GET /exports/credentials/{platform}` reduced to `{platform,
        saved, saved_at}` — no credential field, masked or not."""
        from ..common.errors import UpstreamHTTPError

        p = self._platform(platform)
        try:
            out = self._request("GET", f"/exports/credentials/{p}")
        except UpstreamHTTPError as e:
            if e.status_code == 404:
                return {"platform": p, "saved": False, "saved_at": None}
            raise
        return {"platform": p, "saved": True,
                "saved_at": out.get("createdAt") if isinstance(out, dict) else None}

    def plan_export(self, platform: str, mailbox_ids: Iterable[int]) -> Dict[str, Any]:
        """Check an export, sending nothing: the platform login is saved, every
        mailbox exists and is `active`. Returns `{platform, mailboxes: [{id,
        email, type}]}` or raises `MailpoolExportRefused` with every problem."""
        p = self._platform(platform)
        if isinstance(mailbox_ids, (str, bytes, dict)):
            raise ValueError("`mailbox_ids` must be a list of mailbox ids.")
        ids = [self._id("mailbox_ids[]", i) for i in mailbox_ids]
        if not ids:
            raise ValueError("`mailbox_ids` is empty.")
        if len(ids) > MAX_EXPORT:
            raise ValueError(f"At most {MAX_EXPORT} mailboxes per export; got {len(ids)}.")
        if len(set(ids)) != len(ids):
            raise ValueError("`mailbox_ids` lists a mailbox twice.")

        problems: List[str] = []
        if not self.export_credentials_status(p)["saved"]:
            problems.append(f"no {p} login is saved in Mailpool (save it in the dashboard)")
        known = {m.get("id"): m for m in self._all(self.list_mailboxes)}
        picked = []
        for i in ids:
            m = known.get(i)
            if m is None:
                problems.append(f"mailbox {i} is not in this workspace")
            elif m.get("status") != "active":
                problems.append(f"{m.get('email')} is {m.get('status')}, not active yet")
            else:
                picked.append({"id": i, "email": m.get("email"), "type": m.get("type")})
        if problems:
            raise MailpoolExportRefused(problems)
        return {"platform": p, "mailboxes": picked}

    def export_mailboxes(self, platform: str, mailbox_ids: Iterable[int]) -> Any:
        """`POST /exports/mailboxes` — after `plan_export` passed. Returns the
        batch (`id`, `status`, `exports[]`, `errors[]`). Never retried."""
        plan = self.plan_export(platform, mailbox_ids)
        return _batch(self._request(
            "POST", "/exports/mailboxes",
            json={"platform": plan["platform"],
                  "mailboxIds": [m["id"] for m in plan["mailboxes"]]}))

    def list_export_batches(self, limit=None, offset=None) -> Any:
        """`GET /exports/batches` — `{data: [batch], total}`."""
        out = self._request("GET", "/exports/batches", params=self._page(limit, offset))
        if isinstance(out, dict) and isinstance(out.get("data"), list):
            out["data"] = [_batch(b) for b in out["data"]]
        return out

    def get_export_batch(self, batch_id: int) -> Any:
        """`GET /exports/batches/{id}` — per-mailbox status."""
        return _batch(self._request(
            "GET", f"/exports/batches/{self._id('batch_id', batch_id)}"))
