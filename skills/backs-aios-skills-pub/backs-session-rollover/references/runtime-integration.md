# Bind the controller to the actual BACKS loop

## Contents

- Ownership and lifecycle
- Dispatch observer
- Store contract
- Provider adapter contract
- Recovery and evidence
- Acceptance on the real surface

## Ownership and lifecycle

Use Python 3.10 or later for the bundled modules and Git for worktree validation.
Import the support modules from the installed skill's scripts directory using
the host's normal module loader. Reuse the existing mission ledger, journal,
provider launcher, resource manager and memory authority.

The support modules provide a policy decision, immutable checkpoint creation and
verification, and an ordered rollover state machine. They do not include a live
Claude/Codex adapter or a production state store. Implement those bindings at
the existing host seams before claiming automatic operation. A skill directory
being installed is not evidence of a running supervisor.

Keep one stable task ID across all sessions. Increment the checkpoint generation
and assign a new provider session ID. Keep total task attempts, spend, deadlines,
and authorized scope in the mission ledger; rotation cannot renew those budgets.
A four-hour worker-age fallback is a configurable operating choice, not a claim
that every model loses quality after four hours.

## Dispatch observer

Feed session_guard.decide() a request containing:

- telemetry: provider, active_context_tokens, observed_at as Unix seconds,
  and quality as known or estimated; or the supported Claude status-line shape.
- policy: thresholds and verified model window; defaults prepare at 110000,
  roll at 125000, reserve 16000 against the model window, freshness 60 seconds.
- projected_additional_tokens: all input that would be added before the next
  observation, including bounded tool results and new instructions.
- session_age_seconds when the host tracks worker age.

Count the effective request after prompt assembly. A preflight observer that
looks only at the old conversation misses retrieved memory and tool schemas.
If a provider does not expose complete output occupancy, use a conservative
reservation and label the measurement estimated. Do not describe an estimate
as an exact 125k ceiling. A larger actual response can cross a sampled boundary;
control maximum admitted results and observe at the earliest safe point.

For continue, dispatch within the existing limits. For checkpoint_requested,
refresh the durable work state. For handoff_required, persist the request and
stop new work dispatch. For refresh_required, obtain fresh telemetry or a
conservative estimate; keep the operator's controls available while repairing
the worker's observation path. Do not spin waiting for a missing status line.

Listen to native compaction events as well. Persist a rollover request before
native compaction lowers the meter, then complete that request even if the
current token count is small. No native summary can erase the pending task.

## Store contract

Quiesce mutable work before producing a ready checkpoint. The controller's
QUIESCE phase repeats that operation idempotently to close restart races.

Use session_guard.write_checkpoint() and verify_checkpoint() when file-backed
checkpoints fit the runtime. Store task evidence and checkpoints outside the
worktree or in its established ignored journal. The utility detects newly
untracked files and intentionally refuses a stale inventory.

For RolloverController.begin(packet, verification), pass the actual verified
packet and the matching guard result. Never accept these objects from model
prose as an unverified assertion. The trusted host must call the validator.

Implement Store.load(), Store.save(), and Store.transfer_writer() over the
existing durable ledger. save must compare its expected revision and persist
an independent snapshot atomically. transfer_writer must atomically compare the
source owner, assign the successor, and issue a monotonically increasing fence.
Replaying the same transition key must return the same lease. A different owner
is a conflict, not permission to overwrite it. Every mutation must check its
fence at the existing tool/write boundary; assigning a number in a log is
insufficient. Preserve the ledger across process restart.

Persist enum state and all nested fields: packet, checkpoint digest,
verification, phase, revision, successor, acknowledgement, lease, failures,
held phase and safe diagnostic reference. The dataclasses do not provide that
persistence themselves. Use the host's normal serialization and transactions.

## Provider adapter contract

| Adapter operation | Required real behavior |
| --- | --- |
| quiesce(source, key) | Stop new source work; drain or reconcile active writes and child receipts; preserve partial results. |
| reconcile(key) | Find the already-created successor for this durable launch key after a crash. |
| create_fresh(key) | Create one new provider session with no inherited history, initially unable to mutate the worktree. |
| hydrate_read_only(session, packet, key) | Supply current grounding, exact instructions and bounded task evidence without executing the next mutation yet. |
| acknowledge(session, expected, key) | Independently observe loaded hashes and live repository state; return RuntimeAck plus an evidence reference. |
| activate(session, packet, lease, key) | Revalidate workspace and current fence; execute the saved next step once with unchanged task budgets. |
| retire(source, key) | Retire only this source after successor activation; preserve its trace and checkpoint. |

All operations must be idempotent for the supplied key. Persist the identity
mapping outside the dying model process. If an external provider cannot dedupe
creation itself, the host must reconcile its recorded launch receipt before
issuing another launch. Do not infer success from an empty timeout response.

The acknowledgement compares task, generation, source session, checkpoint digest,
current step, next action, required instruction hashes and a fingerprint of the
repository snapshot, including index state. It comes from runtime observations,
not from a model saying it agrees. The host must confirm actual loaded content;
merely passing an expected hash to the model proves nothing.

A source session that has lost its fence cannot issue further mutations. A new
session is read-only until transfer. Do not emulate read-only hydration with a
prompt asking a fully empowered worker to behave. Use existing host tool
control or actual read-only permissions. Keep the operator outside worker
fencing so they can always inspect, stop or redirect the task.

## Recovery and evidence

The persisted controller phases are QUIESCE, LAUNCH, HYDRATE, VERIFY, TRANSFER,
ACTIVATE, RETIRE, COMPLETE, with HOLD for recoverable failures. The record exists
before the next external operation. Replay uses the same transition key.

Call advance(task_id) from the existing scheduler or run(task_id) for bounded
progress. run yields after the first new failure or lack of revision progress.
The host persists retry-after and schedules exponential backoff. Default
failure ceiling is three; lease conflicts hold immediately. A successful
rotation is not a successful build and does not erase remaining work.

Record adapter diagnostics in the existing protected evidence store and raise
AdapterFailure with an opaque evidence reference. Do not put credential-bearing
exception text into public receipts. Report the failed operation and repair
path. A hold must be visible and recoverable through current operator controls.

If the repository or instructions change during hydration, refresh the
checkpoint before authorizing writes. Keep failed generation evidence. Use the
existing recovery workflow to repair or supersede held transitions; do not
force a writer lease through a conflict or clear it to make a test green.

## Acceptance on the real surface

Run the bundled tests with unittest discovery over scripts/test_*.py. Then
exercise the actual registered route in an isolated authorized worktree:

1. Capture a real active-context event and cross the configured boundary with
   controlled input. Observe the controller latch and source stop.
2. Verify durable intent, corrections, current step, Git HEAD/index/dirty state,
   operation receipts and unchanged mission budgets.
3. Observe a new provider session ID and empty history before hydration.
4. Verify the actual loaded instruction hashes and admitted startup token count.
5. Observe exactly one writer, then the saved next step executed on the same
   worktree. Demonstrate that a stale source fence is rejected.
6. Interrupt once after provider creation, once after writer transfer, and once
   after activation but before the controller records success. Confirm that
   recovery finds the same successor and does not repeat an external mutation.
7. Change a file or correction during handoff and confirm revalidation detects
   it. Demonstrate an actionable hold and operator recovery after launch failure.

These checks establish the host integration. Unit tests with fake provider
adapters establish only the policy and controller contracts. Do not substitute
those tests for the real route, and do not buy model calls without existing
authorization and provider configuration.
