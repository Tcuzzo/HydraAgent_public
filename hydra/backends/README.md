# Hydra Deployment Backends

Production-ready execution backends for Hydra with **worktree isolation** and **bounded execution** guarantees.

## Overview

Two deployment backends providing isolated, resource-bounded task execution:

| Backend | Use Case | Isolation | Resource Limits |
|---------|----------|-----------|-----------------|
| **Docker** | Containerized execution | Container per task | CPU, memory, disk, pids |
| **Modal** | Serverless cloud execution | Bundled deployment | Timeout, memory, regions |

## Installation

```bash
# Backends are part of hydra package
from hydra.backends import DockerBackend, ModalBackend
```

### Dependencies

- **Docker backend**: Docker daemon installed and running
- **Modal backend**: `modal` CLI installed and authenticated (`modal token new`)

## Quick Start

### Docker Backend

```python
from hydra.backends import DockerBackend, BackendConfig

config = BackendConfig(
    timeout_seconds=300,      # 5 minute timeout
    max_memory_mb=2048,       # 2GB memory limit
    max_disk_mb=5120,         # 5GB disk limit
)

backend = DockerBackend(config)

# Execute a command
result = backend.execute(["python3", "script.py"])

if result.success:
    print(f"Output: {result.stdout}")
else:
    print(f"Failed: {result.stderr}")
```

### Modal Backend

```python
from hydra.backends import ModalBackend, BackendConfig

config = BackendConfig(
    timeout_seconds=300,
    max_memory_mb=2048,
    extra_config={
        "modal_region": "us-east",
        "modal_timeout": 300,
    }
)

backend = ModalBackend(config)

# Execute (bundles worktree and deploys to Modal)
result = backend.execute(["python3", "script.py"])
```

## Key Features

### 1. Worktree Isolation

Each execution gets an isolated copy of the source directory:

```python
result = backend.execute(
    ["cat", "config.txt"],
    source_dir="/path/to/project",  # Only this directory is copied
)

# Worktree is created at /tmp/hydra-worktrees/hydra-wt-<task_id>
# Automatically cleaned up after execution
```

**What gets copied:**
- Source files from `source_dir`
- Excludes: `.git/`, `node_modules/`, `__pycache__/`, `.venv/`, `venv/`
- Respects disk limits (default 5GB)

