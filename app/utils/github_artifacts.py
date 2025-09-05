"""
GitHub Actions artifact utilities for uploading HTML reports.

This module provides functions to create and upload artifacts
in GitHub Actions workflows, making HTML reports accessible
even when email delivery fails.
"""

import json
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)


class GitHubArtifactManager:
    """Manages GitHub Actions artifacts for report delivery."""

    def __init__(self):
        self.is_github_actions = self._is_running_in_github_actions()
        self.artifacts_dir = Path("./github_artifacts")
        self.artifacts_dir.mkdir(exist_ok=True)

    def _is_running_in_github_actions(self) -> bool:
        """Check if running in GitHub Actions environment."""
        return bool(os.getenv("GITHUB_ACTIONS"))

    def prepare_html_artifact(
        self, html_files: List[Path], artifact_name: Optional[str] = None
    ) -> Optional[Path]:
        """
        Prepare HTML files as GitHub Actions artifact.

        Args:
            html_files: List of HTML file paths to include
            artifact_name: Optional custom artifact name

        Returns:
            Path to created artifact zip file, or None if not in GitHub Actions
        """
        if not self.is_github_actions:
            logger.debug("Not running in GitHub Actions - skipping artifact creation")
            return None

        if not html_files:
            logger.warning("No HTML files provided for artifact creation")
            return None

        # Generate artifact name
        if not artifact_name:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            artifact_name = f"intelligence-reports-{timestamp}"

        artifact_zip = self.artifacts_dir / f"{artifact_name}.zip"

        try:
            with zipfile.ZipFile(artifact_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for html_file in html_files:
                    if html_file.exists():
                        # Add file with a clean name
                        archive_name = html_file.name
                        zf.write(html_file, archive_name)
                        logger.info(
                            "Added file to artifact", file=str(html_file), archive_name=archive_name
                        )

                # Add metadata file
                metadata = {
                    "created": datetime.utcnow().isoformat(),
                    "workflow_run": os.getenv("GITHUB_RUN_ID"),
                    "repository": os.getenv("GITHUB_REPOSITORY"),
                    "commit": os.getenv("GITHUB_SHA"),
                    "files": [f.name for f in html_files if f.exists()],
                    "description": "SeeRM Intelligence Reports - Email delivery fallback",
                }

                zf.writestr("artifact_metadata.json", json.dumps(metadata, indent=2))

                # Add README for artifact
                readme_content = f"""# SeeRM Intelligence Reports

Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC

## Contents

{chr(10).join(f'- {f.name}' for f in html_files if f.exists())}

## About

These HTML files were generated because email delivery failed.
Each file contains a complete intelligence report that can be viewed in any web browser.

## GitHub Workflow

- Repository: {os.getenv('GITHUB_REPOSITORY', 'Unknown')}
- Run ID: {os.getenv('GITHUB_RUN_ID', 'Unknown')}
- Commit: {os.getenv('GITHUB_SHA', 'Unknown')[:8] + '...' if os.getenv('GITHUB_SHA') else 'Unknown'}

## Usage

1. Download this artifact from the GitHub Actions run
2. Extract the ZIP file
3. Open any HTML file in your web browser
4. Files are self-contained with embedded styling
"""

                zf.writestr("README.md", readme_content)

            logger.info(
                "GitHub Actions artifact created",
                artifact_path=str(artifact_zip),
                artifact_name=artifact_name,
                file_count=len(html_files),
            )

            return artifact_zip

        except Exception as e:
            logger.error(
                "Failed to create GitHub Actions artifact",
                error=str(e),
                artifact_name=artifact_name,
            )
            return None

    def set_workflow_output(self, name: str, value: str) -> None:
        """Set GitHub Actions workflow output."""
        if not self.is_github_actions:
            return

        try:
            github_output = os.getenv("GITHUB_OUTPUT")
            if github_output:
                with open(github_output, "a", encoding="utf-8") as f:
                    f.write(f"{name}={value}\n")
                logger.info("Set workflow output", name=name, value=value)
        except Exception as e:
            logger.error("Failed to set workflow output", error=str(e), name=name)

    def create_job_summary(
        self, html_files: List[Path], delivery_results: List[Dict[str, Any]]
    ) -> None:
        """Create GitHub Actions job summary with report links."""
        if not self.is_github_actions:
            return

        try:
            github_step_summary = os.getenv("GITHUB_STEP_SUMMARY")
            if not github_step_summary:
                return

            summary_content = "# 📊 SeeRM Intelligence Reports\n\n"

            # Delivery status overview
            successful_deliveries = sum(1 for r in delivery_results if r.get("delivered"))
            failed_deliveries = len(delivery_results) - successful_deliveries

            summary_content += f"## 📈 Delivery Summary\n\n"
            summary_content += f"- ✅ Successful deliveries: {successful_deliveries}\n"
            summary_content += f"- ⚠️ Failed deliveries (saved as files): {failed_deliveries}\n\n"

            # File details
            if html_files:
                summary_content += f"## 📁 Generated Reports\n\n"
                for html_file in html_files:
                    if html_file.exists():
                        file_size = html_file.stat().st_size
                        size_kb = file_size / 1024
                        summary_content += f"- 📄 **{html_file.name}** ({size_kb:.1f} KB)\n"

                summary_content += f"\n💡 **Download artifacts** from this workflow run to access HTML reports.\n\n"

            # Delivery details
            if delivery_results:
                summary_content += f"## 📤 Delivery Details\n\n"
                for i, result in enumerate(delivery_results, 1):
                    method = result.get("method", "unknown")
                    status = "✅" if result.get("delivered") else "❌"

                    if method == "email":
                        summary_content += f"{i}. {status} Email delivered successfully\n"
                    elif method == "file":
                        summary_content += f"{i}. {status} Email failed - Saved as HTML file\n"
                        if "error" in result:
                            summary_content += f"   - Error: `{result['error']}`\n"
                    else:
                        summary_content += f"{i}. {status} Delivery failed\n"

                summary_content += "\n"

            summary_content += "---\n"
            summary_content += f"*Generated by SeeRM Intelligence Reports at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC*\n"

            with open(github_step_summary, "w", encoding="utf-8") as f:
                f.write(summary_content)

            logger.info("Created GitHub Actions job summary", files=len(html_files))

        except Exception as e:
            logger.error("Failed to create job summary", error=str(e))


# Convenience function
def create_github_artifact_manager() -> GitHubArtifactManager:
    """Create GitHub Actions artifact manager."""
    return GitHubArtifactManager()
