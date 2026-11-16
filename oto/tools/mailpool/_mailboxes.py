"""Mailbox creation for `MailpoolClient` — a mixin, composed by `client.py`.

Creating a mailbox takes one of the workspace's paid subscription slots. This
module never buys one: `plan_mailboxes` reads the slots, the domains and the
mailboxes already there, and refuses anything that would need a slot that is
not free, a domain that is not active, a domain already set to another type,
or an address that already exists. `create_mailboxes` runs the same plan
immediately before its single `POST /mailboxes`.

What the caller needs to know, and cannot guess:

- **A domain gets its type from its first mailbox.** A domain registered
  without mailboxes is `unassigned`; the first mailbox created on it decides
  `google`, `outlook` or `shared`, and every later mailbox on that domain must
  be of the same type.
- `private` mailboxes (the caller's own SMTP/IMAP server) are not served: they
  need connection settings this module does not take.
- **One `POST /mailboxes` per domain**: Mailpool refuses a single call
  spanning several domains with 400 "Mailboxes count limit exceeded" even
  with free slots, while one domain per call is accepted. A failure after an
  earlier domain landed is `MailpoolCreatePartial`, and so is a domain whose
  call was accepted but whose answer lists no mailbox: what earlier domains
  created is never lost.
- ⚠️ **The create is never retried** (a replay could create twice). On a
  timeout or a 5xx the mailboxes may or may not exist: list the domain's
  mailboxes before calling again.
- A new Google or Outlook mailbox can come back `pending` while the provider
  provisions it; it turns `active` on its own.
- The response carries every new mailbox's passwords: it is served as
  `MAILBOX_FIELDS` like any other mailbox, so they stay in the Mailpool
  dashboard.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

#: Mailbox types this module creates.
CREATE_TYPES = ("google", "outlook", "shared")
#: Most mailboxes in one call.
MAX_CREATE = 50

_REQUIRED = ("email", "firstName", "lastName", "type")
_OPTIONAL = ("signature", "forwardTo", "avatar")
_ADDRESS_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9])?@"
                         r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                         r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z]{2,}$")
_FORWARD_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
_PAGE = 100


class MailpoolCreateRefused(ValueError):
    """A mailbox creation refused BEFORE anything was sent. `problems` lists
    each reason, one per affected address or type."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__("Mailbox creation refused, nothing was sent: "
                         + "; ".join(problems))


class MailpoolCreatePartial(RuntimeError):
    """The create of one domain failed after earlier domains' landed.
    `created` = mailboxes created (as `MAILBOX_FIELDS`), `done` = domains whose
    mailboxes are known, `not_done` = the others, the failed domain first,
    `cause` = the failure. The failed domain (`not_done[0]`) is uncertain: its
    mailboxes may or may not exist when `cause` is a timeout or a 5xx, and they
    likely exist when Mailpool accepted its call but listed none (`cause` says
    so): list them before retrying."""

    def __init__(self, created: List[Dict[str, Any]], done: List[str],
                 not_done: List[str], cause: BaseException):
        self.created, self.done, self.not_done, self.cause = created, done, not_done, cause
        super().__init__(f"Mailboxes created on {', '.join(done)}; not on "
                         f"{', '.join(not_done)} ({cause}).")


