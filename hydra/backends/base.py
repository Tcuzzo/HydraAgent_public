"""
Base backend class for Hydra deployment backends.

All backends must inherit from BackendBase and implement:
- execute(): Run a task with worktree isolation and bounded execution
- health_check(): Verify backend is operational
- cleanup(): Clean up resources after execution
"""

import hashlib
import logging
import os
import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class BackendConfig:
    """Configuration for backend execution."""

    # Worktree isolation
    worktree_root: str = "/tmp/hydra-worktrees"
    worktree_prefix: str = "hydra-wt"

    # Bounded execution
    timeout_seconds: int = 300  # 5 minutes default
    max_memory_mb: int = 2048
    max_cpu_percent: int = 100
    max_disk_mb: int = 5120

    # Retry policy
    max_retries: int = 3
    retry_delay_seconds: float = 1.0

    # Logging
    log_level: str = "INFO"
    capture_output: bool = True

    # Backend-specific config (passed as kwargs to specific backends)
    extra_config: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate configuration."""
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
        if self.max_memory_mb < 64:
            raise ValueError("max_memory_mb must be >= 64")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")


@dataclass
class ExecutionResult:
    """Result of a backend execution."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str
    worktree_path: Optional[str] = None
    execution_time_ms: int = 0
    memory_used_mb: float = 0.0
    backend_name: str = ""
    task_id: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.success

    def raise_if_failed(self) -> None:
        """Raise an exception if execution failed."""
        if not self.success:
            raise RuntimeError(
                f"Execution failed (exit {self.exit_code}): {self.error or self.stderr}"
            )


