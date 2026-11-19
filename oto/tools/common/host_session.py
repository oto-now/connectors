"""A session the HOST keeps: handed to the lib at build, handed back through a callback.

A connector whose access is a session (cookies from a login, a logged-in browser profile)
stores nothing itself. The host gives the session it keeps as text (`jar`) when it builds
the connector, and the connector hands a new or changed session back through `on_jar`;
the host stores it, encrypted, and gives it back at the next build. The text is opaque to
the host and a secret: a session is worth the password for its lifetime.

Two kinds of connector follow this contract: the `session` and `visitor` modes of the
`http` node (`oto.tools.http.session`, a cookie jar) and the connectors executed in the
host's browser (`oto.tools.common.browser_session`, a handle on a logged-in browser
profile). Both refuse a dead session with `SessionRejected`.
"""
from __future__ import annotations

from collections.abc import Callable


class SessionRejected(ValueError):
    """The service refused the account: the login was refused, or the session was
    refused again right after a fresh login, or a session that cannot be renewed
    without a person was refused. `code` is the account mark the host applies
    (`credential_rejected`). The message never carries the login, the password nor a
    cookie."""

    code = "credential_rejected"


class HostJar:
    """The session text the host handed over (`jar`), and the callback (`on_jar`) that
    hands it back. `save` calls `on_jar` only when the text changed."""

    def __init__(self, jar: str | None, on_jar: Callable[[str], None] | None):
        self.text = jar or None
        self._on_jar = on_jar

    def save(self, text: str) -> None:
        if text != self.text:
            self.text = text
            if self._on_jar is not None:
                self._on_jar(text)
