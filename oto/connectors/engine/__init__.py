"""The engine: runs a function of a description, as oto-core's engine runs it for oto 2 (docs/conception/fabrique.md,
« Le moteur Python »).

    from oto.connectors import descriptions, engine

    result = engine.call(descriptions.load("serper"), "search", {"q": "oto"}, credential={"api_key": key})

`call(description, function, arguments, *, credential, settings=None, session=None, access_level=None) -> dict`:

- `description`: a served description (`oto.connectors.descriptions.load`), or any description in the same form;
- `function`: the name of one of its functions; `arguments`: its arguments, already validated by the host against the
  function's served input (JSON Schema 2020-12), pagination's `all_pages` and `max_pages` included;
- `credential`: the value of each credential field (a service account's whole JSON key file in its one field) of the connector, by name, the fields `holder: host` merged in by
  the host. The engine reads no secret anywhere else, neither environment nor file;
- `settings`: the account's settings, by name (a `choice` absent takes its default);
- `access_level`: the access level of the account, as its organisation or team set it (`auth.access_levels`); `None`
  takes `auth.default_level`, else `full`. A level the description does not declare raises `UnknownAccessLevel`; a
  function the level does not serve is refused, `access_level_denied`. A service account claims the scopes of the
  level in its assertion;
- `session`: the `requests.Session` to send with (a test passes a fake one); without it, a session of the engine's
  own, which reads nothing of the environment.

It returns what the caller receives (`serve.result`): with `output.items`, `{items, next_cursor, pages}`; otherwise the
first answer shaped, `{}` for no content, `{"value": …}` for a value that is not an object. `execute` takes the same
arguments and returns the raw answers and the cursor (`Pages`), for a host that reads them itself (the probe,
`serve.probe_health`).

What it raises is in `oto.connectors.engine.errors`; the mark of an account (`marks_account`), its lifting at the first
success and the cost of a call stay with the host.
"""
from __future__ import annotations

from oto.tools.common.credentials import MissingCredential
from oto.tools.common.errors import UpstreamHTTPError

from .errors import (AccountSettingError, AddressRefused, EngineError, InvalidArguments, Refused, TokenRefused,
                     Unreachable, UnreadableAnswer, Unsupported)
from .levels import UnknownAccessLevel
from .run import Pages, call, execute

__all__ = [
    "AccountSettingError", "AddressRefused", "EngineError", "InvalidArguments", "MissingCredential", "Pages", "Refused",
    "TokenRefused", "UnknownAccessLevel", "Unreachable", "UnreadableAnswer", "UpstreamHTTPError", "Unsupported", "call",
    "execute",
]
