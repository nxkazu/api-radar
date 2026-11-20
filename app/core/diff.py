"""Semantic OpenAPI 3.x diff engine.

The engine walks both documents, resolves local references, and emits one
``Change`` per contract difference with a severity:

* ``breaking``     – existing clients can fail (removed endpoint, new required
                     request field, narrowed enum, removed response field, ...)
* ``risky``        – probably needs a code change or manual verification
                     (operationId rename, loosened response schema, ...)
* ``non_breaking`` – additive or informational change

Severity depends on direction: an enum value removed from a *request* schema is
breaking (clients still send it), while removing a value from a *response*
schema is safe. The same comparison routine is therefore reused with a
``direction`` argument instead of maintaining two engines.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from .openapi import (
    HTTP_METHODS,
    ParsedSpec,
    canonical,
    deref,
    enum_values,
    normalize_schema,
    schema_types,
)

Severity = Literal["breaking", "risky", "non_breaking"]

SEVERITY_RANK: dict[str, int] = {"breaking": 0, "risky": 1, "non_breaking": 2}

Direction = Literal["request", "response"]


def _has_refs(value: Any) -> bool:
    """True when a schema tree contains a ``$ref`` anywhere below it.

    Two shallowly-equal schemas can still resolve to different components in the
    base and head documents, so equality alone is not enough to stop recursion.
    """
    if isinstance(value, dict):
        return "$ref" in value or any(_has_refs(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_refs(item) for item in value)
    return False

FIX_HINTS: dict[str, str] = {
    "operation.removed": "Migrate or remove call-sites for this operation before adopting the new spec.",
    "operation.operation_id.changed": "Regenerate any generated client: the operationId changed.",
    "operation.deprecated": "Plan a migration: this operation is now marked deprecated.",
    "parameter.removed": "Remove this parameter from call-sites if the server rejects unknown parameters.",
    "parameter.required.added": "Send the now-required parameter in every call.",
    "parameter.style.changed": "Verify request serialisation; parameter style/explode changed.",
    "request_body.removed": "Stop sending a request body to this operation.",
    "request_body.required.added": "Send a request body for every call; it is now required.",
    "request_body.media_type.removed": "Switch call-sites to a supported media type.",
    "response.removed": "Handle the removed status code: it is no longer part of the contract.",
    "response.header.removed": "Stop depending on this response header.",
    "response.media_type.removed": "Update response parsing: the media type is no longer returned.",
    "schema.type.changed": "Update the client type mapping for this value.",
    "schema.property.removed": "Remove reads of this property; it is no longer part of the payload.",
    "schema.required.added": "Always provide this property.",
    "schema.enum.value.removed": "Replace the removed enum value with a supported alternative.",
    "schema.enum.value.added": "Update exhaustive enum handling for the new value.",
    "schema.constraint.tightened": "Validate inputs against the tighter constraint before calling.",
    "schema.format.changed": "Check serialisation/parsing for the changed format.",
    "schema.variants.changed": "Review the changed union variants and update narrowing logic.",
    "schema.additional_properties.restricted": "Stop sending undeclared properties.",
    "schema.nullable.added": "Handle null values for this field.",
    "schema.nullable.removed": "Stop sending or expecting null for this field.",
    "security.requirement.added": "Supply the new authentication requirement on every call.",
    "security.scheme.removed": "Switch to a supported authentication scheme.",
    "security.scheme.type_changed": "Update the authentication flow for the changed scheme.",
    "server.removed": "Point clients at a supported server URL.",
}


@dataclass
class Change:
    kind: str
    severity: Severity
    location: str
    pointer: str
    message: str
    before: Any = None
    after: Any = None
    fix_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "location": self.location,
            "pointer": self.pointer,
            "message": self.message,
            "before": self.before,
            "after": self.after,
            "fix_hint": self.fix_hint,
        }

    def fingerprint(self) -> str:
        payload = json.dumps(
            {k: getattr(self, k) for k in ("kind", "pointer", "message")}, sort_keys=True
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class DiffResult:
    changes: list[Change] = field(default_factory=list)
    base_title: str = ""
    head_title: str = ""
    base_version: str = ""
    head_version: str = ""

    @property
    def breaking(self) -> list[Change]:
        return [c for c in self.changes if c.severity == "breaking"]

    @property
    def risky(self) -> list[Change]:
        return [c for c in self.changes if c.severity == "risky"]

    @property
    def non_breaking(self) -> list[Change]:
        return [c for c in self.changes if c.severity == "non_breaking"]

    @property
    def has_breaking(self) -> bool:
        return any(c.severity == "breaking" for c in self.changes)

    def counts(self) -> dict[str, int]:
        return {
            "breaking": len(self.breaking),
            "risky": len(self.risky),
            "non_breaking": len(self.non_breaking),
            "total": len(self.changes),
        }

    def fingerprint(self) -> str:
        payload = "|".join(sorted(c.fingerprint() for c in self.changes))
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": {"title": self.base_title, "version": self.base_version},
            "head": {"title": self.head_title, "version": self.head_version},
            "counts": self.counts(),
            "has_breaking": self.has_breaking,
            "fingerprint": self.fingerprint(),
            "changes": [c.to_dict() for c in self.changes],
        }

    def to_markdown(self, **kwargs: Any) -> str:
        from .report import render_markdown

        return render_markdown(self, **kwargs)


class _Differ:
    def __init__(self, base: dict[str, Any], head: dict[str, Any]) -> None:
        self.base = base
        self.head = head
        self.changes: list[Change] = []

    # ------------------------------------------------------------------ utils
    def add(
        self,
        kind: str,
        severity: Severity,
        location: str,
        pointer: str,
        message: str,
        before: Any = None,
        after: Any = None,
    ) -> None:
        self.changes.append(
            Change(
                kind=kind,
                severity=severity,
                location=location,
                pointer=pointer,
                message=message,
                before=before,
                after=after,
                fix_hint=FIX_HINTS.get(kind),
            )
        )

    @staticmethod
    def _loc(location: str, detail: str) -> str:
        return f"{location} › {detail}" if detail else location

    @staticmethod
    def _pointer(pointer: str, detail: str) -> str:
        return f"{pointer}.{detail}" if detail else pointer

    @staticmethod
    def _severity(direction: Direction, request: Severity, response: Severity) -> Severity:
        return request if direction == "request" else response

    # ------------------------------------------------------------- top level
    def run(self) -> list[Change]:
        self._compare_document_meta()
        self._compare_servers()
        self._compare_security(self.base.get("security"), self.head.get("security"), "#.security")
        self._compare_security_schemes()
        self._compare_paths()
        seen: set[tuple[str, str, str]] = set()
        unique: list[Change] = []
        for change in self.changes:
            key = (change.kind, change.pointer, change.message)
            if key not in seen:
                seen.add(key)
                unique.append(change)
        return sorted(
            unique,
            key=lambda c: (SEVERITY_RANK[c.severity], c.location, c.kind, c.message),
        )

    def _compare_document_meta(self) -> None:
        base_openapi = str(self.base.get("openapi", ""))
        head_openapi = str(self.head.get("openapi", ""))
        if base_openapi != head_openapi:
            self.add(
                "spec.version.changed",
                "risky",
                "document",
                "#.openapi",
                f"OpenAPI version changed from {base_openapi} to {head_openapi}",
                base_openapi,
                head_openapi,
            )

        base_info = self.base.get("info") or {}
        head_info = self.head.get("info") or {}
        if str(base_info.get("version", "")) != str(head_info.get("version", "")):
            self.add(
                "info.version",
                "non_breaking",
                "document",
                "#.info.version",
                f"Spec version {base_info.get('version') or '?'} → {head_info.get('version') or '?'}",
                base_info.get("version"),
                head_info.get("version"),
            )
        if str(base_info.get("title", "")) != str(head_info.get("title", "")):
            self.add(
                "info.title",
                "non_breaking",
                "document",
                "#.info.title",
                f"API title changed from {base_info.get('title')!r} to {head_info.get('title')!r}",
            )

    def _compare_servers(self) -> None:
        def urls(document: dict[str, Any]) -> set[str]:
            return {
                str(s.get("url"))
                for s in document.get("servers") or []
                if isinstance(s, dict) and s.get("url")
            }

        base_urls = urls(self.base)
        head_urls = urls(self.head)
        for url in sorted(base_urls - head_urls):
            self.add(
                "server.removed",
                "risky",
                "document",
                "#.servers",
                f"Server URL removed: {url}",
                url,
            )
        for url in sorted(head_urls - base_urls):
            self.add(
                "server.added",
                "non_breaking",
                "document",
                "#.servers",
                f"Server URL added: {url}",
                None,
                url,
            )

    def _compare_security(self, base: Any, head: Any, pointer: str, location: str = "document") -> None:
        def requirements(value: Any) -> set[str]:
            if not isinstance(value, list):
                return set()
            return {canonical(item) for item in value}

        base_reqs = requirements(base)
        head_reqs = requirements(head)
        for req in sorted(head_reqs - base_reqs):
            self.add(
                "security.requirement.added",
                "breaking",
                location,
                pointer,
                f"New security requirement: {req}",
                None,
                json.loads(req),
            )
        for req in sorted(base_reqs - head_reqs):
            self.add(
                "security.requirement.removed",
                "non_breaking",
                location,
                pointer,
                f"Security requirement removed: {req}",
                json.loads(req),
                None,
            )

    def _compare_security_schemes(self) -> None:
        base_schemes = ((self.base.get("components") or {}).get("securitySchemes") or {})
        head_schemes = ((self.head.get("components") or {}).get("securitySchemes") or {})
        for name in sorted(set(base_schemes) - set(head_schemes)):
            self.add(
                "security.scheme.removed",
                "breaking",
                "document",
                f"#.components.securitySchemes.{name}",
                f"Security scheme removed: {name}",
                deref(base_schemes[name], self.base),
            )
        for name in sorted(set(base_schemes) & set(head_schemes)):
            old = deref(base_schemes[name], self.base)
            new = deref(head_schemes[name], self.head)
            if not isinstance(old, dict) or not isinstance(new, dict):
                continue
            if old.get("type") != new.get("type"):
                self.add(
                    "security.scheme.type_changed",
                    "breaking",
                    f"security scheme {name}",
                    f"#.components.securitySchemes.{name}.type",
                    f"Security scheme {name} type changed from {old.get('type')} to {new.get('type')}",
                    old.get("type"),
                    new.get("type"),
                )
            elif old.get("scheme") != new.get("scheme") or old.get("in") != new.get("in"):
                self.add(
                    "security.scheme.type_changed",
                    "risky",
                    f"security scheme {name}",
                    f"#.components.securitySchemes.{name}",
                    f"Security scheme {name} flow changed",
                    {k: old.get(k) for k in ("type", "scheme", "in", "bearerFormat")},
                    {k: new.get(k) for k in ("type", "scheme", "in", "bearerFormat")},
                )
        for name in sorted(set(head_schemes) - set(base_schemes)):
            self.add(
                "security.scheme.added",
                "non_breaking",
                "document",
                f"#.components.securitySchemes.{name}",
                f"Security scheme added: {name}",
            )

    # ---------------------------------------------------------------- paths
    def _compare_paths(self) -> None:
        base_paths = {k: v for k, v in (self.base.get("paths") or {}).items() if k.startswith("/")}
        head_paths = {k: v for k, v in (self.head.get("paths") or {}).items() if k.startswith("/")}

        for path in sorted(set(base_paths) | set(head_paths)):
            old_item = base_paths.get(path) or {}
            new_item = head_paths.get(path) or {}
            if not isinstance(old_item, dict):
                old_item = {}
            if not isinstance(new_item, dict):
                new_item = {}

            for method in HTTP_METHODS:
                old_op = old_item.get(method)
                new_op = new_item.get(method)
                location = f"{method.upper()} {path}"
                pointer = f"#.paths.{path}.{method}"

                if old_op and not new_op:
                    self.add(
                        "operation.removed",
                        "breaking",
                        location,
                        pointer,
                        f"{method.upper()} {path} was removed",
                    )
                elif new_op and not old_op:
                    self.add(
                        "operation.added",
                        "non_breaking",
                        location,
                        pointer,
                        f"{method.upper()} {path} was added",
                    )
                elif old_op and new_op:
                    self._compare_operation(
                        method, path, old_op, new_op, old_item, new_item, location, pointer
                    )

    def _parameters(self, item: Any, operation: Any, document: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        params: dict[tuple[str, str], dict[str, Any]] = {}
        sources = []
        if isinstance(item, dict):
            sources.append(item.get("parameters"))
        if isinstance(operation, dict):
            sources.append(operation.get("parameters"))
        for source in sources:
            for raw in source or []:
                param = deref(raw, document)
                if not isinstance(param, dict):
                    continue
                name, where = param.get("name"), param.get("in")
                if isinstance(name, str) and isinstance(where, str):
                    params[(where, name)] = param
        return params

    def _compare_operation(
        self,
        method: str,
        path: str,
        old_op: dict[str, Any],
        new_op: dict[str, Any],
        old_item: dict[str, Any],
        new_item: dict[str, Any],
        location: str,
        pointer: str,
    ) -> None:
        old_op = deref(old_op, self.base)
        new_op = deref(new_op, self.head)
        if not isinstance(old_op, dict) or not isinstance(new_op, dict):
            return

        old_id, new_id = old_op.get("operationId"), new_op.get("operationId")
        if old_id and new_id and old_id != new_id:
            self.add(
                "operation.operation_id.changed",
                "risky",
                location,
                f"{pointer}.operationId",
                f"operationId changed from {old_id!r} to {new_id!r}",
                old_id,
                new_id,
            )
        elif old_id and not new_id:
            self.add(
                "operation.operation_id.changed",
                "risky",
                location,
                f"{pointer}.operationId",
                f"operationId {old_id!r} was removed",
                old_id,
                None,
            )

        if not old_op.get("deprecated") and new_op.get("deprecated"):
            self.add(
                "operation.deprecated",
                "non_breaking",
                location,
                f"{pointer}.deprecated",
                f"{method.upper()} {path} is now marked deprecated",
            )

        self._compare_parameters(old_item, new_item, old_op, new_op, location, pointer)
        self._compare_request_body(old_op, new_op, location, pointer)
        self._compare_responses(old_op, new_op, location, pointer)
        self._compare_security(
            old_op.get("security"),
            new_op.get("security"),
            f"{pointer}.security",
            location,
        )

    # ----------------------------------------------------------- parameters
    def _compare_parameters(
        self,
        old_item: dict[str, Any],
        new_item: dict[str, Any],
        old_op: dict[str, Any],
        new_op: dict[str, Any],
        location: str,
        pointer: str,
    ) -> None:
        old_params = self._parameters(old_item, old_op, self.base)
        new_params = self._parameters(new_item, new_op, self.head)

        for key in sorted(set(old_params) | set(new_params)):
            where, name = key
            old_param = old_params.get(key)
            new_param = new_params.get(key)
            detail = f"parameter {where}.{name}"
            loc = self._loc(location, detail)
            ptr = self._pointer(pointer, f"parameters.{where}.{name}")

            if old_param and not new_param:
                self.add(
                    "parameter.removed",
                    "breaking" if where == "path" else "risky",
                    loc,
                    ptr,
                    f"{where} parameter {name!r} was removed",
                    old_param.get("required"),
                )
                continue
            if new_param and not old_param:
                if new_param.get("required"):
                    self.add(
                        "parameter.required.added",
                        "breaking",
                        loc,
                        ptr,
                        f"New required {where} parameter {name!r}",
                        None,
                        True,
                    )
                else:
                    self.add(
                        "parameter.added",
                        "non_breaking",
                        loc,
                        ptr,
                        f"New optional {where} parameter {name!r}",
                    )
                continue

            assert old_param is not None and new_param is not None
            if not old_param.get("required") and new_param.get("required"):
                self.add(
                    "parameter.required.added",
                    "breaking",
                    loc,
                    ptr,
                    f"{where} parameter {name!r} became required",
                    False,
                    True,
                )
            if old_param.get("style") != new_param.get("style") or old_param.get(
                "explode"
            ) != new_param.get("explode"):
                self.add(
                    "parameter.style.changed",
                    "risky",
                    loc,
                    ptr,
                    f"Serialisation of {where} parameter {name!r} changed",
                    {"style": old_param.get("style"), "explode": old_param.get("explode")},
                    {"style": new_param.get("style"), "explode": new_param.get("explode")},
                )
            if not old_param.get("deprecated") and new_param.get("deprecated"):
                self.add(
                    "parameter.deprecated",
                    "non_breaking",
                    loc,
                    ptr,
                    f"{where} parameter {name!r} is now deprecated",
                )
            self._diff_schema(
                old_param.get("schema") or {},
                new_param.get("schema") or {},
                direction="request",
                location=loc,
                pointer=f"{ptr}.schema",
                seen=set(),
            )

    # --------------------------------------------------------- request body
    def _compare_request_body(
        self,
        old_op: dict[str, Any],
        new_op: dict[str, Any],
        location: str,
        pointer: str,
    ) -> None:
        old_body = old_op.get("requestBody")
        new_body = new_op.get("requestBody")
        old_body = deref(old_body, self.base) if old_body else None
        new_body = deref(new_body, self.head) if new_body else None
        ptr = f"{pointer}.requestBody"

        if old_body and not new_body:
            self.add(
                "request_body.removed",
                "breaking",
                self._loc(location, "requestBody"),
                ptr,
                "Request body was removed",
            )
            return
        if new_body and not old_body:
            required = bool(new_body.get("required")) if isinstance(new_body, dict) else False
            self.add(
                "request_body.added",
                "breaking" if required else "non_breaking",
                self._loc(location, "requestBody"),
                ptr,
                "Required request body was added" if required else "Optional request body was added",
                None,
                required,
            )
            return
        if not old_body or not new_body:
            return
        assert isinstance(old_body, dict) and isinstance(new_body, dict)

        if not old_body.get("required") and new_body.get("required"):
            self.add(
                "request_body.required.added",
                "breaking",
                self._loc(location, "requestBody"),
                f"{ptr}.required",
                "Request body became required",
                False,
                True,
            )
        self._compare_content(
            old_body.get("content"),
            new_body.get("content"),
            direction="request",
            location=self._loc(location, "requestBody"),
            pointer=f"{ptr}.content",
        )

    # ------------------------------------------------------------ responses
    def _compare_responses(
        self,
        old_op: dict[str, Any],
        new_op: dict[str, Any],
        location: str,
        pointer: str,
    ) -> None:
        old_responses = old_op.get("responses") or {}
        new_responses = new_op.get("responses") or {}
        if not isinstance(old_responses, dict) or not isinstance(new_responses, dict):
            return

        for status in sorted(set(old_responses) | set(new_responses)):
            old_res = old_responses.get(status)
            new_res = new_responses.get(status)
            loc = self._loc(location, f"response {status}")
            ptr = f"{pointer}.responses.{status}"

            if old_res and not new_res:
                self.add(
                    "response.removed",
                    "breaking",
                    loc,
                    ptr,
                    f"Response {status} was removed",
                )
                continue
            if new_res and not old_res:
                self.add(
                    "response.added",
                    "non_breaking",
                    loc,
                    ptr,
                    f"Response {status} was added",
                )
                continue

            old_res = deref(old_res, self.base)
            new_res = deref(new_res, self.head)
            if not isinstance(old_res, dict) or not isinstance(new_res, dict):
                continue

            self._compare_content(
                old_res.get("content"),
                new_res.get("content"),
                direction="response",
                location=loc,
                pointer=f"{ptr}.content",
            )
            self._compare_headers(old_res.get("headers"), new_res.get("headers"), loc, f"{ptr}.headers")

    def _compare_headers(self, base: Any, head: Any, location: str, pointer: str) -> None:
        base_headers = base if isinstance(base, dict) else {}
        head_headers = head if isinstance(head, dict) else {}
        for name in sorted(set(base_headers) - set(head_headers)):
            self.add(
                "response.header.removed",
                "risky",
                self._loc(location, f"header {name}"),
                f"{pointer}.{name}",
                f"Response header {name!r} was removed",
            )

    # -------------------------------------------------------------- content
    def _compare_content(
        self,
        base: Any,
        head: Any,
        *,
        direction: Direction,
        location: str,
        pointer: str,
    ) -> None:
        base_content = base if isinstance(base, dict) else {}
        head_content = head if isinstance(head, dict) else {}
        for media_type in sorted(set(base_content) | set(head_content)):
            old_media = base_content.get(media_type)
            new_media = head_content.get(media_type)
            loc = self._loc(location, media_type)
            ptr = f"{pointer}.{media_type}"

            if old_media is not None and new_media is None:
                self.add(
                    "request_body.media_type.removed"
                    if direction == "request"
                    else "response.media_type.removed",
                    "breaking",
                    loc,
                    ptr,
                    f"Media type {media_type!r} was removed",
                )
                continue
            if new_media is not None and old_media is None:
                self.add(
                    "request_body.media_type.added"
                    if direction == "request"
                    else "response.media_type.added",
                    "non_breaking",
                    loc,
                    ptr,
                    f"Media type {media_type!r} was added",
                )
                continue

            old_schema = (old_media or {}).get("schema") if isinstance(old_media, dict) else None
            new_schema = (new_media or {}).get("schema") if isinstance(new_media, dict) else None
            if old_schema is not None or new_schema is not None:
                self._diff_schema(
                    old_schema or {},
                    new_schema or {},
                    direction=direction,
                    location=loc,
                    pointer=f"{ptr}.schema",
                    seen=set(),
                )

    # --------------------------------------------------------------- schemas
    def _diff_schema(
        self,
        old: Any,
        new: Any,
        *,
        direction: Direction,
        location: str,
        pointer: str,
        seen: set[tuple[str, str, str]],
    ) -> None:
        old_ref = old.get("$ref") if isinstance(old, dict) else None
        new_ref = new.get("$ref") if isinstance(new, dict) else None
        if old_ref or new_ref:
            key = (str(old_ref), str(new_ref), direction)
            if key in seen:
                return  # recursive schema already compared at this position
            seen.add(key)

        old_schema = normalize_schema(old, self.base)
        new_schema = normalize_schema(new, self.head)
        if old_schema == new_schema and not _has_refs(old_schema):
            return
        if not old_schema and not new_schema:
            return

        self._compare_types(old_schema, new_schema, direction, location, pointer)
        self._compare_enums(old_schema, new_schema, direction, location, pointer)
        self._compare_variants(old_schema, new_schema, direction, location, pointer)
        self._compare_formats(old_schema, new_schema, direction, location, pointer)

        old_props = old_schema.get("properties")
        new_props = new_schema.get("properties")
        if isinstance(old_props, dict) or isinstance(new_props, dict):
            self._compare_object(
                old_schema,
                new_schema,
                direction,
                location,
                pointer,
                seen,
            )

        if "items" in old_schema or "items" in new_schema:
            self._diff_schema(
                old_schema.get("items") or {},
                new_schema.get("items") or {},
                direction=direction,
                location=self._loc(location, "items"),
                pointer=f"{pointer}.items",
                seen=seen,
            )

        self._compare_constraints(old_schema, new_schema, direction, location, pointer)
        self._compare_additional_properties(old_schema, new_schema, direction, location, pointer)

        if old_schema.get("deprecated") is not True and new_schema.get("deprecated") is True:
            self.add(
                "schema.deprecated",
                "non_breaking",
                location,
                f"{pointer}.deprecated",
                "Schema is now marked deprecated",
            )
        if (
            "default" in old_schema
            and "default" in new_schema
            and canonical(old_schema["default"]) != canonical(new_schema["default"])
        ):
            self.add(
                "schema.default.changed",
                "non_breaking",
                location,
                f"{pointer}.default",
                "Default value changed",
                old_schema.get("default"),
                new_schema.get("default"),
            )

    def _compare_types(
        self,
        old_schema: dict[str, Any],
        new_schema: dict[str, Any],
        direction: Direction,
        location: str,
        pointer: str,
    ) -> None:
        old_types = schema_types(old_schema)
        new_types = schema_types(new_schema)
        if not old_types and not new_types:
            return

        if not old_types:
            self.add(
                "schema.type.changed",
                self._severity(direction, "breaking", "non_breaking"),
                location,
                pointer,
                f"Schema narrowed from unconstrained to {sorted(new_types)}",
                None,
                sorted(new_types),
            )
            return
        if not new_types:
            self.add(
                "schema.type.changed",
                self._severity(direction, "non_breaking", "risky"),
                location,
                pointer,
                f"Schema widened from {sorted(old_types)} to unconstrained",
                sorted(old_types),
                None,
            )
            return

        nullable_added = new_types - old_types == {"null"}
        nullable_removed = old_types - new_types == {"null"}
        if nullable_added:
            self.add(
                "schema.nullable.added",
                self._severity(direction, "non_breaking", "breaking"),
                location,
                pointer,
                "Value may now be null",
                None,
                True,
            )
        elif nullable_removed:
            self.add(
                "schema.nullable.removed",
                "breaking",
                location,
                pointer,
                "Value may no longer be null",
                True,
                None,
            )
        else:
            removed = old_types - new_types
            added = new_types - old_types
            if removed:
                self.add(
                    "schema.type.changed",
                    "breaking",
                    location,
                    pointer,
                    f"Types no longer accepted: {sorted(removed)}",
                    sorted(old_types),
                    sorted(new_types),
                )
            elif added:
                self.add(
                    "schema.type.changed",
                    self._severity(direction, "non_breaking", "risky"),
                    location,
                    pointer,
                    f"Additional types now allowed: {sorted(added)}",
                    sorted(old_types),
                    sorted(new_types),
                )

    def _compare_enums(
        self,
        old_schema: dict[str, Any],
        new_schema: dict[str, Any],
        direction: Direction,
        location: str,
        pointer: str,
    ) -> None:
        old_enum = enum_values(old_schema)
        new_enum = enum_values(new_schema)
        if old_enum is None and new_enum is None:
            return
        if old_enum is not None and new_enum is None:
            self.add(
                "schema.enum.value.added",
                self._severity(direction, "non_breaking", "breaking"),
                location,
                f"{pointer}.enum",
                "Enum constraint was removed: any value is now allowed",
            )
            return
        if new_enum is not None and old_enum is None:
            self.add(
                "schema.enum.value.removed",
                self._severity(direction, "breaking", "non_breaking"),
                location,
                f"{pointer}.enum",
                "An enum constraint was added",
                None,
                sorted(new_enum),
            )
            return
        assert old_enum is not None and new_enum is not None
        removed = old_enum - new_enum
        added = new_enum - old_enum
        if removed:
            self.add(
                "schema.enum.value.removed",
                self._severity(direction, "breaking", "non_breaking"),
                location,
                f"{pointer}.enum",
                f"Enum values removed: {sorted(removed)}",
                sorted(old_enum),
                sorted(new_enum),
            )
        if added:
            self.add(
                "schema.enum.value.added",
                self._severity(direction, "non_breaking", "breaking"),
                location,
                f"{pointer}.enum",
                f"Enum values added: {sorted(added)}",
                sorted(old_enum),
                sorted(new_enum),
            )

    def _compare_variants(
        self,
        old_schema: dict[str, Any],
        new_schema: dict[str, Any],
        direction: Direction,
        location: str,
        pointer: str,
    ) -> None:
        for key in ("oneOf", "anyOf"):
            old_variants = old_schema.get(key)
            new_variants = new_schema.get(key)
            if not isinstance(old_variants, list) and not isinstance(new_variants, list):
                continue
            old_set = {canonical(normalize_schema(v, self.base)) for v in old_variants or []}
            new_set = {canonical(normalize_schema(v, self.head)) for v in new_variants or []}
            if old_set == new_set:
                continue
            self.add(
                "schema.variants.changed",
                self._severity(direction, "breaking", "risky"),
                location,
                f"{pointer}.{key}",
                f"{key} variants changed "
                f"({len(old_set - new_set)} removed, {len(new_set - old_set)} added)",
                sorted(old_set),
                sorted(new_set),
            )

    def _compare_formats(
        self,
        old_schema: dict[str, Any],
        new_schema: dict[str, Any],
        direction: Direction,
        location: str,
        pointer: str,
    ) -> None:
        old_format, new_format = old_schema.get("format"), new_schema.get("format")
        if old_format == new_format:
            return
        if old_format is None:
            # Adding a format tightens the contract (e.g. any string -> uuid).
            severity: Severity = self._severity(direction, "breaking", "non_breaking")
        elif new_format is None:
            severity = self._severity(direction, "non_breaking", "risky")
        else:
            severity = "risky"
        self.add(
            "schema.format.changed",
            severity,
            location,
            f"{pointer}.format",
            f"Format changed from {old_format!r} to {new_format!r}",
            old_format,
            new_format,
        )

    def _compare_object(
        self,
        old_schema: dict[str, Any],
        new_schema: dict[str, Any],
        direction: Direction,
        location: str,
        pointer: str,
        seen: set[tuple[str, str, str]],
    ) -> None:
        old_props = old_schema.get("properties") or {}
        new_props = new_schema.get("properties") or {}
        if not isinstance(old_props, dict):
            old_props = {}
        if not isinstance(new_props, dict):
            new_props = {}

        for name in sorted(set(old_props) | set(new_props)):
            old_prop = old_props.get(name)
            new_prop = new_props.get(name)
            loc = self._loc(location, f"properties.{name}")
            ptr = f"{pointer}.properties.{name}"

            if old_prop is not None and new_prop is None:
                self.add(
                    "schema.property.removed",
                    self._severity(direction, "risky", "breaking"),
                    loc,
                    ptr,
                    f"Property {name!r} was removed",
                    old_prop,
                )
                continue
            if new_prop is not None and old_prop is None:
                self.add(
                    "schema.property.added",
                    "non_breaking",
                    loc,
                    ptr,
                    f"Property {name!r} was added",
                    None,
                    new_prop,
                )
                continue
            self._diff_schema(
                old_prop or {},
                new_prop or {},
                direction=direction,
                location=loc,
                pointer=ptr,
                seen=seen,
            )

        old_required = set(old_schema.get("required") or [])
        new_required = set(new_schema.get("required") or [])
        for name in sorted(new_required - old_required):
            self.add(
                "schema.required.added",
                self._severity(direction, "breaking", "non_breaking"),
                self._loc(location, f"properties.{name}"),
                f"{pointer}.required",
                f"Property {name!r} became required",
                False,
                True,
            )
        for name in sorted(old_required - new_required):
            self.add(
                "schema.required.removed",
                self._severity(direction, "non_breaking", "risky"),
                self._loc(location, f"properties.{name}"),
                f"{pointer}.required",
                f"Property {name!r} is no longer guaranteed",
                True,
                False,
            )

    def _compare_constraints(
        self,
        old_schema: dict[str, Any],
        new_schema: dict[str, Any],
        direction: Direction,
        location: str,
        pointer: str,
    ) -> None:
        decreases = ("maxLength", "maximum", "maxItems", "maxProperties", "exclusiveMaximum")
        increases = ("minLength", "minimum", "minItems", "minProperties", "exclusiveMinimum")

        for key in decreases + increases:
            old_value, new_value = old_schema.get(key), new_schema.get(key)
            if not isinstance(old_value, (int, float)) or not isinstance(new_value, (int, float)):
                continue
            tightened = new_value < old_value if key in decreases else new_value > old_value
            loosened = new_value > old_value if key in decreases else new_value < old_value
            if not tightened and not loosened:
                continue
            self.add(
                "schema.constraint.tightened" if tightened else "schema.constraint.loosened",
                self._severity(direction, "breaking", "non_breaking") if tightened else "non_breaking",
                location,
                f"{pointer}.{key}",
                f"{key} changed from {old_value} to {new_value}",
                old_value,
                new_value,
            )

        old_pattern, new_pattern = old_schema.get("pattern"), new_schema.get("pattern")
        if old_pattern != new_pattern and new_pattern is not None:
            self.add(
                "schema.constraint.tightened",
                self._severity(direction, "breaking", "non_breaking"),
                location,
                f"{pointer}.pattern",
                "Pattern constraint was added or changed",
                old_pattern,
                new_pattern,
            )
        if old_schema.get("uniqueItems") is not True and new_schema.get("uniqueItems") is True:
            self.add(
                "schema.constraint.tightened",
                self._severity(direction, "breaking", "non_breaking"),
                location,
                f"{pointer}.uniqueItems",
                "Array items must now be unique",
            )

    def _compare_additional_properties(
        self,
        old_schema: dict[str, Any],
        new_schema: dict[str, Any],
        direction: Direction,
        location: str,
        pointer: str,
    ) -> None:
        old_ap = old_schema.get("additionalProperties")
        new_ap = new_schema.get("additionalProperties")
        if old_ap is not False and new_ap is False:
            self.add(
                "schema.additional_properties.restricted",
                self._severity(direction, "breaking", "risky"),
                location,
                f"{pointer}.additionalProperties",
                "Additional properties are no longer allowed",
                old_ap,
                False,
            )
        elif isinstance(old_ap, dict) and isinstance(new_ap, dict):
            self._diff_schema(
                old_ap,
                new_ap,
                direction=direction,
                location=self._loc(location, "additionalProperties"),
                pointer=f"{pointer}.additionalProperties",
                seen=set(),
            )


def diff_documents(base: dict[str, Any], head: dict[str, Any]) -> DiffResult:
    base_info = base.get("info") or {}
    head_info = head.get("info") or {}
    differ = _Differ(base, head)
    changes = differ.run()
    return DiffResult(
        changes=changes,
        base_title=str(base_info.get("title") or ""),
        head_title=str(head_info.get("title") or ""),
        base_version=str(base_info.get("version") or ""),
        head_version=str(head_info.get("version") or ""),
    )


def diff_specs(base: ParsedSpec, head: ParsedSpec) -> DiffResult:
    return diff_documents(base.document, head.document)