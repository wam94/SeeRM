#!/usr/bin/env python3
"""Interactive Gmail OAuth helper for SeeRM teammates.

The weekly digest automation requires a Gmail refresh token with read/send
scopes. This helper walks a teammate through the OAuth consent flow and
updates their local ``~/.seerm/.env`` with the resulting token. When
configured with 1Password details it can also push the updated secrets back
into the shared vault so the bootstrapper remains in sync.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Dict, Optional

from google_auth_oauthlib.flow import InstalledAppFlow

from . import DEFAULT_ENV_REFERENCE

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
CONFIG_HOME = Path.home() / ".seerm"
ENV_PATH = CONFIG_HOME / ".env"
DEFAULT_ENV_REFERENCE_ENV = "SEERM_OP_ENV_REFERENCE"


class OAuthSetupError(RuntimeError):
    """Raised when the Gmail OAuth flow cannot complete."""


def read_env_file(path: Path) -> Dict[str, str]:
    """Load a minimal .env file into a dictionary."""
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def write_env_file(path: Path, data: Dict[str, str]) -> None:
    """Persist environment variables back to disk in a deterministic order."""
    lines = ["# Managed by SeeRM Gmail OAuth helper"]
    for key in sorted(data):
        lines.append(f"{key}={data[key]}")
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def ensure_gmail_credentials(data: Dict[str, str]) -> None:
    """Validate the presence of Gmail OAuth client credentials."""
    missing = [key for key in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET") if not data.get(key)]
    if missing:
        raise OAuthSetupError(
            "Missing Gmail OAuth client credentials in the secrets file. "
            "Populate GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in 1Password "
            "then rerun the helper."
        )


def run_oauth_flow(client_id: str, client_secret: str, scopes: list[str]) -> str:
    """Execute the installed-app OAuth flow and return a refresh token."""
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": ["http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=scopes)
    auth_url, _ = flow.authorization_url(
        prompt="consent", access_type="offline", include_granted_scopes="true"
    )
    webbrowser.open(auth_url, new=1, autoraise=True)
    creds = flow.run_local_server(port=0)
    refresh_token = creds.refresh_token
    if not refresh_token:
        raise OAuthSetupError(
            "Google did not return a refresh token. Ensure you selected the "
            "correct account and granted the requested scopes, then try again."
        )
    return refresh_token


def ensure_1password_cli() -> None:
    """Best-effort validation that the 1Password CLI is ready."""
    if shutil.which("op") is None:
        raise OAuthSetupError(
            "1Password CLI (`op`) is required for --push-to-1password but was not found."
        )
    result = subprocess.run(["op", "whoami"], text=True, capture_output=True)
    if result.returncode != 0:
        raise OAuthSetupError("1Password CLI is not signed in; unlock the account and retry.")


def push_env_to_1password(reference: str, env_content: str) -> None:
    """Update the configured 1Password item with the refreshed .env payload."""
    if not reference.startswith("op://"):
        raise OAuthSetupError("1Password reference must look like op://Vault/Item/Field")
    _, remainder = reference.split("op://", 1)
    parts = remainder.split("/")
    if len(parts) < 3:
        raise OAuthSetupError("1Password reference must include vault, item, and field")
    vault, item, field = parts[0], parts[1], "/".join(parts[2:])
    with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
        tmp.write(env_content)
        tmp.flush()
        command = [
            "op",
            "item",
            "edit",
            item,
            f"{field}=@{tmp.name}",
            "--vault",
            vault,
        ]
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode != 0:
            message = (
                f"Failed to update 1Password item '{item}' in vault '{vault}':\n"
                f"{result.stdout}{result.stderr}"
            )
            raise OAuthSetupError(message)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments for the Gmail OAuth helper."""
    parser = argparse.ArgumentParser(description="Run Gmail OAuth and update SeeRM secrets")
    parser.add_argument(
        "--env-path",
        default=str(ENV_PATH),
        help="Path to the local secrets file that will be updated",
    )
    parser.add_argument(
        "--op-env-reference",
        default=os.environ.get(DEFAULT_ENV_REFERENCE_ENV, DEFAULT_ENV_REFERENCE),
        help="Optional 1Password reference to push the refreshed .env back upstream",
    )
    parser.add_argument(
        "--skip-1password",
        action="store_true",
        help="Skip pushing the updated .env to 1Password",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the Gmail OAuth helper and update secrets as needed."""
    try:
        args = parse_args(argv)
        env_path = Path(args.env_path).expanduser()
        data = read_env_file(env_path)
        ensure_gmail_credentials(data)
        refresh_token = run_oauth_flow(data["GMAIL_CLIENT_ID"], data["GMAIL_CLIENT_SECRET"], SCOPES)
        data["GMAIL_REFRESH_TOKEN"] = refresh_token
        write_env_file(env_path, data)
        if not args.skip_1password:
            ensure_1password_cli()
            push_env_to_1password(args.op_env_reference, env_path.read_text())
        print("Gmail OAuth complete. Secrets updated.")
        return 0
    except OAuthSetupError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover - script entry point
    sys.exit(main())
