"""Provider-neutral rollover protocol; no provider launch or production store supplied.

Integrate Store with the existing durable runtime ledger. save() MUST be a durable
CAS, and transfer_writer() an atomic, idempotent, monotonically fenced lease swap.
Every writer must reject stale fences. Without those guarantees this is NOT safe
for multiple workers. Adapters must persist/reconcile idempotency keys themselves.
HOLD pauses only this transition; existing operator controls remain available.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Protocol


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False) + "\n"
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class StateConflict(RuntimeError):
    """The durable transition changed since it was read; reload, do not overwrite."""


class LeaseConflict(RuntimeError):
    """Another writer owns the task; do not activate either candidate."""


class AdapterFailure(RuntimeError):
    """Adapter saved diagnostics privately; expose only its opaque evidence ID."""
    def __init__(self, evidence_ref: str):
        if not isinstance(evidence_ref, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", evidence_ref):
            raise ValueError("Expected an opaque runtime evidence ID")
        self.evidence_ref = evidence_ref
        super().__init__("Inspect protected runtime failure evidence")


@dataclass(frozen=True)
class FreshSession:
    session_id: str
    empty_history: bool
    read_only: bool


@dataclass(frozen=True)
class Lease:
    task_id: str
    owner: str
    fence: int
    transition_key: str


@dataclass(frozen=True)
class RuntimeAck:
    """Trusted adapter evidence from actual reads, NEVER a parsed model assertion."""
    session_id: str
    observed: dict
    evidence_id: str
    read_only: bool
    runtime_verified: bool


@dataclass(frozen=True)
class Transition:
    packet: dict
    checkpoint_digest: str
    verification: dict
    phase: str = "QUIESCE"
    revision: int = 0
    successor: FreshSession | None = None
    ack: RuntimeAck | None = None
    lease: Lease | None = None
    failures: int = 0
    last_error: str | None = None
    error_ref: str | None = None
    held_phase: str | None = None

    @property
    def task_id(self) -> str:
        return self.packet["task_id"]

    @property
    def key(self) -> str:
        return digest([self.task_id, self.packet["generation"],
                       self.packet["source_session_id"], self.checkpoint_digest])


class Store(Protocol):
    def load(self, task_id: str) -> Transition | None: ...
    def save(self, state: Transition, expected_revision: int | None) -> None:
        """Durably save a detached snapshot, or raise StateConflict; never partial."""
        ...
    def transfer_writer(self, task_id: str, source: str, successor: str,
                        transition_key: str) -> Lease:
        """CAS owner, issue fence; replay SAME key returns SAME lease, else conflict."""
        ...


class Adapter(Protocol):
    """All operations idempotent by key, including activate and retire.

    quiesce drains/suspends pending mutations; never kills unrelated processes.
    create/reconcile return a durable session identity with no inherited history
    or writer tools. hydrate runs read-only. acknowledge independently verifies
    packet files, governing hashes and the live workspace; model agreement fails.
    activate validates the current fence and workspace, resumes the SAME task,
    step and budgets once, and enforces that fence on every subsequent mutation.
    retire affects only the named source after successful successor activation.
    Log failures to protected runtime evidence, then raise AdapterFailure with an
    opaque reference. Never put credentials or raw tool output in that reference.
    """
    def quiesce(self, source: str, key: str) -> None: ...
    def reconcile(self, key: str) -> FreshSession | None: ...
    def create_fresh(self, key: str) -> FreshSession: ...
    def hydrate_read_only(self, session_id: str, packet: dict, key: str) -> None: ...
    def acknowledge(self, session_id: str, expected: dict, key: str) -> RuntimeAck: ...
    def activate(self, session_id: str, packet: dict, lease: Lease, key: str) -> None: ...
    def retire(self, source: str, key: str) -> None: ...


class RolloverController:
    """begin() accepts session_guard.verify_checkpoint output plus its packet.

    Call advance() from the runtime loop or run() for bounded synchronous progress.
    run() yields on a failure or CAS stall; the host scheduler persists retry-after
    and applies backoff before calling again. This module does not sleep/retry hot.
    Each persisted phase records intent BEFORE its external operation. Retrying a
    crash replays that operation with the same key. Failure does not unpause the
    source, erase the checkpoint, or reset mission budgets/attempts. No compaction
    model call is required when the supplied packet is already bounded.
    """
    def __init__(self, store: Store, adapter: Adapter, max_failures: int = 3):
        if max_failures < 1 or max_failures > 3:
            raise ValueError("max_failures must be between one and three")
        self.store, self.adapter, self.max_failures = store, adapter, max_failures

    def begin(self, packet: dict, verification: dict) -> Transition:
        packet, verification = copy.deepcopy((packet, verification))
        sha = digest(packet)
        if (verification.get("ok") is not True or verification.get("allow_resume") is not True
                or verification.get("budgets_exhausted") is not False
                or packet.get("phase") != "ready" or packet.get("schema_version") != 1
                or verification.get("latest_generation") != packet.get("generation")
                or any(verification.get(k) != v for k, v in
                       {"task_id": packet.get("task_id"), "generation": packet.get("generation"),
                        "checkpoint_digest": sha}.items())):
            raise ValueError("A verified, resumable checkpoint is required")
        if not packet.get("source_session_id") or not packet.get("current_step") or not packet.get("next_action"):
            raise ValueError("Missing source identity or continuation action")
        previous = self.store.load(packet["task_id"])
        if previous and previous.checkpoint_digest == sha:
            return previous
        if previous and (previous.phase != "COMPLETE" or not previous.successor
                         or packet["generation"] <= previous.packet["generation"]
                         or packet["source_session_id"] != previous.successor.session_id):
            raise StateConflict("A different transition already owns this task")
        if previous:
            # Warm checkpoints also consume generations; only ready packets hand off.
            if packet["intent"] != previous.packet["intent"]:
                raise ValueError("Rollover must preserve the original task intent")
            for name, value in previous.packet["budgets"].items():
                new_value = packet["budgets"].get(name)
                if (new_value is None or (name.endswith("_limit") and new_value != value)
                        or (name.endswith("_used") and new_value < value)):
                    raise ValueError("Rollover must preserve the outer mission budget ledger")
        state = Transition(packet, sha, verification,
                           revision=previous.revision + 1 if previous else 0)
        self.store.save(state, previous.revision if previous else None)
        return state

    @staticmethod
    def expected_ack(state: Transition) -> dict:
        p = state.packet
        return {"task_id": p["task_id"], "generation": p["generation"],
                "source_session_id": p["source_session_id"],
                "checkpoint_digest": state.checkpoint_digest, "current_step": p["current_step"],
                "next_action": p["next_action"], "worktree_fingerprint": digest(p["repo"]),
                "required_hashes": {k: p[k]["sha256"] for k in
                                    ("intent", "corrections", "constraints", "permissions")}}

    def advance(self, task_id: str) -> Transition:
        state = self.store.load(task_id)
        if state is None:
            raise KeyError("No durable rollover transition")
        if state.phase in ("COMPLETE", "HOLD"):
            return state
        a, key, p = self.adapter, state.key, state.packet
        source = p["source_session_id"]
        try:
            if digest(p) != state.checkpoint_digest:
                raise ValueError("Stored checkpoint changed")
            if state.phase == "QUIESCE":
                a.quiesce(source, key)
                update = {"phase": "LAUNCH"}
            elif state.phase == "LAUNCH":
                child = a.reconcile(key)
                if child is None:
                    child = a.create_fresh(key)
                if (not child.session_id or child.session_id == source
                        or child.empty_history is not True or child.read_only is not True):
                    raise ValueError("Successor must be a fresh read-only session")
                update = {"phase": "HYDRATE", "successor": child}
            elif state.phase == "HYDRATE":
                a.hydrate_read_only(state.successor.session_id, copy.deepcopy(p), key)
                update = {"phase": "VERIFY"}
            elif state.phase == "VERIFY":
                expected = self.expected_ack(state)
                ack = a.acknowledge(state.successor.session_id, copy.deepcopy(expected), key)
                if (ack.session_id != state.successor.session_id or ack.observed != expected
                        or ack.runtime_verified is not True or ack.read_only is not True
                        or not ack.evidence_id):
                    raise ValueError("Successor acknowledgement did not match runtime evidence")
                update = {"phase": "TRANSFER", "ack": copy.deepcopy(ack)}
            elif state.phase == "TRANSFER":
                lease = self.store.transfer_writer(task_id, source, state.successor.session_id, key)
                if (lease.task_id != task_id or lease.owner != state.successor.session_id
                        or lease.transition_key != key or lease.fence < 1):
                    raise LeaseConflict("Writer transfer did not return the expected fence")
                update = {"phase": "ACTIVATE", "lease": lease}
            elif state.phase == "ACTIVATE":
                a.activate(state.successor.session_id, copy.deepcopy(p), state.lease, key)
                update = {"phase": "RETIRE"}
            elif state.phase == "RETIRE":
                a.retire(source, key)
                update = {"phase": "COMPLETE"}
            else:
                raise ValueError("Unknown rollover phase")
            new = replace(state, revision=state.revision + 1, **update)
            self.store.save(new, state.revision)
            return new
        except StateConflict:
            return self.store.load(task_id)
        except Exception as exc:
            failures = state.failures + 1
            hold = isinstance(exc, LeaseConflict) or failures >= self.max_failures
            new = replace(state, revision=state.revision + 1, failures=failures,
                          last_error=type(exc).__name__, phase="HOLD" if hold else state.phase,
                          error_ref=exc.evidence_ref if isinstance(exc, AdapterFailure) else None,
                          held_phase=state.phase if hold else None)
            try:
                self.store.save(new, state.revision)
                return new
            except StateConflict:
                return self.store.load(task_id)

    def run(self, task_id: str) -> Transition:
        previous = self.store.load(task_id)
        for _ in range(32):
            state = self.advance(task_id)
            if (state.phase in ("COMPLETE", "HOLD") or previous and
                    (state.failures > previous.failures or state.revision <= previous.revision)):
                return state
            previous = state
        return state  # Yield to the outer scheduler under sustained contention.
