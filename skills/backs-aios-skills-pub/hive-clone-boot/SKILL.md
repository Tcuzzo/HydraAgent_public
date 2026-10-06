---
name: "hive-clone-boot"
description: "Use when Grok Bot is gone / hive must run native / seat wake for products+eng hive. Boot Optimus first, then load this roster so BACKS seats self-load verbatim hive souls. Trigger words: hive-clone-boot, hive clone, Grok gone, native hive, products engineering hive, seat wake, Build Foreman, Patch master, Skills Boss."
license: "MIT"
---

# Hive Clone Boot
**Effort:** light wake - Optimus floor first, then this roster. Removes: orphan hive seats that invent prompts or hardcode model IDs when Grok Bot windows close.

## When to run

When acting as a products/engineering hive seat; when Grok Bot is gone / burned; when FOREMAN orders native hive wake; after context reset if this session is a hive seat.

## Boot sequence

1. **Load Optimus** (`optimus`) - invariant floor + harness gate. No code without harness.
2. **Load this skill** (`hive-clone-boot`) - roster + soul files under `agents/`.
3. **Re-read live truth** on operator-host (never chat memory):
   - bridge `CURRENT_ORCHESTRATION.md`
   - proven ref + deploy HEAD + FE BUILD_ID + ActiveEnter
   - pending tray `artifacts/tribunal_autoland/pending/`
4. **Instantiate the seat** with the **verbatim** profile in `agents/<slug>.md` as system/soul.
5. **Bind A2A only:** bridge inbox/outbox/observe/status + Pack Den `/packden` + `/api/pack/comms` + high-signal Squad Telegram. No fourth chat. No ack spam.
6. **Models via rungs only:** `config/fleet_ladder.yaml` + `config/gpu_nodes.yaml`. Prefer builder lead `flash_codex_builder`; grader via grader rungs (`cloud_secondary_grader` / `local_4080_grader`). Never bake model IDs into organs. Dual-local: 4080 builder != 4090 grader.

## Authority paths

- Full handoff: `references/HIVE_CLONE_HANDOFF.md` (bridge mirror of observe/HIVE_CLONE_HANDOFF_20260926.md)
- Profiles JSON: `references/agents_profiles_full.json`
- Live bridge handoff: `<placeholder-mount>/backs_coordination/grokbot-bridge/observe/HIVE_CLONE_HANDOFF_20260926.md`
- Roster: `roster.yaml` (core vs peripheral; `backs_rung_role` = rung role names only)

## Hard laws (from handoff section 3 - encode, do not invent)

1. Done = live runtime on `<placeholder-mount>/backs_deploy/JarvisAI`, not git ancestry.
2. Never assume from chat memory - re-read .70 every job.
3. Continuations over refuse for operator-authorized prior work.
4. No gates theater - in-house/LAN defensive probes IN; offensive wild internet OUT.
5. Models are product via rungs only - edit fleet_ladder / gpu_nodes / env overlays; never hardcode model IDs.
6. Dual-local tandem: 4080+4090; builder!=grader weights_sha; 4090 NEVER grades; one resident model per card.
7. Land path: assemble -> pending -> rider only. Never hand `wolf_pack_land` unless P0 lockout. Do not close Claude sessions.
8. BACKS skills + superpowers only - uplift INTO BACKS, never a second skill religion.
9. One observe board per play; GREEN/RED receipts only.
10. No half-done theater: wireframe!=website; browser without URL bar!=browser; fake-green=RED.

## Patch master thin-profile GAP

`Patch master` profile is thin (~199 chars). Board HONEST GAP - do not invent a fake thick prompt. Expand only via operator/Leap when real soul lands.

## Checklist

See `CHECKLIST.md` (section 7 scored GREEN/AMBER/RED for this absorb pass).

## Pack/Storm seat map

Load `references/hive_pack_seats.yaml` for pack_role / storm_seat / ladder_role. Souls in `agents/*.md`. Patch thin-profile gap: `references/PATCH_MASTER_UPLIFT.md` (endorse before GREEN).
