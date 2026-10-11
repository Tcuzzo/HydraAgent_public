"""Durable finite ticks for local, idempotent maintenance. No model/app calls.

Cron enqueues by scheduled-slot key, then invokes tick. One transaction claims
one job; a lease and owner token prevent duplicate completion by a stale worker.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid


class MaintenanceQueue:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, operation TEXT, root TEXT, status TEXT, attempts INTEGER, due REAL, lease REAL, owner TEXT, result TEXT)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, operation, root, key):
        from hydra.specialists import canonical_path
        if operation not in {'index', 'memory_audit'} or not isinstance(key, str) or not 1 <= len(key) <= 256:
            raise ValueError('maintenance needs index/memory_audit and a bounded scheduled-slot key')
        root = canonical_path(Path(root))
        if not root.is_dir():
            raise ValueError('maintenance root must exist')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM jobs WHERE key=?', (key,)).fetchone()
            if existing:
                if existing['root'] != str(root) or existing['operation'] != operation:
                    raise ValueError('idempotency key already belongs to a different job')
                return dict(existing)
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 128:
                raise ValueError('maintenance queue capacity reached')
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)', (key, operation, str(root), 'queued', 0, time.time(), 0, '', '{}'))
        return {'key': key, 'status': 'queued'}

    def snapshot(self):
        with self.db() as db:
            return {'jobs': [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY due DESC LIMIT 128')], 'limit': 128}

    def tick(self):
        now = time.time()
        owner = uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # These two operations only read source and replace derived caches.
            # A crashed owner is retried finitely; app actions are never queued here.
            db.execute("UPDATE jobs SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END, due=? WHERE status='running' AND lease<?", (now + 30, now))
            row = db.execute("SELECT * FROM jobs WHERE status='queued' AND due<=? AND attempts<3 ORDER BY due,key LIMIT 1", (now,)).fetchone()
            if row is None:
                return {'status': 'idle', 'claimed': 0}
            job = dict(row)
            db.execute("UPDATE jobs SET status='running',attempts=attempts+1,lease=?,owner=? WHERE key=?", (now + 120, owner, job['key']))
        try:
            if job['operation'] == 'index':
                from hydra.source_index import SourceIndex
                result = SourceIndex(Path(job['root'])).refresh(timeout=15)
            else:
                from hydra.memory_audit import audit
                full = audit(Path(job['root']))
                result = {'status': full['status'], 'files': len(full['files']), 'bytes': full['bytes'],
                          'duplicate_groups': len(full['exact_duplicate_groups']), 'age_candidates': len(full['age_candidates'])}
            status = 'completed'
        except Exception as exc:
            # Errors are literal and bounded. No payload or source content is logged.
            result = {'status': 'failed', 'error_type': type(exc).__name__}
            status = 'failed' if job['attempts'] + 1 >= 3 else 'queued'
        with self.db() as db:
            updated = db.execute('UPDATE jobs SET status=?,result=?,due=?,lease=0 WHERE key=? AND owner=? AND status=?',
                                 (status, json.dumps(result), time.time() + 30 * 2 ** job['attempts'], job['key'], owner, 'running')).rowcount
        return {'status': status if updated else 'lease_lost', 'claimed': 1, 'key': job['key'], 'result': result,
                'attempt': job['attempts'] + 1, 'max_attempts': 3}
