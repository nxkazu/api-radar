#!/usr/bin/env python3
"""Offline API Radar demo: diff the demo specs and show automated rewrites.

    python scripts/demo.py                # summary + rewrite diff
    python scripts/demo.py --markdown     # full markdown report
    python scripts/demo.py --write        # also write demo/report.md
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.core.diff import diff_specs
from app.core.fixgen import detect_renames, find_references, plan_renames
from app.core.openapi import load_spec_file

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", action="store_true", help="print the full report")
    parser.add_argument("--write", action="store_true", help="write demo/report.md")
    args = parser.parse_args()

    before = load_spec_file(ROOT / "demo/before.yaml")
    after = load_spec_file(ROOT / "demo/after.yaml")
    diff = diff_specs(before, after)

    files = {
        str(path.relative_to(ROOT)): path.read_text()
        for path in (ROOT / "demo").rglob("*")
        if path.is_file() and path.suffix in {".py", ".js", ".ts", ".md", ".yaml"}
    }
    renames = detect_renames(before.document, after.document)
    patches = plan_renames(renames, files)
    inventory = find_references(
        [change.location.split(" ", 1)[1] for change in diff.breaking if change.kind == "operation.removed"],
        files,
    )

    counts = diff.counts()
    print(f"petstore {diff.base_version} -> {diff.head_version}")
    print(
        f"{counts['breaking']} breaking, {counts['risky']} risky, "
        f"{counts['non_breaking']} non-breaking ({counts['total']} total)\n"
    )
    print("Breaking changes:")
    for change in diff.breaking:
        print(f"  - {change.location}: {change.message}")

    print("\nDetected renames:")
    for rename in renames:
        print(f"  - {rename.old} -> {rename.new}")

    print("\nMechanical rewrites:")
    for patch in patches:
        print(f"  - {patch.path} ({patch.replacements} replacement(s))")
        print(patch.unified_diff())

    print("\nCall-site inventory:")
    for path, sites in sorted(inventory.items()):
        for site in sites:
            print(f"  - {path} used at {site.file}:{site.line}")
    report = diff.to_markdown(
        spec_name="petstore",
        source="demo/before.yaml",
        call_sites={path: [(s.file, s.line) for s in sites] for path, sites in inventory.items()},
    )
    if args.write:
        target = ROOT / "demo/report.md"
        target.write_text(report)
        print(f"\nWrote {target}")
    if args.markdown:
        print("\n" + report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())