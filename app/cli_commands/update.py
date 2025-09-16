"""
Check for latest published version and compare to local.

Usage:
  seerm update-check
"""

from __future__ import annotations

import click
import httpx

PACKAGE_NAME = "seerm"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"


@click.command(name="update-check")
def update_check():
    """Check PyPI for newer versions of the SeeRM package."""
    try:
        try:
            from importlib.metadata import version

            local_version = version(PACKAGE_NAME)
        except Exception:
            local_version = "unknown (not installed as a package)"

        with httpx.Client(timeout=10.0) as client:
            r = client.get(PYPI_URL)
            r.raise_for_status()
            data = r.json()
            latest = data.get("info", {}).get("version", "unknown")

        click.echo(f"Local version: {local_version}")
        click.echo(f"Latest  version: {latest}")

        if local_version == "unknown (not installed as a package)":
            click.echo("\nTip: install via pipx for easy upgrades: pipx install seerm")
        elif latest != "unknown" and local_version != latest:
            click.echo("\nA newer version is available.")
            click.echo("Upgrade with: pipx upgrade seerm  (or) pip install -U seerm")
        else:
            click.echo("\nYou are on the latest version.")

    except Exception as e:
        click.echo(f"Update check failed: {e}")
