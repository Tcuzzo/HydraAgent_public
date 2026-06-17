"""
Docker backend for Hydra deployment.

Provides containerized execution with:
- Worktree isolation (mounted as volume)
- Bounded execution (CPU, memory, time limits via Docker)
- Clean container lifecycle management
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

from .base import BackendBase, BackendConfig, ExecutionResult

logger = logging.getLogger(__name__)


class DockerBackend(BackendBase):
    """
    Docker execution backend with resource isolation.

    Features:
    - Container-per-task isolation
    - Resource limits (CPU, memory, disk)
    - Automatic container cleanup
    - Volume mounting for worktree isolation
    """

    name = "docker"

    @staticmethod
    def _resolve_engine(override: Optional[str] = None) -> str:
        """Return the container engine binary to use.

        Priority:
        1. Explicit override (from config extra_config["container_engine"]).
        2. podman  — preferred; rootless, no daemon, works rootless as the current user.
        3. docker  — fallback if podman is absent.
        4. "docker" literal — last resort if neither is on PATH.
        """
        if override:
            return override
        if shutil.which("podman"):
            return "podman"
        if shutil.which("docker"):
            return "docker"
        return "docker"  # last-resort literal; caller will get FileNotFoundError

    def __init__(self, config: Optional[BackendConfig] = None):
        super().__init__(config)
        self.docker_image = self.config.extra_config.get(
            "docker_image", "docker.io/library/python:3.11-slim"
        )
        # Network default is ON (no --network flag) so containers can reach the
        # internet / LAN by default under rootless podman.  Set docker_network="none"
        # to explicitly disable networking.
        self.docker_network = self.config.extra_config.get("docker_network", None)
        self._engine: str = self._resolve_engine(
            self.config.extra_config.get("container_engine")
        )
        self._containers: List[str] = []

    def health_check(self) -> bool:
        """Check if container engine (podman or docker) is running and accessible."""
        try:
            result = subprocess.run(
                [self._engine, "info"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"{self._engine} engine is healthy")
                return True
            else:
                logger.warning(f"{self._engine} health check failed: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            logger.warning(f"{self._engine} health check timed out")
            return False
        except FileNotFoundError:
            logger.warning(f"{self._engine} command not found")
            return False
        except Exception as e:
            logger.warning(f"{self._engine} health check error: {e}")
            return False

    def _execute_in_backend(
        self, worktree_path: str, command: List[str], env: Dict[str, str]
    ) -> ExecutionResult:
        """Execute command in a Docker container."""
        container_name = f"hydra-{os.path.basename(worktree_path)}"

        try:
            # Build docker run command
            docker_cmd = self._build_docker_command(
                container_name, worktree_path, command, env
            )

            # Run container
            result = subprocess.run(
                docker_cmd,
                capture_output=self.config.capture_output,
                text=True,
                timeout=self.config.timeout_seconds + 30,  # Extra time for container startup
            )

            return ExecutionResult(
                success=result.returncode == 0,
                exit_code=result.returncode,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
            )

        except subprocess.TimeoutExpired as e:
            logger.warning(f"Container execution timed out: {container_name}")
            self._force_kill_container(container_name)
            return ExecutionResult(
                success=False,
                exit_code=-9,
                stdout=e.stdout.decode() if e.stdout else "",
                stderr=f"Timeout after {self.config.timeout_seconds}s",
                error="Execution timeout",
            )
        except Exception as e:
            logger.exception(f"Container execution failed: {e}")
            return ExecutionResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                error=str(e),
            )
        finally:
            # Cleanup container
            self._cleanup_container(container_name)

    def _build_docker_command(
        self, container_name: str, worktree_path: str, command: List[str], env: Dict[str, str]
    ) -> List[str]:
        """Build container run command with resource limits."""
        cmd = [
            self._engine,
            "run",
            "--rm",  # Auto-remove on exit
            "--name",
            container_name,
            # Resource limits
            "--memory",
            f"{self.config.max_memory_mb}m",
            "--cpus",
            str(self.config.max_cpu_percent / 100.0),
            "--pids-limit",
            "100",
            # Isolation — network is ON by default (no --network flag).
            # Only pass --network when explicitly configured (e.g. "none" to disable).
        ]

        if self.docker_network is not None:
            cmd.extend(["--network", self.docker_network])

        cmd += [
            "--security-opt",
            "no-new-privileges:true",
            "--read-only",
            "--tmpfs",
            "/tmp:exec,nosuid,size=100m",
            # Worktree volume
            "-v",
            f"{worktree_path}:/workspace:ro",
            "-w",
            "/workspace",
            # Environment
            "-e",
            "HOME=/workspace",
        ]

        # Add environment variables
        for key, value in env.items():
            # Sanitize env var names
            safe_key = "".join(c if c.isalnum() or c == "_" else "_" for c in key)
            cmd.extend(["-e", f"{safe_key}={value}"])

        # Add image and command
        cmd.append(self.docker_image)
        cmd.extend(command)

        return cmd

    def _force_kill_container(self, container_name: str) -> None:
        """Force kill a container."""
        try:
            subprocess.run(
                [self._engine, "kill", container_name],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass

    def _cleanup_container(self, container_name: str) -> None:
        """Ensure container is removed."""
        try:
            subprocess.run(
                [self._engine, "rm", "-f", container_name],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass

    def execute_with_image(
        self,
        command: List[str],
        docker_image: str,
        source_dir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        task_id: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Execute with a specific Docker image.

        Args:
            command: Command to execute
            docker_image: Docker image to use
            source_dir: Source directory for worktree
            env: Environment variables
            task_id: Task identifier

        Returns:
            ExecutionResult
        """
        original_image = self.docker_image
        try:
            self.docker_image = docker_image
            return self.execute(command, source_dir, env, task_id)
        finally:
            self.docker_image = original_image

    def build_image(
        self,
        dockerfile_path: str,
        image_name: str,
        context_dir: Optional[str] = None,
    ) -> bool:
        """
        Build a Docker image from a Dockerfile.

        Args:
            dockerfile_path: Path to Dockerfile
            image_name: Name for the built image
            context_dir: Build context directory

        Returns:
            True if build succeeded
        """
        context_dir = context_dir or os.path.dirname(dockerfile_path)

        try:
            result = subprocess.run(
                [
                    self._engine,
                    "build",
                    "-t",
                    image_name,
                    "-f",
                    dockerfile_path,
                    context_dir,
                ],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes for build
            )

            if result.returncode == 0:
                logger.info(f"Built image {image_name}")
                return True
            else:
                logger.error(f"Image build failed: {result.stderr}")
                return False

        except Exception as e:
            logger.exception(f"Image build failed: {e}")
            return False

    def run_script(
        self,
        script_content: str,
        interpreter: str = "python3",
        source_dir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        task_id: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Run a script in a Docker container.

        Args:
            script_content: Script content to execute
            interpreter: Interpreter to use (python3, bash, node, etc.)
            source_dir: Source directory for worktree
            env: Environment variables
            task_id: Task identifier

        Returns:
            ExecutionResult
        """
        # Write script to temporary file in worktree
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".script", delete=False
        ) as f:
            f.write(script_content)
            script_path = f.name

        try:
            # Copy script to worktree before execution
            worktree_path = self._create_worktree(source_dir, task_id or "script")
            script_dest = os.path.join(worktree_path, os.path.basename(script_path))
            
            shutil.copy2(script_path, script_dest)
            os.chmod(script_dest, 0o755)

            command = [interpreter, os.path.basename(script_path)]
            return self.execute(command, worktree_path, env, task_id)

        finally:
            os.unlink(script_path)


# Convenience function for quick Docker execution
def run_in_docker(
    command: List[str],
    image: str = "docker.io/library/python:3.11-slim",
    timeout_seconds: int = 300,
    max_memory_mb: int = 2048,
    source_dir: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> ExecutionResult:
    """
    Quick helper to run a command in Docker.

    Args:
        command: Command to execute
        image: Docker image to use
        timeout_seconds: Execution timeout
        max_memory_mb: Memory limit
        source_dir: Source directory for worktree
        env: Environment variables

    Returns:
        ExecutionResult
    """
    config = BackendConfig(
        timeout_seconds=timeout_seconds,
        max_memory_mb=max_memory_mb,
        extra_config={"docker_image": image},
    )

    backend = DockerBackend(config)
    return backend.execute(command, source_dir, env)
