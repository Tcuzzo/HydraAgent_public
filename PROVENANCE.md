# Provenance

## Public orchestration additions

The personal-orchestration preview adds new generic specialist profiles, a bounded
task-graph CLI and official MCP integration. The public canon and essence text is
authored for this edition; it does not publish the private original's canon or
methodology. Optional private overlays are loaded from outside the source tree and
are not release assets. The following extraction record describes the original
public baseline; it is not a claim that later releases contain no orchestration.

The optional Zapier harness references official SDK packages under Zapier's terms;
those packages are not copied or relicensed as part of Hydra. Project and human
skill-author credit is distinct from development-tool credit.

This repository — **Hydra (public edition)** — is a sanitized, lean extraction of a
larger private agent codebase ("the original"). It contains the general-purpose
coding-agent core only: no private orchestration methodology, no media/studio
pipelines, no multi-machine swarm, and no operator-private infrastructure, identity,
or secrets.

## Project and tool credit

Hydra is Cuzzo's (Tcuzzo's) project, developed with BACKS AIOS. Credit Cuzzo for
project authorship and BACKS AIOS as the development platform. Human contributors
receive credit for their actual contributions.

Codex (OpenAI), Claude/Claude Code (Anthropic), and other models or coding
assistants receive **tool credit only**. They are tools used within AIOS, not
project authors or co-authors. Record the tools actually used in plain provenance
text or a `Tool-Used:` commit trailer; do not add model or vendor identities to
`Co-Authored-By:`, author lists, or copyright notices. This policy applies to every
model family and provider.

Original skill creators retain credit for their work. This includes the existing
Matt Pocock scaffold credits in the skill library and the upstream creators of
other incorporated skills. Preserve their source links, notices, and licenses.
Attribution to an upstream publisher such as Anthropic for actual borrowed skill
content is source attribution; it does not give Claude or Anthropic co-authorship
of Hydra. See [the full-stack workflow](docs/FULLSTACK-WORKFLOW.md#provenance-and-limits)
for the separately installable skill pack's sources and licenses.

The repository's [Claude Code settings](.claude/settings.json) replace its default
co-author footer with tool credit in future commits and pull requests. All coding
assistants should follow [AGENTS.md](AGENTS.md). Historical model co-author trailers
predating this policy describe tool use and do not confer project authorship.
Changing these files does not remove those trailers from existing Git history.

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
