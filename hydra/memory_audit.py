"""Bounded memory inventory and recoverable snapshots; never deletes originals."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import time

from hydra.specialists import canonical_path, reject_links
from hydra.atomic_write import atomic_write_bytes


def database_inventory(path, *, timeout=5):
    """Read known memory metadata without loading extensions or exposing bodies."""
    reject_links(path)
    deadline = time.monotonic() + timeout
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=min(1, timeout))
    try:
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        columns = {row[1] for row in db.execute('PRAGMA table_info(entries)')}
        if not columns:
            return {'status': 'unknown_schema', 'bytes': path.stat().st_size}
        count = db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
        result = {'status': 'inspected', 'entries': count, 'bytes': path.stat().st_size,
                  'content_inspected': False, 'archived': False}
        if 'superseded_by' in columns:
            result['superseded'] = db.execute('SELECT COUNT(*) FROM entries WHERE superseded_by IS NOT NULL').fetchone()[0]
        if {'scope', 'kind', 'body'} <= columns:
            result['exact_duplicate_groups'] = db.execute('SELECT COUNT(*) FROM (SELECT 1 FROM entries GROUP BY scope,kind,body HAVING COUNT(*)>1)').fetchone()[0]
        return result
    except sqlite3.Error as exc:
        return {'status': 'unavailable', 'error_type': type(exc).__name__, 'content_inspected': False}
    finally:
        db.close()


def audit(root: Path, *, stale_days=30, archive: Path | None = None):
    root = canonical_path(root)
    if not root.is_dir() or type(stale_days) is not int or not 1 <= stale_days <= 36500:
        raise ValueError('memory root must exist and stale_days must be 1..36500')
    if archive is not None:
        archive = canonical_path(archive)
        if archive.is_relative_to(root) or root.is_relative_to(archive):
            raise ValueError('archive must be separate from the active memory tree')
        if archive.exists():
            raise ValueError('archive destination must be new')
    started = time.monotonic()
    cutoff = time.time() - stale_days * 86400
    groups = defaultdict(list)
    records = []
    databases = []
    total = 0
    scanned = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        if time.monotonic() - started > 30:
            raise ValueError('memory audit time bound reached; select a smaller root')
        dirs[:] = [d for d in sorted(dirs) if not d.startswith('.') and d not in {'credentials', 'secrets', 'node_modules', '__pycache__'}
                   and not (Path(folder) / d).is_symlink() and not getattr(Path(folder) / d, 'is_junction', lambda: False)()]
        for name in sorted(files):
            scanned += 1
            remaining = 30 - (time.monotonic() - started)
            if scanned > 10000 or total > 128 * 1024 * 1024 or remaining <= 0:
                raise ValueError('memory audit bound reached; select a smaller root')
            path = Path(folder) / name
            if path.suffix.lower() in {'.sqlite', '.sqlite3', '.db'} and not path.is_symlink():
                databases.append({'path': path.relative_to(root).as_posix(), **database_inventory(path, timeout=min(5, remaining))})
                continue
            if name.startswith('.') or path.suffix.lower() not in {'.md', '.txt', '.json', '.jsonl', '.log', '.yaml', '.yml'}:
                continue
            if any(term in name.casefold() for term in ('secret', 'credential', 'token', 'private_key')):
                continue
            if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
                continue
            if len(records) >= 10000 or total > 128 * 1024 * 1024 or time.monotonic() - started > 30:
                raise ValueError('memory audit bound reached; select a smaller root')
            reject_links(path)
            before = path.stat()
            with path.open('rb') as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024 or path.stat().st_mtime_ns != before.st_mtime_ns:
                raise ValueError('memory changed during audit; retry when its writer is idle')
            digest = hashlib.sha256(raw).hexdigest()
            relative = path.relative_to(root).as_posix()
            groups[digest].append(relative)
            record = {'path': relative, 'sha256': digest, 'bytes': len(raw), 'mtime_ns': before.st_mtime_ns,
                      'age_candidate': before.st_mtime < cutoff}
            records.append(record)
            total += len(raw)
    result = {'schema': 'hydra.memory-audit.v1', 'status': 'completed', 'root': str(root), 'files': records, 'databases': databases,
              'bytes': total, 'exact_duplicate_groups': [paths for paths in groups.values() if len(paths) > 1],
              'age_candidates': [r['path'] for r in records if r['age_candidate']],
              'semantic_contradictions': 'not_inferred_from_similarity',
              'note': 'Age is not proof of obsolescence. Canon/invariants need owner and reference checks before supersession.',
              'originals_changed': False, 'archive_scope': 'text files only; live databases require an engine-consistent backup'}
    if archive is not None:
        archive.mkdir(parents=True)
        (archive / 'objects').mkdir()
        for record in records:
            path = root / record['path']
            reject_links(path)
            with path.open('rb') as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
            if hashlib.sha256(raw).hexdigest() != record['sha256']:
                raise ValueError('memory changed during snapshot; incomplete archive must not be used as a complete backup')
            target = archive / 'objects' / record['sha256']
            if not target.exists():
                atomic_write_bytes(target, raw, overwrite=False)
        atomic_write_bytes(archive / 'manifest.json', json.dumps(result, indent=2).encode(), overwrite=False)
        result['archive'] = str(archive)
    return result
