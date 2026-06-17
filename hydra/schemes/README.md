# Hydra Skills

**Status:** MATERIALIZED PROCEDURAL LIBRARY.

This directory contains bundle catalogs, sample skill documents, and a generated
procedural skill library materialized from the seven 200-entry bundle catalogs.
It is not a thousand-skill Python runtime.

Current local inventory:

- 1420 `SKILL.md` files under `hydra/skills`
- 1400 generated procedural skill contracts under `hydra/skills/generated`
- 16 Python runtime tool modules under `skills/`
- 0 catalog entries claimed as Python runtime tools

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

The bundle files under `hydra/skills/bundles/` remain source catalogs.
The generated files under `hydra/skills/generated/` are the materialized
procedural library produced from those catalogs.
