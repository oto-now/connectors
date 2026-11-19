"""The references and schemas of an OpenAPI document, read as data: a local `$ref` is followed on 8 levels at most, and
anything else is refused by name, never replaced by an empty schema in silence.

`inline(document, node)` returns the node with every `$ref` replaced by its target, written in JSON Schema 2020-12:
`nullable: true` (OpenAPI 3.0) becomes a `null` type, and the words of OpenAPI that are not JSON Schema (`example`,
`discriminator`, `xml`, `externalDocs`, `x-…`) leave. `RefRefused` names what stops it:

- `openapi_ref_external`: a reference outside the document (another file, a URL);
- `openapi_ref_too_deep`: more than 8 references followed one inside the other, a cycle included;
- `openapi_ref_missing`: a local reference to nothing.
"""

from __future__ import annotations

from typing import Any

MAX_REF_DEPTH = 8
REF_EXTERNAL = "openapi_ref_external"
REF_TOO_DEEP = "openapi_ref_too_deep"
REF_MISSING = "openapi_ref_missing"
#: OpenAPI words of a schema that JSON Schema 2020-12 does not have.
NOT_JSON_SCHEMA = ("nullable", "example", "discriminator", "xml", "externalDocs")


class RefRefused(ValueError):
    def __init__(self, code: str, ref: object, reason: str):
        super().__init__(f"{ref!r}: {reason}")
        self.code = code
        self.reason = f"$ref {ref!r}: {reason}"


def resolve(document: dict, node: Any, depth: int = 0) -> tuple[Any, int]:
    """The node a chain of references leads to, and the depth reached."""
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise RefRefused(REF_EXTERNAL, ref, "only references inside the document are followed")
        if depth >= MAX_REF_DEPTH:
            raise RefRefused(REF_TOO_DEEP, ref, f"more than {MAX_REF_DEPTH} references followed one inside the other")
        target: Any = document
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(target, dict) and part in target:
                target = target[part]
            elif isinstance(target, list) and part.isdigit() and int(part) < len(target):
                target = target[int(part)]
            else:
                raise RefRefused(REF_MISSING, ref, "the document has nothing there")
        node, depth = target, depth + 1
    return node, depth


def _with_null(schema: dict) -> dict:
    kind = schema.get("type")
    if isinstance(kind, str):
        schema["type"] = [kind, "null"]
    elif isinstance(kind, list) and "null" not in kind:
        schema["type"] = kind + ["null"]
    return schema


def inline(document: dict, node: Any, depth: int = 0) -> Any:
    """`node` with its references replaced, in JSON Schema 2020-12 (module docstring)."""
    node, depth = resolve(document, node, depth)
    if isinstance(node, list):
        return [inline(document, item, depth) for item in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in NOT_JSON_SCHEMA or (isinstance(key, str) and key.startswith("x-")):
            continue
        if key == "examples" and not isinstance(value, list):
            continue
        if key in ("enum", "const", "default", "examples"):
            out[key] = value
        elif key == "properties" and isinstance(value, dict):
            out[key] = {name: inline(document, sub, depth) for name, sub in value.items()}
        else:
            out[key] = inline(document, value, depth)
    return _with_null(out) if node.get("nullable") is True else out


def example_of(document: dict, holder: Any) -> tuple[bool, Any]:
    """The example a parameter, a media type or a schema carries (`example`, or the first of `examples`), as
    `(found, value)`."""
    holder, _ = resolve(document, holder)
    if not isinstance(holder, dict):
        return False, None
    if "example" in holder:
        return True, holder["example"]
    examples = holder.get("examples")
    if isinstance(examples, dict) and examples:
        first, _ = resolve(document, next(iter(examples.values())))
        if isinstance(first, dict) and "value" in first:
            return True, first["value"]
    if isinstance(examples, list) and examples:
        return True, examples[0]
    if isinstance(holder.get("schema"), dict):
        return example_of(document, holder["schema"])
    return False, None
