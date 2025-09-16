#!/usr/bin/env bash
set -euo pipefail

# Build a single-file binary using PyInstaller.
# Note: Build on each target OS for best compatibility.

python -m pip install --upgrade pyinstaller

pyinstaller \
  --name seerm \
  --onefile \
  --hidden-import googleapiclient.discovery \
  --hidden-import googleapiclient.errors \
  --hidden-import pandas \
  --hidden-import httpx \
  --hidden-import structlog \
  --hidden-import tenacity \
  --hidden-import click \
  app/main.py

echo "\nBinary built at dist/seerm (or dist/seerm.exe on Windows)."
