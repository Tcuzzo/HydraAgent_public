"""Run with python3 -m unittest discover -s scripts -p test_session_guard.py."""
import copy
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

import session_guard as guard


def fixture_git(repo, *arguments):
    return subprocess.run(["git", "-C", str(repo), *arguments],
                          env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def make_checkpoint_fixture(base):
    """Return {repo, task, store, packet}; caller owns a temporary base directory.

    The ready packet is accepted by the real guard. Copy it and increment its
    generation for repeated warm/ready cycles in supervisor integration tests.
    """
    base = Path(base).resolve()
    repo, task = base / "repo", base / "task"
    repo.mkdir(parents=True)
    task.mkdir()
    fixture_git(repo, "init", "--quiet", "-b", "main")
    (repo / "tracked.txt").write_text("initial\n")
    fixture_git(repo, "add", "tracked.txt")
    fixture_git(repo, "-c", "user.name=Fixture", "-c", "user.email=invalid", "-c", "commit.gpgsign=false",
                "commit", "--quiet", "-m", "initial")
    (repo / "tracked.txt").write_text("work in progress\n")
    refs = {}
    for name in ("intent", "corrections", "constraints", "permissions", "evidence"):
        file = task / (name + ".txt")
        file.write_text(name + "\n")
        refs[name] = {"path": file.name, "sha256": guard.hash_path(file)}
    snapshot = guard.repo_snapshot(repo)
    packet = {"schema_version": 1, "task_id": "fixture-task", "generation": 1,
              "source_session_id": "session-a", "phase": "ready",
              "repo": {"root": str(repo), **{key: value for key, value in snapshot.items() if key != "dirty_paths"},
                       "dirty_manifest": [{"path": "tracked.txt", "sha256": guard.hash_path(repo / "tracked.txt")}]},
              **{key: refs[key] for key in ("intent", "corrections", "constraints", "permissions")},
              "current_step": "repair", "next_action": "run the failing reproduction",
              "pending_work": ["verify behavior"], "completed_work": ["reproduce failure"],
              "failed_attempts": [], "evidence_refs": [refs["evidence"]],
              "worker_receipts": [], "inflight_operations": [],
              "budgets": {"attempts_used": 2, "attempts_limit": 8, "task_tokens_used": 12000}}
    return {"repo": repo, "task": task, "store": task / "checkpoints", "packet": packet}


class PolicyTests(unittest.TestCase):
    def request(self, tokens=1000):
        return {"now": 100, "telemetry": {"provider": "codex-app-server", "observed_at": 100,
                "active_context_tokens": tokens, "quality": "known"}}

    def test_threshold_and_projection(self):
        self.assertTrue(guard.decide(self.request(124999))["allow_dispatch"])
        self.assertFalse(guard.decide(self.request(125000))["allow_dispatch"])
        request = self.request(120000)
        request["projected_additional_tokens"] = 6000
        self.assertEqual(guard.decide(request)["reason"], "projected_context_limit")

    def test_small_verified_model_clamps_and_reserves(self):
        request = self.request(16768)
        request["policy"] = {"context_window_tokens": 32768, "context_window_verified": True}
        result = guard.decide(request)
        self.assertEqual(result["rollover_tokens"], 16768)
        self.assertFalse(result["allow_dispatch"])
        request["policy"]["context_window_verified"] = False
        self.assertEqual(guard.decide(request)["action"], "refresh_required")

    def test_unknown_stale_invalid_and_cumulative_usage_fail_closed(self):
        variants = [None, {"provider": "codex-app-server", "observed_at": 100,
                          "total_token_usage": {"total_tokens": 1000}},
                    {"provider": "normalized", "observed_at": 39, "active_context_tokens": 1,
                     "quality": "known"}]
        for invalid in (None, -1, 1.5, True):
            telemetry = self.request()["telemetry"]
            telemetry["active_context_tokens"] = invalid
            variants.append(telemetry)
        for telemetry in variants:
            result = guard.decide({"now": 100, "telemetry": telemetry})
            self.assertEqual(result["action"], "refresh_required")
            self.assertFalse(result["allow_dispatch"])

    def test_claude_cached_tokens_count_once_and_unknown_output_is_labelled(self):
        usage = {"input_tokens": 1000, "cache_creation_input_tokens": 2000,
                 "cache_read_input_tokens": 4000, "output_tokens": 100}
        request = {"now": 100, "telemetry": {"provider": "claude-statusline", "observed_at": 100,
                    "context_window": {"current_usage": usage}}}
        self.assertEqual(guard.decide(request)["active_context_tokens"], 7100)
        usage["output_tokens"] = None
        self.assertEqual(guard.decide(request)["usage_quality"], "estimated")
        request["telemetry"]["context_window"]["current_usage"] = None
        self.assertFalse(guard.decide(request)["allow_dispatch"])

    def test_age_fallback_is_optional_and_warm_checkpoint_allows_work(self):
        request = self.request(110000)
        self.assertEqual(guard.decide(request)["action"], "checkpoint_requested")
        request["session_age_seconds"] = 14400
        self.assertEqual(guard.decide(request)["reason"], "session_age_limit")
        request["policy"] = {"max_session_age_seconds": None}
        self.assertTrue(guard.decide(request)["allow_dispatch"])


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.__dict__.update(make_checkpoint_fixture(self.base))

    def run_git(self, *arguments):
        return fixture_git(self.repo, *arguments)

    def refresh_repo_metadata(self):
        self.packet["repo"].update({key: value for key, value in guard.repo_snapshot(self.repo).items()
                                     if key != "dirty_paths"})

    def save(self, packet=None):
        return guard.write_checkpoint(packet or self.packet, self.task, self.store, self.repo)

    def verify(self):
        return guard.verify_checkpoint(self.task, self.store, 1, self.repo)

    def test_roundtrip_duplicate_and_conflicting_generation(self):
        result = self.save()
        self.assertTrue(result["allow_resume"])
        self.assertEqual(result["checkpoint_digest"], guard.digest(guard.canonical(self.packet)))
        self.assertTrue(self.save()["idempotent"])
        self.packet["next_action"] = "a different action"
        with self.assertRaisesRegex(guard.GuardError, "conflict"):
            self.save()

    def test_intent_corruption_and_replacement_block_adoption(self):
        self.save()
        (self.task / "intent.txt").write_text("silently altered intent")
        with self.assertRaisesRegex(guard.GuardError, "hash mismatch"):
            self.verify()
        self.packet["intent"]["sha256"] = guard.hash_path(self.task / "intent.txt")
        self.packet["generation"] = 2
        with self.assertRaisesRegex(guard.GuardError, "immutable"):
            self.save()

    def test_dirty_file_hash_and_new_unlisted_file_detected(self):
        self.save()
        (self.repo / "tracked.txt").write_text("changed again")
        with self.assertRaisesRegex(guard.GuardError, "hash mismatch"):
            self.verify()
        (self.repo / "tracked.txt").write_text("work in progress\n")
        (self.repo / "unexpected.txt").write_text("new untracked work")
        with self.assertRaisesRegex(guard.GuardError, "inventory"):
            self.verify()

    def test_head_branch_and_expected_root_verified(self):
        self.save()
        with self.assertRaisesRegex(guard.GuardError, "unexpected repository"):
            guard.verify_checkpoint(self.task, self.store, 1, self.task)
        self.run_git("checkout", "--quiet", "-b", "changed-branch")
        with self.assertRaisesRegex(guard.GuardError, "HEAD or branch"):
            self.verify()
        self.run_git("checkout", "--quiet", "main")
        self.run_git("add", "tracked.txt")
        self.run_git("-c", "user.name=Fixture", "-c", "user.email=invalid", "-c", "commit.gpgsign=false",
                     "commit", "--quiet", "-m", "different head")
        with self.assertRaisesRegex(guard.GuardError, "HEAD or branch"):
            self.verify()

    def test_rename_inventory_includes_deleted_source(self):
        self.run_git("mv", "tracked.txt", "renamed.txt")
        self.refresh_repo_metadata()
        self.packet["repo"]["dirty_manifest"] = [
            {"path": "tracked.txt", "sha256": None, "state": "deleted"},
            {"path": "renamed.txt", "sha256": guard.hash_path(self.repo / "renamed.txt")},
        ]
        self.assertTrue(self.save()["allow_resume"])

    def test_staging_changes_detected_with_unchanged_working_bytes(self):
        self.save()
        file = self.repo / "tracked.txt"
        file.write_text("different staged content\n")
        self.run_git("add", "tracked.txt")
        file.write_text("work in progress\n")
        with self.assertRaisesRegex(guard.GuardError, "stale repository index"):
            self.verify()

    @unittest.skipIf(os.name == "nt", "POSIX executable permission required")
    def test_mode_change_on_already_dirty_file_blocks_stale_adoption(self):
        self.save()
        file = self.repo / "tracked.txt"
        before = guard.repo_snapshot(self.repo)
        contents = file.read_bytes()
        file.chmod(stat.S_IMODE(file.stat().st_mode) ^ stat.S_IXUSR)
        after = guard.repo_snapshot(self.repo)
        self.assertEqual(contents, file.read_bytes())
        self.assertEqual(before["dirty_paths"], after["dirty_paths"])
        self.assertEqual(before["index_fingerprint"], after["index_fingerprint"])
        self.assertNotEqual(before["worktree_metadata_fingerprint"], after["worktree_metadata_fingerprint"])
        with self.assertRaisesRegex(guard.GuardError, "stale worktree metadata"):
            self.verify()

    def test_older_complete_generation_cannot_resume(self):
        self.save()
        self.packet["generation"] = 2
        self.save()
        result = self.verify()
        self.assertFalse(result["allow_resume"])
        self.assertEqual(result["latest_generation"], 2)

    def test_deleted_dirty_file_and_reappearance(self):
        (self.repo / "tracked.txt").unlink()
        self.refresh_repo_metadata()
        self.packet["repo"]["dirty_manifest"] = [{"path": "tracked.txt", "sha256": None, "state": "deleted"}]
        self.save()
        (self.repo / "tracked.txt").write_text("reappeared")
        with self.assertRaisesRegex(guard.GuardError, "reappeared"):
            self.verify()

    def test_partial_checkpoint_and_corrupt_complete_marker_not_used(self):
        folder = guard.checkpoint_path(self.store, 1)
        folder.mkdir(parents=True)
        (folder / "packet.json").write_bytes(guard.canonical(self.packet))
        with self.assertRaisesRegex(guard.GuardError, "incomplete"):
            self.verify()
        (folder / "packet.json").unlink()
        folder.rmdir()
        self.save()
        (folder / "COMPLETE").write_text("wrong digest\n")
        with self.assertRaisesRegex(guard.GuardError, "integrity"):
            self.verify()

    def test_inflight_operations_and_workers_prevent_activation(self):
        self.packet["inflight_operations"] = [{"operation_id": "operation-1", "status": "running",
                                               "evidence_refs": self.packet["evidence_refs"]}]
        with self.assertRaisesRegex(guard.GuardError, "in-flight"):
            self.save()
        self.packet["phase"] = "checkpoint"
        self.assertFalse(self.save()["allow_resume"])
        self.packet["generation"] = 2
        self.packet["phase"] = "ready"
        self.packet["inflight_operations"][0]["status"] = "reconciled"
        self.assertTrue(self.save()["allow_resume"])

    def test_budgets_cannot_reset_expand_or_disappear(self):
        self.save()
        self.packet["generation"] = 2
        original = copy.deepcopy(self.packet["budgets"])
        for budgets in ({**original, "attempts_used": 0}, {**original, "attempts_limit": 99},
                        {"attempts_used": 2, "attempts_limit": 8}):
            self.packet["budgets"] = budgets
            with self.assertRaisesRegex(guard.GuardError, "reset or expand"):
                self.save()
        self.packet["budgets"] = {**original, "attempts_used": 8}
        self.assertFalse(self.save()["allow_resume"])

    def test_utf8_byte_bound_traversal_secret_and_external_symlink(self):
        self.packet["next_action"] = "漢" * 20000
        with self.assertRaisesRegex(guard.GuardError, "byte bound"):
            self.save()
        for relative in ("../outside", ".env", "vault/key"):
            with self.assertRaises(guard.GuardError):
                guard.safe_path(self.task, relative)
        outside = self.base / "outside.txt"
        outside.write_text("outside")
        try:
            (self.task / "link.txt").symlink_to(outside)
        except OSError:
            return
        with self.assertRaisesRegex(guard.GuardError, "escapes"):
            guard.safe_path(self.task, "link.txt")


if __name__ == "__main__":
    unittest.main()
