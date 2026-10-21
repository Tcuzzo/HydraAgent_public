# Personal orchestration

Hydra coordinates a bounded team of specialists, supplies their public knowledge libraries, and records what actually ran. A plan becomes a validated dependency graph; each task runs in a separate worker process with a selected model and tool policy. The worker checks its observations and declared artifacts before the dispatcher releases dependent tasks. Execution evidence is also available to later work through shared, workspace-scoped memory.

The main chat model supplies reasoning and tool calls. Needle proposes structured tool choices, and Kev answers typed questions about supplied state. Their proposals do not execute themselves. Bot names, prompts and memory improve task organization and access to evidence; their effectiveness still depends on the configured model and must be evaluated on representative tasks.

## Install

From a source checkout, install the complete local bundle using Python 3.12 or 3.13:

```bash
python3.12 scripts/install_local.py
source "$HOME/.hydraAgent/local-runtime/venv/bin/activate"
hydra team list
```

The installer creates an owned environment, installs CPU PyTorch and the `intelligence` extra, then runs real Needle and official Kev initialization. `--skip-download` installs dependencies without claiming inference readiness. The [quickstart](../QUICKSTART.md) covers Windows, alternate installation locations and chat-provider setup. A separate chat provider remains necessary; the example commands below assume Ollama is running and `qwen3:8b` has been installed and configured.

For MCP, run `python -m pip install ".[mcp]"` from the source checkout in the activated environment. MCP uses the official Python SDK, version 2.3 or later within major version 2. Team planning and public profiles work without the MCP extra. Cloud inference and connected services may incur provider charges.

## Specialists and task graphs

| Profile | Work | Workspace access through Hydra tools |
|---|---|---|
| `hydra_bot` | Compile the goal into scope, a bounded brief and observable acceptance criteria | Read |
| `grok_bot` | Inspect actual source, trace the cause and hand over evidence | Read; bounded HTTP GET for explicit source retrieval |
| `seam_bot` | Repair the shared cause, inspect sibling consumers and report edit/check receipts | Write, subject to policy |
| `vision` | Outcome, audience, acceptance criteria and supplied image evidence | Read |
| `design_taste` | Visual direction, hierarchy, accessibility and review criteria | Read |
| `software_engineer` | Implementation and relevant checks | Write, subject to policy |
| `test_engineer` | Real regression tests and failure evidence | Write, subject to policy |
| `code_reviewer` | Independent inspection of artifacts and evidence | Read |
| `kaizen` | PDCA: observed defect, small improvement, measured result, guard | Read |
| `lean_six_sigma` | DMAIC: define, measure, analyze, improve, control | Read |

```bash
hydra team show seam_bot
hydra team plan "Repair the checkout flow and inspect the result" --provider ollama --model qwen3:8b --output team.json
hydra team run team.json --root . --approval-policy allow
```

The generated plan orders **Hydra Bot → Grok Bot → Seam Bot → test engineer → code reviewer**. The original seven roles remain available for custom plans. Grok Bot is a code-understanding role running on your configured provider; the name does not install or select xAI Grok. Inspect the plan before executing it: `allow` authorizes the writer roles to make changes and run commands.

Each public profile has its own canon, essence and selected library. The package includes actual BACKS playbook text, including invariant-floor, intent-compiler, root-cause-first, seam-engineering and sniper-testing, with source revisions and SHA-256 hashes. These files are checked before being loaded into the worker prompt. `hydra team list`, `hydra team show ROLE` and worker receipts expose the selected library metadata. The playbooks guide reasoning; runtime tools, workspace limits and the current user instruction determine what a worker can do.

Set per-task `provider`, `model`, `family` and `prompt` to specialize routing. Provider settings use `hydra setup`. Only explicitly configured fallbacks are eligible. The generated plan uses one model for convenience and does not require independent model families. Enable `require_independent_review` when you have configured a distinct reviewer.

### A plan with concrete file acceptance

