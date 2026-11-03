#!/usr/bin/env python3
"""Deterministic context policy and immutable, verified checkpoint support.

This utility never launches models or transfers execution authority. A host must
quiesce writers, account for dispatch costs, and adopt a verified ready packet.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

MAX_BYTES = 48000
TERMINAL_WORKERS = {"completed", "failed", "cancelled"}
TERMINAL_OPERATIONS = {"drained", "reconciled"}


class GuardError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise GuardError(message)


def count(value, name):
    require(type(value) is int and value >= 0, "invalid " + name)
    return value


def number(value, name):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            "invalid " + name)
    return value


def string(value, name):
    require(isinstance(value, str) and bool(value.strip()), "invalid " + name)
    return value


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def normalize_usage(telemetry):
    require(isinstance(telemetry, dict), "telemetry unavailable")
    provider = string(telemetry.get("provider"), "telemetry.provider")
    observed = number(telemetry.get("observed_at"), "telemetry.observed_at")
    if provider == "claude-statusline":
        window = telemetry.get("context_window")
        require(isinstance(window, dict), "Claude context window unavailable")
        usage = window.get("current_usage")
        require(isinstance(usage, dict), "Claude current usage unavailable")
        current = sum(count(usage.get(k), k) for k in
                      ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        output = usage.get("output_tokens")
        quality = "estimated" if output is None else "known"
        if output is not None:
            current += count(output, "output_tokens")
    else:
        # In particular, never substitute Codex cumulative total_token_usage.
        current = count(telemetry.get("active_context_tokens"), "active_context_tokens")
        quality = telemetry.get("quality")
        require(quality in ("known", "estimated"), "usage quality must be known or estimated")
    return current, observed, quality, provider


def decide(request):
    try:
        require(isinstance(request, dict), "request must be an object")
        policy = request.get("policy", {})
        require(isinstance(policy, dict), "policy must be an object")
        limit = count(policy.get("rollover_tokens", 125000), "rollover_tokens")
        warm = count(policy.get("warm_checkpoint_tokens", 110000), "warm_checkpoint_tokens")
        reserve = count(policy.get("reserved_tokens", 16000), "reserved_tokens")
        require(0 < warm < limit and reserve > 0, "invalid thresholds or reserve")
        effective = limit
        if "context_window_tokens" in policy:
            require(policy.get("context_window_verified") is True, "model context window unverified")
            effective = min(limit, count(policy["context_window_tokens"], "context_window_tokens") - reserve)
        require(effective > 0, "reserved headroom exhausts model context")
        warm = min(warm, max(1, effective * warm // limit))
        now = number(request.get("now", time.time()), "now")
        current, observed, quality, provider = normalize_usage(request.get("telemetry"))
        freshness = number(policy.get("telemetry_max_age_seconds", 60), "telemetry_max_age_seconds")
        require(-5 <= now - observed <= freshness, "telemetry stale or future-dated")
        projected = current + count(request.get("projected_additional_tokens", 0),
                                    "projected_additional_tokens")
        age_limit = policy.get("max_session_age_seconds", 14400)
        if age_limit is not None:
            number(age_limit, "max_session_age_seconds")
        age = request.get("session_age_seconds")
        if age is not None:
            number(age, "session_age_seconds")
        reason = "below_threshold"
        action = "continue"
        if current >= effective:
            action, reason = "handoff_required", "active_context_limit"
        elif projected >= effective:
            action, reason = "handoff_required", "projected_context_limit"
        elif age is not None and age_limit is not None and age >= age_limit:
            action, reason = "handoff_required", "session_age_limit"
        elif projected >= warm:
            action, reason = "checkpoint_requested", "warm_checkpoint_limit"
        return {"ok": True, "action": action, "reason": reason,
                "allow_dispatch": action != "handoff_required", "provider": provider,
                "usage_quality": quality, "active_context_tokens": current,
                "projected_context_tokens": projected, "rollover_tokens": effective,
                "warm_checkpoint_tokens": warm, "reserved_tokens": reserve}
    except (GuardError, TypeError, OverflowError) as error:
        return {"ok": False, "action": "refresh_required", "allow_dispatch": False,
                "reason": str(error)}


def root_path(value):
    path = Path(string(str(value), "root path"))
    require(path.is_absolute() and path.is_dir(), "root must be an existing absolute directory")
    return path.resolve()


def safe_path(root, value, must_exist=True):
    value = string(value, "relative path")
    parts = value.split("/")
    require(not Path(value).is_absolute() and "\\" not in value and
            all(p not in ("", ".", "..") for p in parts), "unsafe relative path")
    require(not any(p.lower() == "vault" or p.lower() == ".env" or
                    p.lower().startswith(".env.") for p in parts), "secret path refused")
    path = root.joinpath(*parts)
    resolved = path.resolve(strict=must_exist)
    require(resolved.is_relative_to(root), "path escapes declared root")
    return path


def hash_path(path):
    require(path.is_file(), "referenced path is not a regular file")
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def check_ref(root, ref):
    require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}, "invalid reference")
    require(isinstance(ref["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]),
            "invalid reference SHA256")
    require(hash_path(safe_path(root, ref["path"])) == ref["sha256"], "reference hash mismatch")


def git(root, *arguments, allow_detached=False):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *arguments], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    require(result.returncode == 0 or (allow_detached and result.returncode == 1),
            "read-only Git verification failed")
    return result.stdout


def repo_snapshot(root):
    require(Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel").strip())).resolve() == root,
            "repo root does not identify the worktree root")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD",
                 allow_detached=True).decode().strip() or None
    records = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0")
    paths, index = set(), 0
    while index < len(records) and records[index]:
        record = records[index]
        require(len(record) > 3, "invalid Git status record")
        status = record[:2]
        paths.add(record[3:].decode("utf-8"))
        index += 1
        if b"R" in status or b"C" in status:
            require(index < len(records) and records[index], "incomplete Git rename record")
            if b"R" in status:
                paths.add(records[index].decode("utf-8"))
            index += 1
    metadata = []
    for value in sorted(paths):
        path = safe_path(root, value, must_exist=False)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            mode = None
        metadata.append({"path": value, "state": "deleted" if mode is None else "present",
                         "kind": None if mode is None else stat.S_IFMT(mode),
                         "mode": None if mode is None else stat.S_IMODE(mode)})
    return {"branch": branch, "head": head, "dirty_paths": paths,
            "index_fingerprint": digest(git(root, "ls-files", "--stage", "-z")),
            "worktree_metadata_fingerprint": digest(canonical(metadata))}


def check_budgets(budgets):
    require(isinstance(budgets, dict), "budgets must be an object")
    require({"attempts_used", "attempts_limit"} <= set(budgets) <=
            {"attempts_used", "attempts_limit", "task_tokens_used", "task_token_limit",
             "task_cost_used", "task_cost_limit"}, "invalid budget fields")
    for key, value in budgets.items():
        (number if "cost" in key else count)(value, key)
    require(budgets["attempts_limit"] > 0, "attempts_limit must be positive")
    for used, limit in (("task_tokens_used", "task_token_limit"), ("task_cost_used", "task_cost_limit")):
        require(limit not in budgets or used in budgets, "budget limit lacks usage")
    return any(budgets[used] >= budgets[limit] for used, limit in
               (("attempts_used", "attempts_limit"), ("task_tokens_used", "task_token_limit"),
                ("task_cost_used", "task_cost_limit")) if limit in budgets)


def validate_packet(packet, task_dir, repo_root=None, max_bytes=MAX_BYTES):
    fields = {"schema_version", "task_id", "generation", "source_session_id", "phase", "repo",
              "intent", "corrections", "constraints", "permissions", "current_step", "next_action",
              "pending_work", "completed_work", "failed_attempts", "evidence_refs", "worker_receipts",
              "inflight_operations", "budgets"}
    require(isinstance(packet, dict) and set(packet) == fields, "invalid packet fields")
    require(len(canonical(packet)) <= max_bytes, "packet exceeds UTF-8 byte bound")
    require(type(packet["schema_version"]) is int and packet["schema_version"] == 1, "unsupported schema")
    require(count(packet["generation"], "generation") >= 1, "generation starts at one")
    require(packet["phase"] in ("checkpoint", "ready"), "invalid phase")
    for key in ("task_id", "source_session_id", "current_step", "next_action"):
        string(packet[key], key)
    task = root_path(task_dir)
    for key in ("intent", "corrections", "constraints", "permissions"):
        check_ref(task, packet[key])
    for key in ("pending_work", "completed_work"):
        require(isinstance(packet[key], list), "work must be a list")
        for item in packet[key]:
            string(item, key)
    require(isinstance(packet["evidence_refs"], list), "evidence_refs must be a list")
    references = list(packet["evidence_refs"])
    for key, identity, terminal in (("failed_attempts", "summary", None),
                                    ("worker_receipts", "worker_id", TERMINAL_WORKERS),
                                    ("inflight_operations", "operation_id", TERMINAL_OPERATIONS)):
        require(isinstance(packet[key], list), "receipts must be lists")
        identities = set()
        for receipt in packet[key]:
            expected = {identity, "evidence_refs"} | ({"status"} if terminal else set())
            require(isinstance(receipt, dict) and set(receipt) == expected, "invalid receipt")
            name = string(receipt[identity], identity)
            require(name not in identities, "duplicate receipt identity")
            identities.add(name)
            require(isinstance(receipt["evidence_refs"], list) and receipt["evidence_refs"],
                    "receipt requires evidence")
            references.extend(receipt["evidence_refs"])
            if terminal:
                require(receipt["status"] in terminal | {"running", "pending"}, "invalid receipt status")
                require(packet["phase"] != "ready" or receipt["status"] in terminal,
                        "in-flight work prevents ready handoff")
    for ref in references:
        check_ref(task, ref)
    repo = packet["repo"]
    require(isinstance(repo, dict) and set(repo) ==
            {"root", "branch", "head", "index_fingerprint", "worktree_metadata_fingerprint", "dirty_manifest"},
            "invalid repo metadata")
    root = root_path(repo["root"])
    require(repo_root is None or root == root_path(repo_root), "unexpected repository root")
    before = repo_snapshot(root)
    require(repo["branch"] == before["branch"] and repo["head"] == before["head"], "stale repository HEAD or branch")
    require(repo["index_fingerprint"] == before["index_fingerprint"], "stale repository index")
    require(isinstance(repo["dirty_manifest"], list), "dirty_manifest must be a list")
    paths = set()
    for entry in repo["dirty_manifest"]:
        require(isinstance(entry, dict) and {"path", "sha256"} <= set(entry) <= {"path", "sha256", "state"},
                "invalid dirty entry")
        require(entry["path"] not in paths, "duplicate dirty path")
        paths.add(entry["path"])
        path = safe_path(root, entry["path"], must_exist=False)
        if entry.get("state", "present") == "deleted":
            require(entry["sha256"] is None and not os.path.lexists(path), "deleted path reappeared")
        else:
            require(entry.get("state", "present") == "present", "invalid dirty state")
            require(not path.is_symlink(), "dirty symlink requires a host-specific snapshot")
            check_ref(root, {"path": entry["path"], "sha256": entry["sha256"]})
    require(paths == before["dirty_paths"], "dirty inventory incomplete or stale")
    require(repo["worktree_metadata_fingerprint"] == before["worktree_metadata_fingerprint"],
            "stale worktree metadata")
    require(before == repo_snapshot(root), "repository changed during validation")
    exhausted = check_budgets(packet["budgets"])
    return {"phase": packet["phase"], "allow_resume": packet["phase"] == "ready" and not exhausted,
            "budgets_exhausted": exhausted}


def checkpoint_path(store, generation):
    return Path(store) / ("generation-%06d" % count(generation, "generation"))


def load_checkpoint(store, generation, max_bytes=MAX_BYTES):
    folder = checkpoint_path(store, generation)
    require(folder.is_dir() and not folder.is_symlink(), "checkpoint unavailable")
    for name in ("packet.json", "manifest.json", "COMPLETE"):
        require((folder / name).is_file() and not (folder / name).is_symlink(), "incomplete checkpoint")
    require((folder / "packet.json").stat().st_size <= max_bytes, "packet exceeds UTF-8 byte bound")
    data = (folder / "packet.json").read_bytes()
    checksum = digest(data)
    manifest = json.loads((folder / "manifest.json").read_bytes())
    packet = json.loads(data)
    require(manifest == {"schema_version": 1, "task_id": packet["task_id"], "generation": generation,
                         "sha256": checksum, "bytes": len(data)} and packet["generation"] == generation and
            (folder / "COMPLETE").read_text() == checksum + "\n" and canonical(packet) == data,
            "checkpoint integrity mismatch")
    return packet, checksum


def store_path(task_dir, store, create=False):
    task = root_path(task_dir)
    path = Path(store)
    require(path.is_absolute(), "store must be absolute")
    require(path.resolve().is_relative_to(task), "store escapes task directory")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    require(path.is_dir(), "checkpoint store unavailable")
    return path.resolve()


def completed_generations(store):
    return [int(folder.name.split("-")[1]) for folder in Path(store).glob("generation-*")
            if re.fullmatch(r"generation-\d{6,}", folder.name) and (folder / "COMPLETE").is_file()]


def verify_checkpoint(task_dir, store, generation, repo_root=None, max_bytes=MAX_BYTES):
    store = store_path(task_dir, store)
    packet, checksum = load_checkpoint(store, generation, max_bytes)
    result = validate_packet(packet, task_dir, repo_root, max_bytes)
    latest = max(completed_generations(store), default=0)
    result["allow_resume"] = result["allow_resume"] and generation == latest
    return {"ok": True, "task_id": packet["task_id"], "generation": generation,
            "latest_generation": latest, "checkpoint_digest": checksum,
            "packet_bytes": len(canonical(packet)), **result}


def sync_dir(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def write_checkpoint(packet, task_dir, store, repo_root=None, max_bytes=MAX_BYTES):
    validate_packet(packet, task_dir, repo_root, max_bytes)
    store = store_path(task_dir, store, create=True)
    data = canonical(packet)
    generation = packet["generation"]
    destination = checkpoint_path(store, generation)
    if destination.exists():
        old, _ = load_checkpoint(store, generation, max_bytes)
        require(canonical(old) == data, "generation conflict; complete checkpoints are immutable")
        return {**verify_checkpoint(task_dir, store, generation, repo_root, max_bytes), "idempotent": True}
    complete = completed_generations(store)
    require(generation == max(complete, default=0) + 1, "generation must follow latest complete checkpoint")
    if complete:
        previous, _ = load_checkpoint(store, max(complete), max_bytes)
        require(packet["task_id"] == previous["task_id"] and packet["intent"] == previous["intent"],
                "task identity and original intent are immutable")
        for key, value in previous["budgets"].items():
            require(key in packet["budgets"] and
                    (packet["budgets"][key] == value if key.endswith("limit") else packet["budgets"][key] >= value),
                    "rollover cannot reset or expand task budgets")
    checksum = digest(data)
    manifest = {"schema_version": 1, "task_id": packet["task_id"], "generation": generation,
                "sha256": checksum, "bytes": len(data)}
    staging = Path(tempfile.mkdtemp(prefix=".stage-", dir=store))
    try:
        for name, content in (("packet.json", data), ("manifest.json", canonical(manifest)),
                              ("COMPLETE", (checksum + "\n").encode())):
            with (staging / name).open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        sync_dir(staging)
        try:
            os.replace(staging, destination)
        except OSError:
            require(destination.is_dir(), "atomic checkpoint publication failed")
            old, _ = load_checkpoint(store, generation, max_bytes)
            require(canonical(old) == data, "generation conflict")
        sync_dir(store)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {**verify_checkpoint(task_dir, store, generation, repo_root, max_bytes), "idempotent": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("decide", "checkpoint", "verify"))
    parser.add_argument("--task-dir")
    parser.add_argument("--store")
    parser.add_argument("--repo-root")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    args = parser.parse_args()
    try:
        require(args.max_bytes > 0, "max-bytes must be positive")
        if args.command == "decide":
            result = decide(json.load(sys.stdin))
        else:
            require(args.task_dir and args.store, "task-dir and store are required")
            if args.command == "checkpoint":
                raw = sys.stdin.buffer.read(args.max_bytes + 1)
                require(len(raw) <= args.max_bytes, "input exceeds UTF-8 byte bound")
                result = write_checkpoint(json.loads(raw), args.task_dir, args.store,
                                          args.repo_root, args.max_bytes)
            else:
                require(args.generation is not None, "generation is required")
                result = verify_checkpoint(args.task_dir, args.store, args.generation,
                                           args.repo_root, args.max_bytes)
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result["ok"] else 2
    except (GuardError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "allow_dispatch": False, "allow_resume": False, "error": str(error)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
