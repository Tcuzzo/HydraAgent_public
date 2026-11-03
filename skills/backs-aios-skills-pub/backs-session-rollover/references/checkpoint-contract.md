# Checkpoint contract, schema version 1

## Required packet fields

| Field | Meaning |
| --- | --- |
| schema_version | Integer 1. |
| task_id | Stable mission identity across all sessions. |
| generation | Positive monotonically increasing checkpoint generation. |
| source_session_id | Actual provider session being replaced. |
| phase | checkpoint for a durable snapshot, ready for an eligible takeover. |
| intent | Immutable original request reference. |
| corrections | Reference to the exact append-only instruction revision. |
| constraints | Current constraints and governing-law source hashes. |
| permissions | Existing authorized scope and permission revision. |
| repo | Worktree root, branch, HEAD, index fingerprint and dirty inventory. |
| current_step | The unfinished step the successor continues. |
| next_action | Concrete next action, with safe commands when needed. |
| pending_work / completed_work | Lists of scoped work items. |
| failed_attempts | Prior failures and evidence, preventing repeated dead ends. |
| evidence_refs | Exact evidence object references. |
| worker_receipts | Child identity, terminal state, and evidence. |
| inflight_operations | Operation identity, drained/reconciled state, evidence. |
| budgets | Existing attempt ceiling and usage; optional cumulative token/cost limits. |

An artifact reference has exactly path and sha256. Resolve it relative to the
explicit task directory. Reject absolute references, traversal, escaping
symlinks, vault paths and environment-secret files. The explicit worktree root
must match the packet; never let a received packet silently select a different
repository. Private live paths belong only in ignored runtime state.

repo contains root, branch (null for detached HEAD), head, index_fingerprint,
worktree_metadata_fingerprint, and dirty_manifest. The index fingerprint hashes
read-only Git index entries. The worktree metadata fingerprint records dirty
paths' existence, file type, and permissions, including executable-mode changes.
Each dirty entry contains path and sha256, optionally state: present. A deleted
entry uses state: deleted and sha256: null. Validate all nonignored untracked
files, tracked changes and deletions, including rename source paths. Recheck
HEAD, branch and inventory after hashing. The utility refuses dirty symlinks
and submodule directories; use an appropriate host snapshot for those types
without weakening validation or deleting them.

The host must quiesce writers while producing a ready packet and must revalidate
at activation. Hashes identify state; they do not stop a concurrent process.

budgets requires attempts_used and attempts_limit. Optional paired counters are
task_tokens_used/task_token_limit and task_cost_used/task_cost_limit. Preserve
existing ceilings and do not decrease cumulative usage between generations.
Exhaustion allows saving a checkpoint but does not allow restarting the build
under a renewed budget. Deadlines and any other runtime limits remain in the
mission ledger and constraints; a fresh session must inherit them too.

failed_attempts entries contain summary and nonempty evidence_refs.
worker_receipts contain worker_id, status and nonempty evidence_refs. Ready
worker statuses are completed, failed or cancelled. inflight_operations contain
operation_id, status and evidence_refs; ready statuses are drained or reconciled.
Snapshots may record running or pending work; a ready packet cannot.

## Durable file representation

write_checkpoint atomically publishes a new generation directory containing
packet.json, manifest.json and COMPLETE. It fsyncs files and, on POSIX, the
directories before publishing. Python has no portable Windows directory-fsync
operation, so Windows power-loss durability needs the host's verified storage
implementation. A partial staged directory is not a usable checkpoint.

Canonical JSON uses sorted keys, compact separators, UTF-8, no nonfinite
numbers, and one trailing newline. The manifest and COMPLETE marker bind the
packet's SHA-256 and byte length. Repeating the same generation with identical
content is idempotent. Different content for an existing complete generation
is a conflict. Older complete generations remain readable but cannot activate
when a later completed generation exists.

The default 48000-byte bound applies to the packet file. Evidence records are
referenced rather than embedded. This is not a claim that the packet fits in
12000 or any other exact number of model tokens. Token-count the complete
assembled successor prompt before model dispatch.

## Public utility operations

- decide(request) returns continue, checkpoint_requested, handoff_required,
  or refresh_required plus explicit allow_dispatch and measurement quality.
- write_checkpoint(packet, task_dir, store, repo_root) validates and publishes.
- verify_checkpoint(task_dir, store, generation, repo_root) checks current
  evidence and returns the digest, generation and explicit allow_resume flag.
- CLI decide reads its JSON request from stdin. CLI checkpoint reads the packet
  from stdin and requires --task-dir and --store; pass --repo-root explicitly.
- CLI verify takes those paths and --generation; it does not read stdin.

Keep the task directory and checkpoint store outside the worktree or in the
existing ignored journal. Creating checkpoint files as unexplained untracked
project files changes the very inventory being checked.
