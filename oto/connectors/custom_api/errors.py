"""The standard error table of a Custom API: what the import and the takeover of oto 1 propose, written out in the
definition, where the admin reads and changes it (docs/conception/signal-de-refus.md for the semantics).

A 401 marks the account (`credential_rejected`) when the account has a credential to refuse: the host shows it red
until its next success. Without authentication (`none`), nothing marks.
"""

from __future__ import annotations


def standard_errors(label: str, auth_kind: str) -> list[dict]:
    """The table for a tool named `label`, authenticated by `auth_kind`."""
    unauthorized = {
        "status": 401,
        "code": "unauthorized",
        "retryable": False,
        "message": f"{label} refuses the credential of this account: it must be set again.",
    }
    if auth_kind == "oauth2_user":
        unauthorized.update(
            code="authorization_expired",
            marks_account="credential_rejected",
            message=f"{label} no longer accepts this authorization: the person must connect again.",
        )
    elif auth_kind != "none":
        unauthorized["marks_account"] = "credential_rejected"
    else:
        unauthorized["message"] = f"{label} requires an authentication this definition does not declare."
    return [
        {
            "status": [400, 422],
            "code": "invalid_request",
            "retryable": False,
            "message": f"{label} rejected the request: check the arguments.",
        },
        unauthorized,
        {
            "status": 403,
            "code": "forbidden",
            "retryable": False,
            "message": f"{label} refused: this account lacks the right for this object or action.",
        },
        {"status": 404, "code": "not_found", "retryable": False, "message": f"{label} object not found."},
        {
            "status": 429,
            "code": "rate_limited",
            "retryable": True,
            "message": f"{label} rate limit reached: retry later.",
        },
        {
            "status": [500, 502, 503, 504],
            "code": "upstream_unavailable",
            "retryable": True,
            "message": f"{label} is temporarily unavailable: retry later.",
        },
    ]
