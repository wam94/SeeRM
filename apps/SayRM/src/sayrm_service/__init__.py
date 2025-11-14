"""
SayRM service package.

Resolves the repository root for local file paths (templates, DB),
without taking a hard dependency on other app source trees.
"""

from __future__ import annotations

from pathlib import Path

# Determine the repository root robustly by walking up until we find
# a directory that looks like the project root (contains "apps" and
# a top-level requirements.txt). Fallback to a fixed ascent if needed.
_here = Path(__file__).resolve()
_ROOT = None
for parent in _here.parents:
    if (parent / "apps").exists() and (parent / "requirements.txt").exists():
        _ROOT = parent
        break
if _ROOT is None:
    # Fallback: ascend 4 levels to reach repo root from apps/SayRM/src/sayrm_service/
    _ROOT = _here.parents[4]

__all__ = ["_ROOT"]