class MailboxCreateMixin:
    """`plan_mailboxes` / `create_mailboxes` on top of `MailpoolClient`'s
    transport and reads."""

    # --- validation ---------------------------------------------------------

    @staticmethod
    def _check_new_mailboxes(mailboxes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if isinstance(mailboxes, (str, bytes, dict)):
            raise ValueError("`mailboxes` must be a list of mailboxes.")
        items = list(mailboxes)
        if not items:
            raise ValueError("`mailboxes` is empty.")
        if len(items) > MAX_CREATE:
            raise ValueError(f"At most {MAX_CREATE} mailboxes per call; got {len(items)}.")
        out, seen = [], set()
        for i, m in enumerate(items):
            if not isinstance(m, dict):
                raise ValueError(f"mailboxes[{i}] must be an object.")
            unknown = set(m) - set(_REQUIRED) - set(_OPTIONAL)
            if unknown:
                raise ValueError(
                    f"mailboxes[{i}] has unknown fields: {', '.join(sorted(unknown))}.")
            for f in _REQUIRED:
                if not isinstance(m.get(f), str) or not m[f].strip():
                    raise ValueError(f"mailboxes[{i}].{f} is required (a non-empty string).")
            email = m["email"].strip().lower()
            if not _ADDRESS_RE.match(email):
                raise ValueError(f"mailboxes[{i}].email must be an address such as "
                                 "anna@example.com.")
            if email in seen:
                raise ValueError(f"mailboxes[{i}].email {email} appears twice.")
            seen.add(email)
            if m["type"] not in CREATE_TYPES:
                raise ValueError(f"mailboxes[{i}].type must be one of "
                                 f"{', '.join(CREATE_TYPES)}.")
            one = {"email": email, "firstName": m["firstName"].strip(),
                   "lastName": m["lastName"].strip(), "type": m["type"]}
            for f in _OPTIONAL:
                v = m.get(f)
                if v is None or v == "":
                    continue
                if not isinstance(v, str):
                    raise ValueError(f"mailboxes[{i}].{f} must be a string.")
                if f == "forwardTo" and not _FORWARD_RE.match(v.strip()):
                    raise ValueError(f"mailboxes[{i}].forwardTo must be one address.")
                one[f] = v.strip() if f != "signature" else v
            out.append(one)
        return out

    # --- reads --------------------------------------------------------------

    def _all(self, read, **kw) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page = read(limit=_PAGE, offset=offset, **kw)
            data = page.get("data") if isinstance(page, dict) else None
            if not isinstance(data, list):
                raise RuntimeError("Mailpool returned a list that is not a page "
                                   "({data, total}).")
            items += data
            offset += len(data)
            total = page.get("total")
            if not data or (isinstance(total, int) and offset >= total) or len(data) < _PAGE:
                return items

    # --- plan and create ----------------------------------------------------

    def plan_mailboxes(self, mailboxes: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        """Check a creation against the workspace, sending nothing. Returns
        `{mailboxes, slots: {type: {free, used_by_this, left_after}},
        domains: {name: {id, type, becomes}}}`, or raises
        `MailpoolCreateRefused` with every problem found."""
        items = self._check_new_mailboxes(mailboxes)
        problems: List[str] = []

        slots = self.get_subscription_slots()
        used = (slots or {}).get("mailboxes") or {}
        paid = (slots or {}).get("slots") or {}
        wanted: Dict[str, int] = {}
        for m in items:
            wanted[m["type"]] = wanted.get(m["type"], 0) + 1
        slot_plan = {}
        for t, n in sorted(wanted.items()):
            free = int(paid.get(t) or 0) - int(used.get(t) or 0)
            slot_plan[t] = {"free": max(free, 0), "used_by_this": n,
                            "left_after": free - n}
            if n > free:
                problems.append(f"{n} {t} mailbox(es) asked, {max(free, 0)} {t} slot(s) "
                                "free (slots are bought in the Mailpool dashboard)")

        domains = {d.get("domain"): d for d in self._all(self.list_domains)
                   if isinstance(d, dict)}
        by_domain: Dict[str, List[Dict[str, Any]]] = {}
        for m in items:
            by_domain.setdefault(m["email"].split("@", 1)[1], []).append(m)
        domain_plan = {}
        for name, group in sorted(by_domain.items()):
            d = domains.get(name)
            if d is None:
                problems.append(f"{name} is not a domain of this workspace")
                continue
            types = {m["type"] for m in group}
            if len(types) > 1:
                problems.append(f"{name}: one domain takes one mailbox type, got "
                                f"{', '.join(sorted(types))}")
                continue
            t = types.pop()
            if d.get("status") != "active":
                problems.append(f"{name} is {d.get('status')}, not active yet")
            if d.get("type") not in (t, "unassigned"):
                problems.append(f"{name} is a {d.get('type')} domain, {t} asked")
            domain_plan[name] = {"id": d.get("id"), "type": d.get("type"), "becomes": t}
            if isinstance(d.get("id"), int):
                taken = {(x.get("email") or "").lower()
                         for x in self._all(self.list_mailboxes, domain_id=d["id"])}
                for m in group:
                    if m["email"] in taken:
                        problems.append(f"{m['email']} already exists")

        if problems:
            raise MailpoolCreateRefused(problems)
        return {"mailboxes": items, "slots": slot_plan, "domains": domain_plan}

    def create_mailboxes(self, mailboxes: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        """`POST /mailboxes`, ONE call per domain, after `plan_mailboxes`
        passed. Returns `{created: [mailbox as MAILBOX_FIELDS], plan}`. Never
        retried. When a domain's call fails after an earlier one landed,
        `MailpoolCreatePartial` says what exists."""
        from .client import project_mailbox

        plan = self.plan_mailboxes(mailboxes)
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for m in plan["mailboxes"]:
            groups.setdefault(m["email"].split("@", 1)[1], []).append(m)
        created: List[Dict[str, Any]] = []
        done: List[str] = []
        for domain, group in groups.items():
            try:
                out = self._request("POST", "/mailboxes", json=group)
                got = out.get("emails") if isinstance(out, dict) else out
                if not isinstance(got, list):
                    raise RuntimeError(
                        f"Mailpool accepted the mailboxes of {domain} but its answer lists none: "
                        f"they may exist, list {domain}'s mailboxes before calling again.")
            except Exception as e:
                if not done:
                    raise
                raise MailpoolCreatePartial(created, done,
                                            [d for d in groups if d not in done], e) from e
            done.append(domain)
            created += [project_mailbox(m) for m in got]
        return {"created": created, "plan": plan}
