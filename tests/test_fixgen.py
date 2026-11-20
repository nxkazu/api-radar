"""Call-site discovery and mechanical rewrite behaviour."""

from __future__ import annotations

from app.core.fixgen import (
    PathRename,
    detect_renames,
    find_references,
    is_text_file,
    normalize_path,
    plan_renames,
    render_migration_notes,
)


def spec(paths: dict[str, list[str]]) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            path: {method: {"responses": {"200": {"description": "ok"}}} for method in methods}
            for path, methods in paths.items()
        },
    }


def test_normalize_path_collapses_placeholders_and_versions() -> None:
    assert normalize_path("/v1/pets/{petId}") == "/{}/pets/{}"
    assert normalize_path("/api/v2/owners/{ownerId}/pets/{petId}") == "/api/{}/owners/{}/pets/{}"
    assert normalize_path("/pets") == "/pets"


def test_detect_renames_with_version_bump() -> None:
    base = spec({"/v1/owners/{ownerId}": ["get"], "/pets": ["get"]})
    head = spec({"/v2/owners/{ownerId}": ["get"], "/pets": ["get"]})
    renames = detect_renames(base, head)
    assert renames == [PathRename(old="/v1/owners/{ownerId}", new="/v2/owners/{ownerId}")]


def test_detect_renames_ignores_different_shapes_and_methods() -> None:
    base = spec({"/v1/owners/{ownerId}": ["get"], "/legacy": ["get"]})
    head = spec({"/v2/owners/{ownerId}/pets": ["get"], "/modern": ["post"]})
    assert detect_renames(base, head) == []


def test_plan_renames_rewrites_python_fstrings_and_plain_literals(tmp_path) -> None:
    files = {
        "src/client.py": (
            'BASE = "https://api.example.test/v1"\n'
            'response = client.get(f"/v1/owners/{owner_id}")\n'
            'other = client.get("/v1/owners/42")\n'
        )
    }
    renames = [PathRename(old="/v1/owners/{ownerId}", new="/v2/owners/{ownerId}")]
    patches = plan_renames(renames, files)
    assert len(patches) == 1
    patch = patches[0]
    assert patch.replacements == 2
    assert '/v2/owners/{owner_id}' in patch.new_content
    assert '"/v2/owners/42"' in patch.new_content
    assert "-" in patch.unified_diff() and "+\"v2" not in patch.unified_diff()


def test_plan_renames_rewrites_js_template_literals() -> None:
    files = {"web/api.js": "fetch(`/v1/owners/${ownerId}/pets/${petId}`)\n"}
    renames = [
        PathRename(
            old="/v1/owners/{ownerId}/pets/{petId}",
            new="/v2/owners/{ownerId}/pets/{petId}",
        )
    ]
    patches = plan_renames(renames, files)
    assert patches[0].new_content == "fetch(`/v2/owners/${ownerId}/pets/${petId}`)\n"


def test_plan_renames_skips_when_placeholder_counts_do_not_match() -> None:
    files = {"src/client.py": 'client.get("/a/42")\n'}
    renames = [PathRename(old="/a/{id}", new="/a/{team}/{id}")]
    assert plan_renames(renames, files) == []


def test_plan_renames_skips_vendored_and_binary_files() -> None:
    files = {
        "node_modules/pkg/index.js": 'x = "/v1/owners/{ownerId}"\n',
        "package-lock.json": 'url = "/v1/owners/{ownerId}"\n',
        "logo.png": "binary",
        "src/app.ts": 'const x = "/v1/owners/{ownerId}";\n',
    }
    renames = [PathRename(old="/v1/owners/{ownerId}", new="/v2/owners/{ownerId}")]
    patches = plan_renames(renames, files)
    assert [patch.path for patch in patches] == ["src/app.ts"]
    assert is_text_file("src/app.ts")
    assert not is_text_file("logo.png")


def test_find_references_reports_file_and_line() -> None:
    files = {
        "a.py": 'x = 1\nclient.get("/pets/{petId}")\n',
        "b.ts": 'fetch("/pets/${id}")\n',
        "c.md": "nothing here\n",
    }
    inventory = find_references(["/pets/{petId}"], files)
    sites = inventory["/pets/{petId}"]
    assert {(site.file, site.line) for site in sites} == {("a.py", 2), ("b.ts", 1)}


def test_render_migration_notes_contains_summary_and_inventory() -> None:
    files = {"a.py": 'client.get("/pets/{petId}")\n'}
    inventory = find_references(["/pets/{petId}"], files)
    notes = render_migration_notes(
        spec_name="petstore",
        repository="acme/app",
        base_version="1.0",
        head_version="2.0",
        renames=[],
        patches=[],
        inventory=inventory,
    )
    assert "API Radar migration — petstore" in notes
    assert "acme/app" in notes
    assert "a.py:1" in notes
    assert "No safe mechanical rewrites" in notes