"""Command line interface.

Commands
--------
diff     Compare two OpenAPI documents (files or URLs) and print JSON/Markdown.
verify   Run a repository's tests inside a disposable Docker container.
initdb   Create database tables.
serve    Start the API with uvicorn.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from .core.diff import diff_specs
from .core.openapi import SpecError, parse_spec


def _read_source(source: str) -> str:
    if source.startswith(("http://", "https://")):
        response = httpx.get(source, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        return response.text
    return Path(source).read_text()


def _cmd_diff(args: argparse.Namespace) -> int:
    try:
        before = parse_spec(_read_source(args.before))
        after = parse_spec(_read_source(args.after))
    except (OSError, SpecError, httpx.HTTPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = diff_specs(before, after)
    if args.format == "json":
        output = json.dumps(result.to_dict(), indent=2, default=str)
    else:
        output = result.to_markdown(spec_name=args.name, repository=args.repository)

    if args.output:
        Path(args.output).write_text(output + ("" if output.endswith("\n") else "\n"))
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(output)

    counts = result.counts()
    print(
        f"summary: {counts['breaking']} breaking, {counts['risky']} risky, "
        f"{counts['non_breaking']} non-breaking",
        file=sys.stderr,
    )
    if args.exit_code and result.has_breaking:
        return 1
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    from .services.sandbox import SandboxError, run_in_sandbox

    try:
        result = run_in_sandbox(
            args.directory,
            args.cmd,
            image=args.image,
            network=args.network,
            timeout=args.timeout,
        )
    except SandboxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    print(f"sandbox exited with {result.exit_code}", file=sys.stderr)
    return result.exit_code


def _cmd_initdb(_: argparse.Namespace) -> int:
    from .db import init_db

    init_db()
    print("database schema is up to date")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apiradar", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    diff_parser = sub.add_parser("diff", help="diff two OpenAPI documents")
    diff_parser.add_argument("before", help="base spec: path or URL")
    diff_parser.add_argument("after", help="head spec: path or URL")
    diff_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    diff_parser.add_argument("--name", default=None, help="spec name shown in the report")
    diff_parser.add_argument("--repository", default=None)
    diff_parser.add_argument("-o", "--output", default=None, help="write report to a file")
    diff_parser.add_argument(
        "--exit-code",
        action="store_true",
        help="exit with status 1 when breaking changes are found (CI gate)",
    )
    diff_parser.set_defaults(func=_cmd_diff)

    verify_parser = sub.add_parser("verify", help="run tests in a Docker sandbox")
    verify_parser.add_argument("directory")
    verify_parser.add_argument("--cmd", required=True, help="command executed with sh -lc")
    verify_parser.add_argument("--image", default="python:3.12-slim")
    verify_parser.add_argument("--network", default="none", help="docker network mode")
    verify_parser.add_argument("--timeout", type=int, default=900)
    verify_parser.set_defaults(func=_cmd_verify)

    initdb_parser = sub.add_parser("initdb", help="create database tables")
    initdb_parser.set_defaults(func=_cmd_initdb)

    serve_parser = sub.add_parser("serve", help="run the API")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--reload", action="store_true")
    serve_parser.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())