**What's isolated:**
- Working directory (`PWD`)
- Home directory (`HOME`)
- Environment variables
- File system changes (don't persist)

### 2. Bounded Execution

All backends enforce strict resource limits:

```python
config = BackendConfig(
    timeout_seconds=300,      # Hard timeout
    max_memory_mb=2048,       # Memory cap
    max_cpu_percent=100,      # CPU limit (Docker)
    max_disk_mb=5120,         # Worktree size limit
    max_retries=3,            # Retry on transient failures
)
```

**Timeout enforcement:**
- Commands exceeding timeout are killed
- Exit code `-9` indicates timeout
- No hanging processes left behind

**Memory limits:**
- Docker: Enforced via `--memory` flag
- Modal: Enforced by platform

### 3. Retry Logic

Automatic retry with exponential backoff for transient failures:

```python
config = BackendConfig(
    max_retries=3,
    retry_delay_seconds=1.0,  # Base delay, doubles each retry
)
```

**Retryable errors:**
- Exit code 137 (OOM killed)
- Exit code 139 (segfault)
- Exit code -9 (killed)

### 4. Result Handling

```python
from hydra.backends import ExecutionResult

result: ExecutionResult = backend.execute(...)

# Check success
if result.success:
    print(result.stdout)

# Get exit code
print(f"Exit: {result.exit_code}")

# Performance metrics
print(f"Time: {result.execution_time_ms}ms")
print(f"Memory: {result.memory_used_mb}MB")

# Error details
if not result.success:
    print(f"Error: {result.error}")
    print(f"Stderr: {result.stderr}")

# Raise on failure
result.raise_if_failed()  # Raises RuntimeError if not success
```

## Convenience Functions

Quick execution without full setup:

### Docker

```python
from hydra.backends.docker import run_in_docker

result = run_in_docker(
    ["python3", "script.py"],
    image="python:3.11-slim",
    timeout_seconds=60,
    max_memory_mb=512,
)
```

### Modal

```python
from hydra.backends.modal import run_on_modal

result = run_on_modal(
    ["python3", "script.py"],
    timeout_seconds=60,
    max_memory_mb=512,
)
```

## Advanced Usage

### Script Execution

Run scripts directly without creating files:

```python
# Docker
backend.run_script(
    """
    print("Hello from container")
    import sys
    print(sys.version)
    """,
    interpreter="python3"
)
```

### Custom Docker Images

```python
backend = DockerBackend(config)

# Use specific image
result = backend.execute_with_image(
    ["npm", "test"],
    docker_image="node:20-slim",
    source_dir="/path/to/project",
)
```

### Build Docker Images

```python
success = backend.build_image(
    dockerfile_path="/path/to/Dockerfile",
    image_name="my-app:latest",
    context_dir="/path/to/context",
)
```

### Modal Function Deployment

```python
# Deploy a function
function_id = backend.deploy_function(
    function_code="""
def add(a, b):
    return a + b
""",
    function_name="add_function",
    requirements=["numpy"],
)

# Invoke deployed function
result = backend.invoke_function(
    function_name="add_function",
    args={"a": 5, "b": 3},
)
```

## Context Manager Usage

Automatic cleanup with context managers:

```python
with DockerBackend(config) as backend:
    result1 = backend.execute(["echo", "task1"])
    result2 = backend.execute(["echo", "task2"])
    # All worktrees cleaned up on exit
```

## Evaluation Harnesses

The Docker engine harness lives next to the code:

```bash
pytest hydra/backends/test_docker_backend_engine.py
```

### Test Coverage

Each harness validates:
- ✓ Health checks
- ✓ Basic execution
- ✓ Worktree isolation
- ✓ Timeout enforcement
- ✓ Environment isolation
- ✓ Script execution
- ✓ Resource limits
- ✓ Concurrent execution
- ✓ Cleanup behavior
- ✓ Helper functions

## Architecture

```
hydra/backends/
├── __init__.py          # Exports all public classes
├── base.py              # BackendBase abstract class
│   ├── BackendConfig    # Configuration dataclass
│   ├── ExecutionResult  # Result dataclass
│   └── BackendBase      # Abstract base with common logic
├── docker.py            # Docker implementation
├── modal.py             # Modal implementation
└── test_docker_backend_engine.py  # Docker engine harness
```

### Base Class Responsibilities

`BackendBase` provides:
- Worktree creation and management
- Copy-with-limits (excludes large dirs, respects disk quota)
- Environment preparation
- Retry logic with exponential backoff
- Task ID generation
- Cleanup orchestration

### Backend-Specific Logic

Each backend implements:
- `_execute_in_backend()`: Run command in backend environment
- `health_check()`: Verify backend availability

## Security Considerations

### Docker
- Runs with `--read-only` filesystem
- Network disabled by default (`--network none`)
- `no-new-privileges` security option
- `/tmp` mounted as tmpfs (no persistence)

### Modal
- Code bundled and uploaded securely
- Ephemeral execution (no persistence)
- Modal's built-in security

## Troubleshooting

### Docker Not Found
```bash
# Install Docker
sudo apt-get install docker.io
# Or
brew install --cask docker
```

### Modal Not Authenticated
```bash
# Install CLI
pip install modal

# Authenticate
modal token new
```

### Disk Space Issues
```bash
# Clean up worktrees
rm -rf /tmp/hydra-worktrees

# Increase disk limit
config = BackendConfig(max_disk_mb=10240)  # 10GB
```

### Timeout Too Short
```python
config = BackendConfig(timeout_seconds=600)  # 10 minutes
```

## Best Practices

1. **Use worktree isolation** for reproducibility
2. **Set appropriate timeouts** based on task complexity
3. **Monitor disk usage** when copying large projects
4. **Clean up explicitly** if not using context manager
5. **Check health** before batch operations
6. **Use retry logic** for flaky external dependencies
7. **Log execution results** for debugging

## License

Part of Hydra deployment system.
