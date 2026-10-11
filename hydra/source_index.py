"""Shared, incremental source lookup. Keys are verified against current bytes.

SQLite serializes writers on one machine/local disk. A metadata walk detects
ordinary edits; content keys are always rehashed on read. This is a locator,
not authority over source, an instruction store, or a substitute for tests.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from hydra.repo_map import SKIP_DIRS, _extract_python, _extract_nonpython
from hydra.specialists import canonical_path, reject_links

SUFFIXES = {'.py', '.js', '.jsx', '.ts', '.tsx', '.rs', '.go', '.java', '.c', '.h',
            '.cpp', '.cs', '.rb', '.sh', '.ps1', '.md', '.yaml', '.yml', '.toml', '.json', '.sql'}
PRIVATE = {'credentials', 'secrets', '.aws', '.ssh', '.hydra', '.hydraAgent', 'state', 'logs'}
MAX_FILE_BYTES = 1024 * 1024


class SourceIndex:
    def __init__(self, root: Path, *, cache: Path | None = None):
        self.root = canonical_path(root)
        if not self.root.is_dir():
            raise ValueError('source root must be an existing directory')
        identity = hashlib.sha256(str(self.root).encode()).hexdigest()
        self.cache = cache or Path.home() / '.hydraAgent' / 'indexes' / (identity + '.sqlite3')
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            old = db.execute("SELECT value FROM meta WHERE key='root'").fetchone()
            if old and old[0] != str(self.root):
                raise ValueError('index belongs to a different source root')
            db.execute("INSERT OR IGNORE INTO meta VALUES ('root', ?)", (str(self.root),))
            db.execute('CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, stamp TEXT, digest TEXT, symbols TEXT, refs TEXT)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.cache, timeout=2)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            with db:
                yield db
        finally:
            db.close()

    def _paths(self, maximum, deadline):
        paths = []
        for folder, dirs, files in os.walk(self.root, followlinks=False):
            if time.monotonic() > deadline:
                raise ValueError('source index scan time limit exceeded; previous snapshot retained')
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS | PRIVATE and not d.startswith('.')
                             and not (Path(folder) / d).is_symlink()
                             and not getattr(Path(folder) / d, 'is_junction', lambda: False)())
            for name in sorted(files):
                if time.monotonic() > deadline:
                    raise ValueError('source index scan time limit exceeded; previous snapshot retained')
                path = Path(folder) / name
                if name.startswith('.') or path.suffix.lower() not in SUFFIXES or path == self.cache:
                    continue
                if any(s in name.lower() for s in ('credential', 'secret', 'private_key')):
                    continue
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                    continue
                paths.append(path)
                if len(paths) > maximum:
                    raise ValueError('source index file limit exceeded; previous snapshot retained')
        return paths

    @staticmethod
    def _stamp(path):
        stat = path.stat()
        return f'{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}:{stat.st_ino}'

    def refresh(self, *, max_files=20000, timeout=15.0, verify=False):
        if type(max_files) is not int or not 1 <= max_files <= 100000 or not 0 < timeout <= 120:
            raise ValueError('invalid source index bounds')
        started = time.monotonic()
        deadline = started + timeout
        paths = self._paths(max_files, deadline)
        read_files = 0
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = {row[0]: row[1] for row in db.execute('SELECT path, stamp FROM files')}
            seen = set()
            for path in paths:
                if time.monotonic() > deadline:
                    raise ValueError('source index scan time limit exceeded; previous snapshot retained')
                relative = path.relative_to(self.root).as_posix()
                seen.add(relative)
                stamp = self._stamp(path)
                if not verify and existing.get(relative) == stamp:
                    continue
                reject_links(path)
                with path.open('rb') as stream:
                    raw = stream.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES or b'\0' in raw or self._stamp(path) != stamp:
                    raise ValueError('source changed during indexing; retry the bounded refresh')
                source = raw.decode('utf-8', errors='replace')
                defs, refs = _extract_python(source, relative)[:2] if path.suffix == '.py' else _extract_nonpython(source)
                digest = hashlib.sha256(raw).hexdigest()
                db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)',
                           (relative, stamp, digest, json.dumps(defs[:4000]), json.dumps(refs[:4000])))
                read_files += 1
            removed = set(existing) - seen
            db.executemany('DELETE FROM files WHERE path=?', [(p,) for p in removed])
            db.execute("INSERT OR REPLACE INTO meta VALUES ('refreshed_at', ?)", (str(time.time()),))
        return {'status': 'ready', 'files': len(seen), 'read_files': read_files,
                'removed_files': len(removed), 'elapsed_seconds': round(time.monotonic() - started, 6),
                'freshness': 'content_verified' if verify else 'metadata_checked', 'cache': str(self.cache)}

    def search(self, query: str, *, limit=8, refresh=True):
        if not isinstance(query, str) or not query.strip() or len(query) > 2000 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('query must be nonempty, <=2000 characters; limit must be 1..50')
        receipt = self.refresh() if refresh else {'freshness': 'cached_unverified'}
        terms = set(re.findall(r'[\w]+', query.casefold()))
        hits = []
        with self._db() as db:
            for path, digest, definitions, refs in db.execute('SELECT path, digest, symbols, refs FROM files'):
                symbols = json.loads(definitions)
                score = sum(6 * (t in [s.casefold() for s in symbols]) + 3 * (t in path.casefold())
                            + (t in refs.casefold()) for t in terms)
                if score:
                    hits.append({'path': path, 'key': digest + ':' + path, 'symbols': symbols[:40], 'score': score})
        hits.sort(key=lambda item: (-item['score'], item['path']))
        return {'status': 'ready', 'matches': hits[:limit], 'index': receipt,
                'next_step': 'Use source_read(key) to verify current bytes; then scope grep/read to these paths.'}

    def read(self, key: str, *, max_bytes=32768):
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_FILE_BYTES:
            raise ValueError('max_bytes must be 1..1048576')
        if not isinstance(key, str) or len(key) > 4096:
            raise ValueError('invalid source key')
        digest, separator, relative = key.partition(':')
        if not separator or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('invalid source key')
        with self._db() as db:
            row = db.execute('SELECT digest FROM files WHERE path=?', (relative,)).fetchone()
        if not row or row[0] != digest:
            raise ValueError('stale or unknown source key; refresh source lookup')
        path = self.root / relative
        reject_links(path)
        if not canonical_path(path).is_relative_to(self.root) or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('invalid source path')
        with path.open('rb') as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('stale source key; refresh source lookup')
        return {'status': 'ready', 'path': relative, 'key': key, 'content': raw[:max_bytes].decode('utf-8', errors='replace'),
                'bytes_read': min(len(raw), max_bytes), 'truncated': len(raw) > max_bytes, 'freshness': 'content_verified'}
