#!/usr/bin/env python3
"""Generate macOS .app bundles that wrap the SeeRM onboarding scripts.

The produced bundles live in ``dist/mac`` by default and can be copied into a
DMG alongside the repository so non-technical teammates can double-click to run
setup, Gmail OAuth, or the control center UI.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "dist" / "mac"
DEFAULT_IDENTIFIER_ROOT = "com.seerm"
DEFAULT_VERSION = "0.0.0"
DEFAULT_ENV_REFERENCE = "op://SeeRM Deployment/.env"
DEFAULT_MANIFEST_URL = "https://example.com/seerm/latest.json"


@dataclass
class AppBundleSpec:
    """Definition of a generated macOS app bundle."""

    name: str
    identifier_suffix: str
    script_relative_path: str
    use_venv_python: bool
    extra_env: Dict[str, str]
    args: Iterable[str]

    @property
    def identifier(self) -> str:
        """Return the computed CFBundleIdentifier."""
        return f"{DEFAULT_IDENTIFIER_ROOT}.{self.identifier_suffix}".lower()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the app bundler."""
    parser = argparse.ArgumentParser(description="Build macOS app bundles for SeeRM onboarding")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where .app bundles will be written",
    )
    parser.add_argument(
        "--version",
        default=os.environ.get("SEERM_BUILD_VERSION", DEFAULT_VERSION),
        help="Version string baked into the bundle metadata",
    )
    parser.add_argument(
        "--op-env-reference",
        default=os.environ.get("SEERM_OP_ENV_REFERENCE", DEFAULT_ENV_REFERENCE),
        help="1Password reference shared across onboarding tools",
    )
    parser.add_argument(
        "--manifest-url",
        default=os.environ.get("SEERM_UPDATE_MANIFEST", DEFAULT_MANIFEST_URL),
        help="URL that the control center uses for update checks",
    )
    return parser.parse_args()


def ensure_dir(path: Path) -> None:
    """Ensure that a directory exists."""
    path.mkdir(parents=True, exist_ok=True)


def write_info_plist(path: Path, *, name: str, identifier: str, version: str) -> None:
    """Write a minimal Info.plist for the given bundle metadata."""
    info = {
        "CFBundleName": name,
        "CFBundleDisplayName": name,
        "CFBundleExecutable": "run",
        "CFBundleIdentifier": identifier,
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": version,
        "CFBundleShortVersionString": version,
        "LSMinimumSystemVersion": "12.0",
    }
    with path.open("wb") as fp:
        plistlib.dump(info, fp)


def launch_script(
    name: str,
    relative_script: str,
    *,
    use_venv_python: bool,
    extra_env: Dict[str, str],
    args: Iterable[str],
) -> str:
    """Generate the shell launcher used inside a macOS bundle."""
    lines: List[str] = [
        "#!/bin/bash",
        "set -euo pipefail",
        "",
        'APP_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"',
        'DIST_DIR="$(dirname "$APP_ROOT")"',
        'REPO_ROOT="$(dirname "$DIST_DIR")"',
        "",
    ]

    for key, value in extra_env.items():
        lines.append(f"export {key}={shell_quote(value)}")

    if extra_env:
        lines.append("")

    if use_venv_python:
        lines.extend(
            [
                'PYTHON_EXEC="$(command -v python3)"',
                'if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then',
                '    PYTHON_EXEC="$REPO_ROOT/.venv/bin/python"',
                "fi",
            ]
        )
    else:
        lines.append('PYTHON_EXEC="$(command -v python3)"')

    lines.append("")

    arg_text = " ".join(shell_quote(arg) for arg in args)
    command_line = f'exec "$PYTHON_EXEC" "$REPO_ROOT/{relative_script}"'
    if arg_text:
        command_line += f" {arg_text}"
    lines.append(command_line)
    lines.append("")
    return "\n".join(lines)


def shell_quote(value: str) -> str:
    """Return a safely single-quoted shell argument."""
    return "'" + value.replace("'", "'\\''") + "'"


def write_launcher(path: Path, content: str) -> None:
    """Write the launcher file and mark it executable."""
    path.write_text(content)
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_app(spec: AppBundleSpec, *, output_dir: Path, version: str) -> Path:
    """Create an app bundle from the provided specification."""
    bundle_dir = output_dir / f"{spec.name}.app"
    contents_dir = bundle_dir / "Contents"
    macos_dir = contents_dir / "MacOS"
    ensure_dir(macos_dir)
    ensure_dir(contents_dir)

    write_info_plist(
        contents_dir / "Info.plist",
        name=spec.name,
        identifier=spec.identifier,
        version=version,
    )

    launcher = launch_script(
        spec.name,
        spec.script_relative_path,
        use_venv_python=spec.use_venv_python,
        extra_env=spec.extra_env,
        args=spec.args,
    )

    write_launcher(macos_dir / "run", launcher)
    return bundle_dir


def main() -> int:
    """Build all macOS onboarding app bundles."""
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser()
    ensure_dir(output_dir)

    specs: List[AppBundleSpec] = [
        AppBundleSpec(
            name="Setup SeeRM",
            identifier_suffix="setup",
            script_relative_path="scripts/onboarding/bootstrap_teammate.py",
            use_venv_python=False,
            extra_env={
                "SEERM_OP_ENV_REFERENCE": args.op_env_reference,
                "SEERM_UPDATE_VERSION": args.version,
            },
            args=(),
        ),
        AppBundleSpec(
            name="SeeRM Gmail Auth",
            identifier_suffix="gmailauth",
            script_relative_path="scripts/onboarding/gmail_oauth_setup.py",
            use_venv_python=True,
            extra_env={
                "SEERM_OP_ENV_REFERENCE": args.op_env_reference,
            },
            args=(),
        ),
        AppBundleSpec(
            name="SeeRM Control Center",
            identifier_suffix="controlcenter",
            script_relative_path="scripts/onboarding/control_center.py",
            use_venv_python=True,
            extra_env={
                "SEERM_UPDATE_MANIFEST": args.manifest_url,
            },
            args=(),
        ),
    ]

    bundles = [build_app(spec, output_dir=output_dir, version=args.version) for spec in specs]

    print("Generated app bundles:")
    for bundle in bundles:
        print(f" - {bundle}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
