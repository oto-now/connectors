"""What the engine runs, and the named refusal of the rest, judged before anything is sent.

Not run, to be written on demand (oto-now/connectors#206, lot 5): the files of a multipart body (`call.files`), a
rendered file (`output.file`), a background job (`background`), the consent of a person (`auth.kind: oauth2_user`), the
identity of a linked account (`connector.account_identity`), and a function written by hand (`handwritten`, no `call`).
A function that declares one is refused whole, never called halfway.
"""
from __future__ import annotations

from .errors import Unsupported

#: The kinds of `auth` the engine signs.
AUTH_KINDS = ("none", "api_key", "bearer", "basic", "oauth2_client_credentials", "oauth2_jwt_bearer")


def refuse_unsupported(description: dict, fn: dict) -> None:
    """`Unsupported` naming the first form of the function or its connector that the engine does not run."""
    connector = description["connector"]
    where = f"{connector['name']}.{fn['name']}"
    kind = connector["auth"]["kind"]
    if kind not in AUTH_KINDS:
        raise Unsupported(f"{where}: auth {kind} is not run by the Python engine (it runs {', '.join(AUTH_KINDS)}).")
    if "account_identity" in connector:
        raise Unsupported(f"{where}: the identity of a linked account (account_identity) is not run by the Python engine.")
    if "call" not in fn:
        raise Unsupported(f"{where}: a function without call (handwritten) is not run by the Python engine.")
    if "files" in fn["call"]:
        raise Unsupported(f"{where}: a multipart body (call.files) is not run by the Python engine.")
    if "file" in (fn.get("output") or {}):
        raise Unsupported(f"{where}: a rendered file (output.file) is not run by the Python engine.")
    if fn.get("background"):
        raise Unsupported(f"{where}: a background function is not run by the Python engine.")
