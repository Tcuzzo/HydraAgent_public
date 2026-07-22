# Hydra Skills

**Status:** SOURCE CATALOGS + MATERIALIZER.

This directory contains the bundle catalogs and the concrete skill documents
that ship with Hydra. It is not a thousand-skill Python runtime.

Shipped inventory:

- bundle catalogs under `hydra/schemes/bundles/`
- 17 concrete `SKILL.md` documents under `hydra/schemes/`
- Python runtime tool modules under `skills/` (counted separately)
- 0 catalog entries claimed as Python runtime tools

The full procedural library is **generated on your machine**, not shipped:

```bash
hydra skills materialize            # bundles -> hydra/schemes/generated/
```

reads the catalogs under `hydra/schemes/bundles/` and writes one concrete
`SKILL.md` per catalog entry to `hydra/schemes/generated/` (both paths
overridable with `--bundles-root` / `--output-root`).

## What Counts As Real

A procedural skill is materialized only when it has:

- a concrete `SKILL.md`,
- activation guidance,
- a working procedure,
- verification guidance,
- refusal boundaries.

Python runtime tools are counted separately and still require executable modules
and tests.

## Bundle Status

The bundle files under `hydra/schemes/bundles/` remain source catalogs.
Files under `hydra/schemes/generated/` (created by `hydra skills materialize`)
are the materialized procedural library produced from those catalogs.
