"""
Batch cleanup agent for accumulated technical debt.

Performs systematic cleanup of the entire codebase with intelligent
prioritization and safety checks.
"""

import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import structlog

from .pre_commit_agent import PreCommitEnhancementAgent

logger = structlog.get_logger(__name__)


class BatchCleanupAgent:
    """Agent for batch processing code quality improvements."""

    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.app_dir = self.project_root / "app"
        self.pre_commit_agent = PreCommitEnhancementAgent(project_root)

    def run_full_cleanup(self, dry_run: bool = False) -> Dict[str, any]:
        """
        Run complete code quality cleanup on the entire codebase.

        Args:
            dry_run: If True, show what would be done without making changes

        Returns:
            Dictionary with cleanup results and statistics
        """
        results = {
            "start_time": time.time(),
            "dry_run": dry_run,
            "files_processed": 0,
            "issues_found": {},
            "fixes_applied": {},
            "validation_results": {},
            "warnings": [],
        }

        logger.info("Starting batch cleanup", dry_run=dry_run)

        try:
            # Phase 1: Analysis
            logger.info("Phase 1: Analyzing current state")
            analysis = self._analyze_current_state()
            results["issues_found"] = analysis

            if dry_run:
                logger.info("Dry run mode - showing what would be fixed")
                self._show_dry_run_results(analysis)
                return results

            # Phase 2: Safety backup
            logger.info("Phase 2: Creating safety backup")
            backup_success = self._create_safety_backup()
            if not backup_success:
                results["warnings"].append("Failed to create backup - proceeding with caution")

            # Phase 3: Apply fixes in priority order
            logger.info("Phase 3: Applying fixes")
            fixes = self._apply_fixes_with_priority()
            results["fixes_applied"] = fixes
            results["files_processed"] = len(self._get_all_python_files())

            # Phase 4: Validation
            logger.info("Phase 4: Validating fixes")
            validation = self._validate_all_fixes()
            results["validation_results"] = validation

            # Phase 5: Final formatting
            logger.info("Phase 5: Final formatting pass")
            formatting_results = self.pre_commit_agent.run_formatters()
            results["final_formatting"] = formatting_results

            results["end_time"] = time.time()
            results["duration"] = results["end_time"] - results["start_time"]

            logger.info(
                "Batch cleanup completed",
                duration=f"{results['duration']:.2f}s",
                files_processed=results["files_processed"],
            )

        except Exception as e:
            logger.error("Batch cleanup failed", error=str(e))
            results["error"] = str(e)

            # Attempt restore if we have backup
            if not dry_run and hasattr(self, "_backup_dir"):
                self._restore_from_backup()

        return results

    def _analyze_current_state(self) -> Dict[str, List[str]]:
        """Analyze current code quality issues across the codebase."""
        issues = {
            "flake8_violations": [],
            "black_formatting_needed": [],
            "import_organization_needed": [],
            "security_issues": [],
            "syntax_errors": [],
        }

        # Check flake8 issues
        try:
            flake8_result = subprocess.run(
                ["flake8", str(self.app_dir)], capture_output=True, text=True, cwd=self.project_root
            )
            if flake8_result.stdout:
                issues["flake8_violations"] = flake8_result.stdout.strip().split("\n")

        except FileNotFoundError:
            logger.warning("flake8 not available for analysis")

        # Check Black formatting needs
        try:
            black_result = subprocess.run(
                ["black", "--check", "--line-length=100", str(self.app_dir)],
                capture_output=True,
                text=True,
                cwd=self.project_root,
            )
            if black_result.returncode != 0 and "would reformat" in black_result.stderr:
                # Extract file names that need reformatting
                for line in black_result.stderr.split("\n"):
                    if "would reformat" in line:
                        file_path = line.replace("would reformat ", "").strip()
                        issues["black_formatting_needed"].append(file_path)

        except FileNotFoundError:
            logger.warning("Black not available for analysis")

        # Check import organization
        try:
            isort_result = subprocess.run(
                ["isort", "--check-only", "--profile=black", str(self.app_dir)],
                capture_output=True,
                text=True,
                cwd=self.project_root,
            )
            if isort_result.returncode != 0:
                issues["import_organization_needed"] = ["Multiple files need import organization"]

        except FileNotFoundError:
            logger.warning("isort not available for analysis")

        # Check bandit security issues
        try:
            bandit_result = subprocess.run(
                ["bandit", "-r", str(self.app_dir)],
                capture_output=True,
                text=True,
                cwd=self.project_root,
            )
            if bandit_result.stdout and ">> Issue:" in bandit_result.stdout:
                security_issues = bandit_result.stdout.split(">> Issue:")
                issues["security_issues"] = [issue.strip() for issue in security_issues[1:]]

        except FileNotFoundError:
            logger.warning("bandit not available for analysis")

        return issues

    def _show_dry_run_results(self, analysis: Dict[str, List[str]]) -> None:
        """Show what would be fixed in dry run mode."""
        print("\n" + "=" * 60)
        print("BATCH CLEANUP - DRY RUN RESULTS")
        print("=" * 60)

        for category, issues in analysis.items():
            if issues:
                print(f"\n{category.upper().replace('_', ' ')}:")
                print(f"  Issues found: {len(issues)}")

                # Show first few examples
                for i, issue in enumerate(issues[:5]):
                    print(f"  - {issue}")

                if len(issues) > 5:
                    print(f"  ... and {len(issues) - 5} more")

        print(f"\nTotal Python files: {len(self._get_all_python_files())}")
        print("\nTo apply these fixes, run without --dry-run flag")
        print("=" * 60)

    def _create_safety_backup(self) -> bool:
        """Create a backup of the current state."""
        try:
            import shutil
            import tempfile

            self._backup_dir = Path(tempfile.mkdtemp(prefix="seerm_backup_"))

            # Backup the app directory
            shutil.copytree(self.app_dir, self._backup_dir / "app")

            logger.info("Created safety backup", backup_dir=str(self._backup_dir))
            return True

        except Exception as e:
            logger.error("Failed to create backup", error=str(e))
            return False

    def _apply_fixes_with_priority(self) -> Dict[str, any]:
        """Apply fixes in priority order: security > functionality > style."""
        fixes = {"security_fixes": [], "functionality_fixes": [], "style_fixes": []}

        # Priority 1: Security fixes (bare except, etc.)
        logger.info("Applying security fixes")
        security_results = self.pre_commit_agent.run_auto_fixes()
        if security_results.get("security_fixes"):
            fixes["security_fixes"] = security_results["security_fixes"]

        # Priority 2: Functionality fixes (unused imports, syntax)
        logger.info("Applying functionality fixes")
        for fix_type in ["unused_imports", "f_string_fixes"]:
            if security_results.get(fix_type):
                fixes["functionality_fixes"].extend(security_results[fix_type])

        # Priority 3: Style fixes (formatting, line length)
        logger.info("Applying style fixes")
        for fix_type in ["line_length", "import_organization", "docstring_fixes"]:
            if security_results.get(fix_type):
                fixes["style_fixes"].extend(security_results[fix_type])

        return fixes

    def _validate_all_fixes(self) -> Dict[str, any]:
        """Validate that all fixes maintain code correctness."""
        validation = {
            "syntax_valid": True,
            "imports_valid": True,
            "tests_pass": False,
            "issues": [],
        }

        # Check syntax validation
        syntax_issues = self.pre_commit_agent.validate_fixes()
        if syntax_issues["syntax_errors"]:
            validation["syntax_valid"] = False
            validation["issues"].extend(syntax_issues["syntax_errors"])

        if syntax_issues["import_errors"]:
            validation["imports_valid"] = False
            validation["issues"].extend(syntax_issues["import_errors"])

        # Try to run tests if available
        try:
            test_result = subprocess.run(
                ["python", "-m", "pytest", "tests/", "-q"],
                capture_output=True,
                text=True,
                cwd=self.project_root,
                timeout=60,  # 1 minute timeout
            )
            validation["tests_pass"] = test_result.returncode == 0
            if test_result.returncode != 0:
                validation["issues"].append(f"Tests failed: {test_result.stderr}")

        except (FileNotFoundError, subprocess.TimeoutExpired):
            validation["issues"].append("Could not run tests for validation")

        return validation

    def _restore_from_backup(self) -> bool:
        """Restore from backup if fixes caused issues."""
        if not hasattr(self, "_backup_dir") or not self._backup_dir.exists():
            logger.error("No backup available for restoration")
            return False

        try:
            import shutil

            # Remove current app directory
            shutil.rmtree(self.app_dir)

            # Restore from backup
            shutil.copytree(self._backup_dir / "app", self.app_dir)

            logger.info("Restored from backup", backup_dir=str(self._backup_dir))
            return True

        except Exception as e:
            logger.error("Failed to restore from backup", error=str(e))
            return False

    def _get_all_python_files(self) -> List[str]:
        """Get all Python files in the project."""
        python_files = []
        for path in self.app_dir.rglob("*.py"):
            if "__pycache__" not in str(path):
                python_files.append(str(path))
        return python_files

    def create_cleanup_report(self, results: Dict[str, any]) -> str:
        """Create a detailed report of cleanup results."""
        report = []
        report.append("SEERM CODE QUALITY CLEANUP REPORT")
        report.append("=" * 50)
        report.append("")

        if results.get("dry_run"):
            report.append("🔍 DRY RUN MODE - No changes made")
        else:
            report.append("✅ CLEANUP COMPLETED")

        report.append("")
        report.append(f"Duration: {results.get('duration', 0):.2f} seconds")
        report.append(f"Files processed: {results.get('files_processed', 0)}")
        report.append("")

        # Issues found
        if results.get("issues_found"):
            report.append("ISSUES FOUND:")
            for category, issues in results["issues_found"].items():
                if issues:
                    report.append(f"  {category}: {len(issues)} issues")

        # Fixes applied
        if results.get("fixes_applied"):
            report.append("")
            report.append("FIXES APPLIED:")
            for category, files in results["fixes_applied"].items():
                if files:
                    report.append(f"  {category}: {len(files)} files")

        # Validation results
        if results.get("validation_results"):
            validation = results["validation_results"]
            report.append("")
            report.append("VALIDATION RESULTS:")
            report.append(f"  Syntax valid: {'✅' if validation.get('syntax_valid') else '❌'}")
            report.append(f"  Imports valid: {'✅' if validation.get('imports_valid') else '❌'}")
            report.append(f"  Tests pass: {'✅' if validation.get('tests_pass') else '❌'}")

        # Warnings
        if results.get("warnings"):
            report.append("")
            report.append("WARNINGS:")
            for warning in results["warnings"]:
                report.append(f"  ⚠️  {warning}")

        report.append("")
        report.append("=" * 50)

        return "\n".join(report)
