"""Microsoft Graph delegated scopes, by surface, and the reading of a granted `scope`.

A consumer asks for the union of the surfaces it needs at sign-in
(`IDENTITY + FILES + MAIL`…) and checks, before calling a surface, that the grant
covers it: `short(FILES) <= normalize(grant.scope)`.

All the scopes below are consentable by the person themselves, except
`TEAMS_ADMIN` and `DIRECTORY`, which require a tenant administrator (see
`auth.admin_consent_url`). `POWERBI` names another resource: its scopes keep their
full URI as short name, so they never collide with a Graph one.
"""
from __future__ import annotations

GRAPH = "https://graph.microsoft.com/"

#: `offline_access` brings the refresh token, `User.Read` the person's identity (`/me`).
IDENTITY = ("offline_access", "User.Read")
#: SharePoint sites and libraries, OneDrive.
FILES = (GRAPH + "Files.ReadWrite.All", GRAPH + "Sites.ReadWrite.All")
#: The person's mailbox: read, draft, move, delete, send.
MAIL = (GRAPH + "Mail.ReadWrite", GRAPH + "Mail.Send")
#: The person's calendars: read and write events.
CALENDAR = (GRAPH + "Calendars.ReadWrite",)
#: Teams and channels the person belongs to, posting in channels, chats. No admin consent.
TEAMS = (GRAPH + "Team.ReadBasic.All", GRAPH + "Channel.ReadBasic.All",
         GRAPH + "ChannelMessage.Send", GRAPH + "Chat.ReadWrite")
#: OneNote: the person's notebooks and those she can reach in groups and SharePoint
#: sites — read, create and change pages. No admin consent.
NOTES = (GRAPH + "Notes.ReadWrite.All",)
#: The organization's directory: READ users and licences, groups and members, sign-ins,
#: SharePoint sites; WRITE only licence assignment, enabling or disabling an account and
#: group membership (no account created or deleted, no password). Requires a tenant
#: administrator's consent; the person's own Microsoft role still bounds each call.
DIRECTORY = (GRAPH + "Directory.Read.All", GRAPH + "AuditLog.Read.All",
             GRAPH + "Sites.Read.All", GRAPH + "LicenseAssignment.ReadWrite.All",
             GRAPH + "User.EnableDisableAccount.All", GRAPH + "GroupMember.ReadWrite.All")
#: Power BI, its OWN Entra resource (not Graph): READ the workspaces, reports and
#: semantic models the person reaches, and query a model in DAX. A token for it is
#: asked with `POWERBI_REFRESH`, never mixed with Graph scopes in one request.
POWERBI_API = "https://analysis.windows.net/powerbi/api/"
POWERBI = (POWERBI_API + "Workspace.Read.All", POWERBI_API + "Report.Read.All",
           POWERBI_API + "Dataset.Read.All")
#: A Power BI access token from the same refresh token: what was consented for it.
POWERBI_REFRESH = (POWERBI_API + ".default", "offline_access")
#: Reading channel messages: requires a tenant administrator's consent.
TEAMS_ADMIN = (GRAPH + "ChannelMessage.Read.All",)
#: On refresh, ask for everything already consented (`.default`) rather than a list:
#: naming a scope that was never consented fails with AADSTS65001.
REFRESH = (GRAPH + ".default", "offline_access")

# OpenID Connect scopes: not Graph permissions, never prefixed with `GRAPH`.
OIDC = frozenset({"openid", "profile", "email", "offline_access"})


def _strip(scope: str) -> str:
    """`https://graph.microsoft.com/Files.ReadWrite.All` → `Files.ReadWrite.All`."""
    if scope.lower().startswith(GRAPH):
        return scope[len(GRAPH):]
    return scope


_CANONICAL = {_strip(s).lower(): _strip(s)
              for group in (IDENTITY, FILES, MAIL, CALENDAR, TEAMS, NOTES, DIRECTORY, TEAMS_ADMIN,
                             REFRESH, POWERBI)
              for s in group}
_CANONICAL.update({s: s for s in OIDC})


def _canonical(scope: str) -> str:
    name = _strip(scope)
    return _CANONICAL.get(name.lower(), name)


def short(scopes: tuple[str, ...]) -> frozenset[str]:
    """Our scope constants as short canonical names (`Files.ReadWrite.All`,
    `offline_access`), comparable with `normalize`."""
    return frozenset(_canonical(s) for s in scopes)


def normalize(scope: str) -> frozenset[str]:
    """The `scope` string returned by Entra (space-separated, short names or full
    URIs, any case, OIDC scopes included) as a set of short canonical names.

    A name absent from our constants is kept as returned, without its Graph prefix.
    """
    return frozenset(_canonical(s) for s in (scope or "").split())
