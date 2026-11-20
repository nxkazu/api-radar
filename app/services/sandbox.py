"""Docker-based isolated test runner.

Used by ``apiradar verify`` to run a repository's test command in a disposable
container instead of on the host. The command string is executed with
``sh -lc`` inside the image, with the checkout mounted at ``/workspace``.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    command: list[str]

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def build_docker_command(
    repo_dir: str | Path,
    command: str,
    *,
    image: str = "python:3.12-slim",
    network: str = "none",
) -> list[str]:
    workdir = Path(repo_dir).resolve()
    if not workdir.is_dir():
        raise SandboxError(f"{workdir} is not a directory")
    return [
        "docker",
        "run",
        "--rm",
        "--init",
        "-v",
        f"{workdir}:/workspace",
        "-w",
        "/workspace",
        "--network",
        network,
        image,
        "sh",
        "-lc",
        command,
    ]


def run_in_sandbox(
    repo_dir: str | Path,
    command: str,
    *,
    image: str = "python:3.12-slim",
    network: str = "none",
    timeout: int = 900,
) -> SandboxResult:
    if shutil.which("docker") is None:
        raise SandboxError("docker is not installed or not on PATH")
    argv = build_docker_command(repo_dir, command, image=image, network=network)
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"test command timed out after {timeout}s") from exc
    return SandboxResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        command=argv,
    )