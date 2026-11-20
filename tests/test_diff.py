"""Diff engine behaviour: severities, direction, refs, recursion, stability."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.core.diff import diff_documents

# --- helpers -----------------------------------------------------------------


def doc(paths: dict[str, Any], components: dict[str, Any] | None = None, **extra: Any) -> dict:
    document = {
        "openapi": "3.0.3",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": paths,
    }
    if components:
        document["components"] = components
    document.update(extra)
    return document


def op(summary: str = "operation", **extra: Any) -> dict:
    return {"operationId": summary, "responses": {"200": {"description": "ok"}}, **extra}


def json_response(schema: dict, status: str = "200") -> dict:
    return {
        status: {
            "description": "ok",
            "content": {"application/json": {"schema": schema}},
        }
    }


def find(changes, kind: str, location_contains: str | None = None):
    matches = [c for c in changes if c.kind == kind]
    if location_contains is not None:
        matches = [c for c in matches if location_contains in c.location]
    return matches


# --- operation-level ---------------------------------------------------------


def test_identical_documents_produce_no_changes() -> None:
    paths = {"/pets": {"get": op("listPets")}}
    result = diff_documents(doc(paths), doc(deepcopy(paths)))
    assert result.changes == []
    assert result.has_breaking is False
    assert result.counts() == {"breaking": 0, "risky": 0, "non_breaking": 0, "total": 0}


def test_removed_operation_is_breaking() -> None:
    before = doc({"/pets": {"get": op("listPets"), "post": op("createPet")}})
    after = doc({"/pets": {"get": op("listPets")}})
    changes = diff_documents(before, after).changes
    removed = find(changes, "operation.removed")
    assert len(removed) == 1
    assert removed[0].severity == "breaking"
    assert removed[0].location == "POST /pets"


def test_added_operation_is_non_breaking() -> None:
    changes = diff_documents(doc({}), doc({"/pets": {"get": op()}})).changes
    assert find(changes, "operation.added")[0].severity == "non_breaking"


def test_operation_id_and_deprecation() -> None:
    before = doc({"/pets": {"get": op("listPets")}})
    after = doc({"/pets": {"get": op("listAllPets")}})
    changes = diff_documents(before, after).changes
    assert find(changes, "operation.operation_id.changed")[0].severity == "risky"

    deprecated = doc({"/pets": {"get": {**op("listPets"), "deprecated": True}}})
    changes = diff_documents(before, deprecated).changes
    assert find(changes, "operation.deprecated")[0].severity == "non_breaking"


# --- parameters --------------------------------------------------------------


def test_new_required_parameter_is_breaking() -> None:
    before = doc({"/pets": {"get": op()}})
    after = doc(
        {
            "/pets": {
                "get": op(
                    parameters=[
                        {"name": "page_size", "in": "query", "required": True, "schema": {"type": "integer"}}
                    ]
                )
            }
        }
    )
    changes = diff_documents(before, after).changes
    change = find(changes, "parameter.required.added")[0]
    assert change.severity == "breaking"
    assert "page_size" in change.location


def test_optional_parameter_added_is_non_breaking() -> None:
    before = doc({"/pets": {"get": op()}})
    after = doc(
        {
            "/pets": {
                "get": op(
                    parameters=[{"name": "cursor", "in": "query", "schema": {"type": "string"}}]
                )
            }
        }
    )
    changes = diff_documents(before, after).changes
    assert find(changes, "parameter.added")[0].severity == "non_breaking"


def test_parameter_becoming_required_and_removed() -> None:
    param = {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer"}}
    before = doc({"/pets": {"get": op(parameters=[param])}})
    after = doc({"/pets": {"get": op(parameters=[{**param, "required": True}])}})
    assert find(diff_documents(before, after).changes, "parameter.required.added")

    removed = diff_documents(before, doc({"/pets": {"get": op()}})).changes
    assert find(removed, "parameter.removed")[0].severity == "risky"


# --- schema direction --------------------------------------------------------


def test_request_enum_narrowing_is_breaking() -> None:
    param = {
        "name": "status",
        "in": "query",
        "schema": {"type": "string", "enum": ["available", "sold"]},
    }
    before = doc({"/pets": {"get": op(parameters=[param])}})
    narrowed = deepcopy(param)
    narrowed["schema"]["enum"] = ["available"]
    after = doc({"/pets": {"get": op(parameters=[narrowed])}})
    changes = diff_documents(before, after).changes
    change = find(changes, "schema.enum.value.removed")[0]
    assert change.severity == "breaking"


def test_response_enum_widening_is_breaking_narrowing_is_not() -> None:
    def response(statuses: list[str]) -> dict:
        return doc(
            {
                "/pets": {
                    "get": op(
                        responses=json_response(
                            {"type": "string", "enum": statuses}
                        )
                    )
                }
            }
        )

    widened = diff_documents(response(["available"]), response(["available", "archived"]))
    assert find(widened.changes, "schema.enum.value.added")[0].severity == "breaking"

    narrowed = diff_documents(response(["available", "sold"]), response(["available"]))
    assert find(narrowed.changes, "schema.enum.value.removed")[0].severity == "non_breaking"


def test_property_removal_depends_on_direction() -> None:
    schema_before = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "tag": {"type": "string"}},
    }
    schema_after = {"type": "object", "properties": {"id": {"type": "string"}}}

    request_before = doc(
        {
            "/pets": {
                "post": op(
                    requestBody={
                        "required": True,
                        "content": {"application/json": {"schema": schema_before}},
                    }
                )
            }
        }
    )
    request_after = doc(
        {
            "/pets": {
                "post": op(
                    requestBody={
                        "required": True,
                        "content": {"application/json": {"schema": schema_after}},
                    }
                )
            }
        }
    )
    change = find(diff_documents(request_before, request_after).changes, "schema.property.removed")[0]
    assert change.severity == "risky"

    response_before = doc({"/pets": {"get": op(responses=json_response(schema_before))}})
    response_after = doc({"/pets": {"get": op(responses=json_response(schema_after))}})
    change = find(diff_documents(response_before, response_after).changes, "schema.property.removed")[0]
    assert change.severity == "breaking"


def test_required_property_added_depends_on_direction() -> None:
    def with_required(required: list[str]) -> dict:
        return {
            "type": "object",
            "properties": {"id": {"type": "string"}, "created_at": {"type": "string"}},
            "required": required,
        }

    schema_before = with_required(["id"])
    schema_after = with_required(["id", "created_at"])

    request_before = doc(
        {
            "/pets": {
                "post": op(
                    requestBody={
                        "required": True,
                        "content": {"application/json": {"schema": schema_before}},
                    }
                )
            }
        }
    )
    request_after = doc(
        {
            "/pets": {
                "post": op(
                    requestBody={
                        "required": True,
                        "content": {"application/json": {"schema": schema_after}},
                    }
                )
            }
        }
    )
    assert find(
        diff_documents(request_before, request_after).changes, "schema.required.added"
    )[0].severity == "breaking"

    response_before = doc({"/pets": {"get": op(responses=json_response(schema_before))}})
    response_after = doc({"/pets": {"get": op(responses=json_response(schema_after))}})
    assert find(
        diff_documents(response_before, response_after).changes, "schema.required.added"
    )[0].severity == "non_breaking"


def test_nullable_transitions() -> None:
    def schema(nullable: bool) -> dict:
        return {"type": "string", "nullable": nullable}

    request_before = doc(
        {
            "/pets": {
                "post": op(
                    requestBody={
                        "content": {"application/json": {"schema": schema(True)}}
                    }
                )
            }
        }
    )
    request_after = doc(
        {
            "/pets": {
                "post": op(
                    requestBody={
                        "content": {"application/json": {"schema": schema(False)}}
                    }
                )
            }
        }
    )
    assert find(
        diff_documents(request_before, request_after).changes, "schema.nullable.removed"
    )[0].severity == "breaking"

    response_before = doc({"/pets": {"get": op(responses=json_response(schema(False)))}})
    response_after = doc({"/pets": {"get": op(responses=json_response(schema(True)))}})
    assert find(
        diff_documents(response_before, response_after).changes, "schema.nullable.added"
    )[0].severity == "breaking"


# --- constraints / formats ---------------------------------------------------


def test_constraints_and_formats() -> None:
    before = doc(
        {
            "/pets": {
                "get": op(
                    parameters=[
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "maximum": 100},
                        }
                    ]
                )
            }
        }
    )
    tightened = doc(
        {
            "/pets": {
                "get": op(
                    parameters=[
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "maximum": 50},
                        }
                    ]
                )
            }
        }
    )
    loosened = doc(
        {
            "/pets": {
                "get": op(
                    parameters=[
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "maximum": 200},
                        }
                    ]
                )
            }
        }
    )
    assert find(diff_documents(before, tightened).changes, "schema.constraint.tightened")[
        0
    ].severity == "breaking"
    assert find(diff_documents(before, loosened).changes, "schema.constraint.loosened")[
        0
    ].severity == "non_breaking"


# --- refs, allOf, recursion --------------------------------------------------


def test_local_refs_and_component_changes() -> None:
    components_before = {
        "schemas": {
            "Pet": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "tag": {"type": "string"}},
            }
        }
    }
    components_after = {
        "schemas": {"Pet": {"type": "object", "properties": {"id": {"type": "string"}}}}
    }
    paths = {
        "/pets": {
            "get": op(
                responses=json_response({"$ref": "#/components/schemas/Pet"})
            )
        }
    }
    # The path item is identical: only the referenced component changed.
    before = doc(deepcopy(paths), components_before)
    after = doc(deepcopy(paths), components_after)
    changes = diff_documents(before, after).changes
    assert find(changes, "schema.property.removed")[0].severity == "breaking"


def test_all_of_is_flattened_for_required_checks() -> None:
    before_components = {
        "schemas": {
            "Pet": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
            "NewPet": {
                "allOf": [
                    {"$ref": "#/components/schemas/Pet"},
                    {"type": "object", "properties": {"microchip": {"type": "string"}}},
                ]
            },
        }
    }
    after_components = deepcopy(before_components)
    after_components["schemas"]["NewPet"]["allOf"][1]["required"] = ["microchip"]

    def document(components: dict) -> dict:
        return doc(
            {
                "/pets": {
                    "post": op(
                        requestBody={
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/NewPet"}
                                }
                            },
                        }
                    )
                }
            },
            components,
        )

    changes = diff_documents(document(before_components), document(after_components)).changes
    required = find(changes, "schema.required.added")
    assert required
    assert required[0].severity == "breaking"
    assert "microchip" in required[0].location


def test_recursive_schema_terminates_and_reports_change() -> None:
    def document(statuses: list[str]) -> dict:
        return doc(
            {
                "/owners": {
                    "get": op(
                        responses=json_response({"$ref": "#/components/schemas/Owner"})
                    )
                }
            },
            {
                "schemas": {
                    "Owner": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "pets": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Pet"},
                            },
                        },
                    },
                    "Pet": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "status": {"type": "string", "enum": statuses},
                            "owner": {"$ref": "#/components/schemas/Owner"},
                        },
                    },
                }
            },
        )

    result = diff_documents(document(["available"]), document(["available", "archived"]))
    assert find(result.changes, "schema.enum.value.added")


def test_response_removal_media_type_and_security() -> None:
    before = doc(
        {
            "/pets": {
                "get": op(
                    responses={
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {"schema": {"type": "string"}},
                                "text/csv": {"schema": {"type": "string"}},
                            },
                        },
                        "404": {"description": "missing"},
                    }
                )
            }
        },
        security=[{"apiKey": []}],
    )
    after = doc(
        {
            "/pets": {
                "get": op(
                    responses={
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": {"type": "string"}}},
                        }
                    }
                )
            }
        }
    )
    changes = diff_documents(before, after).changes
    assert find(changes, "response.removed")[0].severity == "breaking"
    assert find(changes, "response.media_type.removed")[0].severity == "breaking"
    assert find(changes, "security.requirement.removed")[0].severity == "non_breaking"

    added_security = diff_documents(after, before).changes
    assert find(added_security, "security.requirement.added")[0].severity == "breaking"


# --- determinism -------------------------------------------------------------


def test_changes_are_sorted_and_fingerprint_is_stable() -> None:
    before = doc({"/a": {"get": op()}, "/b": {"get": op("b")}})
    after = doc({"/a": {"delete": op("a2")}, "/b": {"get": op("b")}})
    first = diff_documents(before, after)
    second = diff_documents(deepcopy(before), deepcopy(after))
    assert first.fingerprint() == second.fingerprint()
    severities = [c.severity for c in first.changes]
    assert severities == sorted(severities, key=lambda s: {"breaking": 0, "risky": 1, "non_breaking": 2}[s])


def test_unresolvable_ref_raises() -> None:
    from app.core.openapi import SpecError

    def broken(version: str) -> dict:
        return doc(
            {
                "/pets": {
                    "get": op(responses=json_response({"$ref": "#/components/schemas/Missing"}))
                }
            },
            components={"schemas": {}},
            **{"info": {"title": "Test API", "version": version}},
        )

    with pytest.raises(SpecError):
        diff_documents(broken("1.0.0"), broken("1.0.1"))