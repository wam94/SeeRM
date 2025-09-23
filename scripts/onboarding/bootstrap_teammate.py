#!/usr/bin/env python3
"""Automated Mac onboarding for SeeRM teammates.

This script is designed to run behind an Automator/Platypus wrapper so a
teammate can double-click an app bundle and have their workstation prepared
without touching the terminal. The steps performed are intentionally
idempotent so the same automation can be reused for "check for updates"
flows.

High level flow:
    1. Ensure the 1Password CLI is available and authenticated
    2. Pull the shared `.env` payload from the configured 1Password vault
    3. Write secrets to ``~/.seerm/.env`` (creating the directory as needed)
    4. Ensure Python 3.11 is available and create/refresh ``.venv``
    5. Install SeeRM dependencies and the package in editable mode
    6. Optionally run health checks / smoke tests

The script avoids opinionated UI work so callers can surface progress in the
wrapper of their choice. Status information is returned through structured
logging on stdout.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Iterable, Optional

from . import DEFAULT_ENV_REFERENCE

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_HOME = Path.home() / ".seerm"
ENV_PATH = CONFIG_HOME / ".env"
VERSION_PATH = CONFIG_HOME / "version"
DEFAULT_ENV_REFERENCE_ENV = "SEERM_OP_ENV_REFERENCE"
DEFAULT_VERSION_ENV = "SEERM_UPDATE_VERSION"
SUPPORTED_PYTHON_MAJOR = 3
SUPPORTED_PYTHON_MINOR = 11


class BootstrapError(RuntimeError):
    """Fatal error raised when onboarding cannot continue."""


def log(message: str) -> None:
    """Emit a single-line status update suitable for GUI progress views."""
    print(f"[SeeRM bootstrap] {message}")


def run_command(
    args: Iterable[str], *, check: bool = True, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with consistent settings."""
    kwargs: dict[str, object] = {
        "text": True,
        "env": os.environ,
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
    result = subprocess.run(list(args), **kwargs)  # type: ignore[arg-type]
    if check and result.returncode != 0:
        raise BootstrapError(f"Command failed: {' '.join(args)}\n{result.stdout or ''}")
    return result


def assert_mac() -> None:
    """Guard against accidentally running on unsupported platforms."""
    if platform.system() != "Darwin":
        raise BootstrapError("This bootstrapper currently targets macOS only")


def ensure_1password_cli() -> None:
    """Confirm the 1Password CLI is installed and the user is authenticated."""
    if shutil.which("op") is None:
        raise BootstrapError(
            dedent(
                """
                1Password CLI (`op`) is not installed. Install it from
                https://developer.1password.com/docs/cli/get-started/
                before rerunning the SeeRM setup app.
                """
            ).strip()
        )
    try:
        run_command(["op", "whoami"], capture=True)
    except BootstrapError as exc:  # pragma: no cover - depends on operator state
        raise BootstrapError(
            "1Password CLI is installed but not signed in. Launch 1Password, "
            "unlock the account, then rerun the SeeRM setup app."
        ) from exc


def read_env_payload(reference: str) -> str:
    """Fetch the shared .env payload from 1Password."""
    log(f"Pulling secrets from 1Password ({reference})")
    result = run_command(["op", "read", reference], capture=True)
    payload = (result.stdout or "").strip()
    if not payload:
        raise BootstrapError(
            "Received empty secrets payload from 1Password. Check the vault "
            "item and ensure the .env field has been populated."
        )
    return payload


def write_env_file(payload: str) -> None:
    """Persist the secrets payload to ~/.seerm/.env with secure permissions."""
    CONFIG_HOME.mkdir(mode=0o700, exist_ok=True)
    ENV_PATH.write_text(payload)
    os.chmod(ENV_PATH, 0o600)
    log(f"Wrote secrets to {ENV_PATH}")


def locate_python() -> Path:
    """Locate a Python 3.11 interpreter."""
    candidate = shutil.which("python3.11")
    if candidate:
        return Path(candidate)
    raise BootstrapError(
        dedent(
            """
            Python 3.11 is required but was not found. Download the universal
            installer from https://www.python.org/downloads/macos/ or deploy
            it via your device management tooling, then rerun the SeeRM setup app.
            """
        ).strip()
    )


def ensure_virtualenv(python_path: Path) -> Path:
    """Create or refresh the repository virtual environment."""
    venv_path = REPO_ROOT / ".venv"
    log(f"Ensuring virtual environment at {venv_path}")
    run_command([str(python_path), "-m", "venv", str(venv_path)])
    return venv_path / "bin" / "python"


def pip_install(python_executable: Path) -> None:
    """Install dependencies using the virtual environment interpreter."""
    log("Installing SeeRM dependencies (this may take a minute)")
    run_command(
        [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip",
            "wheel",
            "setuptools",
        ]
    )
    run_command(
        [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "-r",
            str(REPO_ROOT / "requirements.txt"),
        ]
    )
    run_command([str(python_executable), "-m", "pip", "install", "-e", str(REPO_ROOT)])


def record_version(version: Optional[str]) -> None:
    """Store the deployed version so the update checker can compare builds."""
    if not version:
        return
    CONFIG_HOME.mkdir(mode=0o700, exist_ok=True)
    VERSION_PATH.write_text(version)
    log(f"Recorded deployed version {version} at {VERSION_PATH}")


def run_post_install_checks(python_executable: Path) -> None:
    """Run lightweight smoke checks to confirm the environment works."""
    log("Running SeeRM health check")
    run_command([str(python_executable), "-m", "app.main", "health"], capture=True)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Return parsed CLI arguments for the bootstrapper."""
    parser = argparse.ArgumentParser(description="Bootstrap a teammate's SeeRM workstation")
    parser.add_argument(
        "--op-env-reference",
        default=os.environ.get(DEFAULT_ENV_REFERENCE_ENV, DEFAULT_ENV_REFERENCE),
        help="1Password secret reference in op://<vault>/<item>/<field> format",
    )
    parser.add_argument(
        "--version",
        default=os.environ.get(DEFAULT_VERSION_ENV),
        help="Optional version string recorded to ~/.seerm/version",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip dependency installation (use only for diagnostics)",
    )
    parser.add_argument(
        "--skip-health-check",
        action="store_true",
        help="Skip the post-install health command",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the bootstrap workflow and surface a process exit code."""
    try:
        args = parse_args(argv)
        assert_mac()
        ensure_1password_cli()
        payload = read_env_payload(args.op_env_reference)
        write_env_file(payload)
        python_path = locate_python()
        venv_python = ensure_virtualenv(python_path)
        if not args.skip_install:
            pip_install(venv_python)
        record_version(args.version)
        if not args.skip_health_check:
            run_post_install_checks(venv_python)
        log("SeeRM bootstrap completed successfully")
        return 0
    except BootstrapError as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover - script entry point
    sys.exit(main())
