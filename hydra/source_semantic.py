"""Opt-in local embeddings over source-map metadata, never full source bodies."""
import json
import math
import time
from urllib.parse import urlsplit

from hydra.embeddings import config, identity
from hydra.local_model_worker import predict_local


def _configuration():
    cfg = config()
    if not cfg:
        raise ValueError('set HYDRA_EMBEDDING_CONFIG before embedding the source map')
    if cfg['backend'] != 'openvino' and urlsplit(cfg.get('endpoint', '')).hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('source-map embeddings require a local endpoint or OpenVINO')
    return cfg


def _schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS source_vectors (path TEXT, identity TEXT, digest TEXT, vector TEXT, PRIMARY KEY(path,identity))')


def _vector(values):
    if not values or len(values) > 8192 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in values):
        raise ValueError('source embedding must have 1..8192 finite dimensions')
    norm = math.sqrt(sum(x*x for x in values))
    if not norm or not math.isfinite(norm):
        raise ValueError('source embedding must have finite nonzero norm')
    return [x/norm for x in values]


def fill(index, *, batch=16):
    if type(batch) is not int or not 1 <= batch <= 64:
        raise ValueError('embedding batch must be 1..64')
    cfg = _configuration()
    model_id = identity(cfg)
    index.refresh()
    with index._db() as db:
        _schema(db)
        # A model switch intentionally starts a separate cache. Stale vectors
        # can never participate in retrieval because the digest must match.
        rows = db.execute('SELECT f.path,f.digest,f.symbols,f.refs FROM files f LEFT JOIN source_vectors v ON f.path=v.path AND v.identity=? AND f.digest=v.digest WHERE v.path IS NULL ORDER BY f.path LIMIT ?', (model_id, batch)).fetchall()
        db.execute('DELETE FROM source_vectors WHERE path NOT IN (SELECT path FROM files) OR identity != ?', (model_id,))
    written = 0
    texts = ['search_document: ' + (path + '\n' + symbols + '\n' + refs)[:16000] for path, digest, symbols, refs in rows]
    vectors = predict_local({'backend': 'embedding', 'config': cfg, 'texts': texts}, timeout=30)['vectors'] if texts else []
    if len(vectors) != len(rows):
        raise ValueError('embedding worker returned a mismatched batch')
    for (path, digest, symbols, refs), values in zip(rows, vectors):
        vector = _vector(values)
        with index._db() as db:
            current = db.execute('SELECT digest FROM files WHERE path=?', (path,)).fetchone()
            if current and current[0] == digest:
                db.execute('INSERT OR REPLACE INTO source_vectors VALUES (?,?,?,?)', (path, model_id, digest, json.dumps(vector)))
                written += 1
    with index._db() as db:
        pending = db.execute('SELECT COUNT(*) FROM files f LEFT JOIN source_vectors v ON f.path=v.path AND v.identity=? AND f.digest=v.digest WHERE v.path IS NULL', (model_id,)).fetchone()[0]
    return {'status': 'ready' if pending == 0 else 'partial', 'embedded': written, 'pending': pending,
            'embedding_identity': model_id, 'content': 'paths, definitions and references only',
            'next_step': 'Run another finite index embed tick if pending is nonzero.'}


def search(index, query, *, limit=8):
    if not isinstance(query, str) or not query.strip() or len(query) > 2000 or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('invalid semantic source query or limit')
    cfg = _configuration()
    model_id = identity(cfg)
    receipt = index.refresh()
    target = _vector(predict_local({'backend': 'embedding', 'config': cfg, 'texts': ['search_query: ' + query]}, timeout=30)['vectors'][0])
    deadline = time.monotonic() + 5
    hits, scanned = [], 0
    with index._db() as db:
        _schema(db)
        for path, digest, raw in db.execute('SELECT f.path,f.digest,v.vector FROM files f JOIN source_vectors v ON f.path=v.path AND f.digest=v.digest WHERE v.identity=? ORDER BY f.path LIMIT 20001', (model_id,)):
            if time.monotonic() >= deadline or scanned >= 20000:
                return {'status': 'degraded', 'matches': [], 'reason': 'semantic search bound exceeded; use lexical lookup'}
            vector = json.loads(raw)
            if len(vector) != len(target):
                raise ValueError('embedding dimension changed without a new model revision')
            score = sum(x*y for x,y in zip(vector, target))
            hits.append({'path': path, 'key': digest + ':' + path, 'score': round(score, 6)})
            scanned += 1
    hits.sort(key=lambda hit: (-hit['score'], hit['path']))
    return {'status': 'ready' if hits else 'not_indexed', 'matches': hits[:limit], 'index': receipt,
            'embedding_identity': model_id, 'searched': scanned,
            'next_step': 'Verify selected content with source_read(key); embeddings are locators, not evidence.'}
