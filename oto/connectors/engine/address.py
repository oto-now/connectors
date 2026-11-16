"""The address of a call: the settings of the account, the templates that cite them, and the guard of an address that
comes from a setting.

`base_url` is a template (a fixed address included) whose `{name}` are settings of the connector; `base_urls` gives
one address per value of a `choice` setting; `token_url` and `token_urls` of a token exchange read the same way. A
`url` or `text` setting brings its host from what an administrator typed: the address is then guarded at each request
(`guard`), https only, never a private, loopback, link-local or reserved host, no redirect followed (the transport
follows none). A `choice` setting only picks among declared values.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional, Union
from urllib.parse import urlsplit

from oto.tools.common.url_guard import assert_public_https_url

from .errors import AccountSettingError, AddressRefused

#: What a `text` or `choice` value may hold to enter an address: letters, digits, `-` and `_`.
SUBSTITUTABLE = re.compile(r"^[A-Za-z0-9_-]+$")
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
#: At most, in characters, a setting value.
SETTING_VALUE_MAX = 2000

#: A declared address: a template, or `{setting, values}`, one address per value of a `choice` setting.
AddressSpec = Union[str, dict]


@dataclass(frozen=True)
class Address:
    """A resolved address; `guarded`: its host comes from a setting typed by an administrator."""

    url: str
    guarded: bool


def _https_problem(value: str) -> Optional[str]:
    """The problem of an address typed for a `url` setting: https, no credentials, query, fragment nor trailing
    slash. Whether its host is public is judged at each request (`guard`)."""
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        return "must start with https://"
    if "@" in parts.netloc or parts.query or parts.fragment or value.endswith("/") or "?" in value or "#" in value:
        return "must have no credentials, query, fragment nor trailing slash"
    return None


def _value_problem(setting: dict, value: object) -> Optional[str]:
    if not isinstance(value, str):
        return "is not a text"
    if len(value) > SETTING_VALUE_MAX:
        return f"is longer than {SETTING_VALUE_MAX} characters"
    if setting["type"] == "url":
        return _https_problem(value)
    if not SUBSTITUTABLE.match(value):
        return "may only hold letters, digits, - and _"
    if setting["type"] == "choice":
        return None if value in setting["choices"] else f"must be one of {', '.join(setting['choices'])}"
    if setting["type"] == "text":
        return None if re.search(setting["pattern"], value) else f"must match {setting['pattern']}"
    return f"has a type the engine does not know ({setting['type']})"


def account_settings(connector: dict, given: Optional[Mapping[str, str]]) -> dict[str, str]:
    """The settings of the account as the call reads them: each declared setting, its value given or the default of a
    `choice`; `AccountSettingError` for the first one missing or invalid. A setting the connector does not declare is
    refused too: a typo would otherwise pass unnoticed."""
    given = dict(given or {})
    declared = {setting["name"]: setting for setting in connector.get("settings", [])}
    unknown = sorted(set(given) - set(declared))
    if unknown:
        raise AccountSettingError(unknown[0], "is not a setting of this connector")
    settings: dict[str, str] = {}
    for name, setting in declared.items():
        value = given.get(name, setting.get("default"))
        if value is None:
            raise AccountSettingError(name, "is not set")
        problem = _value_problem(setting, value)
        if problem is not None:
            raise AccountSettingError(name, problem)
        settings[name] = value
    return settings


def base_address(connector: dict) -> AddressSpec:
    """The declared base address of a connector: `base_urls`, or `base_url`."""
    return connector["base_urls"] if "base_urls" in connector else connector["base_url"]


def resolve(spec: AddressSpec, settings: Mapping[str, str], connector: dict) -> Address:
    """The address resolved on the settings of the account (already checked by `account_settings`); guarded as soon as
    a `url` or `text` setting enters it."""
    if isinstance(spec, dict):
        return Address(url=spec["values"][settings[spec["setting"]]], guarded=False)
    types = {setting["name"]: setting["type"] for setting in connector.get("settings", [])}
    guarded = False

    def substitute(match: re.Match) -> str:
        nonlocal guarded
        name = match.group(1)
        if name not in types:
            raise AccountSettingError(name, "is cited by an address but is not a setting of this connector")
        if types[name] != "choice":
            guarded = True
        return settings[name]

    return Address(url=PLACEHOLDER.sub(substitute, spec), guarded=guarded)


def guard(url: str) -> None:
    """`AddressRefused` unless `url` is https and its host resolves to public addresses only. Called before each
    request to a guarded address; the address is never in the message."""
    try:
        assert_public_https_url(url)
    except ValueError:
        raise AddressRefused("The address of this account is not a public https address: refused. Ask whoever "
                             "manages the account to fix its settings.") from None
