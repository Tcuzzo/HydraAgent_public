# Hydra Skills Quickstart

Skills live under `hydra/schemes/bundles` — a set of curated skill bundles, each
with sample `SKILL.md` contracts the skill spine can discover and route to. A
materializer can expand the bundle catalogs into additional procedural skill
contracts on demand (nothing pre-generated is shipped in this repo).

Useful commands:

```bash
python3 -m hydra skills list
python3 -m hydra skills doctor
python3 -m hydra skills route "fix a failing test"
python3 -m hydra skills materialize --dry-run
```

To promote a procedural skill into a runtime capability:

1. Start from its `SKILL.md`.
2. Add a Python runtime tool, workflow, or eval when the task needs execution.
3. Add a focused test or eval.
4. Run the test through Hydra's real runtime where applicable.
5. Update `hydra/schemes/index.yaml` counts only after the proof exists.
