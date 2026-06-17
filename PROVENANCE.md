# Provenance

This repository — **Hydra (public edition)** — is a sanitized, lean extraction of a
larger private agent codebase ("the original"). It contains the general-purpose
coding-agent core only: no private orchestration methodology, no media/studio
pipelines, no multi-machine swarm, and no operator-private infrastructure, identity,
or secrets.

## Original fingerprint

The private original was hashed at extraction time so this public edition can be
proven to derive from it. The fingerprint is the SHA-256 of a sorted manifest of the
SHA-256 of every tracked file in the original working tree:

```
original_aggregate_sha256: 3111345e0e5c1d87f2872f17d6e5f5b2e2302cd6c2126b46dea4956de98de5d8
files_in_original:         3788
extracted:                 2026-06-16
```

The original itself is **not** published. Only the lean coding-agent core appears
here, rebuilt with a fresh git history (no inherited commits) so that no private
content or secret is recoverable from history.

## What was deliberately left out

- Private build/iteration methodology and its harness.
- A self-debugging subsystem and repo-surgery primitive.
- Media/studio generation (image/video/audio) and the content pipeline.
- Multi-machine swarm / collaboration fabric and remote-execution backends.
- All operator identity, machine addresses, chat IDs, tokens, and runtime logs.

## What was kept (the lean core)

The agent loop, tri-tier model routing, the hybrid (vector + keyword) memory
kernel, the skill spine, the file/shell/search/HTTP tool surface, the
approval/trust model, the optional Telegram control surface, and the TOTP-gated
autonomous ("yolo") mode. See [README.md](README.md).
