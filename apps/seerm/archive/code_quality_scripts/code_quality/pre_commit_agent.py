"""
Pre-commit hook enhancement agent.

Automatically fixes common linting and formatting issues before they become
blocking problems in the development workflow.
"""

import ast
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import structlog

logger = structlog.get_logger(__name__)


class PreCommitEnhancementAgent:
    """Agent that enhances pre-commit hooks with intelligent auto-fixes."""

    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.app_dir = self.project_root / "app"

    def run_auto_fixes(self, file_paths: Optional[List[str]] = None) -> Dict[str, List[str]]:
        """
        Run all auto-fixes on specified files or entire app directory.

        Args:
            file_paths: Specific files to fix, or None for all Python files in app/

        Returns:
            Dictionary mapping fix type to list of files modified
        """
        if file_paths is None:
            file_paths = self._get_python_files()

        results = {
            "unused_imports": [],
            "line_length": [],
            "f_string_fixes": [],
            "import_organization": [],
            "docstring_fixes": [],
            "security_fixes": [],
        }

        for file_path in file_paths:
            try:
                path_obj = Path(file_path)
                if not path_obj.exists() or not str(path_obj).endswith(".py"):
                    continue

                logger.info("Processing file", file=str(path_obj))

                # Read original content
                with open(path_obj, "r", encoding="utf-8") as f:
                    original_content = f.read()

                modified_content = original_content
                file_modified = False

                # Apply fixes in order
                content, modified = self._fix_unused_imports(modified_content, path_obj)
                if modified:
                    modified_content = content
                    file_modified = True
                    results["unused_imports"].append(str(path_obj))

                content, modified = self._fix_line_length(modified_content)
                if modified:
                    modified_content = content
                    file_modified = True
                    results["line_length"].append(str(path_obj))

                content, modified = self._fix_f_strings(modified_content)
                if modified:
                    modified_content = content
                    file_modified = True
                    results["f_string_fixes"].append(str(path_obj))

                content, modified = self._fix_bare_except(modified_content)
                if modified:
                    modified_content = content
                    file_modified = True
                    results["security_fixes"].append(str(path_obj))

                # Write back if modified
                if file_modified:
                    with open(path_obj, "w", encoding="utf-8") as f:
                        f.write(modified_content)
                    logger.info("Auto-fixed file", file=str(path_obj))

            except Exception as e:
                logger.warning("Failed to process file", file=str(file_path), error=str(e))

        return results

    def _get_python_files(self) -> List[str]:
        """Get all Python files in the app directory."""
        python_files = []
        for path in self.app_dir.rglob("*.py"):
            if "__pycache__" not in str(path):
                python_files.append(str(path))
        return python_files

    def _fix_unused_imports(self, content: str, file_path: Path) -> Tuple[str, bool]:
        """Remove unused imports while preserving necessary ones."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return content, False

        # Find all imports
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((alias.name, alias.asname))
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imports.append((alias.name, alias.asname))

        # Find used names (simple heuristic)
        used_names = set()
        for line in content.split("\n"):
            # Skip import lines
            if line.strip().startswith(("import ", "from ")):
                continue
            # Extract identifiers
            words = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", line)
            used_names.update(words)

        # Remove obvious unused imports
        lines = content.split("\n")
        modified_lines = []
        modified = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                # Check for common unused patterns
                if any(
                    pattern in stripped
                    for pattern in [
                        "import os",  # Often unused
                        "import logging",  # When structlog is used instead
                        "from rich import print as rprint",  # Specific to your codebase
                    ]
                ):
                    # Simple check - if the imported name doesn't appear elsewhere
                    import_name = self._extract_import_name(stripped)
                    if import_name and import_name not in used_names:
                        logger.info("Removing unused import", line=stripped, file=str(file_path))
                        modified = True
                        continue

            modified_lines.append(line)

        return "\n".join(modified_lines), modified

    def _extract_import_name(self, import_line: str) -> Optional[str]:
        """Extract the usable name from an import statement."""
        if "import" not in import_line:
            return None

        # Handle 'from x import y as z'
        if " as " in import_line:
            return import_line.split(" as ")[-1].strip()

        # Handle 'import x'
        if import_line.strip().startswith("import "):
            return import_line.replace("import ", "").strip().split(".")[0]

        # Handle 'from x import y'
        if "from " in import_line and " import " in import_line:
            parts = import_line.split(" import ")[-1].strip()
            return parts.split(",")[0].strip()

        return None

    def _fix_line_length(self, content: str) -> Tuple[str, bool]:
        """Fix basic line length issues."""
        lines = content.split("\n")
        modified_lines = []
        modified = False

        for line in lines:
            if len(line) > 100:
                # Simple fixes for common patterns
                if line.strip().startswith("#"):
                    # Long comments - try to wrap
                    if len(line.strip()) > 100:
                        # Split long comments
                        words = line.strip()[1:].split()
                        wrapped_lines = []
                        current_line = "#"

                        for word in words:
                            if len(current_line + " " + word) <= 100:
                                current_line += " " + word
                            else:
                                wrapped_lines.append(current_line)
                                current_line = "# " + word

                        wrapped_lines.append(current_line)

                        # Preserve original indentation
                        indent = len(line) - len(line.lstrip())
                        wrapped_with_indent = [" " * indent + wrapped_lines[0][1:]]
                        for wrapped in wrapped_lines[1:]:
                            wrapped_with_indent.append(" " * indent + wrapped)

                        modified_lines.extend(wrapped_with_indent)
                        modified = True
                        continue

                # String concatenation fixes
                if "+" in line and '"' in line:
                    # Try to split long string concatenations
                    fixed_line = self._fix_string_concatenation(line)
                    if fixed_line != line:
                        modified_lines.append(fixed_line)
                        modified = True
                        continue

            modified_lines.append(line)

        return "\n".join(modified_lines), modified

    def _fix_string_concatenation(self, line: str) -> str:
        """Fix long string concatenation lines."""
        # Simple pattern for string concatenation
        if " + " in line and len(line) > 100:
            parts = line.split(" + ")
            if len(parts) > 1:
                indent = len(line) - len(line.lstrip())
                fixed_parts = [parts[0]]
                for part in parts[1:]:
                    fixed_parts.append(" " * (indent + 4) + "+ " + part.strip())
                return "\n".join(fixed_parts)
        return line

    def _fix_f_strings(self, content: str) -> Tuple[str, bool]:
        """Fix f-strings that are missing placeholders."""
        lines = content.split("\n")
        modified_lines = []
        modified = False

        for line in lines:
            # Find f-strings without placeholders
            if '"' in line or "'" in line:
                # Check if there are no {} placeholders
                f_string_pattern = r'f["\'][^"\']*["\']'
                matches = re.findall(f_string_pattern, line)

                for match in matches:
                    if "{" not in match:
                        # Remove f-prefix if no placeholders
                        fixed_match = match[1:]  # Remove 'f' prefix
                        line = line.replace(match, fixed_match)
                        modified = True
                        logger.info(
                            "Fixed f-string without placeholders", original=match, fixed=fixed_match
                        )

            modified_lines.append(line)

        return "\n".join(modified_lines), modified

    def _fix_bare_except(self, content: str) -> Tuple[str, bool]:
        """Fix bare except clauses."""
        lines = content.split("\n")
        modified_lines = []
        modified = False

        for line in lines:
            stripped = line.strip()
            if stripped == "except:":
                # Replace bare except with Exception
                indent = len(line) - len(line.lstrip())
                fixed_line = " " * indent + "except Exception:"
                modified_lines.append(fixed_line)
                modified = True
                logger.info("Fixed bare except clause")
            else:
                modified_lines.append(line)

        return "\n".join(modified_lines), modified

    def run_formatters(self) -> Dict[str, bool]:
        """Run Black and isort formatters."""
        results = {}

        try:
            # Run Black
            black_result = subprocess.run(
                ["black", "--line-length=100", str(self.app_dir)],
                capture_output=True,
                text=True,
                cwd=self.project_root,
            )
            results["black"] = black_result.returncode == 0
            if black_result.returncode != 0:
                logger.warning("Black formatting failed", error=black_result.stderr)
            else:
                logger.info("Black formatting completed successfully")

        except FileNotFoundError:
            logger.warning("Black not found, skipping formatting")
            results["black"] = False

        try:
            # Run isort
            isort_result = subprocess.run(
                ["isort", "--profile=black", "--line-length=100", str(self.app_dir)],
                capture_output=True,
                text=True,
                cwd=self.project_root,
            )
            results["isort"] = isort_result.returncode == 0
            if isort_result.returncode != 0:
                logger.warning("isort failed", error=isort_result.stderr)
            else:
                logger.info("isort completed successfully")

        except FileNotFoundError:
            logger.warning("isort not found, skipping import sorting")
            results["isort"] = False

        return results

    def validate_fixes(self) -> Dict[str, List[str]]:
        """Validate that fixes don't break syntax."""
        issues = {"syntax_errors": [], "import_errors": []}

        for file_path in self._get_python_files():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                ast.parse(content)
            except SyntaxError as e:
                issues["syntax_errors"].append(f"{file_path}:{e.lineno} - {e.msg}")
            except Exception as e:
                issues["import_errors"].append(f"{file_path} - {str(e)}")

        return issues
