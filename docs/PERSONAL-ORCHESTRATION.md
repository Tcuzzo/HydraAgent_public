# Personal orchestration preview

Hydra coordinates bounded specialist tasks, connects explicitly configured MCP tools, and records what ran. The runtime is model configurable; effectiveness depends on the chosen model's tool use, reasoning and vision capabilities. It does not automatically select or benchmark the "strongest" model.

## Install

From a clone in a virtual environment:

```bash
python -m pip install -e ".[mcp]"
hydra team list
```

MCP uses the official Python SDK, version 2.3 or later within major version 2. Team planning and public profiles work without the MCP extra. Local inference requires a separately installed model server and suitable hardware. Cloud inference may incur provider charges.

## Specialists and task graphs

| Profile | Work | Workspace access through Hydra tools |
|---|---|---|
| `vision` | Outcome, audience, acceptance criteria and supplied image evidence | Read |
| `design_taste` | Visual direction, hierarchy, accessibility and review criteria | Read |
| `software_engineer` | Implementation and relevant checks | Write, subject to policy |
| `test_engineer` | Real regression tests and failure evidence | Write, subject to policy |
| `code_reviewer` | Independent inspection of artifacts and evidence | Read |
| `kaizen` | PDCA: observed defect, small improvement, measured result, guard | Read |
| `lean_six_sigma` | DMAIC: define, measure, analyze, improve, control | Read |

```bash
hydra team show kaizen
hydra team plan "Repair the checkout flow and inspect the result" --provider ollama --model YOUR_MODEL --output team.json
hydra team run team.json --root . --approval-policy allow
```

The generated plan uses seven ordered tasks with Kaizen and Lean Six Sigma sharing the final read-only phase. Reduce the plan to the roles needed for a small task. Set per-task `provider`, `model`, `family` and `prompt` to specialize routing. Provider settings use the existing `hydra setup` configuration; there is no hidden paid fallback.

```json
{
  "schema": "hydra.team.v1",
  "goal": "Implement the requested change, then inspect it",
  "provider": "ollama",
  "model": "YOUR_BUILDER_MODEL",
  "family": "YOUR_BUILDER_FAMILY",
  "max_concurrency": 2,
  "max_iterations": 8,
  "timeout_seconds": 300,
  "require_independent_review": true,
  "tasks": [
    {"id": "build", "specialist": "software_engineer"},
    {"id": "review", "specialist": "code_reviewer", "depends_on": ["build"],
     "provider": "YOUR_REVIEW_PROVIDER", "model": "YOUR_REVIEW_MODEL", "family": "YOUR_REVIEW_FAMILY"}
  ]
}
```

Family names are operator declarations, not attested model identities. Independent review requires distinct declarations and a reviewer dependent on every builder. It cannot certify independence from an arbitrary endpoint's model label.

All graphs are validated before dispatch. Up to 64 tasks, 16 concurrent readers, 100 iterations per task and 3,600 seconds per task are supported; use smaller limits for normal work. Every writer must be ordered relative to every other task. Failed prerequisites block dependents. Explicit tool errors or nonzero command exits make the task incomplete even if the model returns a fluent answer. Expected failing tests therefore need their own consciously designed workflow; they are not silently treated as success.

Each worker gets its public canon and essence, optional private overlay, assigned work and bounded prerequisite observations. Reports record tool outcome flags and hashes, model selection and completion status under `~/.hydraAgent/team-runs/` by default. `--output-root` selects another local directory. Reports and model responses can contain private task information; do not publish them as public profiles. `verified` remains false: inspect the artifacts and relevant checks before accepting the result.

For vision, add `"images": ["screenshots/page.png"]` to a task. PNG/JPEG files must be within the workspace, at most four images and 5 MiB per image. Use a multimodal OpenAI-compatible HTTP model. This passes real image inputs; it does not take screenshots or grant a text-only model vision.

Codex CLI uses its own native tools and filesystem sandbox, so it is reported separately from Hydra tool dispatch. Hydra's MCP capability filtering applies to Hydra-dispatched tools, not arbitrary native client integrations. Image attachments in this worker require the HTTP path. Do not treat a native client's filesystem sandbox as a guarantee about external service side effects.

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
hydra public-profiles --source hydra/runtime_data/specialists --output ./public-profiles --roles vision design_taste software_engineer code_reviewer test_engineer kaizen lean_six_sigma
```

The destination must be new. Inputs must declare `edition: generic-public` with an ID, canon, essence, license and credits. Decoded text is scanned for common credentials, email addresses, IP addresses, private home paths and optional operator literals from `--private-terms-file` (a local JSON string list). Linked paths are rejected. A deterministic hash manifest contains public filenames only. Findings report categories, not matched secrets.

This is a publication guard, **not an automatic IP/PII anonymizer**. It cannot infer every person's name, business secret or proprietary technique. It blocks detected material rather than silently deleting context. Public release still requires semantic review; private data must never be made public by changing an edition label. Human and upstream skill authors retain attribution; Codex and Claude/Anthropic are tools, not project coauthors.

## Readiness

Covered by real subprocess, filesystem, scripted-model transport and MCP round-trip tests: discovery, file scope, schema errors, startup deadlines, bounded output capture, nested-process timeout cleanup, specialist handoffs, failed prerequisite blocking and publication guards. Scripted model transport proves runtime behavior, not model intelligence. Live app actions, representative task benchmarks, long-running recovery, native-client integration isolation and production multi-user operation require further validation before calling Hydra a polished unattended personal agent.
