---
name: cam-iot-inventory
description: "Use when inventorying cameras/IoT/Wi-Fi on the live BACKS Secure console — MAC-sticky camera identity, honest wifi-scan, LO5 inventory≠picture. Compose with secure-delivery + live-research. Trigger words: cam inventory, IoT inventory, camera MAC sticky, wifi-scan iface, RTSP unreachable, DHCP churn camera id."
license: MIT
---

# Cam / IoT Inventory (RED-first prototype)

**Effort:** medium — pairs agents to *live* sticky-MAC serving code; forbids inventing MAC/SSID/wlan0. Removes: inventory-ready theater, fake camera MAC, wifi green without iface/router host.

**Compose:** load `secure-delivery` + `live-research` with this skill. Absorb foreign homelab-network behaviors *into* BACKS only — do **not** install foreign skill packs.

**Status:** worktree prototype only. Do **not** `install.sh` into live agent homes. Do **not** `wolf_pack_land`.

## Paired serving code (prove before green claims)

Live deploy tree: `<BACKS_DEPLOY_ROOT>`

| Symbol | Path | Role |
|--------|------|------|
| `apply_host_remap_for_mac` | `backend/services/camera_streams.py` | Same MAC after host/IP remap → update host, keep camera id |
| `resolve_camera_sticky` | `backend/services/camera_streams.py` | Resolve inventory row by normalized MAC (sticky id) |
| `_normalize_mac` | `backend/services/camera_streams.py` | Canonical MAC form for sticky key |
| cameras API | `GET /api/security/cameras` | Inventory rows: `id`, `host`, `mac`/`mac_address`, snapshot honesty fields |
| snapshot | `GET /api/security/cameras/{id}/snapshot` | Picture path = **serving tree**, never `[candidate-tree-root]/...` |
| wifi-scan | `POST /api/security/wifi-scan` | Via `BACKS_AI_WIFI_SCAN_IFACE` / router host only — honest error if unset |
| FE chrome | `frontend/components/security/camera-grid.tsx` | **AMBER** — Secure eye 2026-09-19: API missing-mac is honest; FE does **not** yet emit `data-camera-mac` / `data-camera-mac-missing` (skill doc was ahead of chrome) |

**Tip pairing (LOCKED — Security refine 2026-09-19):**
- Proven / Secure #3 tip: `<proven-tip-sha>` (full tip SHA when resolved on deploy: `<proven-tip-sha>`)
- Deploy HEAD sticky helpers: `<deploy-head-sha>` (prove on live `HEAD` / `refs/backs/proven/production`)
- Prove with:
  - `git -C <BACKS_DEPLOY_ROOT> merge-base --is-ancestor <proven-tip-sha> HEAD`
  - `git -C ... rev-parse --short=12 HEAD` starts with `<deploy-head-sha>` **or** is descendant that still contains sticky helpers
  - `rg -n 'apply_host_remap_for_mac|resolve_camera_sticky|_normalize_mac' backend/services/camera_streams.py`

Stream attach / Sentry wrap is a **separate** seam — out of scope for this skill's red contract.

## Fixture table (API MAC must match red contract — no invent)

| id | MAC | host (LAN) |
|----|-----|------------|
| camera-1 | `<camera-mac-1>` | `<camera-lan-host-1>` |
| camera-2 | `<camera-mac-2>` | `<camera-lan-host-2>` |
| camera-3 | `<camera-mac-3>` | `<camera-lan-host-3>` |

Sticky law: after host remap, **same MAC → same camera id** via `apply_host_remap_for_mac` / `resolve_camera_sticky`.

## Laws (LO5 + inventory honesty)

1. **LO5:** inventory ready ≠ picture. `snapshot_available` / `snapshot_status` win over "camera listed".
2. **Never invent** MAC, SSID, or `wlan0` success on the camera host.
3. **Wi-Fi only** through `BACKS_AI_WIFI_SCAN_IFACE` / router-host path — missing iface → loud honest error (AMBER/RED), not fake AP list.
4. **Snapshot path** from serving-tree deploy, not `[candidate-tree-root]/...`.
5. **API mac fixtures** above are the contract — mismatch vs live `GET /api/security/cameras` is RED until serving matches (do not patch skill fixtures to paper over drift).

## Red cases (must fail loud until serving proves otherwise)

1. **DHCP churn same MAC** — host for camera-1 changes; `resolve_camera_sticky` / `apply_host_remap_for_mac` keep `id=camera-1`.
2. **Missing mac loud** — API `_normalize_mac` → `""` (no invent) = PASS. FE `data-camera-mac-missing` = **AMBER not live yet** (Secure eye).
3. **Unreachable :554** — RTSP down → snapshot honesty fails soft; inventory row may still exist (LO5).
4. **Concurrent snapshot stampede** — parallel `/snapshot` for camera-1/camera-2/camera-3 must not wedge or invent green frames.
5. **Wifi no-iface honest error** — unset `BACKS_AI_WIFI_SCAN_IFACE` (and no router host) → `ok=false` + clear error; no wlan0 invent.

## Agent steps (prototype)

1. `live-research`: read live `camera_streams.py` sticky helpers + cameras API JSON; pin SHAs (`6e33` tip + `<deploy-head-sha>` HEAD).
2. Diff API macs vs fixture table — board mismatches; do not invent.
3. Exercise red cases as **contracts** (prove red or prove serving already green with evidence).
4. `secure-delivery`: no secrets in observe; report-only; no install/land.
5. Observe board under `grokbot-bridge/observe/`; **ping Security** for Secure eye on red cases before any pack land.

## Non-goals

Foreign skill install; production pack land; wolf_pack_land; MFA/Act-fold; inventing RTSP green; hardcoding model IDs.

## Secure eye (2026-09-19)
**CLEARED** — five red cases PASS (R2 FE attrs AMBER). Receipt: `observe/<review-receipt>.md`. No install/land from Secure.
