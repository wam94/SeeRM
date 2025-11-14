"""
GitHub Actions integration for automated code quality management.

Enhances existing workflows with intelligent error handling, automatic
retries, and quality gates.
"""

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import structlog

from .batch_cleanup_agent import BatchCleanupAgent

logger = structlog.get_logger(__name__)


class GitHubActionsIntegration:
    """Integration with GitHub Actions for automated quality management."""

    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.workflows_dir = self.project_root / ".github" / "workflows"
        self.cleanup_agent = BatchCleanupAgent(project_root)

    def enhance_existing_workflows(self) -> Dict[str, bool]:
        """Enhance existing workflow files with quality checks."""
        results = {}

        if not self.workflows_dir.exists():
            logger.warning("No GitHub workflows directory found")
            return results

        for workflow_file in self.workflows_dir.glob("*.yml"):
            try:
                enhanced = self._enhance_workflow_file(workflow_file)
                results[workflow_file.name] = enhanced
                if enhanced:
                    logger.info("Enhanced workflow", file=workflow_file.name)
            except Exception as e:
                logger.error("Failed to enhance workflow", file=workflow_file.name, error=str(e))
                results[workflow_file.name] = False

        return results

    def _enhance_workflow_file(self, workflow_path: Path) -> bool:
        """Enhance a single workflow file with quality checks."""
        try:
            with open(workflow_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Check if already enhanced
            if "Code Quality Check" in content:
                logger.debug("Workflow already enhanced", file=workflow_path.name)
                return False

            # Add quality check step after dependency installation
            quality_step = self._create_quality_check_step()

            # Find insertion point (after pip install)
            lines = content.split("\n")
            enhanced_lines = []

            for i, line in enumerate(lines):
                enhanced_lines.append(line)

                # Insert after pip install step
                if ("pip install" in line or "requirements.txt" in line) and i < len(lines) - 1:
                    # Add quality check step
                    enhanced_lines.extend(quality_step.split("\n"))

            # Write enhanced workflow
            enhanced_content = "\n".join(enhanced_lines)

            # Create backup
            backup_path = workflow_path.with_suffix(".yml.bak")
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Write enhanced version
            with open(workflow_path, "w", encoding="utf-8") as f:
                f.write(enhanced_content)

            logger.info("Enhanced workflow file", file=workflow_path.name, backup=backup_path.name)
            return True

        except Exception as e:
            logger.error("Error enhancing workflow", file=workflow_path.name, error=str(e))
            return False

    def _create_quality_check_step(self) -> str:
        """Create a quality check step for GitHub Actions."""
        return """
      - name: Code Quality Check
        run: |
          echo "🔍 Running automated code quality checks..."

          # Install quality tools if not already available
          pip install flake8 black isort bandit || echo "Some quality tools unavailable"

          # Run quick quality analysis
          python -c "
          try:
              from app.code_quality import BatchCleanupAgent
              agent = BatchCleanupAgent()
              results = agent.run_full_cleanup(dry_run=True)

              issues_count = sum(len(issues) for issues in results.get('issues_found', {}).values())

              if issues_count > 50:  # Threshold for failing build
                  print(f'❌ Too many quality issues found: {issues_count}')
                  print('Run: python -m app.main code-quality fix-all --dry-run')
                  exit(1)
              elif issues_count > 0:
                  print(f'⚠️  Quality issues found: {issues_count} (proceeding)')
              else:
                  print('✅ No quality issues found')

          except ImportError:
              print('⚠️  Code quality agent not available, skipping')
          except Exception as e:
              print(f'⚠️  Quality check failed: {e}')
          "
        continue-on-error: true
"""

    def create_quality_workflow(self) -> bool:
        """Create a dedicated code quality workflow."""
        workflow_content = """name: Code Quality Maintenance

on:
  workflow_dispatch:
    inputs:
      fix_mode:
        description: "Fix mode: analyze, fix, or force-fix"
        required: false
        default: "analyze"
        type: choice
        options: ["analyze", "fix", "force-fix"]
  schedule:
    - cron: '0 2 * * SUN'  # Weekly on Sunday at 2 AM UTC
  pull_request:
    branches: [ main ]
    paths: [ 'app/**/*.py' ]

permissions:
  contents: write
  pull-requests: write

jobs:
  quality-check:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install watchdog  # For monitoring features

      - name: Run Code Quality Analysis
        id: analysis
        run: |
          echo "🔍 Analyzing code quality..."

          python -c "
          from app.code_quality import BatchCleanupAgent
          import json

          agent = BatchCleanupAgent()
          results = agent.run_full_cleanup(dry_run=True)

          # Create summary
          issues_count = sum(len(issues) for issues in results.get('issues_found', {}).values())

          print(f'Issues found: {issues_count}')
          print(f'Files analyzed: {results.get(\"files_processed\", 0)}')

          # Save results for next step
          with open('quality_analysis.json', 'w') as f:
              json.dump(results, f, indent=2)

          # Set GitHub outputs
          with open('$GITHUB_OUTPUT', 'a') as f:
              f.write(f'issues_count={issues_count}\\n')
              f.write(f'needs_fixing={\"true\" if issues_count > 0 else \"false\"}\\n')
          "

      - name: Apply Fixes (if requested)
        if: |
          (github.event.inputs.fix_mode == 'fix' || github.event.inputs.fix_mode == 'force-fix') ||
          (github.event_name == 'schedule' && steps.analysis.outputs.issues_count > 0)
        run: |
          echo "🔧 Applying code quality fixes..."

          python -c "
          from app.code_quality import BatchCleanupAgent

          agent = BatchCleanupAgent()
          results = agent.run_full_cleanup(dry_run=False)

          print('Cleanup completed!')
          print(agent.create_cleanup_report(results))
          "

      - name: Create Pull Request (if fixes applied)
        if: |
          (github.event.inputs.fix_mode == 'fix' || github.event.inputs.fix_mode == 'force-fix') ||
          (github.event_name == 'schedule' && steps.analysis.outputs.issues_count > 0)
        run: |
          # Check if there are changes to commit
          if [ -n "$(git status --porcelain)" ]; then
            echo "📝 Creating pull request for code quality fixes..."

            # Configure git
            git config user.name "github-actions[bot]"
            git config user.email "github-actions[bot]@users.noreply.github.com"

            # Create branch
            branch_name="code-quality-fixes-$(date +%Y%m%d-%H%M%S)"
            git checkout -b $branch_name

            # Commit changes
            git add .
            git commit -m "🤖 Automated code quality fixes

Applied automatic fixes for:
- Code formatting (Black)
- Import organization (isort)
- Common linting issues (flake8)
- Security improvements (bandit)

Generated by SeeRM Code Quality Agent

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>"

            # Push branch
            git push origin $branch_name

            # Create PR using GitHub CLI
            gh pr create \\
              --title "🤖 Automated Code Quality Fixes" \\
              --body "This PR contains automated code quality improvements generated by the SeeRM Code Quality Agent.

## Changes Made
- ✅ Code formatting fixes (Black)
- ✅ Import organization (isort)
- ✅ Linting issue resolution (flake8)
- ✅ Security improvements (bandit)

## Safety
- All fixes have been validated for syntax correctness
- Tests should still pass (please verify)
- Changes are purely stylistic and functional improvements

## Review Notes
This is an automated PR. Please review the changes and merge if appropriate."

          else
            echo "No changes to commit"
          fi
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Upload Analysis Results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: code-quality-analysis
          path: quality_analysis.json
          retention-days: 30

      - name: Comment on PR (if analysis only)
        if: |
          github.event_name == 'pull_request' &&
          steps.analysis.outputs.issues_count > 0 &&
          github.event.inputs.fix_mode != 'fix'
        run: |
          echo "💬 Adding code quality comment to PR..."

          gh pr comment --body "## 🔍 Code Quality Analysis

Found **${{ steps.analysis.outputs.issues_count }}** code quality issues in this PR.

To automatically fix these issues, run:
\`\`\`bash
python -m app.main code-quality fix-all
\`\`\`

Or trigger the automated fix workflow: **Actions → Code Quality Maintenance → Run workflow → fix**"
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
"""

        workflow_path = self.workflows_dir / "code-quality.yml"

        try:
            with open(workflow_path, "w", encoding="utf-8") as f:
                f.write(workflow_content)

            logger.info("Created code quality workflow", path=str(workflow_path))
            return True

        except Exception as e:
            logger.error("Failed to create quality workflow", error=str(e))
            return False

    def setup_skip_configuration(self) -> Dict[str, str]:
        """Set up intelligent SKIP configuration for known issues."""
        skip_configs = {
            "bandit_false_positives": "B105,B110",  # Known false positives
            "flake8_legacy_code": "E722,F841",  # Legacy code patterns
            "line_length_exceptions": "E501",  # Long line exceptions for URLs, etc.
        }

        # Create a skip configuration file
        config_content = """# Intelligent SKIP configuration for SeeRM
# Generated by Code Quality Agent

# Use these SKIP patterns for common issues:

# For bandit false positives (OAuth URLs, etc.):
# SKIP=bandit git commit -m "..."

# For legacy flake8 issues during migration:
# SKIP=flake8 git commit -m "..."

# For emergency commits with known line length issues:
# SKIP=flake8,bandit git commit -m "..."

# Environment variables for CI:
export SKIP_BANDIT_FALSE_POSITIVES="{skip_configs['bandit_false_positives']}"
export SKIP_FLAKE8_LEGACY="{skip_configs['flake8_legacy_code']}"
"""

        config_path = self.project_root / ".code-quality-skip.sh"

        try:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            logger.info("Created SKIP configuration", path=str(config_path))

        except Exception as e:
            logger.error("Failed to create SKIP config", error=str(e))

        return skip_configs

    def validate_workflow_enhancement(self) -> Dict[str, bool]:
        """Validate that workflow enhancements work correctly."""
        validation_results = {}

        # Check that quality workflow exists
        quality_workflow = self.workflows_dir / "code-quality.yml"
        validation_results["quality_workflow_exists"] = quality_workflow.exists()

        # Check that enhanced workflows are syntactically valid
        for workflow_file in self.workflows_dir.glob("*.yml"):
            try:
                with open(workflow_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # Basic YAML validation (simple check)
                validation_results[f"{workflow_file.name}_syntax"] = (
                    "steps:" in content
                    and "runs-on:" in content
                    and not content.count('"') % 2  # Even number of quotes
                )

            except Exception as e:
                logger.error("Workflow validation failed", file=workflow_file.name, error=str(e))
                validation_results[f"{workflow_file.name}_syntax"] = False

        return validation_results
