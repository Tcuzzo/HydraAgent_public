# Memory that reaches the next task

Hydra learns execution experience locally. A completed task contributes observed
tool actions, their real success or failure signals, source paths, and provenance.
The next task retrieves relevant prior observations. The selected reasoning model
still decides what to do, and the runtime still owns tool execution and approval.

## What the runtime does

1. Resolve the selected memory root and actual project workspace.
2. Read the current durable lessons, permitted project memory, project wiki, and
   that workspace's execution episodes. Redact secret-like text before indexing.
3. Reuse unchanged vectors and index new chunks in bounded batches. The SQLite
   store combines vector similarity and FTS5 keyword ranking with reciprocal-rank
   fusion. Observed same-source links add one bounded graph hop.
4. Restrict retrieval to the current source manifest before selecting the best
   matches. Old cached copies and other workspaces cannot fill the candidate pool.
5. Format selected evidence inside the complete context budget, including labels.
   A missing embedding service uses query-ranked lexical retrieval and reports
   `backend: lexical`, `fallback: true`, and the reason.
6. After real tool execution, save a workspace-scoped episode with task, tool,
   artifact and outcome nodes. Later tasks can retrieve the episode and inspect
   relationships through shared artifacts.

This process improves the information available to later tasks. It does not
certify the reasoning model's answers or train its neural weights. Separate local
Needle and Kev/Laya training uses the supervised training workflow and held-out
evaluation described in [TRAINING.md](TRAINING.md).

## Where information lives

The default memory root is `~/.hydra-memory`. Set `HYDRA_MEMORY_ROOT` before launch
or use the command's memory-root option to choose an isolated location. Normal
vector cache paths are rebased beneath the chosen root and retain the configured
embedding identity in their filename. The explicit store-path injection seam is
still available to embedding integrations and tests.

Durable operator lessons remain in `workspace/memory/hydra-lessons.md`.
Execution observations live in `workspace/episodes/<workspace-key>/` underneath
the same memory root. A workspace key is derived from the canonical project
path, so execution history from another project is not injected into this one.
SQLite remains the derived retrieval cache; source files are authoritative.

Editing or removing a source changes the next retrieval. Historical SQLite rows
and audit events remain available; current source membership prevents their old
versions from resurfacing. Identical content is deduplicated exactly. Similar
embeddings never authorize discarding different or contradictory instructions.

## What an execution observation means

An episode records the actual tool name, bounded redacted arguments, explicit
status fields and a hash of the tool result. Artifact references include a
content hash when an existing file is small enough to fingerprint. The graph
contains observed task-to-tool and tool-to-artifact edges; it does not invent
causal links or undocumented properties of the code.

- `executed`: the recorded tool path completed without an explicit failure.
- `failed`: at least one observed tool call returned or raised a failure.
- `incomplete`: the loop did not finish naturally, even if its recorded calls
  executed.
- `quality_verified: false`: execution observations do not prove correctness.

Failures remain negative experience. They can help a later task avoid repeating
an unsuccessful command, but they are never promoted into successful fixes.
The model's final narrative is not copied into learned facts. A text-only answer
without tool execution produces a skipped receipt.

## Inspect the evidence

An explicit operator lesson can be added using the existing command:

```bash
hydra remember "Invalidate the cache when the schema changes." --source "reviewed migration test"
```

The graph can be inspected without a model call through the same source records:

```python
from pathlib import Path
from hydra.execution_learning import query_execution_knowledge

report = query_execution_knowledge(
    "cache migration",
    repo_root=Path.cwd(),
    limit=5,
)
print(report)
```

`matches` contains the directly relevant episodes. `related` contains at most one
hop to other episodes that referenced the same actual artifact path. Every result
includes its source record. Recheck file content and run current verification
before reusing a previous procedure.

## Resource and correctness boundaries

Recall returns at most the requested character budget, including its header.
It does not inject a header when there are no selected records. Source reads
use a 128 KiB window per document; the append-only lesson file uses its recent
window. Project wiki discovery is bounded to 128 files. Episode discovery uses
the latest 256 records per workspace; each episode records at most 32 tool
observations and notes truncation. Older source history remains on disk.

Incremental vector indexing schedules at most 32 new chunks per recall and
checks a ten-second work window between embedding requests; each in-flight
request has its own transport timeout. Partial indexing reports its status and
uses lexical retrieval over the current corpus. A later turn resumes indexing
without re-embedding unchanged chunks.

Concurrent creation and appends of operator lessons use the existing process
lock. SQLite initialization and exact duplicate writes are serialized. Entry,
vector, keyword and history writes share one rollbackable transaction. Embedding
vectors containing nonnumeric, infinite or NaN values fail before persistence.

The regression suite covers new lesson recall without manual migration, updated
and deleted sources, lexical fallback relevance, strict budgets, repeated
process access, concurrent inserts, negative outcomes, workspace isolation,
secret redaction and real artifact relationships. Deterministic vectors in these
tests verify persistence and routing; live embedding relevance is a separate
test and must be reported separately.
