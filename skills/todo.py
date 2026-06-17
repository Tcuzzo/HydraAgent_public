"""skills.todo — persistent per-workspace task list.

Mirror of OpenMono's `TodoTool`. Lets an agent (or operator) maintain
a checklist that persists across iterations of the agent loop and
across `python3 -m hydra ask` invocations.

Storage: JSON file at `<root>/.hydra_todos.json`. The skill creates it
on first write; subsequent reads tolerate a missing file (empty list).
A malformed JSON file is refused (the operator must fix or delete it)
rather than silently overwritten.

Actions:
  - `list` — return all current todos
  - `add <text>` — append a new todo
  - `done <id>` — mark a todo done
  - `undone <id>` — unmark
  - `remove <id>` — delete a todo (free up the id)
  - `clear` — drop all todos

Every action returns the full list — the agent always sees the
current state without a second call.

Returns: `ok`, `action`, `todos` (always — full current list after
the action), `affected_id` (when applicable).

Maturity: SCAFFOLDED. Promoted by §10.32.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

from skills.fs_read import SkillError

STORE_NAME = ".hydra_todos.json"
VALID_ACTIONS = frozenset({"list", "add", "done", "undone", "remove", "clear", "start", "assert_closed"})

# Status values for the single-in-progress discipline.
STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _store_path(root: Path) -> Path:
    return root / STORE_NAME


def _load(root: Path) -> tuple[list[dict], int]:
    """Return (todos, next_id). next_id is a monotonic counter that
    outlives `remove` and `clear` — once an id has been issued it is
    never re-used."""
    store = _store_path(root)
    if not store.is_file():
        return [], 1
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SkillError(
            f"todo store {store} is malformed JSON: {e}; fix or delete it"
        ) from e
    if not isinstance(data, dict) or "todos" not in data:
        raise SkillError(
            f"todo store {store} has wrong shape; expected mapping with "
            f"`todos` and `next_id`. Got: {type(data).__name__}"
        )
    todos = data.get("todos") or []
    if not isinstance(todos, list):
        raise SkillError(f"todo store {store}: `todos` must be a list")
    next_id = data.get("next_id")
    if not isinstance(next_id, int) or next_id < 1:
        # Tolerate an older or hand-edited store: derive a safe next_id.
        next_id = max((t.get("id", 0) for t in todos), default=0) + 1
    return todos, next_id


def _save(root: Path, todos: list[dict], next_id: int) -> None:
    store = _store_path(root)
    payload = json.dumps(
        {"next_id": next_id, "todos": todos}, indent=2
    ).encode("utf-8")
    tmp = store.with_name(store.name + ".hydra-tmp")
    try:
        with tmp.open("wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, store)
    except OSError as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise SkillError(f"todo store write failed: {e}") from e


def run(
    action: str,
    root: str | Path,
    *,
    text: str | None = None,
    todo_id: int | None = None,
) -> dict:
    """Perform `action` against the workspace todo store. Raise
    `SkillError` on refusal."""
    if action not in VALID_ACTIONS:
        raise SkillError(
            f"unknown action {action!r}; valid: {sorted(VALID_ACTIONS)}"
        )
    root_resolved = Path(root).resolve()
    if not root_resolved.is_dir():
        raise SkillError(f"root is not a directory: {root_resolved}")

    todos, next_id = _load(root_resolved)
    affected_id: int | None = None

    if action == "list":
        pass  # nothing to do; return current
    elif action == "add":
        if not text or not isinstance(text, str) or not text.strip():
            raise SkillError("add requires a non-empty `text`")
        new = {
            "id": next_id,
            "text": text.strip(),
            "status": STATUS_PENDING,
            "done": False,
            "created_at": _now_iso(),
            "done_at": None,
        }
        todos.append(new)
        affected_id = next_id
        next_id += 1
        _save(root_resolved, todos, next_id)
    elif action in ("done", "undone", "remove"):
        if not isinstance(todo_id, int):
            raise SkillError(f"{action} requires `todo_id` (int)")
        match = next((t for t in todos if t["id"] == todo_id), None)
        if match is None:
            raise SkillError(f"no todo with id={todo_id}")
        if action == "done":
            match["done"] = True
            match["done_at"] = _now_iso()
            match["status"] = STATUS_DONE
        elif action == "undone":
            match["done"] = False
            match["done_at"] = None
            match["status"] = STATUS_PENDING
        else:  # remove
            todos = [t for t in todos if t["id"] != todo_id]
        affected_id = todo_id
        _save(root_resolved, todos, next_id)
    elif action == "clear":
        todos = []
        _save(root_resolved, todos, next_id)
    elif action == "start":
        # Single-in-progress discipline: reachable through the tool boundary.
        if not isinstance(todo_id, int):
            raise SkillError("start requires `todo_id` (int)")
        target = next((t for t in todos if t["id"] == todo_id), None)
        if target is None:
            raise SkillError(f"no todo with id={todo_id}")
        if target.get("status") == STATUS_DONE or target.get("done"):
            raise SkillError(
                f"todo id={todo_id} is already done; cannot start a completed item"
            )
        # Demote any other in_progress to pending.
        for t in todos:
            if t["id"] != todo_id and t.get("status") == STATUS_IN_PROGRESS:
                t["status"] = STATUS_PENDING
        target["status"] = STATUS_IN_PROGRESS
        affected_id = todo_id
        _save(root_resolved, todos, next_id)
    elif action == "assert_closed":
        # Read-only closure gate: raises SkillError when open todos remain.
        open_todos = [
            t for t in todos
            if t.get("status") != STATUS_DONE and not t.get("done")
        ]
        if open_todos:
            ids = [str(t["id"]) for t in open_todos]
            texts = "; ".join(t.get("text", "?") for t in open_todos[:3])
            raise SkillError(
                f"{len(open_todos)} open todo(s) remain (ids {', '.join(ids)}): {texts}"
            )

    return {
        "ok": True,
        "action": action,
        "todos": todos,
        "affected_id": affected_id,
        "count": len(todos),
        "open_count": sum(1 for t in todos if not t["done"]),
        "in_progress_count": sum(1 for t in todos if t.get("status") == STATUS_IN_PROGRESS),
    }


# ---------------------------------------------------------------------------
# Single-in-progress discipline
# ---------------------------------------------------------------------------

def start(todo_id: int, root: "str | Path") -> dict:
    """Mark a todo as in_progress, demoting any other in_progress todo to pending.

    Raises SkillError if the todo does not exist or is already done.
    Returns the full list as in `run`.
    """
    root_resolved = Path(root).resolve()
    if not root_resolved.is_dir():
        raise SkillError(f"root is not a directory: {root_resolved}")
    todos, next_id = _load(root_resolved)
    target = next((t for t in todos if t["id"] == todo_id), None)
    if target is None:
        raise SkillError(f"no todo with id={todo_id}")
    if target.get("status") == STATUS_DONE or target.get("done"):
        raise SkillError(
            f"todo id={todo_id} is already done; cannot start a completed item"
        )
    # Demote any other in_progress to pending.
    for t in todos:
        if t["id"] != todo_id and t.get("status") == STATUS_IN_PROGRESS:
            t["status"] = STATUS_PENDING
    target["status"] = STATUS_IN_PROGRESS
    _save(root_resolved, todos, next_id)
    return {
        "ok": True,
        "action": "start",
        "todos": todos,
        "affected_id": todo_id,
        "count": len(todos),
        "open_count": sum(1 for t in todos if not t.get("done")),
        "in_progress_count": sum(1 for t in todos if t.get("status") == STATUS_IN_PROGRESS),
    }


# ---------------------------------------------------------------------------
# Closure accountability
# ---------------------------------------------------------------------------

def open_items(root: "str | Path") -> list[dict]:
    """Return all todos that are NOT done (pending or in_progress).

    The loop may call this before declaring 'done' to surface dangling items.
    Returns an empty list when everything is closed.
    """
    root_resolved = Path(root).resolve()
    todos, _ = _load(root_resolved) if root_resolved.is_dir() else ([], 1)
    return [
        t for t in todos
        if t.get("status") != STATUS_DONE and not t.get("done")
    ]


def assert_all_closed(root: "str | Path") -> None:
    """Raise SkillError when any todo is still pending or in_progress.

    The agent loop calls this before marking a run 'done' to prevent silent
    dangling-item drops. Does NOT raise on an empty store.
    """
    items = open_items(root)
    if not items:
        return
    ids = [str(t["id"]) for t in items]
    texts = "; ".join(t.get("text", "?") for t in items[:3])
    raise SkillError(
        f"{len(items)} open todo(s) remain (ids {', '.join(ids)}): {texts}"
    )
