"""In-memory fault-injection fixtures ONLY; these do not certify a live adapter."""
import copy
import unittest

from rollover_supervisor import (AdapterFailure, FreshSession, Lease, LeaseConflict, RolloverController,
                                 RuntimeAck, StateConflict, Transition, digest)


class Crash(BaseException):
    pass


class MemoryStore:
    """Single-process testing fixture; deliberately NOT production persistence."""
    def __init__(self):
        self.state, self.owner, self.fence = None, "source", 1
        self.transfers, self.crash_on_save = {}, None

    def load(self, task_id):
        return copy.deepcopy(self.state)

    def save(self, state, expected_revision):
        if state.phase == self.crash_on_save:
            self.crash_on_save = None
            raise Crash()
        actual = self.state.revision if self.state else None
        if actual != expected_revision:
            raise StateConflict()
        self.state = copy.deepcopy(state)

    def transfer_writer(self, task_id, source, successor, transition_key):
        if transition_key in self.transfers:
            return self.transfers[transition_key]
        if self.owner != source:
            raise LeaseConflict()
        self.owner, self.fence = successor, self.fence + 1
        lease = Lease(task_id, successor, self.fence, transition_key)
        self.transfers[transition_key] = lease
        return lease


class FixtureAdapter:
    def __init__(self, store):
        self.store, self.children, self.hydrated, self.active = store, {}, {}, {}
        self.paused, self.retired = set(), set()
        self.launches, self.hydrate_attempts = 0, 0
        self.launch_attempts, self.fail_launch = 0, False
        self.fail_hydrate, self.bad_ack, self.inherit_history = False, False, False

    def quiesce(self, source, key):
        self.paused.add(source)

    def reconcile(self, key):
        return self.children.get(key)

    def create_fresh(self, key):
        self.launch_attempts += 1
        if self.fail_launch:
            raise AdapterFailure("failure-receipt")
        if key not in self.children:
            self.launches += 1
            name = "successor" if self.launches == 1 else "successor-%d" % self.launches
            self.children[key] = FreshSession(name, not self.inherit_history, True)
        return self.children[key]

    def hydrate_read_only(self, session_id, packet, key):
        self.hydrate_attempts += 1
        if self.fail_hydrate:
            raise RuntimeError("Injected hydration failure")
        self.hydrated[session_id] = copy.deepcopy(packet)

    def acknowledge(self, session_id, expected, key):
        # Derive evidence from loaded state, independently of the requested ACK.
        p = self.hydrated[session_id]
        observed = RolloverController.expected_ack(Transition(p, digest(p), {}))
        if self.bad_ack:
            observed["required_hashes"]["corrections"] = "wrong"
        return RuntimeAck(session_id, observed, "inspection-receipt", True, True)

    def activate(self, session_id, packet, lease, key):
        if self.store.owner != session_id or self.store.fence != lease.fence:
            raise LeaseConflict()
        self.active.setdefault(key, copy.deepcopy(packet))

    def retire(self, source, key):
        self.retired.add(source)