Save the following as `artifact-team.json`. It asks for a tiny, deliberately exact output so the runtime can check real bytes. The SHA-256 value is for the six bytes `ready\n`. Start with no `status.txt`, or with different content: `must_change` intentionally rejects an unchanged file. Both example models must be installed; choose equivalent configured routes if these do not fit your hardware.

```json
{
  "schema": "hydra.team.v1",
  "goal": "Create status.txt containing exactly ready followed by one newline, then inspect the file.",
  "provider": "ollama",
  "model": "qwen3:8b",
  "family": "qwen",
  "max_concurrency": 2,
  "max_iterations": 8,
  "timeout_seconds": 300,
  "require_independent_review": true,
  "tasks": [
    {
      "id": "build",
      "specialist": "seam_bot",
      "prompt": "Write status.txt with the exact requested content. Report the file operation.",
      "acceptance": {
        "artifacts": [{
          "path": "status.txt",
          "sha256": "ed1a545bb85e55816bbf9566b028b2a0bc456b88f49f6f266c0401048824194b",
          "must_change": true
        }]
      }
    },
    {
      "id": "review",
      "specialist": "code_reviewer",
      "depends_on": ["build"],
      "provider": "ollama",
      "model": "llama3.1:8b",
      "family": "llama",
      "prompt": "Read status.txt, check the exact content and report any discrepancy.",
      "acceptance": {
        "artifacts": [{
          "path": "status.txt",
          "sha256": "ed1a545bb85e55816bbf9566b028b2a0bc456b88f49f6f266c0401048824194b"
        }]
      }
    }
  ]
}
```

Run it from the workspace:

```bash
hydra team run artifact-team.json --root . --approval-policy allow
```

An artifact condition can require existence, an exact `sha256`, and/or `must_change` relative to the worker's initial snapshot. Paths must be relative to the workspace; linked paths and traversal are rejected. A task may declare up to 16 artifacts, each at most 16 MiB. Choose conditions that establish the result you actually need: existence alone gives little assurance. Missing, mismatched or unchanged required artifacts fail the task even when the model says it succeeded.

Work and review roles also require an actual successful task observation, or satisfied artifact conditions. Selector proposals, skill listing and memory bookkeeping do not count as task observations. A prose-only answer without runtime evidence cannot complete these roles. Successful file reads and command exits establish those operations; they do not establish that the goal is fully solved.

### Independent review and scheduling

With `require_independent_review: true`, a reviewer must depend on every writer, including Seam Bot and the test engineer. Its effective route must differ from every writer's provider/model identity and endpoint/account/model identity, and its declared family must differ. The check uses the route actually selected, so a fallback can invalidate independence. Missing, failed or colliding review produces a RED report and a nonzero CLI exit.

Family names are operator declarations, not attested model identities. Changing a label cannot prove different model weights. The sample uses different installed models and families; independently inspect the review findings and file evidence as well.

All graphs are validated before dispatch. Up to 64 tasks, 16 concurrent readers, 100 iterations per task and 3,600 seconds per task are supported; use smaller limits for normal work. Every writer must be ordered relative to every other task. Failed prerequisites block dependents. Explicit tool errors or nonzero command exits make the task incomplete even if the model returns a fluent answer. Expected failing tests therefore need their own consciously designed workflow; they are not silently treated as success.

### Receipts, memory and native clients

Each worker receives its public canon, essence and library, optional private overlay, assigned work and bounded prerequisite observations. Reports record model selection, tool outcome flags and hashes, artifact conditions, library provenance and memory receipts under `~/.hydraAgent/team-runs/` by default. `--output-root` selects another local directory. Reports and model responses can contain private task information; do not publish them as public profiles. `verified` remains false: a GREEN execution report is not a software-quality certification.

`ask`, `chat` and teams share `HYDRA_MEMORY_ROOT`, defaulting to `~/.hydra-memory`. `team run --memory-root /path/to/store` selects another store. Workers recall relevant historical evidence for their actual workspace, then record observed successful and failed tool results with bounded redacted arguments and hashes. Final model narration does not become a learned fact. Records retain `quality_verified: false`; memory warnings are reported separately from task outcomes.

