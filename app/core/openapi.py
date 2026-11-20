"""OpenAPI document loading, local ``$ref`` resolution and schema normalisation.

Only local references (``#/...``) are supported. Remote references would make a
scan depend on arbitrary third-party hosts at diff time, so they are rejected
with an explicit error instead of silently producing a wrong diff.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

HTTP_METHODS: tuple[str, ...] = (
    "get",
    "put",
    "post",
    "delete",
    "patch",
    "head",
    "options",
    "trace",
)

MAX_REF_DEPTH = 50


class SpecError(ValueError):
    """Raised when a document is not a usable OpenAPI 3.x spec."""


@dataclass(frozen=True)
class ParsedSpec:
    document: dict[str, Any]
    title: str
    version: str
    raw: str
    sha256: str

    @property
    def openapi_version(self) -> str:
        return str(self.document.get("openapi", ""))


def parse_spec(raw: str | bytes) -> ParsedSpec:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:  # pragma: no cover - defensive
            raise SpecError("spec is not valid UTF-8") from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        try:
            document = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise SpecError(f"spec is neither valid JSON nor valid YAML: {exc}") from exc

    if not isinstance(document, dict):
        raise SpecError("spec root must be an object")

    openapi = str(document.get("openapi", ""))
    if not openapi.startswith("3."):
        raise SpecError(f"only OpenAPI 3.x is supported, got openapi={openapi!r}")
    if not isinstance(document.get("paths"), dict):
        raise SpecError("spec has no 'paths' object")

    info = document.get("info") or {}
    return ParsedSpec(
        document=document,
        title=str(info.get("title") or "Untitled API"),
        version=str(info.get("version") or ""),
        raw=raw,
        sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


def load_spec_file(path: str | Path) -> ParsedSpec:
    return parse_spec(Path(path).read_bytes())


def resolve_pointer(document: dict[str, Any], ref: str) -> Any:
    """Resolve a local JSON pointer such as ``#/components/schemas/Pet``."""
    if not ref.startswith("#/"):
        raise SpecError(f"only local $ref values are supported, got {ref!r}")

    node: Any = document
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            try:
                node = node[int(token)]
            except (ValueError, IndexError) as exc:
                raise SpecError(f"unresolvable $ref {ref!r}") from exc
        elif isinstance(node, dict) and token in node:
            node = node[token]
        else:
            raise SpecError(f"unresolvable $ref {ref!r}")
    return node


def deref(node: Any, document: dict[str, Any]) -> Any:
    """Follow a chain of local ``$ref`` values, preserving sibling keys."""
    depth = 0
    while isinstance(node, dict) and "$ref" in node:
        depth += 1
        if depth > MAX_REF_DEPTH:
            raise SpecError(f"$ref chain deeper than {MAX_REF_DEPTH} (cycle?)")
        target = resolve_pointer(document, str(node["$ref"]))
        if not isinstance(target, dict):
            return target
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        node = {**target, **siblings} if siblings else target
    return node


def schema_types(schema: dict[str, Any]) -> set[str]:
    """Return the set of JSON types a (normalised) schema accepts."""
    raw = schema.get("type")
    types: set[str] = set()
    if isinstance(raw, list):
        types.update(str(t) for t in raw)
    elif raw:
        types.add(str(raw))
    elif "properties" in schema:
        types.add("object")
    elif "items" in schema:
        types.add("array")
    if schema.get("nullable") is True:
        types.add("null")
    return types


def normalize_schema(schema: Any, document: dict[str, Any]) -> dict[str, Any]:
    """Resolve a schema, flatten ``allOf`` and normalise the nullable flag.

    The result is a shallow, comparable view: nested properties are left in
    place and are normalised lazily during recursion.
    """
    resolved = deref(schema, document)
    if not isinstance(resolved, dict):
        return {}

    if "allOf" in resolved and isinstance(resolved["allOf"], list):
        merged: dict[str, Any] = {
            k: v for k, v in resolved.items() if k not in ("allOf", "properties", "required")
        }
        properties: dict[str, Any] = {}
        required: list[str] = []

        branches = [resolved] if resolved.get("properties") else []
        branches += list(resolved["allOf"])
        for branch in branches:
            part = normalize_schema(branch, document)
            properties.update(part.get("properties") or {})
            for name in part.get("required") or []:
                if name not in required:
                    required.append(name)
            for key, value in part.items():
                if key not in ("properties", "required", "allOf"):
                    merged.setdefault(key, value)

        if properties:
            merged["properties"] = properties
        if required:
            merged["required"] = required
        resolved = merged

    if resolved.get("type") is None and "properties" in resolved:
        resolved = {**resolved, "type": "object"}

    return resolved


def enum_values(schema: dict[str, Any]) -> set[str] | None:
    """Canonicalise enum values so mixed-type enums compare correctly."""
    values = schema.get("enum")
    if not isinstance(values, list):
        return None
    return {json.dumps(value, sort_keys=True, default=str) for value in values}


def canonical(schema: Any) -> str:
    return json.dumps(schema, sort_keys=True, default=str)


def is_ref(node: Any) -> bool:
    return isinstance(node, dict) and isinstance(node.get("$ref"), str)