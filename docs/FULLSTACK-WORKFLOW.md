# Full-stack work with Hydra and BACKS

Use your configured programming model to build the application, and local skills to guide design, browser checks, security review, and follow-up searches. These are instructions and reference material. They do not install a model, grant tool permissions, or guarantee the resulting application's quality.

Keep the existing BACKS harness in charge of planning, ownership, budgets, approvals, and verification. The skills below supply guidance for individual stages; they do not replace that workflow or require a particular model family.

| Stage | Optional skill | Evidence to retain |
| --- | --- | --- |
| Design the interface | `frontend-design` | Audience, primary task, visual direction, responsive layout, keyboard focus and accessible states |
| Verify a running application | `webapp-testing` | Real browser actions, assertions, screenshots, browser errors, server logs |
| Review a change for security defects | `differential-review` | Baseline commit, changed trust boundaries, callers, concrete findings and untested paths |
| Find related defects after confirming a bug | `variant-analysis` | Root cause, exact-match search, broader searches, triaged matches and regression coverage |

Programming still includes the application's backend, API contracts, data migrations, authentication, validation, error handling, and deployment configuration. Select checks appropriate to the actual stack and run them through the host's approved tools. Use the existing BACKS build and repair workflow for those steps.

## Load the skills in Hydra

Obtain and review the upstream skill directories, retaining their licenses and supporting files. The portable full-stack bundle prepared for this workspace contains all four under `fullstack-skills/skills/`. It is a separate artifact, not a bundled dependency of the public Hydra wheel.

Inspect a specific trusted local directory:

```bash
hydra skills --skills-root /path/to/fullstack-skills/skills list
hydra skills --skills-root /path/to/fullstack-skills/skills show frontend-design
hydra skills route "Use frontend-design, webapp-testing, differential-review and variant-analysis" --skills-root /path/to/fullstack-skills/skills --format json
```

`--skills-root` belongs to `hydra skills`; it is not an `ask` flag. Set `HYDRA_SKILLS_ROOT` for agent turns and their skill tools. For example, in PowerShell:

```powershell
$env:HYDRA_SKILLS_ROOT = 'C:\path\to\fullstack-skills\skills'
hydra skills list
hydra ask "Use frontend-design to design a responsive account page and webapp-testing for browser proof" --root C:\path\to\my-project --runtime-only
```

On macOS or Linux:

```bash
export HYDRA_SKILLS_ROOT=/path/to/fullstack-skills/skills
hydra skills list
hydra ask "Use frontend-design to design a responsive account page and webapp-testing for browser proof" --root /path/to/my-project --runtime-only
```

Remove `--runtime-only` when ready to run with your configured model. [Model configuration](MODELS.md) remains separate. Normal approval policy still applies.

An explicit skills root **replaces** Hydra's default skill search roots. It does not merge directories or accept a list separated by path delimiters. If you want these playbooks alongside existing local skills, place the reviewed directories together under one trusted root. `skills doctor` can report missing core playbooks when pointed at a pack containing only these four; that does not mean the four failed to load.

Hydra routes the explicit name of any installed skill. It also recognizes phrases such as `responsive UI`, `browser proof`, `security review`, and `bug variants` for this set. Routing is deterministic keyword selection, not a claim that the model follows every instruction correctly. `skills show` prints a summary; the runtime `skill_show` tool reads actual instructions in bounded pages. Follow its `next_offset` until complete. Use its `relative_path` argument to read a linked reference within that skill directory. It cannot read outside the registered directory or execute a helper script.

## Use the same pack with BACKS

In a Codex host, the four installed directories can live under `~/.codex/skills/`; start a new session if the host has not refreshed its skill catalog. On another BACKS host, configure that host's local skill discovery to point at the reviewed directories. Copying a pack does not configure a remote host, its model endpoint, browser tooling, or credentials.

Ask the existing harness to apply these stages as relevant:

1. Define the working application and its acceptance checks. Load the project's own instructions and inspect the actual stack.
2. Use `frontend-design` before substantial interface work. Implement frontend and backend changes with the configured programming model and existing project conventions.
3. Run focused regression checks, then verify the real user journey with `webapp-testing`. Inspect the rendered page before choosing selectors; wait for a meaningful ready state with a timeout. Record both successful and rejected/error flows.
4. Use `differential-review` against the actual baseline and final diff. Prioritize authentication, authorization, data validation, filesystem/network boundaries, secrets, and dependencies touched by the change.
5. If a defect is confirmed, use `variant-analysis` to search the same root cause elsewhere. Keep findings separate from unconfirmed candidates, fix confirmed instances, and rerun the relevant proof.
6. Report what changed, the checks that actually ran, remaining limitations, and the location of the evidence. Follow the existing harness's release or deployment policy.

The portable skill folders do not install the upstream `differential-review:adversarial-modeler` agent, `/variant-analysis:variants` command, `issue-writer`, or `audit-context-building` plugin. If those integrations are unavailable, perform the documented steps using the included `adversarial.md`, methodology, search/triage references, and report template. Use ordinary available subagents only when supported by the host. Do not claim an unavailable slash command ran.

## Browser and process boundaries

Use the host's native browser tooling when available, or provision Playwright and a browser explicitly in the project's environment. The pack alone does not install them. Start application services with managed process handles, bounded readiness checks, logs written to files or actively drained streams, and cleanup of the process tree when the task finishes.

The original `webapp-testing/scripts/with_server.py` is retained unchanged for provenance. It has not been executed or certified by this integration; its pipe and child-process lifecycle warrants review before use. The recommended workflow uses the host's managed processes instead. Set finite budgets for browser actions, server readiness, security searches, and repair attempts. Do not start an unbounded workflow merely because an upstream example suggests one.

## Provenance and limits

The workspace's portable artifact includes a per-file SHA-256 manifest, an offline verification script, and these pinned sources:

- [Anthropic skills](https://github.com/anthropics/skills/tree/dbd4588f9e1033efb41dad4bef2f7947c8993d44): `frontend-design` and `webapp-testing`, with their embedded Apache-2.0 license files.
- [Trail of Bits skills](https://github.com/trailofbits/skills/tree/442fc9d6c89b1e937e6f7a477e7071ea75fcbea4): `differential-review` and `variant-analysis`, with the upstream CC-BY-SA-4.0 license retained.

Upstream skill files are unchanged. Local workflow documentation and Hydra routing integration are separate. Preserve attribution and the applicable license when redistributing or adapting upstream material. Superpowers is not included or installed by this pack; an optional recommendation is not an installation. No private BACKS code, private Hydra corpus, model weights, or credentials are included.

Discovery, routing, and readable instructions are integration checks. They are not evidence of trained programming ability, a successful full-stack application, a browser pass, or a complete security audit. Those claims require a real project run and recorded results.
