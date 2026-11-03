---
name: "backs-pack-sync"
description: "Compare private BACKS runtime skill coverage with the public AIOS pack without disclosing names or content. Add a public skill only from a separately authored, reviewed public bridge. Trigger words: pack drift, public skills sync, safe bridge, AIOS pack."
license: "MIT"
---

# Privacy-safe pack sync

The BACKS runtime contains private-only skills. Their names, descriptions,
paths, and bodies are not public pack material. The sync command reports only
the count of runtime skills absent from this pack.

```bash
BACKS_SOURCE=/path/to/private/runtime bash bin/backs-pack-sync
```

`--bridge-all-missing` is disabled. Never run an automatic bulk export from
the private runtime into this public repository.

To publish one portable skill, author a complete public file at
`public-bridges/<public-name>/SKILL.md`. Review its license, IP, PII, network
topology, and references independently. Then run:

```bash
bash bin/backs-pack-sync --bridge <public-name>
```

The command copies only that public-authored file. It never reads a BACKS skill
body or description. It checks the file for common private literals and runs
the pack scrubber; a full public hygiene test and independent review still
precede release. Any skill whose useful behavior depends on private runtime
files stays in the private BACKS runtime.
