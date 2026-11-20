"""CLI and Docker sandbox command construction."""

from __future__ import annotations

import json
from pathlib import Path

from app.cli import main
from app.services.sandbox import SandboxError, build_docker_command


def test_diff_cli_json_and_exit_code(tmp_path, capsys) -> None:
    output = tmp_path / "report.json"
    code = main(
        [
            "diff",
            "demo/before.yaml",
            "demo/after.yaml",
            "--format",
            "json",
            "-o",
            str(output),
            "--exit-code",
        ]
    )
    assert code == 1
    payload = json.loads(output.read_text())
    assert payload["has_breaking"] is True
    assert payload["counts"]["breaking"] > 0
    captured = capsys.readouterr()
    assert "summary:" in captured.err


def test_diff_cli_without_exit_code_succeeds(tmp_path) -> None:
    target = tmp_path / "report.md"
    code = main(["diff", "demo/before.yaml", "demo/after.yaml", "-o", str(target)])
    assert code == 0
    text = target.read_text()
    assert "API Radar report" in text
    assert "operation.removed" in text


def test_diff_cli_reports_missing_file(capsys) -> None:
    assert main(["diff", "does-not-exist.yaml", "demo/after.yaml"]) == 2
    assert "error:" in capsys.readouterr().err


def test_build_docker_command_mounts_repo(tmp_path) -> None:
    command = build_docker_command(tmp_path, "pytest -q", image="python:3.12-slim", network="none")
    assert command[0] == "docker"
    assert "-v" in command
    mount = command[command.index("-v") + 1]
    assert mount == f"{tmp_path.resolve()}:/workspace"
    assert command[-4:] == ["python:3.12-slim", "sh", "-lc", "pytest -q"]
    assert command[command.index("--network") + 1] == "none"


def test_build_docker_command_rejects_missing_directory(tmp_path) -> None:
    missing = Path(tmp_path) / "nope"
    try:
        build_docker_command(missing, "true")
    except SandboxError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected SandboxError")