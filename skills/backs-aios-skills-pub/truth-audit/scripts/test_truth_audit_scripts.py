"""Regression tests for the truth-audit tools. Stdlib only:
python -m unittest discover -s <this dir> -p "test_*.py"
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ball_scope_check  # noqa: E402
import cmp_trees  # noqa: E402
import ledger  # noqa: E402

LANE = """# Lane SVC · service loop

Isolation: read-only.

### SVC-01 · P0 · Worker retries forever on a rejected credential
- status: OPEN · confidence: high
- surface: background worker behind the main window
- evidence (read-only, service log):
  - `service.log`: 602 of 602 loop errors today are HTTP 401
  - heartbeat state is "error"
- root cause (theater): the loop swallows every exception
- root cause (product): the credential was rotated
- fix seam: `runtime/loop.py` run_forever
- proof of done: an injected 401 in a sandbox stops the loop and shows one alert

### SVC-02 | P2 | Health page says green while the worker is down
* **Surface and evidence:** `GET /health` returns ok=true while heartbeat says error
* **Fix-seam**: `health.py` health()
* **Proof:** health is red within one loop of the injected failure

### SVC-03 · P3 · The synced notes are real (checked against the source API)
- status: ADJUDICATED-NOT-A-BUG · confidence: high
- evidence: 20 random notes from a copy of `notes.db` match the source API field for field

### SVC-04 · P1 · No evidence here
- status: OPEN · confidence: low
- fix seam: somewhere
- proof of done: something

## Side effects
none
"""

PLAN = """# Plan

## Wave 1 - Stop the bleeding

#### Ball 1A · Loop tells the truth - SVC-01
**Scope:** `runtime/loop.py`, `tests/test_loop.py` (new)

#### Ball 1B · Health - SVC-02
**Scope:** `health.py`, `tests/test_health.py`
**Scope (OPS):** `OPS/deploy/unit.service`

## Wave 2 - Next

#### Ball 2A · Again
**Scope:** `runtime/loop.py`

