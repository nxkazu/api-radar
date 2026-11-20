"""Call-site discovery and mechanical fix generation.

Two things happen here:

1. ``detect_renames`` finds paths that kept their shape but changed a segment
   (``/pets/{petId}`` → ``/animals/{petId}``). Those can be rewritten safely in
   string literals without understanding the surrounding code.
2. ``find_references`` inventories every literal use of a changed path so the
   report can point a developer at the exact file and line, even when the fix
   cannot be automated.

Everything is pure text processing; no repository is ever executed.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from .openapi import HTTP_METHODS

TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rb",
    ".java",
    ".kt",
    ".kts",
    ".cs",
    ".php",
    ".rs",
    ".swift",
    ".scala",
    ".sh",
    ".md",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
}

SKIP_DIRS = {
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "target",
    "coverage",
}

SKIP_FILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "Cargo.lock",
    "go.sum",
}

PLACEHOLDER = re.compile(r"\{[^}/]+\}")
VERSION_SEGMENT = re.compile(r"^v\d+(?:[a-z]+\d*)?$", re.IGNORECASE)


@dataclass(frozen=True)
class PathRename:
    old: str
    new: str


@dataclass
class CallSite:
    path: str
    file: str
    line: int
    text: str


@dataclass
class FilePatch:
    path: str
    old_content: str
    new_content: str
    replacements: int

    def unified_diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.old_content.splitlines(keepends=True),
                self.new_content.splitlines(keepends=True),
                fromfile=f"a/{self.path}",
                tofile=f"b/{self.path}",
            )
        )


def is_text_file(path: str) -> bool:
    if path.rsplit("/", 1)[-1] in SKIP_FILES:
        return False
    if any(part in SKIP_DIRS for part in path.split("/")[:-1]):
        return False
    return path.lower().endswith(tuple(TEXT_EXTENSIONS))


def normalize_path(path: str) -> str:
    """Collapse placeholders and version segments: /v1/pets/{id} -> /{}/pets/{}."""
    segments = []
    for segment in PLACEHOLDER.sub("{}", path).split("/"):
        segments.append("{}" if VERSION_SEGMENT.match(segment) else segment)
    return "/".join(segments)


def _operations(document: dict) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for path, item in (document.get("paths") or {}).items():
        if not path.startswith("/") or not isinstance(item, dict):
            continue
        methods = {m for m in HTTP_METHODS if isinstance(item.get(m), dict)}
        if methods:
            result[path] = methods
    return result


def detect_renames(base: dict, head: dict) -> list[PathRename]:
    """Detect path renames by comparing normalised path templates."""
    base_ops = _operations(base)
    head_ops = _operations(head)
    removed = set(base_ops) - set(head_ops)
    added = set(head_ops) - set(base_ops)

    by_shape: dict[str, list[str]] = {}
    for path in added:
        by_shape.setdefault(normalize_path(path), []).append(path)

    renames: list[PathRename] = []
    for old in sorted(removed):
        candidates = by_shape.get(normalize_path(old), [])
        if len(candidates) != 1:
            continue
        new = candidates[0]
        # Require the HTTP surface to overlap so `/v1/pets` → `/v2/users` with
        # completely different methods is not treated as a mechanical rename.
        if not (base_ops[old] & head_ops[new]):
            continue
        renames.append(PathRename(old=old, new=new))
    return renames


def _literal_regex(template: str) -> re.Pattern[str]:
    """Match a quote-delimited literal that follows a path template.

    Placeholders match any non-slash text, which covers concrete values
    (``/pets/42``), f-string fields (``/pets/{pet_id}``) and JS template
    expressions (``/pets/${id}``).
    """
    parts = PLACEHOLDER.split(template)
    pattern = r"(?P<q>['\"`])" + re.escape(parts[0])
    for index, part in enumerate(parts[1:]):
        pattern += rf"(?P<p{index}>[^/\"'`\s]+)"
        pattern += re.escape(part)
    pattern += r"(?P=q)"
    return re.compile(pattern)


def _rebuild(new_template: str, values: list[str]) -> str | None:
    placeholders = PLACEHOLDER.findall(new_template)
    if len(placeholders) != len(values):
        return None
    result = new_template
    for placeholder, value in zip(placeholders, values, strict=True):
        result = result.replace(placeholder, value, 1)
    return result


def _apply_one(content: str, rename: PathRename) -> tuple[str, int, int]:
    regex = _literal_regex(rename.old)
    applied = 0
    skipped = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal applied, skipped
        quote = match.group("q")
        values = [match.group(f"p{i}") for i in range(len(match.groups()) - 1)]
        rebuilt = _rebuild(rename.new, values)
        if rebuilt is None:
            skipped += 1
            return match.group(0)
        applied += 1
        return f"{quote}{rebuilt}{quote}"

    return regex.sub(replace, content), applied, skipped


def find_references(paths: Iterable[str], files: dict[str, str]) -> dict[str, list[CallSite]]:
    """Inventory literal references to ``paths`` across ``files``."""
    compiled = [(path, _literal_regex(path)) for path in paths]
    inventory: dict[str, list[CallSite]] = {path: [] for path in paths}
    for file, content in files.items():
        if not is_text_file(file):
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            for path, regex in compiled:
                if regex.search(line):
                    inventory[path].append(
                        CallSite(path=path, file=file, line=line_number, text=line.strip()[:200])
                    )
    return inventory


def plan_renames(
    renames: Iterable[PathRename],
    files: dict[str, str],
    *,
    max_file_bytes: int = 200_000,
    max_files: int = 25,
) -> list[FilePatch]:
    renames = list(renames)
    patches: list[FilePatch] = []
    if not renames:
        return patches

    for path in sorted(files):
        if len(patches) >= max_files:
            break
        if not is_text_file(path):
            continue
        content = files[path]
        if len(content.encode("utf-8", errors="ignore")) > max_file_bytes:
            continue

        updated = content
        replacements = 0
        for rename in renames:
            updated, applied, _skipped = _apply_one(updated, rename)
            replacements += applied
        if replacements and updated != content:
            patches.append(
                FilePatch(
                    path=path,
                    old_content=content,
                    new_content=updated,
                    replacements=replacements,
                )
            )
    return patches


def render_migration_notes(
    *,
    spec_name: str,
    repository: str,
    base_version: str,
    head_version: str,
    renames: list[PathRename],
    patches: list[FilePatch],
    inventory: dict[str, list[CallSite]],
) -> str:
    lines = [
        f"# API Radar migration — {spec_name}",
        "",
        f"Repository: `{repository}`  ",
        f"Spec: `{spec_name}`  ",
        f"Version: `{base_version}` → `{head_version}`",
        "",
        "## Mechanical rewrites in this PR",
        "",
    ]
    if patches:
        for patch in patches:
            lines.append(f"- `{patch.path}` — {patch.replacements} reference(s) rewritten")
            if renames:
                for rename in renames:
                    lines.append(f"  - `{rename.old}` → `{rename.new}`")
    else:
        lines.append("_No safe mechanical rewrites were possible. See the call-site inventory below._")
    lines += ["", "## Call-site inventory", ""]
    any_sites = False
    for path, sites in sorted(inventory.items()):
        if not sites:
            continue
        any_sites = True
        lines.append(f"### `{path}` ({len(sites)})")
        for site in sites[:50]:
            lines.append(f"- `{site.file}:{site.line}` — `{site.text}`")
        if len(sites) > 50:
            lines.append(f"- …and {len(sites) - 50} more")
        lines.append("")
    if not any_sites:
        lines.append("_No literal references found._")
        lines.append("")
    lines += [
        "## Before merging",
        "",
        "- [ ] Run the project's test suite against this branch.",
        "- [ ] Review every rewritten reference (paths only; payloads may need changes too).",
        "- [ ] Check the full report in `.apiradar/` for non-mechanical changes.",
        "",
        "_Generated by API Radar._",
        "",
    ]
    return "\n".join(lines)