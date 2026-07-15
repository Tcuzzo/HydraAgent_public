# Security Policy

Hydra runs on **your** machine, with **your** keys, doing what **you** approve.
That makes its security posture simple to state and worth reporting against.

## Reporting a vulnerability

Please report vulnerabilities **privately** through
[GitHub Security Advisories](https://github.com/Tcuzzo/HydraAgent_public/security/advisories/new)
— do not open a public issue for anything exploitable.

Include what you can: the affected file or tool contract, a minimal
reproduction, and what an attacker gains. You can expect an acknowledgement
within **7 days** and a fix or a clear written assessment within **30 days**
for confirmed issues. If we can't reproduce it, we'll say so and keep the
thread open rather than closing it quietly.

## Supported versions

The **latest `main`** is the supported version. There are no backported
security fixes to older tags — update to current `main` to receive fixes.

## Scope — what Hydra's security model promises

- **Local agent, your machine.** Hydra is a local CLI agent. It has no hosted
  service, no telemetry, and ships **no credentials** — you bring your own
  keys (`BYO keys`), and they stay in your environment files.
- **Approval-gated shell.** Shell execution sits behind an approval gate.
  Destructive commands always require approval. The public edition also ships
  `non_destructive_auto_allow: false` — a flag the runtime **enforces** (not just
  documentation), so a fresh install asks before running *non-destructive*
  commands too, until you explicitly set it `true` in
  `.hydraAgent/tools/shell.yaml`.
- **Telegram remote with 2FA.** The optional Telegram remote gates unattended
  mode behind a 6-digit 2FA code; approving risky actions is an explicit tap.
- **Bounded file tools.** File read/write/edit are workspace-scoped with
  path-escape protection.

In scope: anything that breaks one of those promises — sandbox/path escapes,
approval-gate bypasses, secret leakage into logs or memory files, prompt- or
tool-injection paths that reach the shell without approval, and supply-chain
issues in the pinned dependency set (`constraints.txt`).

Out of scope: issues that require the operator to have already granted the
access being "exploited" (Hydra intentionally does what its operator
approves), and vulnerabilities in the models or providers you connect.
