#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade build
python -m build

echo "\nBuilt distributions in dist/. To install with pipx:"
echo "  pipx install dist/seerm-*.whl"
