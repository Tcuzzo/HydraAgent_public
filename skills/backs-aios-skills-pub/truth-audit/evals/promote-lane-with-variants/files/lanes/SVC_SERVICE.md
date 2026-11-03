# Lane SVC · sync worker · 2026-01-15

Auditor: lane SVC. Read-only on the product.
Isolation: logs and heartbeat read in place (read-only); no requests sent.

### SVC-01 · P0 · Worker retries forever on a rejected credential
- status: OPEN · confidence: high
- surface: background worker behind the main window
- evidence (read-only, service log):
  - `service.log`: 602 of 602 loop errors today are HTTP 401
  - heartbeat state is "error"
- root cause (theater): the loop swallows every exception
- root cause (product): the credential was rotated
- fix seam: `runtime/sync_worker.py` run_forever, through a new `runtime/loop_guard.py`
- proof of done: an injected 401 in a sandbox stops the loop and shows one alert

### SVC-02 | P2 | Health page says green while the worker is down
* **Surface and evidence:** `GET /health` returns ok=true while heartbeat says error
* **Fix-seam**: `api/health.py` health()
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

## Not checked
the cloud API's own logs (no access)