class BackendBase(ABC):
    """
    Abstract base class for all Hydra execution backends.

    Provides:
    - Worktree isolation (copy-on-write style isolation)
    - Bounded execution (timeout, memory, CPU limits)
    - Retry logic with exponential backoff
    - Resource cleanup
    """

    name: str = "base"

    def __init__(self, config: Optional[BackendConfig] = None):
        self.config = config or BackendConfig()
        self._active_worktrees: List[str] = []
        logger.setLevel(getattr(logging, self.config.log_level))

    @abstractmethod
    def _execute_in_backend(
        self, worktree_path: str, command: List[str], env: Dict[str, str]
    ) -> ExecutionResult:
        """
        Execute command in the backend-specific environment.

        Args:
            worktree_path: Path to isolated worktree
            command: Command and arguments to execute
            env: Environment variables

        Returns:
            ExecutionResult with stdout, stderr, exit_code, etc.
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if backend is healthy and ready for execution.

        Returns:
            True if backend is operational
        """
        pass

    def execute(
        self,
        command: List[str],
        source_dir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        task_id: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Execute a command with worktree isolation and bounded execution.

        Args:
            command: Command and arguments to execute
            source_dir: Source directory to isolate (default: current dir)
            env: Environment variables to set
            task_id: Unique task identifier (auto-generated if not provided)

        Returns:
            ExecutionResult with execution details
        """
        task_id = task_id or self._generate_task_id(command)
        start_time = time.time()

        try:
            # Create isolated worktree
            worktree_path = self._create_worktree(source_dir, task_id)
            self._active_worktrees.append(worktree_path)

            # Prepare environment
            exec_env = self._prepare_environment(env, worktree_path)

            # Execute with retries
            result = self._execute_with_retries(worktree_path, command, exec_env, task_id)

            # Attach metadata
            result.worktree_path = worktree_path
            result.backend_name = self.name
            result.task_id = task_id
            result.execution_time_ms = int((time.time() - start_time) * 1000)

            return result

        except Exception as e:
            logger.exception(f"Execution failed for task {task_id}")
            return ExecutionResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
                backend_name=self.name,
                task_id=task_id,
                error=str(e),
            )
        finally:
            # Cleanup happens in cleanup() call or __del__
            pass

    def _execute_with_retries(
        self, worktree_path: str, command: List[str], env: Dict[str, str], task_id: str
    ) -> ExecutionResult:
        """Execute with retry logic."""
        last_result = None

        for attempt in range(self.config.max_retries + 1):
            if attempt > 0:
                delay = self.config.retry_delay_seconds * (2 ** (attempt - 1))
                logger.info(f"Retry {attempt}/{self.config.max_retries} after {delay}s")
                time.sleep(delay)

            try:
                result = self._execute_in_backend(worktree_path, command, env)
                last_result = result

                # Success or non-retryable error
                if result.success or result.exit_code not in [137, 139, -9]:
                    return result

                # Retryable errors (OOM, segfault, killed)
                logger.warning(f"Retryable error: exit {result.exit_code}")

            except Exception as e:
                last_result = ExecutionResult(
                    success=False,
                    exit_code=-1,
                    stdout="",
                    stderr=str(e),
                    backend_name=self.name,
                    task_id=task_id,
                    error=str(e),
                )
                logger.warning(f"Exception during execution: {e}")

        return last_result or ExecutionResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr="All retries exhausted",
            backend_name=self.name,
            task_id=task_id,
            error="All retries exhausted",
        )

    def _create_worktree(self, source_dir: Optional[str], task_id: str) -> str:
        """
        Create an isolated worktree for the task.

        Uses copy-on-write semantics where possible.
        """
        source_dir = source_dir or os.getcwd()
        safe_task_id = self._sanitize_task_id(task_id)

        worktree_path = os.path.join(
            self.config.worktree_root,
            f"{self.config.worktree_prefix}-{safe_task_id}",
        )

        # Ensure root exists
        os.makedirs(self.config.worktree_root, exist_ok=True)

        # Create worktree with bounded copy
        self._copy_with_limits(source_dir, worktree_path)

        logger.debug(f"Created worktree at {worktree_path}")
        return worktree_path

    def _copy_with_limits(self, src: str, dst: str) -> None:
        """Copy directory with disk space limits."""
        if os.path.exists(dst):
            shutil.rmtree(dst)

        total_copied = 0
        max_bytes = self.config.max_disk_mb * 1024 * 1024

        for root, dirs, files in os.walk(src):
            # Skip common large directories
            dirs[:] = [
                d
                for d in dirs
                if d not in [".git", "node_modules", "__pycache__", ".venv", "venv"]
            ]

            rel_path = os.path.relpath(root, src)
            dst_root = os.path.join(dst, rel_path) if rel_path != "." else dst
            os.makedirs(dst_root, exist_ok=True)

            for file in files:
                src_file = os.path.join(root, file)
                dst_file = os.path.join(dst_root, file)

                try:
                    file_size = os.path.getsize(src_file)
                    if total_copied + file_size > max_bytes:
                        logger.warning(
                            f"Disk limit reached ({self.config.max_disk_mb}MB), skipping {src_file}"
                        )
                        continue

                    shutil.copy2(src_file, dst_file)
                    total_copied += file_size

                except Exception as e:
                    logger.warning(f"Failed to copy {src_file}: {e}")

        logger.debug(f"Copied {total_copied / 1024 / 1024:.2f}MB to worktree")

    def _prepare_environment(
        self, env: Optional[Dict[str, str]], worktree_path: str
    ) -> Dict[str, str]:
        """Prepare execution environment."""
        base_env = os.environ.copy()

        # Set worktree as working directory
        base_env["HYDRA_WORKTREE"] = worktree_path
        base_env["PWD"] = worktree_path
        base_env["HOME"] = worktree_path  # Isolate home

        # Add any provided env vars
        if env:
            base_env.update(env)

        return base_env

    def cleanup(self, worktree_path: Optional[str] = None) -> None:
        """
        Clean up resources.

        Args:
            worktree_path: Specific worktree to clean (None = all active)
        """
        paths_to_clean = [worktree_path] if worktree_path else self._active_worktrees.copy()

        for path in paths_to_clean:
            if path and os.path.exists(path):
                try:
                    shutil.rmtree(path)
                    logger.debug(f"Cleaned up worktree: {path}")
                    if path in self._active_worktrees:
                        self._active_worktrees.remove(path)
                except Exception as e:
                    logger.error(f"Failed to cleanup {path}: {e}")

    def _generate_task_id(self, command: List[str]) -> str:
        """Generate unique task ID from command + timestamp."""
        content = f"{time.time()}:{' '.join(command)}"
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    def _sanitize_task_id(self, task_id: str) -> str:
        """Sanitize task ID for filesystem use."""
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup all worktrees."""
        self.cleanup()
        return False
