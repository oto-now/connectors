"""The 3CX XAPI schema — what a PBX exposes, read from its own `$metadata`.

The Configuration API (`/xapi/v1`, OData v4) serves a few hundred resources:
entity sets (`Users`, `Queues`, `InboundRules`…), singletons (settings such as
`VoicemailSettings`), and bound or unbound operations — actions (POST, they
act) and functions (GET, they read). Their list depends on the PBX version, so
it is never hard-coded here: `Schema.parse` reads the PBX's `$metadata`, and
every path a caller asks for is checked against it before any request
(`Schema.path`). An unknown resource, key, navigation or operation is a
`ValueError` naming what exists, never a 404 from the PBX.

Pure: no network, no credential. The document comes from the PBX, so it is
parsed with `defusedxml` (no entity expansion).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from defusedxml.ElementTree import fromstring as _safe_fromstring

_EDM = "{http://docs.oasis-open.org/odata/ns/edm}"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NUMERIC = {"Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.Byte", "Edm.SByte",
            "Edm.Decimal", "Edm.Double", "Edm.Single"}
# Properties whose value is a credential: masked in what is read, refused in
# what is written. Matched on the property NAME; a boolean flag about a secret
# (`AllowChangePassword`, `UseSeparateAuthId`) carries no secret and is a bool,
# which `mask` never touches.
SECRET_NAME = re.compile(
    r"(password|secret|apikey|accesskey|licensekey|token|vmpin|pinnumber|^authid$|^key$"
    r"|provlink|provisionlink|provisioninglink|provisionurl|^certificate$)", re.I)
# Operations that hand out or replace a credential (a phone's SIP secret, a
# provisioning link, an API key, an OAuth token, a licence key): never called
# through oto, whatever the rights of the account.
SECRET_OPERATION = re.compile(
    r"(password|secret|token|apikey|provlink|oauth|licensekey|credential)", re.I)
MASK = "***"


@dataclass(frozen=True)
class EntityType:
    name: str
    key: Tuple[Tuple[str, str], ...]          # ((name, Edm type), …)
    properties: Dict[str, str]                # name → type
    navigations: Dict[str, str]               # name → type (Collection(Pbx.X) or Pbx.X)


@dataclass(frozen=True)
class Operation:
    name: str
    kind: str                                  # "action" | "function"
    bound_to: Optional[str]                    # "Pbx.User", "Collection(Pbx.User)", None
    parameters: Tuple[Tuple[str, str], ...]    # without the binding parameter
    returns: Optional[str]


@dataclass
class Schema:
    namespace: str
    types: Dict[str, EntityType]
    enums: Dict[str, Tuple[str, ...]]
    sets: Dict[str, str]                       # entity set → type name (unqualified)
    singletons: Dict[str, str]
    operations: List[Operation] = field(default_factory=list)

    # ------------------------------------------------------------------
    # parsing
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, xml: bytes | str) -> "Schema":
        root = _safe_fromstring(xml)  # the PBX's document: untrusted XML
        schema = root.find(f".//{_EDM}Schema")
        if schema is None:
            raise ValueError("3CX $metadata without a Schema element.")
        ns = schema.get("Namespace") or ""
        raw = {e.get("Name"): e for e in schema.findall(f"{_EDM}EntityType")}
        raw.update({e.get("Name"): e for e in schema.findall(f"{_EDM}ComplexType")})

        def chain(name: str) -> List[ET.Element]:
            out, seen = [], set()
            while name and name in raw and name not in seen:
                seen.add(name)
                out.append(raw[name])
                name = (raw[name].get("BaseType") or "").split(".")[-1]
            return out[::-1]  # base first, so a subtype overrides

        types: Dict[str, EntityType] = {}
        for name in raw:
            props: Dict[str, str] = {}
            navs: Dict[str, str] = {}
            key: List[str] = []
            for el in chain(name):
                props.update({p.get("Name"): p.get("Type") for p in el.findall(f"{_EDM}Property")})
                navs.update({p.get("Name"): p.get("Type")
                             for p in el.findall(f"{_EDM}NavigationProperty")})
                refs = [r.get("Name") for r in el.findall(f"{_EDM}Key/{_EDM}PropertyRef")]
                key = refs or key
            types[name] = EntityType(name, tuple((k, props.get(k, "Edm.String")) for k in key),
                                     props, navs)
        enums = {e.get("Name"): tuple(m.get("Name") for m in e.findall(f"{_EDM}Member"))
                 for e in schema.findall(f"{_EDM}EnumType")}
        container = root.find(f".//{_EDM}EntityContainer")
        sets = {s.get("Name"): s.get("EntityType").split(".")[-1]
                for s in container.findall(f"{_EDM}EntitySet")} if container is not None else {}
        singles = {s.get("Name"): s.get("Type").split(".")[-1]
                   for s in container.findall(f"{_EDM}Singleton")} if container is not None else {}
        ops: List[Operation] = []
        for kind in ("Action", "Function"):
            for el in schema.findall(f"{_EDM}{kind}"):
                params = [(p.get("Name"), p.get("Type")) for p in el.findall(f"{_EDM}Parameter")]
                bound = el.get("IsBound") == "true" and params
                ret = el.find(f"{_EDM}ReturnType")
                ops.append(Operation(
                    el.get("Name"), kind.lower(), params[0][1] if bound else None,
                    tuple(params[1:] if bound else params),
                    ret.get("Type") if ret is not None else None))
        return cls(ns, types, enums, sets, singles, ops)

    # ------------------------------------------------------------------
    # lookups
    # ------------------------------------------------------------------

    def resource_type(self, resource: str) -> EntityType:
        name = self.sets.get(resource) or self.singletons.get(resource)
        if name is None:
            raise ValueError(f"3CX has no resource {resource!r} — "
                             "threecx_config(op='catalog') lists them.")
        return self.types[name]

    def is_singleton(self, resource: str) -> bool:
        return resource in self.singletons

    def operations_of(self, resource: str, *, on_entity: bool) -> List[Operation]:
        """The operations bound to one entity of `resource` (`on_entity=True`)
        or to the resource itself (its collection, or the singleton)."""
        t = f"{self.namespace}.{self.resource_type(resource).name}"
        if self.is_singleton(resource):
            want = {t}
        else:
            want = {t} if on_entity else {f"Collection({t})"}
        return [o for o in self.operations if o.bound_to in want]

    def operation(self, name: str, resource: Optional[str], *, on_entity: bool) -> Operation:
        pool = (self.operations_of(resource, on_entity=on_entity) if resource
                else [o for o in self.operations if o.bound_to is None])
        if SECRET_OPERATION.search(name):
            raise ValueError(f"{name} hands out or replaces a credential: it is never called "
                             "through oto — use the 3CX admin console.")
        found = [o for o in pool if o.name == name]
        if not found:
            where = (f"{resource}({'key' if on_entity else ''})" if resource else "the root")
            raise ValueError(f"3CX has no operation {name!r} on {where} — available: "
                             f"{sorted({o.name for o in pool})[:60]}")
        return found[0]

    def describe(self, resource: str) -> Dict[str, Any]:
        """What a caller needs to read or write `resource`: its key, properties
        (enum types with their values), navigations and operations."""
        et = self.resource_type(resource)

        def typ(t: str, depth: int = 0) -> Any:
            base = t[len("Collection("):-1] if t.startswith("Collection(") else t
            short = base.split(".")[-1]
            if not base.startswith(f"{self.namespace}."):
                return t
            if short in self.enums:
                return {"type": t, "values": list(self.enums[short])}
            inner = self.types.get(short)
            if inner is not None and not inner.key and depth == 0:
                # A complex value (a destination, office hours…): its fields, one level.
                return {"type": t, "properties": {n: typ(x, 1)
                                                  for n, x in inner.properties.items()}}
            return t

        def ops(on_entity: bool) -> List[Dict[str, Any]]:
            return [{"name": o.name, "kind": o.kind,
                     "parameters": {n: typ(p) for n, p in o.parameters},
                     **({"returns": o.returns} if o.returns else {})}
                    for o in self.operations_of(resource, on_entity=on_entity)
                    if not SECRET_OPERATION.search(o.name)]

        out: Dict[str, Any] = {
            "resource": resource,
            "kind": "singleton" if self.is_singleton(resource) else "collection",
            "type": et.name,
            "properties": {n: typ(t) for n, t in et.properties.items()},
            "navigations": dict(et.navigations),
        }
        if self.is_singleton(resource):
            out["operations"] = ops(True)
        else:
            out["key"] = {n: t for n, t in et.key}
            out["operations_on_collection"] = ops(False)
            out["operations_on_entity"] = ops(True)
        return out

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------

    def key_segment(self, resource: str, key: Any) -> str:
        """`(5)`, `('1001')`, or `(A='x',B=3)` — typed from the schema."""
        et = self.resource_type(resource)
        if self.is_singleton(resource):
            raise ValueError(f"{resource} is a singleton: it takes no key.")
        if not et.key:
            raise ValueError(f"3CX declares no key for {resource}: address it as a "
                             "collection, or through an operation.")
        if len(et.key) == 1 and not isinstance(key, dict):
            (_, typ), = et.key
            return f"({literal(key, typ)})"
        if not isinstance(key, dict) or set(key) != {n for n, _ in et.key}:
            raise ValueError(f"{resource} has a composite key: pass key as "
                             f"{{{', '.join(n for n, _ in et.key)}}}.")
        return "(" + ",".join(f"{n}={literal(key[n], t)}" for n, t in et.key) + ")"

    def path(self, resource: str, *, key: Any = None, navigation: Optional[str] = None,
             operation: Optional[Operation] = None, arguments: Optional[dict] = None) -> str:
        """The XAPI path for a resource, one of its entities, a navigation of
        that entity, and/or a bound operation — every segment checked."""
        if operation is not None and resource is None:
            return f"/{operation.name}" + (_call_args(operation, arguments)
                                           if operation.kind == "function" else "")
        et = self.resource_type(resource)
        out = f"/{resource}"
        if key is not None:
            out += self.key_segment(resource, key)
        if navigation is not None:
            if key is None and not self.is_singleton(resource):
                raise ValueError("navigation needs the key of the entity it starts from.")
            if navigation not in et.navigations:
                raise ValueError(f"{et.name} has no navigation {navigation!r} — available: "
                                 f"{sorted(et.navigations)}")
            out += f"/{navigation}"
        if operation is not None:
            out += f"/{self.namespace}.{operation.name}"
            if operation.kind == "function":
                out += _call_args(operation, arguments)
        return out

    def check_body(self, resource: str, body: dict, *, navigation: Optional[str] = None) -> None:
        """A body may only name properties or navigations of the target type,
        and never a credential."""
        if not isinstance(body, dict) or not body:
            raise ValueError("body must be a non-empty object of property → value.")
        et = self.resource_type(resource)
        if navigation is not None:
            nav = et.navigations.get(navigation) or ""
            short = nav.replace("Collection(", "").rstrip(")").split(".")[-1]
            et = self.types.get(short, et)
        unknown = sorted(k for k in body if k not in et.properties and k not in et.navigations
                         and not k.startswith("@odata."))
        if unknown:
            raise ValueError(f"{et.name} has no property {unknown} — "
                             f"threecx_config(op='schema', resource='{resource}') lists them.")
        secrets = secret_paths(body)
        if secrets:
            raise ValueError(f"credentials are never written through oto ({secrets}): "
                             "set them in the 3CX admin console.")


def literal(value: Any, typ: str) -> str:
    """An OData literal for a key or function argument of type `typ`."""
    if typ in _NUMERIC:
        if isinstance(value, bool) or not re.fullmatch(r"-?\d+(\.\d+)?", str(value)):
            raise ValueError(f"expected a number ({typ}), received {value!r}.")
        return str(value)
    if typ == "Edm.Boolean":
        if isinstance(value, bool):
            return "true" if value else "false"
        if str(value).lower() in ("true", "false"):
            return str(value).lower()
        raise ValueError(f"expected a boolean, received {value!r}.")
    if typ in ("Edm.DateTimeOffset", "Edm.Guid", "Edm.Date"):
        text = str(value)
        if not re.fullmatch(r"[0-9A-Za-z:.+\-]+", text):
            raise ValueError(f"expected a {typ} literal, received {value!r}.")
        return quote(text, safe=":.-")
    text = str(value)
    return "'" + quote(text.replace("'", "''"), safe="") + "'"


def _call_args(op: Operation, arguments: Optional[dict]) -> str:
    arguments = dict(arguments or {})
    names = {n for n, _ in op.parameters}
    unknown = sorted(set(arguments) - names)
    if unknown:
        raise ValueError(f"{op.name} takes no argument {unknown} — it takes {sorted(names)}.")
    missing = sorted(n for n, _ in op.parameters if n not in arguments)
    if missing:
        raise ValueError(f"{op.name} needs {missing}.")
    return "(" + ",".join(f"{n}={literal(arguments[n], t)}" for n, t in op.parameters) + ")"


def check_identifier(value: str, what: str) -> str:
    if not isinstance(value, str) or not _IDENT.match(value):
        raise ValueError(f"{what} must be a 3CX name such as Users or InboundRules — "
                         f"received {value!r}.")
    return value


def secret_paths(value: Any, prefix: str = "") -> List[str]:
    """The paths of every non-empty credential in `value` (nested objects and
    lists included)."""
    found: List[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(k, str) and SECRET_NAME.search(k) and isinstance(v, str) and v:
                found.append(here)
            else:
                found.extend(secret_paths(v, here))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found.extend(secret_paths(v, f"{prefix}[{i}]"))
    return found


def mask(value: Any) -> Any:
    """`value` with every credential string replaced by `***`."""
    if isinstance(value, dict):
        return {k: (MASK if isinstance(k, str) and SECRET_NAME.search(k)
                    and isinstance(v, str) and v else mask(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value
