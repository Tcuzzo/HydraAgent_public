"""hydra/container_sandbox.py — rootless container sandbox core.

Python implementation of the peer agent container sandbox pattern.

Three public functions:
  detect_sandbox_engine(which=None) -> "podman" | "docker" | None
  build_sandbox_run_args(**kwargs)   -> list[str]   PURE
  run_in_sandbox(**kwargs)           -> dict         injectable runner/detect

Activated by HYDRA_EXEC_SANDBOX env var in skills/bash.py — OFF by default.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable

# ---------------------------------------------------------------------------
# SANDBOX_DEFAULTS — resource caps applied when the caller doesn't specify.
# Image overridable via HYDRA_SANDBOX_IMAGE env var.
# ---------------------------------------------------------------------------
SANDBOX_DEFAULTS: dict = dict(
    image=os.environ.get("HYDRA_SANDBOX_IMAGE", "docker.io/library/python:3.11-slim"),
    cpus=2,
    memory="2g",
    pids=512,
    workdir="/work",
)


# ---------------------------------------------------------------------------
# detect_sandbox_engine
# Prefer podman, then docker. Injectable `which` for unit tests.
# ---------------------------------------------------------------------------

def detect_sandbox_engine(
    which: Callable[[str], str | None] | None = None,
) -> str | None:
    """Return 'podman', 'docker', or None (no engine found).

    `which` defaults to shutil.which — injectable for tests.
    """
    _which = which if which is not None else shutil.which
    if _which("podman"):
        return "podman"
    if _which("docker"):
        return "docker"
    return None


# ---------------------------------------------------------------------------
# build_sandbox_run_args
# PURE — returns the argv list AFTER the engine binary.
#
# Strict ordering (mirrors the JS reference exactly):
#   run --rm
#   [--network none]        only when network is False
#   [-v src:dst[:ro]] …     mounts
#   -w workdir
#   [--cpus N]
#   [--memory M]
#   [--pids-limit N]
#   [-e K=V] …
#   [--user U]
#   <image>
#   sh -lc <command>
# ---------------------------------------------------------------------------

def build_sandbox_run_args(
    *,
    engine: str = "podman",   # kept for API symmetry; not used in arg list
    image: str | None,
    command: str | None,
    mounts: tuple | list = (),
    workdir: str = "/work",
    network: bool = True,
    cpus: int | float | None = None,
    memory: str | None = None,
    pids: int | None = None,
    env: dict | None = None,
    user: str | None = None,
) -> list[str]:
    """Return the argv that comes AFTER the engine binary. PURE — no side effects."""
    if not image:
        raise ValueError("build_sandbox_run_args: image is required")
    if not command:
        raise ValueError("build_sandbox_run_args: command is required")

    args: list[str] = ["run", "--rm"]

    # Network: push NOTHING when on; push flags only when explicitly off.
    if not network:
        args += ["--network", "none"]

    # Mounts
    for mount in mounts:
        src = mount["source"]
        dst = mount["target"]
        ro = mount.get("ro", False)
        spec = f"{src}:{dst}" + (":ro" if ro else "")
        args += ["-v", spec]

    # Working directory
    args += ["-w", workdir]

    # Resource caps — only when supplied
    if cpus is not None:
        args += ["--cpus", str(cpus)]
    if memory is not None:
        args += ["--memory", str(memory)]
    if pids is not None:
        args += ["--pids-limit", str(pids)]

    # Env vars
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]

    # User
    if user is not None:
        args += ["--user", str(user)]

    # Image + sh entrypoint
    args += [image, "sh", "-lc", command]

    return args


# ---------------------------------------------------------------------------
# run_in_sandbox
# Thin runner. Never falls back to host execution — honest degrade only.
# All external dependencies (engine detect, subprocess.run) are injectable.
# ---------------------------------------------------------------------------

def _default_runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, **kwargs)


def run_in_sandbox(
    *,
    command: str,
    cwd_mount: str | None = None,
    image: str | None = None,
    workdir: str | None = None,
    network: bool = True,
    cpus: int | float | None = None,
    memory: str | None = None,
    pids: int | None = None,
    env: dict | None = None,
    user: str | None = None,
    timeout: float = 120,
    engine: str | None = None,
    detect: Callable[[], str | None] | None = None,
    runner: Callable | None = None,
) -> dict:
    """Run `command` inside a rootless container. NEVER falls back to host.

    Returns a result dict with keys:
      ok, exit_code, stdout, stderr, engine, timed_out, reason (on error).
    """
    # Resolve engine — explicit override skips detect entirely
    if engine is None:
        _detect = detect if detect is not None else detect_sandbox_engine
        engine = _detect()

    if not engine:
        return {
            "ok": False,
            "reason": "no-container-engine",
            "engine": None,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
        }

    # Apply SANDBOX_DEFAULTS for unset fields
    resolved_image = image if image is not None else SANDBOX_DEFAULTS["image"]
    resolved_workdir = workdir if workdir is not None else SANDBOX_DEFAULTS["workdir"]
    resolved_cpus = cpus if cpus is not None else SANDBOX_DEFAULTS["cpus"]
    resolved_memory = memory if memory is not None else SANDBOX_DEFAULTS["memory"]
    resolved_pids = pids if pids is not None else SANDBOX_DEFAULTS["pids"]

    # Prepend cwd_mount as a read-write mount (agent must be able to write)
    effective_mounts: list[dict] = []
    if cwd_mount:
        effective_mounts.append({"source": cwd_mount, "target": resolved_workdir})

    # Extra mounts from env: HYDRA_SANDBOX_EXTRA_MOUNTS="src:dst[:ro],src2:dst2".
    # Lets the cage carry e.g. ssh material so a caged agent can reach remote hosts
    # (e.g. ssh into a remote host). Full permissions INSIDE the sandbox is the operator's
    # model; the cage itself stays least-privilege vs the host (no --privileged).
    _extra = os.environ.get("HYDRA_SANDBOX_EXTRA_MOUNTS", "").strip()
    if _extra:
        for spec in _extra.split(","):
            spec = spec.strip()
            if not spec:
                continue
            parts = spec.split(":")
            if len(parts) >= 3:
                effective_mounts.append({"source": parts[0], "target": parts[1], "ro": parts[2] == "ro"})
            elif len(parts) == 2:
                effective_mounts.append({"source": parts[0], "target": parts[1]})

    args = build_sandbox_run_args(
        engine=engine,
        image=resolved_image,
        command=command,
        mounts=effective_mounts,
        workdir=resolved_workdir,
        network=network,
        cpus=resolved_cpus,
        memory=resolved_memory,
        pids=resolved_pids,
        env=env,
        user=user,
    )

    _runner = runner if runner is not None else _default_runner

    try:
        proc = _runner(
            [engine, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "timed_out": True,
            "reason": "timeout",
            "engine": engine,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "engine": engine,
        "timed_out": False,
    }