```bash
hydra local-memory --workspace-root . --query "status.txt"
hydra local-memory --workspace-root . --query "status.txt" --graph
```

The evidence graph links recorded tasks to tools and referenced artifacts. Retrieval can use those links to include related episodes; it does not infer that a past action caused an improvement. Semantic retrieval falls back visibly to bounded lexical search when unavailable. Recheck current source before relying on history. Neural weight training is a separate [dataset, fine-tuning and evaluation workflow](TRAINING.md).

For vision, add `"images": ["screenshots/page.png"]` to a task. PNG/JPEG files must be within the workspace, at most four images and 5 MiB per image. Use a multimodal OpenAI-compatible HTTP model. This passes real image inputs; it does not take screenshots or grant a text-only model vision.

Codex CLI uses its own native tools and filesystem sandbox, so it is reported separately from Hydra tool dispatch. Hydra does not receive the native client's tool-event receipts. Native work and review roles therefore need explicit `acceptance.artifacts`; use hashes or required changes to make that evidence meaningful. A native client's zero exit and final answer alone cannot satisfy the observation gate. Hydra's MCP capability filtering applies to Hydra-dispatched tools, not arbitrary native client integrations. Image attachments in this worker require the HTTP path. A native filesystem sandbox also does not establish limits on external service side effects.

Process cleanup covers ordinary descendants, nested tool processes and inherited output pipes. It is not an OS sandbox against deliberate daemonization or malicious process escape; run untrusted code in an external container or VM. Retained stdout/stderr is bounded to 64 KiB per stream, and success-pattern matching uses that retained prefix.

## MCP client and server

Serve read-only Hydra tools over stdio:

```bash
hydra mcp serve --root /path/to/workspace
```

Configure an MCP host to launch the installed `hydra` executable with those arguments. Use `--approval-policy allow` only when the host is authorized to invoke writing and execution tools. The stdio server exposes no unauthenticated network listener.

Create a local client configuration:

```json
{
  "servers": {
    "workspace": {
      "command": "hydra",
      "args": ["mcp", "serve", "--root", "/path/to/workspace"],
      "read_only_tools": ["fs_read", "list_directory", "hydra_specialists"],
      "timeout": 30
    },
    "remote": {
      "url_env": "MY_MCP_URL",
      "bearer_token_env": "MY_MCP_TOKEN",
      "read_only_tools": [],
      "timeout": 30
    }
  }
}
```

Use an absolute executable path where PATH differs between applications. On Windows, invoke Node scripts with `node.exe` and their absolute `.mjs` path rather than a shell command string. Subprocess `env` maps child variable names to existing parent environment-variable names; it does not contain literal credentials.

```bash
hydra mcp tools workspace --config mcp.json
hydra mcp call workspace fs_read --arguments '{"path":"README.md"}' --config mcp.json --approval-policy deny
```

The example JSON quoting is for POSIX shells; use your shell's native quoting or an MCP host on Windows. Set `HYDRA_MCP_CONFIG` to the absolute configuration path to make `mcp_servers`, `mcp_tools` and `mcp_call` available to `hydra ask` and team workers.

The client supports stdio and Streamable HTTP. Remote endpoints require HTTPS; loopback HTTP is allowed. Tools are schema-validated. Only the operator's explicit `read_only_tools` list grants read-only classification; remote annotations do not. Other calls go through action policy, and read-only specialist roles reject them even if an operator override is enabled. Servers are trusted executable/network dependencies: discovery itself may start a process. Configure only servers you intend to run.

Connections currently open per discovery/call, with finite deadlines, pagination limits and a 1 MiB application result limit. That limit is checked after SDK parsing, not a transport-level memory quota. Avoid untrusted or unbounded servers. Production connection pooling, authenticated Hydra HTTP hosting and automatic OAuth enrollment are not implemented here.

## Official Zapier SDK harness

