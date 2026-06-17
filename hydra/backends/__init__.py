"""
Hydra Deployment Backends

Execution backends for distributed task execution with worktree isolation
and bounded execution guarantees.
"""

from .base import BackendBase, ExecutionResult, BackendConfig
from .docker import DockerBackend
from .modal import ModalBackend

__all__ = [
    "BackendBase",
    "ExecutionResult",
    "BackendConfig",
    "DockerBackend",
    "ModalBackend",
]
