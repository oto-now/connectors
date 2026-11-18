"""Mailbox edits for `MailpoolClient` — a mixin, composed by `client.py`.

`PUT /mailboxes/{id}` changes a mailbox's sender settings in place: first and
last name, signature, forwarding address and, on Google and Outlook mailboxes
only, the avatar. Nothing else of a mailbox is editable through it (not its
address, type or domain), and warmup settings are another object (`/warmup`).

What the caller needs to know, and cannot guess:

- **Only what changes is sent.** `update_mailbox` reads the mailbox first,
  compares, and sends the fields whose value differs; when nothing differs it
  sends nothing and returns the mailbox as read. `plan_mailbox_update` does the
  same read and comparison and sends nothing at all.
- **The answer is checked.** After the write, every field sent must hold its
  new value and every field not sent its previous one; otherwise
  `MailpoolUpdateNotKept` is raised with the values before the write, so the
  caller can see what to put back.
- **Clearing a field**: only the avatar is documented as removable
  (`avatar=None`). A name, a signature or a forwarding address is replaced,
  never emptied: an empty value is refused before anything is sent.
- ⚠️ The write is never retried. On a timeout or a 5xx the change may or may
  not have landed: read the mailbox before calling again.
- The response carries the mailbox's passwords: it is served as
  `MAILBOX_FIELDS` like any other mailbox.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

#: Editable fields, as Mailpool names them, keyed by the argument that sets them.
UPDATE_FIELDS = {"first_name": "firstName", "last_name": "lastName",
                 "signature": "signature", "forward_to": "forwardTo",
                 "avatar": "avatar"}
#: Mailbox types whose avatar can be set (Mailpool reference: UpdateGoogleMailbox,
#: UpdateOutlookMailbox; shared and private mailboxes take no avatar).
AVATAR_TYPES = ("google", "outlook")

_FORWARD_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


#: An argument left out: the field is not touched (`None` removes the avatar).
UNSET: Any = _Unset()


class MailpoolUpdateNotKept(RuntimeError):
    """A mailbox write whose answer does not show the expected mailbox.
    `not_applied` = fields sent that do not hold the value sent,
    `changed` = fields NOT sent whose value changed anyway, `previous` = the
    editable fields before the write, `mailbox` = the mailbox as read after it
    (as `MAILBOX_FIELDS`). The mailbox was changed in some way: read it before
    anything else."""

    def __init__(self, mailbox_id: int, not_applied: List[str], changed: List[str],
                 previous: Dict[str, Any], mailbox: Any):
        self.mailbox_id, self.not_applied, self.changed = mailbox_id, not_applied, changed
        self.previous, self.mailbox = previous, mailbox
        parts = []
        if not_applied:
            parts.append(f"not applied: {', '.join(not_applied)}")
        if changed:
            parts.append(f"changed without being sent: {', '.join(changed)}")
        super().__init__(f"Mailpool did not keep the expected settings of mailbox "
                         f"{mailbox_id} ({'; '.join(parts)}); the values before the write "
                         "are in `previous`.")


def _editable(mailbox: Any) -> Dict[str, Any]:
    m = mailbox if isinstance(mailbox, dict) else {}
    return {api: m.get(api) for api in UPDATE_FIELDS.values()}


class MailboxUpdateMixin:
    """`plan_mailbox_update` / `update_mailbox` on top of `MailpoolClient`'s
    transport and reads."""

    @staticmethod
    def _check_changes(given: Dict[str, Any]) -> Dict[str, Any]:
        """The arguments given, validated, as Mailpool field names."""
        out: Dict[str, Any] = {}
        for arg, value in given.items():
            if value is UNSET:
                continue
            if arg == "avatar" and value is None:
                out["avatar"] = None
                continue
            if not isinstance(value, str):
                raise ValueError(f"`{arg}` must be a string"
                                 + (" (or None to remove the avatar)." if arg == "avatar" else "."))
            text = value if arg == "signature" else value.strip()
            if not text.strip():
                raise ValueError(f"`{arg}` is empty: Mailpool documents no way to clear it; "
                                 "pass a new value or leave it out.")
            if arg == "forward_to" and not _FORWARD_RE.match(text):
                raise ValueError("`forward_to` must be one address such as replies@example.com.")
            out[UPDATE_FIELDS[arg]] = text
        if not out:
            raise ValueError("Nothing to update: pass at least one of "
                             + ", ".join(UPDATE_FIELDS) + ".")
        return out

    def _plan_update(self, mailbox_id: Any, given: Dict[str, Any]):
        mailbox_id = self._id("mailbox_id", mailbox_id)
        wanted = self._check_changes(given)
        current = self.get_mailbox(mailbox_id)
        if not isinstance(current, dict) or not current.get("id"):
            raise RuntimeError(f"Mailpool did not return mailbox {mailbox_id}.")
        if "avatar" in wanted and current.get("type") not in AVATAR_TYPES:
            raise ValueError(f"Mailbox {mailbox_id} is a {current.get('type')} mailbox: only "
                             f"{' and '.join(AVATAR_TYPES)} mailboxes take an avatar.")
        before = _editable(current)
        changes = {f: {"before": before[f], "after": v} for f, v in wanted.items()
                   if before[f] != v}
        plan = {"mailbox_id": mailbox_id, "email": current.get("email"),
                "type": current.get("type"), "changes": changes,
                "unchanged": sorted(f for f in wanted if f not in changes)}
        return plan, current, before

    def plan_mailbox_update(self, mailbox_id: int, *, first_name: Any = UNSET,
                            last_name: Any = UNSET, signature: Any = UNSET,
                            forward_to: Any = UNSET, avatar: Any = UNSET) -> Dict[str, Any]:
        """Read the mailbox and compare, sending nothing. Returns `{mailbox_id,
        email, type, changes: {field: {before, after}}, unchanged: [field]}`
        (Mailpool field names). Refuses an avatar on a shared or private
        mailbox."""
        return self._plan_update(mailbox_id, {
            "first_name": first_name, "last_name": last_name, "signature": signature,
            "forward_to": forward_to, "avatar": avatar})[0]

    def update_mailbox(self, mailbox_id: int, *, first_name: Any = UNSET,
                       last_name: Any = UNSET, signature: Any = UNSET,
                       forward_to: Any = UNSET, avatar: Any = UNSET) -> Any:
        """`PUT /mailboxes/{id}` with the fields that differ (see
        `plan_mailbox_update`); returns the mailbox as `MAILBOX_FIELDS`. Leave
        an argument out to keep the field; `avatar=None` removes the avatar.
        Nothing is sent when nothing differs. Never retried; an answer that
        does not show the change raises `MailpoolUpdateNotKept`."""
        from .client import project_mailbox

        plan, current, before = self._plan_update(mailbox_id, {
            "first_name": first_name, "last_name": last_name, "signature": signature,
            "forward_to": forward_to, "avatar": avatar})
        mailbox_id = plan["mailbox_id"]
        if not plan["changes"]:
            return current
        body = {f: c["after"] for f, c in plan["changes"].items()}
        answer = project_mailbox(self._request("PUT", f"/mailboxes/{mailbox_id}", json=body))
        after = answer if isinstance(answer, dict) and answer.get("id") else None
        if after is None or _differences(after, body, before) != ([], []):
            # The answer may be partial: the mailbox read back is what decides.
            after = self.get_mailbox(mailbox_id)
        not_applied, changed = _differences(after, body, before)
        if not_applied or changed:
            raise MailpoolUpdateNotKept(mailbox_id, not_applied, changed, before, after)
        return after


def _differences(after: Any, body: Dict[str, Any],
                 before: Dict[str, Any]) -> "tuple[List[str], List[str]]":
    """(fields sent that do not hold the value sent, fields not sent that
    changed)."""
    got = _editable(after)
    return (sorted(f for f, v in body.items() if got[f] != v),
            sorted(f for f in before if f not in body and got[f] != before[f]))
