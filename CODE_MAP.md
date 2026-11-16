# CODE_MAP.md

Facts and locations only. One line per fact. Read this before walking the tree.

## Fingerprint

`git ls-files | sha256sum` → `1757563f8f8169e5f996585df6d6eae4beb8ba3a6e7ea6de51b4b03ae692275d`

Refresh the map only when this differs from the live tree (files added, moved, or renamed).

## Entry points

- `python -m hydra` — CLI entry point; parser assembled in `hydra/__main__.py`.
- `bin/spawn-hydra` — single launch command for the interactive chat (EliteTUI).
- `gateways/tui/` — TUI surfaces; `gateways/tui/elite.py` is the interactive chat path.
- `gateways/telegram/` — Telegram operator gateway; `gateways/telegram/operator.py` is the operator command surface.

## Seams

| Path | Purpose |
|---|---|
| `hydra/providers.py` | Provider registry: env-file parsing, key aliases, client construction. `default_env_dir()` reads the env dir at call time. |
| `hydra/rung_resolver.py` | Resolves the BACKS host for the rung chain (`backs_url()`). |
| `hydra/model_routing.py` | Chooses the rung; `set_resolver()` is the test seam. |
| `hydra/decision_ladder.py` | The decision ladder (backs/kev/laya/jev tiers) and `backs_origin()`. |
| `hydra/loop.py` | The agent loop (`AgentLoop`, `LoopError`). |
| `hydra/go_bridge.py` | Provider client bridge (`_apply_provider_aliases`). |
| `hydra/worker_aci.py` | ACI commands for worker jobs. |
| `hydra/worker_sessions.py` | Isolated worker execution sessions. |
| `hydra/proc.py` | Process helpers (`Popen` sites use `communicate(timeout=...)`). |
| `hydra/container_sandbox.py` | Rootless container runner; forwards `timeout` to `subprocess.run`. |
| `hydra/resolution_gate.py` | Runs a pytest id as a gate; `timeout=120` via `**kwargs`. |
| `hydra/setup.py` | Writes provider env files under the env dir. |
| `hydra/cli/cmd_*.py` | One module per CLI command; each registers its subparser. |
| `core/config.py` | Shared config primitives. |
| `gateways/telegram/operator.py` | Operator-only Telegram command surface. |

## Tests

- `hydra/test_*.py` — 133 files, colocated with the package.
- `conftest.py` (repo root) — autouse fixtures: `offline_resolver` (no live BACKS) and the hermetic environment fixture (isolated `HYDRA_ENV_DIR`, cleared endpoint/credential/routing vars).
- `skills/` — vendored mirror of `backs-aios-skills`; `skills/backs-aios-skills-pub/VENDORED.md` says do not hand-edit, fix upstream and re-sync.

## Commands

- Install: `pip install -e ".[test,mcp]" -c constraints.txt`
- Test: `pytest -q`
- Topology scan: `bash .github/scripts/ip-topology-scan.sh`
- CI: `.github/workflows/ci.yml` — secret scan, unsafe-code scan, topology scan, cross-platform smoke + tests.

## Guard seams worth knowing

- `hydra/test_no_model_provider_secret_literals.py` — provider-agnosticism guard; enforces the structural (model-shaped literal) scan when BACKS is unreachable.
- `hydra/test_provider_agnosticism_guard.py` — the structural detector itself (no host, no network, no committed vocabulary).
- `hydra/test_public_scrub_guard.py` — fails if operator paths/hostnames/MACs return to tracked files.
