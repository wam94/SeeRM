#!/usr/bin/env python3
"""Minimal GUI for running common SeeRM workflows on macOS.

The control center is intended to be wrapped in a Platypus/Automator app so
non-technical teammates can run the core CLI activities with buttons:

* Health check (ensures dependencies and credentials are valid)
* Weekly digest dry run (fetches Gmail CSV, renders reports without send)
* Weekly digest send (production run)
* Check for updates (consults the published manifest)

The UI intentionally keeps dependencies light (only Tkinter from the Python
standard library) so it works in the stock CPython runtime installed by the
bootstrapper.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

try:  # Tkinter is part of the stdlib but missing on some stripped Python builds
    import tkinter as tk
    from tkinter import messagebox, scrolledtext
except Exception as exc:  # pragma: no cover - depends on runtime
    raise SystemExit("Tkinter is required for the SeeRM control center") from exc

CONFIG_HOME = Path.home() / ".seerm"
VERSION_PATH = CONFIG_HOME / "version"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_ENV = "SEERM_UPDATE_MANIFEST"
DEFAULT_MANIFEST_URL = "https://example.com/seerm/latest.json"


def log(message: str) -> None:
    """Print to stdout so the wrapper app can capture logs if needed."""
    print(f"[SeeRM control-center] {message}")


def detect_python() -> str:
    """Return the virtualenv Python if present, otherwise fall back to sys.executable."""
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def run_subprocess(args: Iterable[str], output_queue: queue.Queue[str]) -> int:
    """Execute a command, streaming output lines into the UI queue."""
    process = subprocess.Popen(
        list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    assert process.stdout is not None
    for line in process.stdout:
        output_queue.put(line.rstrip())
    return process.wait()


def load_local_version() -> Optional[str]:
    """Return the locally installed version string, if recorded."""
    if VERSION_PATH.exists():
        return VERSION_PATH.read_text().strip() or None
    return None


def fetch_remote_manifest(url: str) -> dict[str, str]:
    """Download and parse the remote update manifest."""
    with urllib.request.urlopen(
        url, timeout=10
    ) as response:  # nosec - controlled URL in deployment
        data = response.read().decode("utf-8")
    manifest = json.loads(data)
    if "version" not in manifest or "url" not in manifest:
        raise ValueError("Manifest missing required fields 'version' and 'url'")
    return manifest


class ControlCenter(tk.Tk):
    """Simple Tkinter window that runs SeeRM commands in background threads."""

    def __init__(self, python_path: str, manifest_url: str) -> None:
        """Initialise the control center window and widgets."""
        super().__init__()
        self.python_path = python_path
        self.manifest_url = manifest_url
        self.title("SeeRM Control Center")
        self.geometry("720x480")
        self.resizable(width=True, height=True)

        self.output = scrolledtext.ScrolledText(self, state=tk.DISABLED, wrap=tk.WORD)
        self.output.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        button_frame = tk.Frame(self)
        button_frame.pack(fill=tk.X, padx=16, pady=(0, 16))

        buttons = [
            ("Health Check", self.run_health_check),
            ("Digest Dry Run", self.run_digest_dry_run),
            ("Send Weekly Digest", self.run_digest_full),
            ("Check for Updates", self.run_update_check),
        ]
        for label, command in buttons:
            tk.Button(button_frame, text=label, command=command, width=20).pack(
                side=tk.LEFT, padx=4
            )

        self.queue: queue.Queue[str] = queue.Queue()
        self.after(200, self._poll_queue)

    # ----------------------------- UI helpers -----------------------------

    def append_output(self, text: str) -> None:
        """Append a line to the scrollback output box."""
        self.output.configure(state=tk.NORMAL)
        self.output.insert(tk.END, text + "\n")
        self.output.see(tk.END)
        self.output.configure(state=tk.DISABLED)

    def run_in_background(self, description: str, command: Iterable[str]) -> None:
        """Spawn a worker thread that executes a subprocess command."""
        self.append_output("")
        self.append_output(f"→ {description}")

        def worker() -> None:
            start = time.time()
            try:
                returncode = run_subprocess(command, self.queue)
                duration = time.time() - start
                self.queue.put(f"← {description} finished in {duration:.1f}s (exit {returncode})")
                if returncode != 0:
                    messagebox.showerror("SeeRM", f"'{description}' failed (exit {returncode})")
            except Exception as exc:  # pragma: no cover - background thread
                self.queue.put(f"← {description} failed: {exc}")
                messagebox.showerror("SeeRM", f"{description} failed\n{exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self) -> None:
        """Drain queued output from background threads."""
        while not self.queue.empty():
            line = self.queue.get_nowait()
            self.append_output(line)
        self.after(200, self._poll_queue)

    # --------------------------- Command buttons --------------------------

    def run_health_check(self) -> None:
        """Run the SeeRM health check command."""
        command = [self.python_path, "-m", "app.main", "health"]
        self.run_in_background("Health check", command)

    def run_digest_dry_run(self) -> None:
        """Execute the dry-run digest workflow."""
        command = [
            self.python_path,
            "-m",
            "app.main",
            "--dry-run",
            "digest-dry-run",
            "--max-messages",
            "1",
        ]
        self.run_in_background("Digest dry run", command)

    def run_digest_full(self) -> None:
        """Trigger the full digest send flow."""
        command = [self.python_path, "-m", "app.main", "digest"]
        self.run_in_background("Send weekly digest", command)

    def run_update_check(self) -> None:
        """Fetch the remote manifest and show version information."""
        self.append_output("→ Checking for updates…")

        def worker() -> None:
            try:
                manifest = fetch_remote_manifest(self.manifest_url)
                local_version = load_local_version() or "not installed"
                remote_version = manifest["version"]
                if local_version == remote_version:
                    message = f"You are on the latest version ({remote_version})."
                else:
                    message = (
                        "A new version is available!\n"
                        f"Current: {local_version}\n"
                        f"Latest:  {remote_version}\n"
                        f"Download: {manifest['url']}"
                    )
                self.queue.put(message)
                messagebox.showinfo("SeeRM", message)
            except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
                error_msg = f"Update check failed: {exc}"
                self.queue.put(error_msg)
                messagebox.showerror("SeeRM", error_msg)

        threading.Thread(target=worker, daemon=True).start()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments when launching the control center."""
    parser = argparse.ArgumentParser(description="Graphical control center for SeeRM")
    parser.add_argument(
        "--manifest-url",
        default=os.environ.get(DEFAULT_MANIFEST_ENV, DEFAULT_MANIFEST_URL),
        help="HTTP(S) URL pointing at the deployment manifest JSON",
    )
    parser.add_argument(
        "--python",
        default=None,
        help="Optional override for the Python executable used to run commands",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the control center application."""
    args = parse_args(argv)
    python_path = args.python or detect_python()
    app = ControlCenter(python_path=python_path, manifest_url=args.manifest_url)
    log("Launching SeeRM control center UI")
    app.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    sys.exit(main())