class RolloverTests(unittest.TestCase):
    def setUp(self):
        # Synthetic verifier output isolates controller tests from guard/Git tests.
        self.packet = {"schema_version": 1, "task_id": "task", "generation": 1,
                       "source_session_id": "source", "phase": "ready",
                       "current_step": "repair", "next_action": "verify changed behavior",
                       "repo": {"head": "fixture", "dirty_manifest": []},
                       "budgets": {"attempts_used": 2, "attempts_limit": 5,
                                   "task_tokens_used": 230000, "task_token_limit": 500000},
                       "failed_attempts": [{"summary": "previous attempt", "evidence_refs": []}]}
        for k in ("intent", "corrections", "constraints", "permissions"):
            self.packet[k] = {"sha256": digest(k)}
        self.verified = {"ok": True, "allow_resume": True, "budgets_exhausted": False,
                         "task_id": "task", "generation": 1, "latest_generation": 1,
                         "checkpoint_digest": digest(self.packet)}
        self.store = MemoryStore()
        self.adapter = FixtureAdapter(self.store)
        self.controller = RolloverController(self.store, self.adapter)
        self.controller.begin(self.packet, self.verified)

    def test_resume_same_task_step_budget_and_attempts(self):
        result = self.controller.run("task")
        self.assertEqual(result.phase, "COMPLETE")
        self.assertEqual(list(self.adapter.active.values()), [self.packet])
        self.assertEqual(self.adapter.launches, 1)
        self.assertEqual(self.store.owner, "successor")
        self.assertEqual(self.adapter.retired, {"source"})
        self.assertEqual(self.controller.begin(self.packet, self.verified), result)

    def test_crash_after_create_before_save_reconciles_same_child(self):
        self.controller.advance("task")  # Persist LAUNCH before creating.
        self.store.crash_on_save = "HYDRATE"
        with self.assertRaises(Crash):
            self.controller.advance("task")
        self.assertEqual(self.store.state.phase, "LAUNCH")
        restarted = RolloverController(self.store, self.adapter)
        self.assertEqual(restarted.run("task").phase, "COMPLETE")
        self.assertEqual(self.adapter.launches, 1)

    def test_crash_after_lease_transfer_replays_same_fence(self):
        self.store.crash_on_save = "ACTIVATE"
        with self.assertRaises(Crash):
            self.controller.run("task")
        self.assertEqual(self.store.owner, "successor")
        self.assertEqual(self.controller.run("task").phase, "COMPLETE")
        self.assertEqual(self.store.fence, 2)

    def test_crash_after_activation_does_not_resume_work_twice(self):
        self.store.crash_on_save = "RETIRE"
        with self.assertRaises(Crash):
            self.controller.run("task")
        self.assertEqual(len(self.adapter.active), 1)
        self.assertEqual(self.controller.run("task").phase, "COMPLETE")
        self.assertEqual(len(self.adapter.active), 1)

    def test_wrong_ack_never_transfers_or_activates(self):
        self.adapter.bad_ack = True
        for _ in range(3):  # Each call represents a later scheduler wake-up.
            result = self.controller.run("task")
        self.assertEqual((result.phase, result.failures), ("HOLD", 3))
        self.assertEqual(self.store.owner, "source")
        self.assertFalse(self.adapter.active)
        self.assertIn("source", self.adapter.paused)

    def test_conflicting_writer_isolates_both_candidates(self):
        self.store.owner = "another-owner"
        result = self.controller.run("task")
        self.assertEqual((result.phase, result.held_phase), ("HOLD", "TRANSFER"))
        self.assertEqual(self.store.owner, "another-owner")
        self.assertFalse(self.adapter.active)
        self.assertFalse(self.adapter.retired)

    def test_three_failures_hold_with_checkpoint_and_budget_intact(self):
        self.adapter.fail_hydrate = True
        for _ in range(3):
            result = self.controller.run("task")
        self.assertEqual((result.phase, self.adapter.hydrate_attempts), ("HOLD", 3))
        self.assertEqual(result.packet, self.packet)
        self.assertEqual(self.controller.run("task"), result)
        self.assertEqual(self.adapter.hydrate_attempts, 3)

    def test_forked_history_is_rejected(self):
        self.adapter.inherit_history = True
        for _ in range(3):
            result = self.controller.run("task")
        self.assertEqual(result.phase, "HOLD")
        self.assertFalse(self.adapter.hydrated)
        self.assertFalse(self.adapter.active)

    def test_launch_failure_yields_for_scheduler_backoff_then_holds(self):
        self.adapter.fail_launch = True
        for attempt in range(1, 4):
            result = self.controller.run("task")
            self.assertEqual(self.adapter.launch_attempts, attempt)
            self.assertEqual(result.failures, attempt)
            self.assertEqual(result.error_ref, "failure-receipt")
        self.assertEqual(result.phase, "HOLD")
        self.assertIn("source", self.adapter.paused)

    def test_stale_checkpoint_and_budget_reset_are_rejected(self):
        altered = copy.deepcopy(self.packet)
        altered["budgets"]["attempts_used"] = 0
        with self.assertRaises(ValueError):
            self.controller.begin(altered, self.verified)
        with self.assertRaises(ValueError):
            self.controller.begin(self.packet, dict(self.verified, allow_resume=False))
        with self.assertRaises(ValueError):
            self.controller.begin(self.packet, dict(self.verified, latest_generation=2))
        self.controller.run("task")
        altered.update(generation=2, source_session_id="successor")
        updated_verification = dict(self.verified, generation=2, latest_generation=2,
                                    checkpoint_digest=digest(altered))
        with self.assertRaises(ValueError):
            self.controller.begin(altered, updated_verification)
        altered["budgets"] = copy.deepcopy(self.packet["budgets"])
        altered["intent"]["sha256"] = digest("different task")
        updated_verification["checkpoint_digest"] = digest(altered)
        with self.assertRaises(ValueError):
            self.controller.begin(altered, updated_verification)


class GuardIntegrationTests(unittest.TestCase):
    def test_two_rollovers_with_interleaved_warm_checkpoints(self):
        import session_guard as guard
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from test_session_guard import make_checkpoint_fixture

        # Reuse the real temporary Git repository and hashed-file fixture.
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = SimpleNamespace(**make_checkpoint_fixture(temporary.name))
        store = MemoryStore()
        store.owner = fixture.packet["source_session_id"]
        adapter = FixtureAdapter(store)
        controller = RolloverController(store, adapter)
        previous_packet = None
        for warm_generation, ready_generation in ((1, 2), (3, 4)):
            packet = copy.deepcopy(fixture.packet)
            packet.update(generation=warm_generation, phase="checkpoint", source_session_id=store.owner)
            packet["budgets"]["attempts_used"] += warm_generation // 2
            warm = guard.write_checkpoint(packet, fixture.task, fixture.store, fixture.repo)
            self.assertFalse(warm["allow_resume"])
            with self.assertRaises(ValueError):
                controller.begin(packet, warm)
            if previous_packet:
                stale = guard.verify_checkpoint(fixture.task, fixture.store, 2, fixture.repo)
                self.assertFalse(stale["allow_resume"])
                with self.assertRaises(ValueError):
                    controller.begin(previous_packet, stale)
            packet.update(generation=ready_generation, phase="ready")
            guard.write_checkpoint(packet, fixture.task, fixture.store, fixture.repo)
            loaded, checksum = guard.load_checkpoint(fixture.store, ready_generation)
            verified = guard.verify_checkpoint(fixture.task, fixture.store, ready_generation, fixture.repo)
            self.assertEqual(verified["latest_generation"], ready_generation)
            self.assertEqual(checksum, digest(loaded))
            controller.begin(loaded, verified)
            result = controller.run(loaded["task_id"])
            self.assertEqual(result.phase, "COMPLETE")
            self.assertEqual(result.packet, loaded)
            previous_packet = loaded
        self.assertEqual(adapter.launches, 2)
        self.assertEqual(store.owner, "successor-2")
        self.assertEqual(store.fence, 3)
        self.assertEqual(adapter.retired, {fixture.packet["source_session_id"], "successor"})


if __name__ == "__main__":
    unittest.main()