Requires a Node runtime compatible with the official SDK (Zapier recommends Node 20+).

```bash
hydra zapier setup --directory ./my-zapier-harness
cd my-zapier-harness
npm install --ignore-scripts
npm run zapier -- login
```

This prepares `@zapier/zapier-sdk@0.115.1` and `@zapier/zapier-sdk-cli@0.86.7`, plus a shell-free stdio MCP configuration. It never copies SDK source into Hydra, creates an account, or executes an app action. Set `HYDRA_MCP_CONFIG` to the generated `mcp.json`, then inspect discovery with `hydra mcp tools zapier --config /path/to/mcp.json`. Keep `read_only_tools` empty until you have reviewed the specific tools; generic action runners may write even when given a read-sounding action name.

Connect your own services through Zapier's account flow before requesting actions. The public [SDK documentation](https://docs.zapier.com/sdk) describes a free open beta, not a perpetual unlimited entitlement. [Hosted Zapier MCP](https://docs.zapier.com/mcp/home) has separate plan/task accounting, and connected services may charge. The documentation repository's MIT license does not relicense the SDK packages: the packages incorporate Zapier's terms. Hydra's own runtime and local MCP path remain open source and usable independently.

## Private knowledge and public editions

Private knowledge is optional and explicit:

```bash
hydra team run team.json --root . --private-profiles /path/outside/repository/private-profiles --approval-policy allow
```

That directory contains selected role files such as `kaizen.json`:

```json
{"canon":"Your private operating knowledge","essence":"Your private working principles"}
```

Only `canon` and `essence` are accepted. Missing role files retain their public profile; a missing explicitly selected directory fails. Keep the directory outside the source repository and installed package. The contents enter the selected model's prompt, so choose local inference when that knowledge must stay on your machine. This is prompt grounding, not model-weight training.

Public profiles are separately authored generic text in `hydra/runtime_data/specialists/`. Original private canon is not included. Prepare a deliberate public edition, review ownership and license requirements, then export selected roles:

```bash
hydra public-profiles --source hydra/runtime_data/specialists --output ./public-profiles --roles hydra_bot grok_bot seam_bot test_engineer code_reviewer
```

Run this from the source checkout. Any of the ten public role IDs can be selected. The destination must be new. Inputs must declare `edition: generic-public` with an ID, canon, essence, license and credits; an optional `library` list holds playbook IDs. Profile export preserves those references and does not copy private knowledge or a memory store. Runtime library loading checks that the referenced content is bundled and matches its hash. Decoded text is scanned for common credentials, email addresses, IP addresses, private home paths and optional operator literals from `--private-terms-file` (a local JSON string list). Linked paths are rejected. A deterministic hash manifest contains public filenames only. Findings report categories, not matched secrets.

This is a publication guard, **not an automatic IP/PII anonymizer**. It cannot infer every person's name, business secret or proprietary technique. It blocks detected material rather than silently deleting context. Public release still requires semantic review; private data must never be made public by changing an edition label. Human and upstream skill authors retain attribution; Codex and Claude/Anthropic are tools, not project coauthors.

## Readiness

The repository includes focused tests using real CLI processes, workers and filesystems with scripted external model transports. They exercise text-only completion rejection, artifact hashes and required changes, required-review failure, writer ordering, native-client artifact requirements and library provenance. Other runtime tests cover MCP round trips, file scope, startup deadlines, bounded output capture, nested-process timeout cleanup, specialist handoffs, failed prerequisites and publication guards. These tests establish particular runtime behaviors; scripted model answers do not measure intelligence.

`hydra init` provides a separate measured check that Needle and Kev load and complete bounded local probes. It does not benchmark coding quality or validate every provider. Representative tasks, review quality, live app actions, long-running recovery and production multi-user operation still require evaluation in the intended environment. The host command path also needs external confinement when running untrusted code. See the [quickstart](../QUICKSTART.md) and [training guide](TRAINING.md) for installation and evaluation boundaries.
