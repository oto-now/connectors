"""The errors of the engine, each named for what the host does with it.

Raised before anything is sent: `Unsupported` (a form of the format the engine does not run), `InvalidArguments` (an
argument the request cannot be built without), `AccountSettingError` (a setting of the account missing or invalid),
`AddressRefused` (a guarded address that is not public https), `MissingCredential` (a credential field absent, the
library's own error, `oto.tools.common.credentials`).

Raised from an answer: `Refused` (a refusal the description names: its error table, a refusal of the function, a check
of the arguments or of the answer, a service account key that cannot be read), `UpstreamHTTPError` (a status the
description names nothing for, `oto.tools.common.errors`, its body masked of every secret of the call), `TokenRefused`
(the token endpoint of an exchange refused), `UnreadableAnswer` (a success that is not JSON, too large, or a token
answer without its token), `Unreachable` (no answer: network failure or time out).

No message carries a secret, the request's address (an `api_key` may travel in its query) nor a cause that would:
`Unreachable` is raised without its cause.
"""
from __future__ import annotations

from typing import Optional


class EngineError(Exception):
    """Base of the engine's own errors."""


class Unsupported(EngineError):
    """The function declares a form the engine does not run (files, a rendered file, a background job, a consent of a
    person…): refused before anything is sent, never called halfway."""


class InvalidArguments(EngineError, ValueError):
    """An argument the request cannot be built without (a path parameter, the whole body), or an argument the
    description cannot place (a list in a query without `query_arrays`): nothing was sent."""


class AccountSettingError(EngineError, ValueError):
    """A setting of the account the address needs is missing, or its value no longer fits its declaration."""

    def __init__(self, setting: str, reason: str):
        self.setting = setting
        self.reason = reason
        super().__init__(f"setting {setting} {reason}")


class AddressRefused(EngineError):
    """A guarded address (its host comes from a setting of the account) is not https, or resolves to a private,
    loopback, link-local or reserved host: refused before any connection. The address itself is never in the message
    (it may carry a key in its query)."""


class Refused(EngineError):
    """A refusal the description names: `code` (the named refusal), `message` (the description's, never the vendor's
    body), `status_code` (the vendor's status, `None` for a check of the arguments, of the answer, or of a key),
    `retryable`, `marks_account` (`credential_rejected`, `quota_exhausted` or `None`)."""

    def __init__(self, code: str, message: str, *, status_code: Optional[int] = None, retryable: bool = False,
                 marks_account: Optional[str] = None):
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.marks_account = marks_account
        super().__init__(f"{message} ({code})")

    @property
    def credential_rejected(self) -> bool:
        """The refusal says the credential no longer authenticates."""
        return self.marks_account == "credential_rejected"


class TokenRefused(EngineError):
    """The token endpoint of an exchange refused: `status_code`, `error` (the OAuth error code of its body, RFC 6749
    § 5.2, when readable), `marks_account` (`credential_rejected` when a service account's grant is no longer valid,
    `invalid_grant`). Nothing else of the body is read."""

    def __init__(self, message: str, *, status_code: int, error: Optional[str] = None,
                 marks_account: Optional[str] = None):
        self.status_code = status_code
        self.error = error
        self.marks_account = marks_account
        self.retryable = False
        super().__init__(message)

    @property
    def credential_rejected(self) -> bool:
        """The refusal says the credential no longer authenticates."""
        return self.marks_account == "credential_rejected"


class UnreadableAnswer(EngineError):
    """A success whose body cannot be read: not JSON, larger than the engine reads, or a token answer without its
    token."""


class Unreachable(EngineError):
    """The vendor did not answer: a network failure, or no answer within the delay (`timed_out`)."""

    def __init__(self, message: str, *, timed_out: bool):
        self.timed_out = timed_out
        super().__init__(message)