#### Ball 2B · Clash
**Scope:** `runtime/*.py`
"""


class LedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.path = self.dir / "ledger.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_ledger(self, *args: str) -> int:
        return ledger.main(["--ledger", str(self.path), *args])

    def add(self, eid: str = "SVC-09", sev: str = "P1", **kw: str) -> int:
        args = ["add", "--id", eid, "--sev", sev, "--title", "t", "--evidence", kw.get("evidence", "cmd -> out"),
                "--fix-seam", "x.py", "--proof", "observed", "--by", kw.get("by", "coordinator")]
        if "task" in kw:
            args += ["--task", kw["task"]]
        return self.run_ledger(*args)

    def test_duplicate_id_refused(self) -> None:
        self.assertEqual(0, self.add())
        self.assertEqual(1, self.add())

    def test_missing_evidence_refused(self) -> None:
        self.assertEqual(1, self.add(evidence="  "))

    def to(self, status: str, by: str = "coordinator", eid: str = "SVC-09", *extra: str) -> int:
        return self.run_ledger("status", eid, status, "--by", by, *extra)

    def walk_to_in_build(self, builder: str = "builder", by: str | None = None) -> None:
        self.assertEqual(0, self.to("PLANNED"))
        self.assertEqual(0, self.to("IN-BUILD", by or builder, "SVC-09", "--builder", builder))

    def test_backward_move_refused(self) -> None:
        self.add(task="1A")
        self.assertEqual(0, self.to("PLANNED"))
        self.assertEqual(0, self.run_ledger("status", "SVC-09", "IN-BUILD", "--by", "coordinator", "--builder", "b1"))
        self.assertEqual(1, self.run_ledger("status", "SVC-09", "PLANNED", "--by", "coordinator"))

    def test_p1_needs_task_before_in_build(self) -> None:
        self.add()
        self.assertEqual(1, self.run_ledger("status", "SVC-09", "IN-BUILD", "--by", "coordinator", "--builder", "b1"))
        self.assertEqual(0, self.run_ledger("status", "SVC-09", "PLANNED", "--by", "coordinator", "--task", "1A"))
        self.assertEqual(0, self.run_ledger("status", "SVC-09", "IN-BUILD", "--by", "coordinator", "--builder", "b1"))

    def test_p2_may_build_without_task(self) -> None:
        self.add(sev="P2")
        self.assertEqual(0, self.to("PLANNED"))
        self.assertEqual(0, self.run_ledger("status", "SVC-09", "IN-BUILD", "--by", "coordinator", "--builder", "b1"))

    def test_proven_and_closed_need_evidence(self) -> None:
        self.add(task="1A")
        self.walk_to_in_build()
        self.assertEqual(1, self.run_ledger("status", "SVC-09", "PROVEN", "--by", "reader"))
        self.assertEqual(0, self.run_ledger("status", "SVC-09", "PROVEN", "--by", "reader", "--evidence", "row 7"))
        self.assertEqual(1, self.run_ledger("status", "SVC-09", "CLOSED", "--by", "grader"))

    def test_builder_or_prover_cannot_close(self) -> None:
        self.add(task="1A")
        self.walk_to_in_build()
        self.run_ledger("status", "SVC-09", "PROVEN", "--by", "reader", "--evidence", "row 7")
        self.assertEqual(1, self.run_ledger("status", "SVC-09", "CLOSED", "--by", "builder", "--evidence", "x"))
        self.assertEqual(1, self.run_ledger("status", "SVC-09", "CLOSED", "--by", "reader", "--evidence", "x"))
        self.assertEqual(0, self.run_ledger("status", "SVC-09", "CLOSED", "--by", "grader", "--evidence", "run 3"))
        self.assertEqual(1, self.run_ledger("status", "SVC-09", "ADJUDICATED-NOT-A-BUG", "--by", "g",
                                            "--evidence", "x"))
        self.assertEqual(0, self.run_ledger("check"))

    def test_adjudication_needs_evidence(self) -> None:
        self.add()
        self.assertEqual(1, self.run_ledger("status", "SVC-09", "ADJUDICATED-NOT-A-BUG", "--by", "c"))
        self.assertEqual(0, self.run_ledger("status", "SVC-09", "ADJUDICATED-NOT-A-BUG", "--by", "c",
                                            "--evidence", "re-ran: data matches"))

    def test_check_catches_hand_edit_and_rule_break(self) -> None:
        self.add()
        self.add(eid="SVC-10")
        self.assertEqual(0, self.run_ledger("check"))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["evidence"] = "rewritten later"
        lines[0] = json.dumps(first, sort_keys=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(1, self.run_ledger("check"))

    def test_check_replays_rules_over_appended_lines(self) -> None:
        self.add()
        # Someone bypasses the tool and appends an illegal close with a valid chain link.
        ledger.append(self.path, {"kind": "status", "id": "SVC-09", "status": "CLOSED", "by": "x", "evidence": ""})
        self.assertEqual(1, self.run_ledger("check"))

    def test_import_lane_tolerates_variants_and_keeps_positive_findings(self) -> None:
        lane = self.dir / "SVC_SERVICE.md"
        lane.write_text(LANE, encoding="utf-8")
        rc = self.run_ledger("import-lane", str(lane), "--by", "ledger-keeper")
        self.assertEqual(1, rc, "SVC-04 has no evidence and must be refused")
        entries, problems = ledger.fold(ledger.read_events(self.path))
        self.assertEqual([], problems)
        self.assertEqual({"SVC-01", "SVC-02", "SVC-03"}, set(entries))
        self.assertIn("602 of 602", entries["SVC-01"]["evidence"])
        self.assertIn("(read-only, service log)", entries["SVC-01"]["evidence"])
        self.assertIn("theater", entries["SVC-01"]["root_cause"])
        self.assertIn("product", entries["SVC-01"]["root_cause"])
        self.assertEqual("high", entries["SVC-01"]["confidence"])
        self.assertIn("/health", entries["SVC-02"]["evidence"])
        self.assertEqual("health.py health()", entries["SVC-02"]["fix_seam"].replace("`", ""))
        self.assertEqual("ADJUDICATED-NOT-A-BUG", entries["SVC-03"]["status"])
        # Re-import is idempotent.
        self.run_ledger("import-lane", str(lane), "--by", "ledger-keeper")
        self.assertEqual(4, len(ledger.read_lines(self.path)))
        self.assertEqual(0, self.run_ledger("check"))

    def test_render_writes_status_board(self) -> None:
        self.add()
        self.assertEqual(0, self.run_ledger("render"))
        text = (self.dir / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("| SVC-09 | P1 | OPEN |", text)


LANE_OK = LANE.split("### SVC-04")[0] + "## Side effects\nnone\n"


class LedgerRegressionTest(LedgerTest):
    """Grade findings on 3f492be. Each test failed before its fix."""

    # 1. forward-only means next-only
    def test_skip_to_closed_refused(self) -> None:
        self.add(task="1A")
        self.assertEqual(1, self.to("CLOSED", "builder", "SVC-09", "--evidence", "x"))
        self.assertEqual(1, self.to("IN-BUILD", "coordinator", "SVC-09", "--builder", "b1"))
        self.assertEqual(1, self.to("PROVEN", "reader", "SVC-09", "--evidence", "x"))

    def test_p0_cannot_reach_proven_without_task(self) -> None:
        self.add(sev="P0")
        self.assertEqual(1, self.to("PROVEN", "reader", "SVC-09", "--evidence", "x"))
        self.assertEqual(0, self.to("PLANNED"))
        self.assertEqual(1, self.to("IN-BUILD", "coordinator", "SVC-09", "--builder", "b1"))
        self.assertEqual(1, self.to("PROVEN", "reader", "SVC-09", "--evidence", "x"))
        entries, _ = ledger.fold(ledger.read_events(self.path))
        self.assertEqual("PLANNED", entries["SVC-09"]["status"])

    def test_check_flags_historical_skip(self) -> None:
        self.add(task="1A")
        ledger.append(self.path, {"kind": "status", "id": "SVC-09", "status": "PROVEN", "by": "r",
                                  "evidence": "row 1"})
        self.assertEqual(1, self.run_ledger("check"))

    # 3. heading separators and unparseable ID headings
    def test_heading_separators_and_refusal(self) -> None:
        lane = self.dir / "SEP_LANE.md"
        body = "- status: OPEN · confidence: high\n- evidence: cmd -> out\n- fix seam: a.py\n- proof of done: seen\n"
        lane.write_text(
            "### SEP-01 — P0 — em dash separators\n" + body +
            "### SEP-02: P1: colon separators\n" + body +
            "### SEP-03 - P2 - hyphen separators\n" + body +
            "### SEP-04 – P3 – en dash separators\n" + body +
            "### SEP-05 ~~ P9 ~~ cannot parse\n" + body, encoding="utf-8")
        rc = self.run_ledger("import-lane", str(lane), "--by", "k")
        self.assertEqual(1, rc, "an unparseable ID heading must fail the import")
        entries, _ = ledger.fold(ledger.read_events(self.path))
        self.assertEqual({"SEP-01", "SEP-02", "SEP-03", "SEP-04"}, set(entries))
        self.assertEqual("P0", entries["SEP-01"]["sev"])
        self.assertEqual("colon separators", entries["SEP-02"]["title"])

    # 8. truncation is detected through the head file
    def test_truncation_detected(self) -> None:
        self.add()
        self.add(eid="SVC-10")
        self.assertEqual(0, self.run_ledger("check"))
        first = self.path.read_text(encoding="utf-8").splitlines()[0]
        self.path.write_text(first + "\n", encoding="utf-8")
        self.assertEqual(1, self.run_ledger("check"))

    def test_missing_head_file_is_a_problem(self) -> None:
        self.add()
        pathlib.Path(str(self.path) + ".head").unlink()
        self.assertEqual(1, self.run_ledger("check"))

    # 9. a non-JSON line is a named refusal, not a traceback
    def test_non_json_line_refused_by_every_command(self) -> None:
        self.add()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write("not json at all\n")
        for args in (("list",), ("status", "SVC-09", "PLANNED", "--by", "c"), ("render",), ("check",)):
            with self.subTest(args=args):
                self.assertEqual(1, self.run_ledger(*args))

    # 10. changed evidence on re-import is refused; amend appends an update
    def test_reimport_with_changed_evidence_needs_amend(self) -> None:
        lane = self.dir / "SVC_SERVICE.md"
        lane.write_text(LANE_OK, encoding="utf-8")
        self.assertEqual(0, self.run_ledger("import-lane", str(lane), "--by", "k"))
        lane.write_text(LANE_OK.replace("602 of 602", "700 of 700"), encoding="utf-8")
        self.assertEqual(1, self.run_ledger("import-lane", str(lane), "--by", "k"))
        new = [f for f in ledger.parse_lane(lane.read_text(encoding="utf-8")) if f["id"] == "SVC-01"][0]
        self.assertEqual(1, self.run_ledger("amend", "SVC-01", "--by", "k"), "amend with no field is refused")
        self.assertEqual(0, self.run_ledger("amend", "SVC-01", "--by", "k", "--evidence", new["evidence"],
                                            "--note", "recount"))
        entries, _ = ledger.fold(ledger.read_events(self.path))
        self.assertIn("700 of 700", entries["SVC-01"]["evidence"])
        self.assertEqual(0, self.run_ledger("import-lane", str(lane), "--by", "k"))
        self.assertEqual(0, self.run_ledger("check"))

    # 11. the builder cannot prove its own entry
    def test_in_build_actor_cannot_prove(self) -> None:
        self.add(task="1A")
        self.walk_to_in_build("builder")
        self.assertEqual(1, self.to("PROVEN", "builder", "SVC-09", "--evidence", "row 1"))
        self.assertEqual(0, self.to("PROVEN", "reader", "SVC-09", "--evidence", "row 1"))


class ScopeCheckTest(unittest.TestCase):
    def test_parse_and_scope(self) -> None:
        ball = ball_scope_check.find_ball(PLAN, "1B")
        # A (OPS) field normalizes away its own leading OPS/ (one key rule for both modes).
        self.assertEqual(["health.py", "tests/test_health.py", "deploy/unit.service"],
                         ball_scope_check.scope_paths(ball["lines"]))
        self.assertEqual(["health.py", "tests/test_health.py", "deploy/unit.service"],
                         ball_scope_check.scope_paths(ball["lines"], "OPS"))
        self.assertEqual(["health.py", "tests/test_health.py"],
                         ball_scope_check.scope_paths(ball["lines"], "APP"))

    def test_in_scope(self) -> None:
        scope = ["runtime/loop.py", "tests/*.py"]
        self.assertTrue(ball_scope_check.in_scope("src/pkg/runtime/loop.py", scope))
        self.assertTrue(ball_scope_check.in_scope("tests/test_loop.py", scope))
        self.assertFalse(ball_scope_check.in_scope("runtime/other.py", scope))

    def test_disjoint_flags_shared_files_in_a_wave_only(self) -> None:
        problems = ball_scope_check.overlaps(PLAN)
        self.assertEqual(1, len(problems), problems)
        self.assertIn("2A and 2B", problems[0])

    def test_branch_out_of_scope_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

            def git(*a: str) -> None:
                subprocess.run(["git", "-c", "safe.directory=*", "-C", str(repo), *a],
                               check=True, capture_output=True, env=env)
            git("init", "-q", "-b", "main")
            (repo / "health.py").write_text("x\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-q", "-m", "base")
            git("switch", "-q", "-c", "ball/1B")
            (repo / "health.py").write_text("y\n", encoding="utf-8")
            git("add", "health.py")
            git("commit", "-q", "-m", "fix")
            plan = pathlib.Path(raw + "-plan.md")
            plan.write_text(PLAN, encoding="utf-8")
            self.addCleanup(plan.unlink)
            args = ["1B", "--plan", str(plan), "--repo", str(repo), "--base", "main"]
            self.assertEqual(0, ball_scope_check.main(args))
            # An untracked drive-by file is a change outside the ball's scope.
            (repo / "other.py").write_text("z\n", encoding="utf-8")
            self.assertEqual(1, ball_scope_check.main(args))
            # A coordinator ball with no Scope line is reported, not silently passed.
            plan.write_text(PLAN + "\n#### Ball 0.1 · Freeze what runs (coordinator)\n- [ ] snapshot\n",
                            encoding="utf-8")
            self.assertEqual(2, ball_scope_check.main(["0.1", "--plan", str(plan), "--repo", str(repo)]))


WRAPPED_PLAN = """## Wave 1

