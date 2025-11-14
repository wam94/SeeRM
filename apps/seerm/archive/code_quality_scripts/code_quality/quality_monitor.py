"""
Real-time code quality monitor.

Watches for file changes and proactively applies quality fixes
to maintain code standards throughout the development process.
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import structlog
from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .pre_commit_agent import PreCommitEnhancementAgent

logger = structlog.get_logger(__name__)


class CodeQualityFileHandler(FileSystemEventHandler):
    """File system event handler for code quality monitoring."""

    def __init__(self, monitor: "CodeQualityMonitor"):
        self.monitor = monitor
        self.last_processed = {}  # Track last processing time per file
        self.processing_delay = 2.0  # Seconds to wait before processing

    def on_modified(self, event):
        """Handle file modification events."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        # Only process Python files in the app directory
        if (
            file_path.suffix == ".py"
            and "app/" in str(file_path)
            and "__pycache__" not in str(file_path)
        ):

            # Debounce rapid changes
            now = time.time()
            last_time = self.last_processed.get(str(file_path), 0)

            if now - last_time > self.processing_delay:
                self.last_processed[str(file_path)] = now
                self.monitor.queue_file_for_processing(str(file_path))


class CodeQualityMonitor:
    """Real-time monitor for code quality issues."""

    def __init__(self, project_root: str = ".", auto_fix: bool = True):
        self.project_root = Path(project_root)
        self.app_dir = self.project_root / "app"
        self.auto_fix = auto_fix
        self.pre_commit_agent = PreCommitEnhancementAgent(project_root)

        # Processing queue
        self.processing_queue = asyncio.Queue()
        self.is_running = False
        self.observer = None

        # Statistics
        self.stats = {
            "files_processed": 0,
            "fixes_applied": 0,
            "errors_encountered": 0,
            "start_time": None,
        }

    async def start_monitoring(self) -> None:
        """Start the real-time monitoring service."""
        if self.is_running:
            logger.warning("Monitor is already running")
            return

        logger.info(
            "Starting code quality monitor",
            project_root=str(self.project_root),
            auto_fix=self.auto_fix,
        )

        self.is_running = True
        self.stats["start_time"] = time.time()

        # Set up file system watcher
        self.observer = Observer()
        handler = CodeQualityFileHandler(self)
        self.observer.schedule(handler, str(self.app_dir), recursive=True)
        self.observer.start()

        logger.info("File system watcher started")

        # Start processing queue worker
        asyncio.create_task(self._process_queue())

        logger.info("Code quality monitor is now active")

    def stop_monitoring(self) -> None:
        """Stop the monitoring service."""
        if not self.is_running:
            return

        logger.info("Stopping code quality monitor")

        self.is_running = False

        if self.observer:
            self.observer.stop()
            self.observer.join()

        logger.info("Code quality monitor stopped", stats=self.get_statistics())

    def queue_file_for_processing(self, file_path: str) -> None:
        """Queue a file for quality processing."""
        if self.is_running:
            try:
                self.processing_queue.put_nowait(file_path)
                logger.debug("Queued file for processing", file=file_path)
            except asyncio.QueueFull:
                logger.warning("Processing queue is full, skipping", file=file_path)

    async def _process_queue(self) -> None:
        """Process queued files for quality improvements."""
        logger.info("Started queue processor")

        while self.is_running:
            try:
                # Wait for file to process (with timeout to check is_running)
                file_path = await asyncio.wait_for(self.processing_queue.get(), timeout=1.0)

                await self._process_file(file_path)
                self.stats["files_processed"] += 1

            except asyncio.TimeoutError:
                # Normal timeout, continue loop
                continue
            except Exception as e:
                self.stats["errors_encountered"] += 1
                logger.error("Error in queue processor", error=str(e))

    async def _process_file(self, file_path: str) -> None:
        """Process a single file for quality improvements."""
        try:
            logger.debug("Processing file", file=file_path)

            if not Path(file_path).exists():
                logger.debug("File no longer exists, skipping", file=file_path)
                return

            if self.auto_fix:
                # Run auto-fixes on this specific file
                results = self.pre_commit_agent.run_auto_fixes([file_path])

                fixes_count = sum(len(fixes) for fixes in results.values())
                if fixes_count > 0:
                    self.stats["fixes_applied"] += fixes_count
                    logger.info("Applied auto-fixes", file=Path(file_path).name, fixes=fixes_count)

                    # Optionally run formatter on the file
                    await self._format_single_file(file_path)
            else:
                # Just analyze and report issues
                issues = self._analyze_file(file_path)
                if issues:
                    logger.info(
                        "Quality issues detected", file=Path(file_path).name, issues=len(issues)
                    )

        except Exception as e:
            logger.error("Error processing file", file=file_path, error=str(e))

    async def _format_single_file(self, file_path: str) -> None:
        """Run Black and isort on a single file."""
        try:
            # Run Black on single file
            import subprocess

            black_result = await asyncio.create_subprocess_exec(
                "black",
                "--line-length=100",
                file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root,
            )
            await black_result.wait()

            # Run isort on single file
            isort_result = await asyncio.create_subprocess_exec(
                "isort",
                "--profile=black",
                "--line-length=100",
                file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root,
            )
            await isort_result.wait()

            logger.debug("Formatted file", file=Path(file_path).name)

        except Exception as e:
            logger.warning("Failed to format file", file=file_path, error=str(e))

    def _analyze_file(self, file_path: str) -> List[str]:
        """Analyze a single file for quality issues."""
        issues = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Check for common issues
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                # Long lines
                if len(line) > 100:
                    issues.append(f"Line {i}: Line too long ({len(line)} > 100)")

                # F-strings without placeholders
                if ('"' in line or "'" in line) and "{" not in line:
                    issues.append(f"Line {i}: F-string without placeholders")

                # Bare except
                if line.strip() == "except:":
                    issues.append(f"Line {i}: Bare except clause")

        except Exception as e:
            issues.append(f"Error analyzing file: {str(e)}")

        return issues

    def get_statistics(self) -> Dict[str, any]:
        """Get monitoring statistics."""
        stats = self.stats.copy()

        if stats["start_time"]:
            stats["uptime_seconds"] = time.time() - stats["start_time"]
            stats["uptime_formatted"] = f"{stats['uptime_seconds']:.1f}s"

        stats["is_running"] = self.is_running
        stats["queue_size"] = self.processing_queue.qsize() if self.is_running else 0

        return stats

    def manual_scan(self, target_path: Optional[str] = None) -> Dict[str, any]:
        """Perform a manual scan of files for quality issues."""
        scan_path = Path(target_path) if target_path else self.app_dir

        results = {"files_scanned": 0, "issues_found": [], "fixes_available": []}

        logger.info("Starting manual scan", path=str(scan_path))

        for python_file in scan_path.rglob("*.py"):
            if "__pycache__" not in str(python_file):
                results["files_scanned"] += 1

                # Analyze file
                issues = self._analyze_file(str(python_file))
                if issues:
                    results["issues_found"].extend(
                        [f"{python_file.name}: {issue}" for issue in issues]
                    )
                    results["fixes_available"].append(str(python_file))

        logger.info(
            "Manual scan completed",
            files_scanned=results["files_scanned"],
            issues_found=len(results["issues_found"]),
        )

        return results


class MonitorConfig:
    """Configuration for the code quality monitor."""

    def __init__(self):
        self.auto_fix = True
        self.processing_delay = 2.0
        self.max_queue_size = 100
        self.file_extensions = [".py"]
        self.exclude_patterns = ["__pycache__", ".git", ".pytest_cache"]

    @classmethod
    def from_env(cls) -> "MonitorConfig":
        """Create configuration from environment variables."""
        config = cls()

        config.auto_fix = os.getenv("CODE_QUALITY_AUTO_FIX", "true").lower() == "true"
        config.processing_delay = float(os.getenv("CODE_QUALITY_DELAY", "2.0"))
        config.max_queue_size = int(os.getenv("CODE_QUALITY_QUEUE_SIZE", "100"))

        return config
