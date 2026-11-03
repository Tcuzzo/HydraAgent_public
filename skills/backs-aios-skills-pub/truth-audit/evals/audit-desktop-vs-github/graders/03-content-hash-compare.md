---
type: llm
focus: trace
weight: 1
---

The agent compared the running tree with the remote's content using file hashes (for example scripts/cmp_trees.py), and checked the remote live (for example git ls-remote) instead of trusting a local clone.
