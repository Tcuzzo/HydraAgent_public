# Public source map for rollover integration

These observations are source inspection, not evidence of the private runtime's
current deployment. Recheck live call paths before editing. The skill composes
the existing BACKS continuity work instead of replacing it.

## BACKS skills

Inspected revision: b032db975f807190c230bcd5698ee319a17a3850.

| Existing source | Reusable behavior | Rollover connection |
| --- | --- | --- |
| skills/session-handoff/SKILL.md | Goal, branches, dirty work, verdicts, traps, exact next command, cold verification | Build the bounded handoff; preserve its readable summary. |
| skills/context-compiler/SKILL.md | Evidence-based context, exclusions, current state, concrete next action | Assemble fresh input while retaining the original instruction records. |
| skills/bounded-loops/SKILL.md | Checkpoint each iteration, safe boundaries, one writer, budgets | Add rollover as an outer-loop transition without resetting mission counters. |
| hooks/hooks.json | SessionStart, Skill events, existing tool-grounding integration | Compose lifecycle notification; do not replace or weaken grounding. |

The inspected hook registration contains no active-token rollover observer or
fresh-session supervisor. A written handoff method does not itself schedule one.

Sources: [Session Handoff](https://github.com/Tcuzzo/backs-aios-skills/blob/b032db975f807190c230bcd5698ee319a17a3850/skills/session-handoff/SKILL.md),
[Context Compiler](https://github.com/Tcuzzo/backs-aios-skills/blob/b032db975f807190c230bcd5698ee319a17a3850/skills/context-compiler/SKILL.md),
[Bounded Loops](https://github.com/Tcuzzo/backs-aios-skills/blob/b032db975f807190c230bcd5698ee319a17a3850/skills/bounded-loops/SKILL.md),
[hook registration](https://github.com/Tcuzzo/backs-aios-skills/blob/b032db975f807190c230bcd5698ee319a17a3850/hooks/hooks.json).

## Hydra runtime

Inspected revision: 81924595924f8645f3ad329da15b29d7747b6569.

### Storage and active memory diverge

In gateways/tui/elite.py, the completed-turn path calls compact_session() on
persisted records. However, _stream_turn() reads a separate _chat_messages
list and appends user/assistant turns to that list. Automatic storage compaction
does not replace the active list in the inspected path.

Manual session compaction calls _reset_live_chat_history(), which retains only
system messages. This reduces active history without the explicit task-state
rehydration required by a durable handoff. A source fix must connect both paths
to the same verified continuation packet.

Source: [elite TUI](https://github.com/Tcuzzo/HydraAgent_public/blob/81924595924f8645f3ad329da15b29d7747b6569/gateways/tui/elite.py).

### Retention thresholds are not token thresholds

hydra/session_memory.py compacts above 80 records or 128 KiB by archiving the
source and retaining a system message and the latest 24 other records.
rotate_session() retains the storage session ID. Neither operation alone
creates a new frontier model session. Preserve useful archives and add the
validated task handoff at the inference boundary.

Source: [session storage](https://github.com/Tcuzzo/HydraAgent_public/blob/81924595924f8645f3ad329da15b29d7747b6569/hydra/session_memory.py).

### Place checks around actual model dispatch

hydra/loop.py supplies growing messages and tool schemas to client.chat().
Its output max_tokens setting is not an input budget. Add the active-context
observer before calls and after completed tool results; yield a distinct
rollover outcome to the outer controller. Preserve the existing step and
attempt counters.

hydra/mission_loop.py already has mission records, evidence and continuation
plans. Reuse those identities and the existing memory authority. Its generic
continuation plan is not by itself a fresh Claude/Codex conversation.

Sources: [agent loop](https://github.com/Tcuzzo/HydraAgent_public/blob/81924595924f8645f3ad329da15b29d7747b6569/hydra/loop.py),
[mission loop](https://github.com/Tcuzzo/HydraAgent_public/blob/81924595924f8645f3ad329da15b29d7747b6569/hydra/mission_loop.py).

Do not use the TUI's cumulative character statistic as the context meter:
it recounts history across turns. Do not replace the active worktree with a
tracked-files-only copy, which can lose the relationship to untracked work.
