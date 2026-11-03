# Ladder keys (Patch-owned yaml — this skill does not enforce them)
The keys below are a **proposal Patch may land into
`config/fleet_ladder.yaml`**. This skill does **not** claim to read or enforce
them in the present tense — the present-tense enforcement is whatever the
configured runner actually parses on the live host. Where the live deploy
does not carry a key, the policy here stands as desired doctrine and the
field is logged as a GAP for Patch.

| Key | Expected shape / value | Notes |
|-----|------------------------|-------|
| `thinking_floor` | `medium` (semantic) | Where the transport supports a knob; semantic baseline lives here as desired doctrine regardless |
| `thinking_scale` | rung max ↔ medium (semantic) | Autonomic up/down within this band only — semantic, not numeric-on-every-transport |
| `research_seams` | list or map | Maps research → cheap-builder / frontier specs before heavy lift |
| `requires_research_seams` | `true` on frontier / design / reason rungs | Existing HARD gate (deploy may still GAP until land) |
| `capabilities` | per-rung list | Capability tags each rung carries (long context, vision, code, reason, ...) |

If keys are **absent** on live deploy: skill still teaches this policy and
boards **GAP** for Patch. Never hardcode model IDs; resolve seats via ladder +
`gpu_nodes.yaml` only.
