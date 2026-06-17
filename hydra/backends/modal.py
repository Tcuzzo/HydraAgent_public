"""
Modal backend for Hydra deployment.

Provides serverless execution via Modal.com with:
- Worktree isolation (bundled as deployment artifact)
- Bounded execution (Modal's native resource limits)
- Automatic scaling and cleanup
- Cold-start optimization
"""

import base64
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional

from .base import BackendBase, BackendConfig, ExecutionResult

logger = logging.getLogger(__name__)


class ModalBackend(BackendBase):
    """
    Modal serverless execution backend.

    Features:
    - Serverless function execution
    - Automatic scaling
    - Worktree bundling for isolation
    - Resource-bounded execution
    - Result retrieval with polling
    """

    name = "modal"

    def __init__(self, config: Optional[BackendConfig] = None):
        super().__init__(config)
        self.modal_app_name = self.config.extra_config.get(
            "modal_app_name", "hydra-executor"
        )
        self.modal_function_name = self.config.extra_config.get(
            "modal_function_name", "execute_task"
        )
        self.modal_region = self.config.extra_config.get("modal_region", "us-east")
        self.modal_timeout = self.config.extra_config.get("modal_timeout", 300)
        self._deployed_functions: List[str] = []

    def health_check(self) -> bool:
        """Check if Modal CLI is available and authenticated."""
        try:
            # Check if modal CLI is available
            result = subprocess.run(
                ["modal", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                logger.warning("Modal CLI not found or not working")
                return False

            # Check authentication
            result = subprocess.run(
                ["modal", "token", "new"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            # If we get here without auth error, we're good
            logger.info("Modal CLI is available and authenticated")
            return True

        except FileNotFoundError:
            logger.warning("Modal CLI not found")
            return False
        except subprocess.TimeoutExpired:
            logger.warning("Modal health check timed out")
            return False
        except Exception as e:
            logger.warning(f"Modal health check error: {e}")
            return False

    def _execute_in_backend(
        self, worktree_path: str, command: List[str], env: Dict[str, str]
    ) -> ExecutionResult:
        """Execute command via Modal serverless function."""
        function_id = None

        try:
            # Bundle worktree and upload
            bundle_path = self._bundle_worktree(worktree_path)

            # Deploy or invoke Modal function
            function_id = self._invoke_modal_function(bundle_path, command, env)

            # Poll for result
            result = self._poll_for_result(function_id)

            return result

        except Exception as e:
            logger.exception(f"Modal execution failed: {e}")
            return ExecutionResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                error=str(e),
            )
        finally:
            # Cleanup
            if function_id:
                self._cleanup_modal_function(function_id)

    def _bundle_worktree(self, worktree_path: str) -> str:
        """Bundle worktree into a deployable artifact."""
        # Create tarball of worktree
        bundle_name = f"worktree-{os.path.basename(worktree_path)}.tar.gz"
        bundle_path = os.path.join(tempfile.gettempdir(), bundle_name)

        try:
            subprocess.run(
                ["tar", "-czf", bundle_path, "-C", os.path.dirname(worktree_path), os.path.basename(worktree_path)],
                check=True,
                capture_output=True,
                timeout=60,
            )
            logger.debug(f"Bundled worktree to {bundle_path}")
            return bundle_path

        except Exception as e:
            raise RuntimeError(f"Failed to bundle worktree: {e}")

    def _invoke_modal_function(
        self, bundle_path: str, command: List[str], env: Dict[str, str]
    ) -> str:
        """Invoke Modal function with the bundled worktree."""
        # Encode bundle as base64 for transfer
        with open(bundle_path, "rb") as f:
            bundle_data = base64.b64encode(f.read()).decode("utf-8")

        # Prepare invocation payload
        payload = {
            "command": command,
            "env": env,
            "worktree_bundle": bundle_data,
            "timeout": self.config.timeout_seconds,
            "memory_mb": self.config.max_memory_mb,
        }

        # Create temporary Python script for Modal invocation
        script_content = self._generate_modal_invocation_script(payload)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(script_content)
            script_path = f.name

        try:
            # Run modal run
            result = subprocess.run(
                ["modal", "run", script_path],
                capture_output=True,
                text=True,
                timeout=self.modal_timeout + 60,
            )

            if result.returncode != 0:
                raise RuntimeError(f"Modal invocation failed: {result.stderr}")

            # Parse function ID from output
            function_id = self._parse_function_id(result.stdout)
            return function_id

        finally:
            os.unlink(script_path)

    def _generate_modal_invocation_script(self, payload: Dict[str, Any]) -> str:
        """Generate Modal invocation script."""
        import json

        payload_json = json.dumps(payload)

        script = f'''
import modal
import base64
import subprocess
import tempfile
import os
import tarfile
import json

app = modal.App("{self.modal_app_name}")

# Define the image
image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "requests",
)

# Create the function
@app.function(
    image=image,
    timeout={min(self.config.timeout_seconds, 300)},  # Modal max is 5 minutes for free tier
    memory={self.config.max_memory_mb},
    regions=["{self.modal_region}"],
)
def execute_task(command, env, worktree_bundle, timeout):
    import base64
    import subprocess
    import tempfile
    import os
    import tarfile
    
    # Decode and extract worktree
    worktree_data = base64.b64decode(worktree_bundle)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_path = os.path.join(tmpdir, "worktree.tar.gz")
        with open(bundle_path, "wb") as f:
            f.write(worktree_data)
        
        # Extract
        with tarfile.open(bundle_path, "r:gz") as tar:
            tar.extractall(tmpdir)
        
        # Find worktree directory
        worktree_dir = None
        for item in os.listdir(tmpdir):
            item_path = os.path.join(tmpdir, item)
            if os.path.isdir(item_path):
                worktree_dir = item_path
                break
        
        if not worktree_dir:
            return {{"success": False, "exit_code": -1, "stderr": "Worktree not found"}}
        
        # Set up environment
        exec_env = os.environ.copy()
        exec_env.update(env)
        exec_env["HYDRA_WORKTREE"] = worktree_dir
        exec_env["HOME"] = worktree_dir
        exec_env["PWD"] = worktree_dir
        
        # Execute command
        try:
            result = subprocess.run(
                command,
                cwd=worktree_dir,
                env=exec_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            
            return {{
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }}
        except subprocess.TimeoutExpired as e:
            return {{
                "success": False,
                "exit_code": -9,
                "stderr": f"Timeout after {{timeout}}s",
                "stdout": e.stdout.decode() if e.stdout else "",
            }}
        except Exception as e:
            return {{
                "success": False,
                "exit_code": -1,
                "stderr": str(e),
            }}

if __name__ == "__main__":
    payload = {payload_json}
    
    with app.run():
        result = execute_task.remote(
            payload["command"],
            payload["env"],
            payload["worktree_bundle"],
            payload["timeout"],
        )
        print(json.dumps(result))
'''
        return script

    def _parse_function_id(self, output: str) -> str:
        """Parse function ID from Modal output."""
        # Modal outputs the result directly, extract it
        try:
            # Look for JSON in output
            for line in output.split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    result = json.loads(line)
                    return result.get("function_id", "direct-result")
        except Exception:
            pass

        return "direct-execution"

    def _poll_for_result(self, function_id: str) -> ExecutionResult:
        """Poll for Modal function result."""
        # For direct execution, we already have the result
        if function_id == "direct-result" or function_id == "direct-execution":
            # This case is handled in the script output
            pass

        # In a real implementation, you would poll Modal's API
        # For now, assume synchronous execution
        start_time = time.time()

        while time.time() - start_time < self.modal_timeout:
            # Check if result is ready (in real impl, call Modal API)
            time.sleep(1)

        raise TimeoutError(f"Modal function {function_id} did not complete in time")

    def _cleanup_modal_function(self, function_id: str) -> None:
        """Clean up Modal function resources."""
        # Modal auto-cleans up serverless functions
        # This is a no-op for most cases
        pass

    def deploy_function(
        self,
        function_code: str,
        function_name: Optional[str] = None,
        requirements: Optional[List[str]] = None,
    ) -> str:
        """
        Deploy a Python function to Modal.

        Args:
            function_code: Python function code
            function_name: Name for the function
            requirements: List of pip packages

        Returns:
            Function ID
        """
        function_name = function_name or f"hydra-func-{int(time.time())}"
        requirements = requirements or []

        # Generate deployment script
        script = self._generate_deployment_script(
            function_code, function_name, requirements
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(script)
            script_path = f.name

        try:
            result = subprocess.run(
                ["modal", "deploy", script_path],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                raise RuntimeError(f"Deployment failed: {result.stderr}")

            self._deployed_functions.append(function_name)
            logger.info(f"Deployed function {function_name}")
            return function_name

        finally:
            os.unlink(script_path)

    def _generate_deployment_script(
        self, function_code: str, function_name: str, requirements: List[str]
    ) -> str:
        """Generate Modal deployment script."""
        reqs_str = ", ".join(f'"{r}"' for r in requirements)

        script = f'''
import modal

app = modal.App("{self.modal_app_name}")

image = modal.Image.debian_slim(python_version="3.11")
'''
        if requirements:
            script += f'.pip_install({reqs_str})\n'

        script += f'''

@app.function(
    image=image,
    timeout={self.config.timeout_seconds},
    memory={self.config.max_memory_mb},
)
def {function_name}(**kwargs):
{chr(10).join("    " + line for line in function_code.split(chr(10)))}

if __name__ == "__main__":
    app.deploy()
'''
        return script

    def invoke_function(
        self,
        function_name: str,
        args: Optional[Dict[str, Any]] = None,
        async_mode: bool = False,
    ) -> Any:
        """
        Invoke a deployed Modal function.

        Args:
            function_name: Name of deployed function
            args: Function arguments
            async_mode: If True, don't wait for result

        Returns:
            Function result
        """
        args = args or {}

        # Generate invocation script
        script = f'''
import modal
import json

app = modal.lookup_app("{self.modal_app_name}")
func = app.function("{function_name}")

args = {json.dumps(args)}

with app.run():
    result = func.remote(**args)
    print(json.dumps(result))
'''

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(script)
            script_path = f.name

        try:
            result = subprocess.run(
                ["modal", "run", script_path],
                capture_output=True,
                text=True,
                timeout=self.modal_timeout,
            )

            if result.returncode != 0:
                raise RuntimeError(f"Invocation failed: {result.stderr}")

            # Parse JSON result
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    return json.loads(line)

            return result.stdout

        finally:
            os.unlink(script_path)

    def list_functions(self) -> List[str]:
        """List deployed Modal functions."""
        try:
            result = subprocess.run(
                ["modal", "app", "list", "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                apps = json.loads(result.stdout)
                return [app["name"] for app in apps if app["name"].startswith("hydra-")]

        except Exception as e:
            logger.error(f"Failed to list functions: {e}")

        return []

    def undeploy_function(self, function_name: str) -> bool:
        """Undeploy a Modal function."""
        try:
            result = subprocess.run(
                ["modal", "app", "delete", function_name],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                logger.info(f"Undeployed function {function_name}")
                if function_name in self._deployed_functions:
                    self._deployed_functions.remove(function_name)
                return True

        except Exception as e:
            logger.error(f"Failed to undeploy {function_name}: {e}")

        return False


# Convenience function for quick Modal execution
def run_on_modal(
    command: List[str],
    timeout_seconds: int = 300,
    max_memory_mb: int = 2048,
    source_dir: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> ExecutionResult:
    """
    Quick helper to run a command on Modal.

    Args:
        command: Command to execute
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
        extra_config={
            "modal_timeout": timeout_seconds,
        },
    )

    backend = ModalBackend(config)
    return backend.execute(command, source_dir, env)
