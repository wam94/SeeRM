"""Utilities for normalising LLM payloads that should contain JSON objects."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


def coerce_json_payload(text: Optional[str]) -> Dict[str, Any]:
    """
    Extract and parse a JSON object from an LLM response.

    Handles code fences, extra prose, and partial JSON snippets.
    """
    if not text:
        raise ValueError("Empty payload")

    candidate = text.strip()

    if candidate.startswith("```"):
        parts = candidate.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("{"):
                candidate = part
                break

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if match:
            snippet = match.group(0)
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                pass
        raise ValueError("Could not extract JSON from payload")
