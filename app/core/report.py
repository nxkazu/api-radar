"""Markdown rendering for diffs and scans."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .diff import Change, DiffResult

SEVERITY_TITLES = {
    "breaking": "Breaking changes",
    "risky": "Needs review",
    "non_breaking": "Non-breaking changes",
}
SEVERITY_ICONS = {"breaking": "🔴", "risky": "🟠", "non_breaking": "🟢"}


def _md_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    text = str(value)
    if len(text) > 160:
        text = text[:157] + "..."
    return f"`{_md_escape(text)}`"


def _change_block(index: int, change: Change) -> list[str]:
    lines = [f"#### {index}. {change.location}", "", change.message, ""]
    lines.append(f"- Kind: `{change.kind}`")
    if change.before is not None or change.after is not None:
        lines.append(f"- Before: {_fmt(change.before)}")
        lines.append(f"- After: {_fmt(change.after)}")
    if change.fix_hint:
        lines.append(f"- Suggested action: {change.fix_hint}")
    lines.append("")
    return lines


def render_markdown(
    diff: DiffResult,
    *,
    repository: str | None = None,
    spec_name: str | None = None,
    source: str | None = None,
    generated_at: datetime | None = None,
    call_sites: dict[str, Iterable[tuple[str, int]]] | None = None,
) -> str:
    counts = diff.counts()
    title = spec_name or diff.head_title or "API"
    stamp = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"# API Radar report — {title}",
        "",
        f"Generated {stamp} by [API Radar](https://github.com/nxkazu/api-radar).",
        "",
        "| | |",
        "|---|---|",
    ]
    if repository:
        lines.append(f"| Repository | `{repository}` |")
    if spec_name:
        lines.append(f"| Spec | `{spec_name}` |")
    if source:
        lines.append(f"| Source | `{source}` |")
    lines.append(f"| Base version | `{diff.base_version or '?'}` |")
    lines.append(f"| Head version | `{diff.head_version or '?'}` |")
    lines.append(f"| Fingerprint | `{diff.fingerprint()}` |")
    lines.append("")
    lines += [
        "## Summary",
        "",
        "| Severity | Count |",
        "|---|---:|",
        f"| 🔴 Breaking | {counts['breaking']} |",
        f"| 🟠 Needs review | {counts['risky']} |",
        f"| 🟢 Non-breaking | {counts['non_breaking']} |",
        f"| **Total** | **{counts['total']}** |",
        "",
    ]

    if not diff.changes:
        lines += ["No contract differences detected between the two versions.", ""]
        return "\n".join(lines)

    verdict = (
        "**Verdict: breaking changes detected.** Upgrading without code changes can break clients."
        if diff.has_breaking
        else "**Verdict: no breaking changes detected.** Review the flagged items before upgrading."
    )
    lines += [verdict, ""]

    counter = 0
    for severity in ("breaking", "risky", "non_breaking"):
        changes = [c for c in diff.changes if c.severity == severity]
        if not changes:
            continue
        lines += [
            f"## {SEVERITY_ICONS[severity]} {SEVERITY_TITLES[severity]} ({len(changes)})",
            "",
        ]
        for change in changes:
            counter += 1
            lines += _change_block(counter, change)

    if call_sites:
        lines += ["## Call-sites found in the repository", ""]
        found = False
        for path, sites in sorted(call_sites.items()):
            sites = list(sites)
            if not sites:
                continue
            found = True
            lines.append(f"### `{path}` — {len(sites)} reference(s)")
            for file, line in sites:
                lines.append(f"- `{file}:{line}`")
            lines.append("")
        if not found:
            lines.append("_No literal references to the changed paths were found._")
            lines.append("")

    lines += [
        "## How to use this report",
        "",
        "1. Start with the breaking changes; each one lists the exact location in the spec.",
        "2. Search the repository for the affected paths/fields (the call-site inventory above is generated automatically when the source is a GitHub repository).",
        "3. Upgrade the client, run the test suite, then update the tracked spec version in API Radar.",
        "",
        "---",
        "",
        "_API Radar compares OpenAPI documents semantically. It resolves local `$ref`s, "
        "flattens `allOf`, and classifies each difference by direction: what is safe for a "
        "request may be breaking for a response._",
        "",
    ]
    return "\n".join(lines)