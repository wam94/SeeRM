"""Template loading + rendering helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class TemplateDefinition:
    id: str
    title: str
    description: str
    body: str
    tags: list[str]


class TemplateService:
    """Loads email templates from a JSON manifest."""

    def __init__(self, template_manifest: Path) -> None:
        self._manifest = template_manifest

    def _read(self) -> List[dict]:
        if not self._manifest.exists():
            raise FileNotFoundError(f"Template manifest not found at {self._manifest}")
        data = json.loads(self._manifest.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Template manifest must be a list of templates")
        return data

    def list_templates(self) -> List[TemplateDefinition]:
        templates = []
        for item in self._read():
            templates.append(
                TemplateDefinition(
                    id=item["id"],
                    title=item.get("title", item["id"]),
                    description=item.get("description", ""),
                    body=item.get("body", ""),
                    tags=item.get("tags", []),
                )
            )
        return templates

    def get_template(self, template_id: str) -> Optional[TemplateDefinition]:
        for template in self.list_templates():
            if template.id == template_id:
                return template
        return None

