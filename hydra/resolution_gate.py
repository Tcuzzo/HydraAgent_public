"""hydra/resolution_gate.py — Two-List Resolution Gate (SWE-bench model).

A slice/job is DONE only when it PROVES the fix.  The gate enforces:

  FAIL_TO_PASS  — every listed test must PASS after the change
                  (the fix actually works)
  PASS_TO_PASS  — every listed test must PASS after the change
                  (no regression introduced)

No partial credit: one failure in either list → resolved=False.

Baseline Guard (anti-hollow-green)
-----------------------------------
When a baseline runner is supplied, the gate verifies that every
fail_to_pass test was FAILING before the change.  A test that was already
green at baseline proves nothing — it is a "hollow green" and the gate
rejects it.

If no baseline runner is supplied the gate CANNOT verify the pre-change
state.  It still enforces the after-state (necessary but not sufficient)
and logs a clear WARNING so the operator knows the baseline-red check was
skipped.  It NEVER silently accepts a possibly-hollow fail_to_pass.

Job/slice contract extension
-----------------------------
A job packet may carry an optional ``resolution_spec`` field:

    {
      "resolution_spec": {
        "fail_to_pass": ["tests/test_foo.py::test_bar"],
        "pass_to_pass": ["tests/test_existing.py::test_baz"]
      }
    }

When present, run_worker_job treats a verify-command exit-0 as NECESSARY but
NOT SUFFICIENT.  The job is only 'passed' when the gate's resolved=True.

See build/queue.jsonl slice schema note at the bottom of this module for
the documented field.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("hydra.resolution_gate")


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class HollowGreenError(ValueError):
    """Raised when a fail_to_pass test was already passing at baseline.

    This means the test never proved the fix — it was hollow green.
    The gate refuses to resolve in this state.
    """


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ResolutionSpec:
    """The two-list gate specification attached to a job or slice.

    Parameters
    ----------
    fail_to_pass:
        Test IDs that must be FAILING before the change and PASSING after.
        These prove the fix actually works.
    pass_to_pass:
        Test IDs that must continue PASSING after the change.
        These prove no regression was introduced.
    """

    fail_to_pass: list[str]
    pass_to_pass: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.fail_to_pass, list):
            raise TypeError(
                f"fail_to_pass must be a list, got {type(self.fail_to_pass).__name__}"
            )
        if not isinstance(self.pass_to_pass, list):
            raise TypeError(
                f"pass_to_pass must be a list, got {type(self.pass_to_pass).__name__}"
            )
        for item in self.fail_to_pass:
            if not isinstance(item, str):
                raise ValueError(f"fail_to_pass items must be strings, got {type(item).__name__}")
        for item in self.pass_to_pass:
            if not isinstance(item, str):
                raise ValueError(f"pass_to_pass items must be strings, got {type(item).__name__}")

    @classmethod
    def from_dict(cls, data: dict) -> "ResolutionSpec":
        """Construct from a plain dict (e.g., parsed from JSON job packet)."""
        return cls(
            fail_to_pass=list(data.get("fail_to_pass", [])),
            pass_to_pass=list(data.get("pass_to_pass", [])),
        )


@dataclass
class ResolutionResult:
    """The outcome of running the resolution gate.

    Attributes
    ----------
    resolved:
        True only when every fail_to_pass test passes AND every pass_to_pass
        test passes AND no hollow-green was detected.
    failing_fail_to_pass:
        IDs of fail_to_pass tests that still fail after the change.
    failing_pass_to_pass:
        IDs of pass_to_pass tests that fail after the change (regressions).
    hollow_tests:
        IDs of fail_to_pass tests that were ALREADY passing at baseline
        (hollow-green — they proved nothing).  Empty when no baseline check
        was performed.
    baseline_skipped:
        True when no baseline runner was supplied and the pre-change check
        was skipped.  A WARNING is logged in this case.
    """

    resolved: bool
    failing_fail_to_pass: list[str] = field(default_factory=list)
    failing_pass_to_pass: list[str] = field(default_factory=list)
    hollow_tests: list[str] = field(default_factory=list)
    baseline_skipped: bool = False

    def __repr__(self) -> str:
        parts = [f"resolved={self.resolved}"]
        if self.failing_fail_to_pass:
            parts.append(f"failing_fail_to_pass={self.failing_fail_to_pass!r}")
        if self.failing_pass_to_pass:
            parts.append(f"failing_pass_to_pass={self.failing_pass_to_pass!r}")
        if self.hollow_tests:
            parts.append(f"hollow_tests={self.hollow_tests!r}")
        if self.baseline_skipped:
            parts.append("baseline_skipped=True")
        return f"ResolutionResult({', '.join(parts)})"


# ---------------------------------------------------------------------------
# Gate implementation
# ---------------------------------------------------------------------------

class ResolutionGate:
    """Enforces the two-list ALL-OR-NOTHING resolution contract.

    Parameters
    ----------
    spec:
        The ResolutionSpec describing which tests must pass/pass.
    run_test:
        Callable[str, bool] — injectable test runner.  Receives a test ID,
        returns True if the test passes, False if it fails.
        In production this typically invokes pytest for a single test node.
    """

    def __init__(
        self,
        spec: ResolutionSpec,
        run_test: Callable[[str], bool],
    ) -> None:
        self.spec = spec
        self._runner = run_test

    # ------------------------------------------------------------------
    # Injectable hooks (can be patched in tests)
    # ------------------------------------------------------------------

    def _run_test_impl(self, test_id: str) -> bool:
        """Run a single test in the AFTER-change state.  Patchable."""
        return self._runner(test_id)

    def _baseline_run_test_impl(self, test_id: str) -> bool:
        """Run a single test in the BEFORE-change (baseline) state.  Patchable."""
        # Default: delegate to the same runner.  In practice the caller
        # supplies a separate baseline_runner; this method exists so tests
        # can patch it independently of _run_test_impl.
        return self._runner(test_id)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def evaluate(
        self,
        baseline_runner: Callable[[str], bool] | None = None,
    ) -> ResolutionResult:
        """Run the gate and return a ResolutionResult.

        Parameters
        ----------
        baseline_runner:
            Optional callable[str, bool] that runs tests against the
            PRE-CHANGE state.  When supplied, the gate checks that every
            fail_to_pass test was FAILING at baseline (anti-hollow-green).
            When None, the baseline check is skipped and a WARNING is logged.
        """
        spec = self.spec

        # Short-circuit: empty spec is trivially resolved
        if not spec.fail_to_pass and not spec.pass_to_pass:
            if baseline_runner is None and False:  # never warn for empty specs
                pass
            return ResolutionResult(resolved=True)

        baseline_skipped = baseline_runner is None

        # --- Baseline guard -------------------------------------------------
        # Check which fail_to_pass tests were ALREADY passing at baseline.
        # If baseline_runner is None, skip but warn.
        hollow_tests: list[str] = []

        if baseline_runner is None:
            # Cannot check baseline; warn and continue with after-state only.
            if spec.fail_to_pass:
                logger.warning(
                    "hydra.resolution_gate: baseline runner not supplied — "
                    "skipping pre-change red check for fail_to_pass tests %s. "
                    "GREEN STATUS MAY BE HOLLOW: tests are verified in the "
                    "after-state only.  Supply a baseline_runner to fully "
                    "enforce the anti-hollow-green contract.",
                    spec.fail_to_pass,
                )
        else:
            for test_id in spec.fail_to_pass:
                baseline_passed = baseline_runner(test_id)
                if baseline_passed:
                    # This test was ALREADY green before the change — hollow!
                    hollow_tests.append(test_id)
                    logger.warning(
                        "hydra.resolution_gate: HOLLOW GREEN detected — "
                        "fail_to_pass test %r was ALREADY PASSING at baseline. "
                        "It proves nothing about the fix.  Marking as hollow.",
                        test_id,
                    )

        # If any hollow tests were found, refuse to resolve.
        if hollow_tests:
            return ResolutionResult(
                resolved=False,
                failing_fail_to_pass=[],
                failing_pass_to_pass=[],
                hollow_tests=hollow_tests,
                baseline_skipped=baseline_skipped,
            )

        # --- After-state checks --------------------------------------------
        failing_f2p: list[str] = []
        for test_id in spec.fail_to_pass:
            if not self._run_test_impl(test_id):
                failing_f2p.append(test_id)

        failing_p2p: list[str] = []
        for test_id in spec.pass_to_pass:
            if not self._run_test_impl(test_id):
                failing_p2p.append(test_id)

        resolved = not failing_f2p and not failing_p2p

        return ResolutionResult(
            resolved=resolved,
            failing_fail_to_pass=failing_f2p,
            failing_pass_to_pass=failing_p2p,
            hollow_tests=[],
            baseline_skipped=baseline_skipped,
        )


# ---------------------------------------------------------------------------
# Convenience: build a gate from a job packet dict
# ---------------------------------------------------------------------------

def gate_from_job(
    job: dict,
    run_test: Callable[[str], bool],
) -> "ResolutionGate | None":
    """Extract and return a ResolutionGate from a job packet, or None.

    Returns None when the job has no ``resolution_spec`` field (back-compat).
    """
    spec_raw = job.get("resolution_spec")
    if spec_raw is None:
        return None
    if not isinstance(spec_raw, dict):
        raise ValueError(
            f"resolution_spec must be a JSON object, got {type(spec_raw).__name__}"
        )
    spec = ResolutionSpec.from_dict(spec_raw)
    return ResolutionGate(spec, run_test=run_test)


# ---------------------------------------------------------------------------
# Production test runner (subprocess-based, patchable)
# ---------------------------------------------------------------------------

def make_subprocess_test_runner(repo_root: "Path | None" = None) -> "Callable[[str], bool]":
    """Return a test runner that runs pytest with cwd=repo_root.

    Parameters
    ----------
    repo_root:
        The repository root directory.  Pytest is invoked with cwd=repo_root
        so test collection always resolves relative to the repo, regardless of
        the process's ambient working directory.  When None, the runner falls
        back to the process cwd (legacy behaviour — avoids breakage for callers
        that never supplied a root, but logs a warning).

    Returns
    -------
    Callable[[str], bool]
        A runner that accepts a test node ID and returns True if it passes.
    """
    import subprocess
    import sys
    from pathlib import Path as _Path

    cwd: "_Path | None" = None
    if repo_root is not None:
        cwd = _Path(repo_root).expanduser().resolve()

    def _runner(test_id: str) -> bool:
        kwargs: dict = dict(
            capture_output=True,
            text=True,
            timeout=120,
        )
        if cwd is not None:
            kwargs["cwd"] = str(cwd)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", test_id, "-q", "--tb=no", "-x"],
            **kwargs,
        )
        return proc.returncode == 0

    return _runner


def subprocess_test_runner(test_id: str) -> bool:
    """Run a single pytest test node and return True if it passes.

    This is the PRODUCTION runner.  Tests inject their own callable via the
    run_test parameter to avoid spawning subprocesses.

    NOTE: this convenience function does NOT set cwd, so pytest collects tests
    relative to the process's ambient working directory.  In production,
    prefer make_subprocess_test_runner(repo_root) which sets cwd explicitly.

    The command is:
        python3 -m pytest <test_id> -q --tb=no -x

    Returns True if exit code is 0, False otherwise.
    """
    return make_subprocess_test_runner(None)(test_id)


# ---------------------------------------------------------------------------
# Slice schema documentation (for build/queue.jsonl)
# ---------------------------------------------------------------------------
#
# Each slice in build/queue.jsonl may carry an optional resolution_spec:
#
#   {
#     "slice_id": "S7-fix-foo",
#     "hypothesis": "...",
#     "verify_commands": ["python3 -m pytest tests/ -q"],
#     "resolution_spec": {
#       "fail_to_pass": [
#         "tests/test_foo.py::test_the_fix"
#       ],
#       "pass_to_pass": [
#         "tests/test_foo.py::test_existing_behavior",
#         "tests/test_bar.py::test_regression"
#       ]
#     }
#   }
#
# When resolution_spec is absent, the slice passes if verify_commands all
# exit 0 (legacy behavior, back-compat preserved).
#
# When resolution_spec is present:
#   - verify_commands exit-0 is NECESSARY but NOT SUFFICIENT
#   - The gate additionally runs the fail_to_pass tests (must pass) and
#     pass_to_pass tests (must pass) in the after-change state
#   - The gate also checks that fail_to_pass tests were FAILING at baseline
#     (anti-hollow-green); if no baseline runner is available a WARNING is
#     logged but the after-state check still runs
#   - resolved=True only when ALL lists are satisfied AND no hollow-green
# ---------------------------------------------------------------------------
