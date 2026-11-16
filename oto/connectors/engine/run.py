"""A call of a described function, page by page: what `oto.connectors.engine.call` runs.

In order: the function found, and refused whole if it declares a form the engine does not run (`support`); the access
level resolved, and the function refused if the level does not serve it (`levels`); the checks
of its arguments; the settings of the account and the address; then each page: the request built and signed, sent,
its answer read (`answer`) and checked (`expect`), the cursor of the next page read (`serve.next_cursor`) and placed
by `serve.call_arguments`, until `all_pages`/`max_pages` (`serve.page_count`) or the last page.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

import requests

from .. import serve
from . import answer, levels, request, transport
from .address import account_settings, base_address, resolve
from .auth import fingerprint, signer
from .checks import run_checks, run_expectations
from .support import refuse_unsupported


@dataclass(frozen=True)
class Pages:
    """The answers of a call, one per page read, as the vendor sent them, and the cursor of the next page (`None` at
    the last one)."""

    answers: list
    next_cursor: Any


def _timeout(connector: dict, fn: dict) -> float:
    return fn.get("timeout_s", connector.get("timeout_s", transport.DEFAULT_TIMEOUT_S))


def _session() -> requests.Session:
    """A session of the engine's own: it reads nothing of the environment (`trust_env`: no proxy variable, no
    `.netrc`, where a secret could lie)."""
    session = requests.Session()
    session.trust_env = False
    return session


def execute(description: dict, function: str, arguments: Mapping[str, Any], *, credential: Mapping[str, str],
            settings: Optional[Mapping[str, str]] = None, session: Optional[requests.Session] = None,
            access_level: Optional[str] = None) -> Pages:
    """Run the function and return its raw answers (see `oto.connectors.engine.call`)."""
    fn = serve.function(description, function)
    refuse_unsupported(description, fn)
    connector = description["connector"]
    level = levels.resolve(connector, access_level)
    levels.refuse_unserved(connector, fn, level)
    args = dict(arguments)
    run_checks(fn, args)
    settings = account_settings(connector, settings)
    address = resolve(base_address(connector), settings, connector)
    target = transport.Target(label=connector["label"], base_url=address.url, timeout_s=_timeout(connector, fn),
                              rate=connector.get("rate_limit"), pace_key=fingerprint(connector, credential),
                              guarded=address.guarded)
    own = session is None
    session = _session() if own else session
    try:
        sign = signer(connector, credential, settings=settings, session=session,
                      timeout_s=connector.get("timeout_s", transport.DEFAULT_TIMEOUT_S), level=level)
        answers: list = []
        cursor = None
        for _ in range(serve.page_count(fn, args)):
            page = serve.call_arguments(fn, args, cursor)

            def signed(fresh: bool, page: dict = page) -> request.Request:
                return request.build(connector, fn, page, sign.sign(fresh))

            read = answer.read(session, target, signed(False), description=description, fn=fn, secrets=sign.secrets,
                               resign=(lambda: signed(True)) if sign.renews else None)
            run_expectations(fn, read)
            answers.append(read)
            cursor = serve.next_cursor(fn, read)
            if cursor is None:
                break
        return Pages(answers=answers, next_cursor=cursor)
    finally:
        if own:
            session.close()


def call(description: dict, function: str, arguments: Mapping[str, Any], *, credential: Mapping[str, str],
         settings: Optional[Mapping[str, str]] = None, session: Optional[requests.Session] = None,
         access_level: Optional[str] = None) -> dict:
    """Run the function and return what the caller receives (`serve.result`)."""
    pages = execute(description, function, arguments, credential=credential, settings=settings, session=session,
                    access_level=access_level)
    return serve.result(serve.function(description, function), pages.answers, pages.next_cursor)