#### Ball 1A · Wrapped scope
**Goal:** something.
**Scope:** `runtime/loop_guard.py` (new), `runtime/sync_worker.py` (`run_forever` only),
`api/health.py` (`health`), `tests/runtime/test_loop_guard.py` (new),
`Makefile`

**Interfaces:** `LoopGuard.run(fn)` returns `T | None`

#### Ball 1B · Clash on a wrapped line
**Scope:** `docs/notes.md`,
`api/health.py`
"""


class ScopeRegressionTest(unittest.TestCase):
    # 2. scope wraps over lines; 4. bare names count; symbols in parentheses do not
    def test_wrapped_scope_is_read_whole(self) -> None:
        ball = ball_scope_check.find_ball(WRAPPED_PLAN, "1A")
        self.assertEqual(["runtime/loop_guard.py", "runtime/sync_worker.py", "api/health.py",
                          "tests/runtime/test_loop_guard.py", "Makefile"],
                         ball_scope_check.scope_paths(ball["lines"]))

    def test_disjoint_sees_clash_on_wrapped_line(self) -> None:
        problems = ball_scope_check.overlaps(WRAPPED_PLAN)
        self.assertEqual(1, len(problems), problems)
        self.assertIn("api/health.py", problems[0])

    def test_template_worked_ball_scope_is_complete(self) -> None:
        tpl = (HERE.parent / "references" / "plan_template.md").read_text(encoding="utf-8")
        ball_md = tpl.split("## Worked example ball", 1)[1]
        ball = ball_scope_check.find_ball(ball_md, "1A")
        self.assertEqual(5, len(ball_scope_check.scope_paths(ball["lines"])))

    # 2 + 6. a token that is not a relative path is refused by name
    def test_unparseable_scope_tokens_refused(self) -> None:
        for bad in ("/etc/example.conf", "../outside.py", "C:/abs/file.py", "/etc/passwd", "a b.py"):
            plan = f"#### Ball 9Z · bad\n**Scope:** `ok.py`, `{bad}`\n"
            with self.subTest(token=bad):
                with self.assertRaises(ball_scope_check.ScopeError) as cm:
                    ball_scope_check.scope_paths(ball_scope_check.find_ball(plan, "9Z")["lines"])
                self.assertIn(bad, str(cm.exception))

    def test_unparseable_scope_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            plan = pathlib.Path(raw, "PLAN.md")
            plan.write_text("## Wave 1\n#### Ball 9Z · bad\n**Scope:** `../x.py`\n", encoding="utf-8")
            self.assertEqual(2, ball_scope_check.main(["9Z", "--plan", str(plan)]))
            self.assertEqual(2, ball_scope_check.main(["--disjoint", "--plan", str(plan)]))

    # 5. one matcher for both modes
    def test_disjoint_uses_the_ball_matcher(self) -> None:
        plan = ("## Wave 1\n#### Ball 1A · a\n**Scope:** `runtime/loop.py`\n\n"
                "#### Ball 1B · b\n**Scope:** `src/pkg/runtime/loop.py`\n")
        self.assertTrue(ball_scope_check.in_scope("src/pkg/runtime/loop.py", ["runtime/loop.py"]))
        self.assertEqual(1, len(ball_scope_check.overlaps(plan)))

    # 7. a missing base is a clean error
    def test_missing_base_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw)
            subprocess.run(["git", "-c", "safe.directory=*", "-C", str(repo), "init", "-q"], check=True)
            plan = pathlib.Path(raw + "-plan.md")
            plan.write_text(PLAN, encoding="utf-8")
            self.addCleanup(plan.unlink)
            self.assertEqual(2, ball_scope_check.main(["1B", "--plan", str(plan), "--repo", str(repo),
                                                       "--base", "no-such-base"]))


class LedgerRound3Test(LedgerTest):
    """Grade findings on 2e19f22. Each test failed before its fix."""

    # 1. adjudication follows the closer rule
    def test_builder_cannot_adjudicate_own_entry(self) -> None:
        self.add(sev="P0", task="1A")
        self.walk_to_in_build("builder")
        self.assertEqual(1, self.to("ADJUDICATED-NOT-A-BUG", "builder", "SVC-09", "--evidence", "fine"))

    def test_prover_cannot_adjudicate(self) -> None:
        self.add(task="1A")
        self.walk_to_in_build("builder")
        self.assertEqual(0, self.to("PROVEN", "reader", "SVC-09", "--evidence", "row 1"))
        self.assertEqual(1, self.to("ADJUDICATED-NOT-A-BUG", "reader", "SVC-09", "--evidence", "fine"))
        self.assertEqual(0, self.to("ADJUDICATED-NOT-A-BUG", "grader", "SVC-09", "--evidence", "fine"))

    def test_check_flags_historical_self_adjudication(self) -> None:
        self.add(task="1A")
        self.walk_to_in_build("builder")
        ledger.append(self.path, {"kind": "status", "id": "SVC-09", "status": "ADJUDICATED-NOT-A-BUG",
                                  "by": "builder", "evidence": "fine"})
        self.assertEqual(1, self.run_ledger("check"))

    # 2. the refusal prints the exact value an amend must carry
    def test_changed_lane_refusal_prints_expected_value(self) -> None:
        import contextlib
        import io
        lane = self.dir / "SVC_SERVICE.md"
        lane.write_text(LANE_OK, encoding="utf-8")
        self.run_ledger("import-lane", str(lane), "--by", "k")
        lane.write_text(LANE_OK.replace("602 of 602", "700   of\n  700"), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(1, self.run_ledger("import-lane", str(lane), "--by", "k"))
        expected = [f for f in ledger.parse_lane(lane.read_text(encoding="utf-8")) if f["id"] == "SVC-01"][0]
        printed = ledger._norm(expected["evidence"])
        self.assertIn(printed, err.getvalue())
        self.assertEqual(0, self.run_ledger("amend", "SVC-01", "--by", "k", "--evidence", printed))
        self.assertEqual(0, self.run_ledger("import-lane", str(lane), "--by", "k"))

    # 3. the builder or prover cannot amend
    def test_builder_cannot_amend(self) -> None:
        self.add(task="1A")
        self.walk_to_in_build("builder")
        self.assertEqual(1, self.run_ledger("amend", "SVC-09", "--by", "builder", "--proof", "easier proof"))
        self.assertEqual(0, self.run_ledger("amend", "SVC-09", "--by", "keeper", "--proof", "sharper proof"))
        self.assertEqual(0, self.to("PROVEN", "reader", "SVC-09", "--evidence", "row 1"))
        self.assertEqual(1, self.run_ledger("amend", "SVC-09", "--by", "reader", "--evidence", "x"))


class ScopeRound3Test(unittest.TestCase):
    # 4. prose after Scope ends the field
    def test_prose_line_ends_scope(self) -> None:
        plan = ("#### Ball 1A · a\n**Scope:** `a.py`,\n  `b.py`\n, `c.py`\n"
                "This ball also mentions it in passing.\n`d.py`\n")
        ball = ball_scope_check.find_ball(plan, "1A")
        self.assertEqual(["a.py", "b.py", "c.py"], ball_scope_check.scope_paths(ball["lines"]))

    # 5. path-like tokens inside parentheses count
    def test_path_in_parentheses_counts(self) -> None:
        plan = "#### Ball 1A · a\n**Scope:** `a.py` (`run` only, and `lib/helper.py`), `b.py` (`conf.yaml`)\n"
        ball = ball_scope_check.find_ball(plan, "1A")
        self.assertEqual(["a.py", "lib/helper.py", "b.py", "conf.yaml"],
                         ball_scope_check.scope_paths(ball["lines"]))

    # 6. disjoint honors repo keys
    def test_disjoint_honors_repo_keys(self) -> None:
        plan = ("## Wave 1\n#### Ball 1A · a\n**Scope (APP):** `deploy.sh`\n\n"
                "#### Ball 1B · b\n**Scope (OPS):** `deploy.sh`\n\n"
                "#### Ball 1C · c\n**Scope (OPS):** `deploy.sh`\n")
        problems = ball_scope_check.overlaps(plan)
        self.assertEqual(1, len(problems), problems)
        self.assertIn("1B and 1C", problems[0])

    # 7. an unknown ball is an error
    def test_unknown_ball_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            plan = pathlib.Path(raw, "PLAN.md")
            plan.write_text(PLAN, encoding="utf-8")
            try:
                rc = ball_scope_check.main(["ZZ", "--plan", str(plan)])
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 1
            self.assertEqual(2, rc)

    # 10. optional content hashes on scope paths detect drift at the base
    def test_scope_hash_parses_to_plain_path(self) -> None:
        plan = "#### Ball 1A · a\n**Scope:** `api/health.py@sha256:0123abcd`, `new.py`\n"
        ball = ball_scope_check.find_ball(plan, "1A")
        self.assertEqual(["api/health.py", "new.py"], ball_scope_check.scope_paths(ball["lines"]))
        self.assertEqual({"api/health.py": "0123abcd"}, ball_scope_check.scope_hashes(ball["lines"]))
        bad = "#### Ball 1B · b\n**Scope:** `x.py@sha256:12`\n"
        with self.assertRaises(ball_scope_check.ScopeError):
            ball_scope_check.scope_paths(ball_scope_check.find_ball(bad, "1B")["lines"])

    def test_scope_hash_drift_exit_1(self) -> None:
        import hashlib
        with tempfile.TemporaryDirectory() as raw:
            repo = pathlib.Path(raw)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

            def git(*a: str) -> None:
                subprocess.run(["git", "-c", "safe.directory=*", "-c", "core.autocrlf=false", "-C", str(repo), *a],
                               check=True, capture_output=True, env=env)
            git("init", "-q", "-b", "main")
            (repo / "health.py").write_bytes(b"x\n")
            git("add", ".")
            git("commit", "-q", "-m", "base")
            good = hashlib.sha256(b"x\n").hexdigest()[:12]
            plan = pathlib.Path(raw + "-plan.md")
            self.addCleanup(plan.unlink)
            args = ["1A", "--plan", str(plan), "--repo", str(repo), "--base", "main"]
            plan.write_text(f"#### Ball 1A · a\n**Scope:** `health.py@sha256:{good}`\n", encoding="utf-8")
            self.assertEqual(0, ball_scope_check.main(args))
            plan.write_text("#### Ball 1A · a\n**Scope:** `health.py@sha256:deadbeef00`\n", encoding="utf-8")
            self.assertEqual(1, ball_scope_check.main(args))


class LedgerRound4Test(LedgerTest):
    """Grade findings on 2bcff70. Each test failed before its fix."""

    # 6. IN-BUILD records the real builder, and every actor rule excludes it
    def test_in_build_needs_builder(self) -> None:
        self.add(sev="P2")
        self.assertEqual(0, self.to("PLANNED"))
        self.assertEqual(1, self.to("IN-BUILD", "coordinator"))
        self.assertEqual(0, self.to("IN-BUILD", "coordinator", "SVC-09", "--builder", "b1"))

    def test_recorded_builder_excluded_everywhere(self) -> None:
        self.add(task="1A")
        self.walk_to_in_build("b1", by="coordinator")
        self.assertEqual(1, self.to("PROVEN", "b1", "SVC-09", "--evidence", "row 1"))
        self.assertEqual(1, self.to("PROVEN", "coordinator", "SVC-09", "--evidence", "row 1"))
        self.assertEqual(1, self.run_ledger("amend", "SVC-09", "--by", "b1", "--proof", "easier"))
        self.assertEqual(1, self.to("ADJUDICATED-NOT-A-BUG", "b1", "SVC-09", "--evidence", "fine"))
        self.assertEqual(0, self.to("PROVEN", "reader", "SVC-09", "--evidence", "row 1"))
        self.assertEqual(1, self.to("CLOSED", "b1", "SVC-09", "--evidence", "run 1"))
        self.assertEqual(0, self.to("CLOSED", "grader", "SVC-09", "--evidence", "run 1"))
        self.assertEqual(0, self.run_ledger("check"))

    def test_check_flags_in_build_without_builder(self) -> None:
        self.add(sev="P2")
        self.to("PLANNED")
        ledger.append(self.path, {"kind": "status", "id": "SVC-09", "status": "IN-BUILD", "by": "coordinator"})
        self.assertEqual(1, self.run_ledger("check"))


class ScopeRound4Test(unittest.TestCase):
    # 1. one repo-key rule for both modes
    def plan(self, a: str, b: str) -> str:
        return f"## Wave 1\n#### Ball 1A · a\n{a}\n\n#### Ball 1B · b\n{b}\n"

    def test_untagged_clashes_with_tagged(self) -> None:
        plan = self.plan("**Scope:** `api/health.py`", "**Scope (APP):** `api/health.py`")
        self.assertEqual(1, len(ball_scope_check.overlaps(plan)))

    def test_untagged_key_prefix_is_normalized(self) -> None:
        plan = self.plan("**Scope:** `APP/api/health.py`", "**Scope (APP):** `api/health.py`")
        self.assertEqual(1, len(ball_scope_check.overlaps(plan)))

    def test_different_keys_never_clash(self) -> None:
        plan = self.plan("**Scope (APP):** `api/health.py`", "**Scope (OPS):** `api/health.py`")
        self.assertEqual([], ball_scope_check.overlaps(plan))

    # 2 + 3. a line that ends the field but carries paths is refused by line
    def test_prose_with_path_after_scope_refused(self) -> None:
        plan = "#### Ball 1A · a\n**Scope:** `a.py`,\nalso touches `b.py` here\n"
        with self.assertRaises(ball_scope_check.ScopeError) as cm:
            ball_scope_check.scope_paths(ball_scope_check.find_ball(plan, "1A")["lines"])
        self.assertIn("also touches", str(cm.exception))

    def test_indented_prose_ends_field(self) -> None:
        quiet = "#### Ball 1A · a\n**Scope:** `a.py`,\n    and nothing else\n"
        self.assertEqual(["a.py"], ball_scope_check.scope_paths(ball_scope_check.find_ball(quiet, "1A")["lines"]))
        loud = "#### Ball 1A · a\n**Scope:** `a.py`,\n    and also `lib/b.py`\n"
        with self.assertRaises(ball_scope_check.ScopeError):
            ball_scope_check.scope_paths(ball_scope_check.find_ball(loud, "1A")["lines"])

    def test_refused_line_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            plan = pathlib.Path(raw, "PLAN.md")
            plan.write_text("## Wave 1\n#### Ball 1A · a\n**Scope:** `a.py`\nsee `b.py`\n", encoding="utf-8")
            self.assertEqual(2, ball_scope_check.main(["1A", "--plan", str(plan)]))
            self.assertEqual(2, ball_scope_check.main(["--disjoint", "--plan", str(plan)]))

    # 4. hash algorithm suffix
    def test_hash_suffix_case_and_algorithm(self) -> None:
        ok = "#### Ball 1A · a\n**Scope:** `a.py@SHA256:0123ABCD`\n"
        self.assertEqual({"a.py": "0123abcd"},
                         ball_scope_check.scope_hashes(ball_scope_check.find_ball(ok, "1A")["lines"]))
        bad = "#### Ball 1A · a\n**Scope:** `a.py@md5:0123abcd`\n"
        with self.assertRaises(ball_scope_check.ScopeError):
            ball_scope_check.scope_paths(ball_scope_check.find_ball(bad, "1A")["lines"])

    # 5. a hash on a glob is refused at parse time
    def test_hash_on_glob_refused(self) -> None:
        plan = "#### Ball 1A · a\n**Scope:** `tests/*.py@sha256:0123abcd`\n"
        with self.assertRaises(ball_scope_check.ScopeError):
            ball_scope_check.scope_paths(ball_scope_check.find_ball(plan, "1A")["lines"])


class CmpTreesTest(unittest.TestCase):
    def test_crlf_is_equal_and_content_change_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            a, b = pathlib.Path(raw, "a"), pathlib.Path(raw, "b")
            for d in (a, b):
                (d / "src").mkdir(parents=True)
                (d / "__pycache__").mkdir()
            (a / "src" / "m.py").write_bytes(b"x = 1\r\ny = 2\r\n")
            (b / "src" / "m.py").write_bytes(b"x = 1\ny = 2\n")
            (a / "__pycache__" / "junk.txt").write_bytes(b"1")
            (b / "__pycache__" / "junk.txt").write_bytes(b"2")
            self.assertEqual(0, cmp_trees.main([str(a), str(b)]))
            (b / "src" / "m.py").write_bytes(b"x = 1\ny = 3\n")
            (b / "only_b.txt").write_bytes(b"new")
            res = cmp_trees.compare(cmp_trees.walk(str(a), cmp_trees.DEFAULT_SKIP),
                                    cmp_trees.walk(str(b), cmp_trees.DEFAULT_SKIP))
            self.assertEqual(["src/m.py"], res["changed"])
            self.assertEqual(["only_b.txt"], res["only_b"])
            self.assertEqual(1, cmp_trees.main([str(a), str(b)]))

    def test_missing_dir_is_loud(self) -> None:
        self.assertEqual(2, cmp_trees.main(["no-such-dir-a", "no-such-dir-b"]))


class CmpTreesRegressionTest(unittest.TestCase):
    # 12. binary files are not CRLF-normalized; walk errors exit 2; dir symlinks reported
    def test_binary_crlf_difference_is_a_change(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            a, b = pathlib.Path(raw, "a"), pathlib.Path(raw, "b")
            a.mkdir()
            b.mkdir()
            (a / "blob.bin").write_bytes(b"\x00\x01\r\n\x02")
            (b / "blob.bin").write_bytes(b"\x00\x01\n\x02")
            self.assertEqual(1, cmp_trees.main([str(a), str(b)]))

    def test_walk_error_exits_2_naming_path(self) -> None:
        from unittest import mock
        with tempfile.TemporaryDirectory() as raw:
            a, b = pathlib.Path(raw, "a"), pathlib.Path(raw, "b")
            a.mkdir()
            b.mkdir()
            real_walk = os.walk

            def failing_walk(top, *args, **kw):
                onerror = kw.get("onerror")
                if onerror and str(top) == str(a):
                    onerror(PermissionError(13, "denied", str(a / "locked")))
                yield from real_walk(top, *args, **kw)
            with mock.patch.object(cmp_trees.os, "walk", failing_walk):
                self.assertEqual(2, cmp_trees.main([str(a), str(b)]))

    def test_dir_symlink_reported_not_silently_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            a, b, real = pathlib.Path(raw, "a"), pathlib.Path(raw, "b"), pathlib.Path(raw, "real")
            a.mkdir()
            b.mkdir()
            real.mkdir()
            (real / "f.txt").write_text("x", encoding="utf-8")
            try:
                os.symlink(real, a / "linked", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("cannot create a directory symlink here")
            tree = cmp_trees.walk(str(a), cmp_trees.DEFAULT_SKIP)
            self.assertIn("linked/", tree)
            self.assertTrue(tree["linked/"].startswith("dirlink:"))
            self.assertEqual(1, cmp_trees.main([str(a), str(b)]))


if __name__ == "__main__":
    unittest.main()
