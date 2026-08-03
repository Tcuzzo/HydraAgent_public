"""RED contract tests for skills.todo + skills.fs_edit correctness seams.

Two findings closed here:

* todo.run() open_count used ``not t["done"]`` (subscript) while the standalone
  ``start()``/``open_items`` helpers used ``t.get("done")``. A hand-edited or
  legacy todo line missing the ``done`` key raised KeyError inside ``run()``.
  Fixed: ``t.get("done")`` consistently — tolerant of a missing key.

* fs_edit.run() reported ``replacements = count`` even when the file held fewer
  occurrences than ``count`` (the guard only refuses the ``occurrences > count``
  ambiguous case). The trace/user was told ``count`` edits happened when only
  ``occurrences`` did. Fixed: report ``min(count, occurrences)`` — the ACTUAL
  number made.

No mocks: both tests write a real file/store to disk (tmp_path) and assert the
real side-effect (returned dict + file content read back).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from skills import fs_edit, todo


# ---------------------------------------------------------------------------
# todo: missing "done" key must not KeyError in run()
# ---------------------------------------------------------------------------


def test_run_open_count_tolerates_missing_done_key(tmp_path: Path) -> None:
    """A hand-edited/legacy todo line missing the ``done`` key must not crash
    run() — open_count degrades to .get("done") like start()/open_items."""
    store = tmp_path / todo.STORE_NAME
    store.write_text(
        json.dumps(
            {
                "next_id": 2,
                "todos": [
                    {
                        "id": 1,
                        "text": "legacy item missing done key",
                        "status": todo.STATUS_PENDING,
                        "created_at": "2026-08-03T00:00:00+00:00",
                        "done_at": None,
                        # NOTE: "done" intentionally absent — hand-edited legacy row.
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # run("list") returns the full dict including open_count; before the fix the
    # subscript ``not t["done"]`` raised KeyError on this row.
    result = todo.run("list", tmp_path)

    assert result["ok"] is True
    assert result["count"] == 1
    # A missing done key is treated as not-done (consistent with open_items).
    assert result["open_count"] == 1
    assert result["todos"][0]["text"] == "legacy item missing done key"


def test_run_open_count_counts_done_correctly(tmp_path: Path) -> None:
    """Sanity: with a normal done item present, open_count is 0 (regression
    guard against the .get change accidentally flipping the truth sense)."""
    todo.run("add", tmp_path, text="finish the seam")
    todo.run("done", tmp_path, todo_id=1)

    result = todo.run("list", tmp_path)
    assert result["open_count"] == 0
    assert result["count"] == 1


# ---------------------------------------------------------------------------
# fs_edit: replacements_made must report the ACTUAL number made, not count
# ---------------------------------------------------------------------------


def test_fs_edit_reports_actual_replacements_when_occurrences_below_count(
    tmp_path: Path,
) -> None:
    """When the file has fewer occurrences than ``count``, the skill must
    report the ACTUAL number of replacements made (min), not the requested
    ``count`` — so the user and the trace see the truth."""
    target = tmp_path / "doc.txt"
    target.write_text("foo bar foo bar baz", encoding="utf-8")

    # 'foo' occurs 2x; ask for count=5. The guard only refuses occurrences >
    # count, so this proceeds. Before the fix it reported replacements_made=5
    # while only 2 were actually made.
    result = fs_edit.run(
        path="doc.txt",
        old_string="foo",
        new_string="QUX",
        root=tmp_path,
        count=5,
    )

    assert result["ok"] is True
    assert result["replacements_made"] == 2, (
        f"expected 2 actual replacements, got {result['replacements_made']}"
    )
    # And the file truly has only 2 QUX (truth verified on disk, not the dict).
    assert target.read_text(encoding="utf-8") == "QUX bar QUX bar baz"


def test_fs_edit_reports_count_when_occurrences_equal_count(tmp_path: Path) -> None:
    """Regression guard: when occurrences == count, report count (the .min
    change must not under-report the exact-match case)."""
    target = tmp_path / "doc.txt"
    target.write_text("foo bar foo bar baz", encoding="utf-8")

    result = fs_edit.run(
        path="doc.txt",
        old_string="foo",
        new_string="QUX",
        root=tmp_path,
        count=2,
    )
    assert result["replacements_made"] == 2
    assert target.read_text(encoding="utf-8") == "QUX bar QUX bar baz"


def test_fs_edit_still_refuses_ambiguous_occurrences_above_count(tmp_path: Path) -> None:
    """Regression guard for the existing refusal: occurrences > count (and not
    'all') must still be refused — the truth-reporting fix must not relax the
    ambiguity guard."""
    target = tmp_path / "doc.txt"
    target.write_text("foo bar foo bar foo", encoding="utf-8")  # 3 occurrences

    with pytest.raises(fs_edit.SkillError):
        fs_edit.run(
            path="doc.txt",
            old_string="foo",
            new_string="QUX",
            root=tmp_path,
            count=1,
        )
    # File untouched.
    assert target.read_text(encoding="utf-8") == "foo bar foo bar foo"