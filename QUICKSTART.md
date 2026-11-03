# Quickstart

Install Hydra, connect a model, and run your first task.

## What you need

**Required.** Python 3.11 or newer, and the install command below:

```bash
pipx install git+https://github.com/Tcuzzo/HydraAgent_public.git
```

From a clone, use the install in [Develop from source](#develop-from-source):

```bash
python -m pip install -e ".[test]" -c constraints.txt
```

**Optional.** Each piece unlocks one capability and turns on the tests that need it. Skipped tests are expected when an optional piece is missing; failed tests are not. `python -m pytest -q -rs` prints each skip reason.

- Local role config. Copy the example and replace the placeholders:

  ```bash
  cp .hydraAgent/hydra.example.yaml .hydraAgent/hydra.yaml
  ```

  That file sets `provider`, `model`, and `family` per role, and can set a subagent model. It stays out of git. `hydra/test_model_config_ssot.py` checks `.hydraAgent/hydra.yaml` when the file is there, and skips with `.hydraAgent/hydra.yaml not present` when it is not.

- A reachable Ollama, plus sqlite-vec (`vec0`). Unlocks persistent vector memory. `pip install -e ".[memory]"` installs sqlite-vec; Linux x86-64 can also use the vendored extension. `hydra/test_unified_memory.py` skips the live embedding tests when Ollama is unreachable, and skips the vec0 tests when the extension cannot load.

- Linux Bubblewrap (`bwrap`). Unlocks confined worker verification (`sudo apt-get install bubblewrap` on Ubuntu). `hydra/test_sandbox_required.py` skips the live isolation tests with `positive isolation requires usable Linux bwrap; refusal is independently tested` until `bwrap` works.

- The `mcp` package (`pip install -e ".[mcp]"`). Unlocks the MCP server and client. `hydra/test_mcp_runtime.py` skips with `could not import 'mcp'` until that package imports.

- `fcntl.flock` (POSIX). Unlocks the GPU-busy lock-holder tests in `hydra/test_gpu_busy_cloud_fallback.py`. They skip when `fcntl` cannot be imported.

- Symlink and hard-link support from the OS. `hydra/test_fullstack_skill_integration.py` skips when symlink creation fails. `hydra/test_runtime_tool_safety.py` skips when hard-link creation fails.

How to turn the optional product capabilities on, and how to check them, is in [Optional capabilities and how to enable them](#optional-capabilities-and-how-to-enable-them).

## 1. Install

```bash
pipx install git+https://github.com/Tcuzzo/HydraAgent_public.git
```

No API keys ship with Hydra.

## 2. Choose a model

```bash
hydra
```

Bare `hydra` opens the chat surface; on a fresh install it shows the
**connect a model** panel right there. (`hydra setup` walks the same choices
from the command line.)

Pick one path:

- Local and free: install Ollama, pull a model, and let Hydra use it.
- Cloud: enter your own provider key when prompted.
- Sign in with ChatGPT: use your ChatGPT account via the Codex CLI — no API key.

## 3. Ask Hydra to do something

```bash
hydra ask "summarize this folder" --approval-policy deny
```

Run it from the folder you want Hydra to see. It reads, searches, and edits files within that folder, and (with your approval) can run shell commands on your machine.

## Safety

Risky tools ask first; in scripts or CI they are blocked unless you opt in with `--approval-policy allow`.

## Optional capabilities and how to enable them

None of these are required for `hydra ask`. Names such as `<your-model>` and `<needle-url>` are placeholders. Replace them with values from your own machine, and keep the quotes in the shell examples so the angle brackets are not read as redirection. Do not commit those files.

### Local models (Ollama)

A local chat and coding server. The built-in provider is `ollama` (alias `local`). With no key, the endpoint is `http://localhost:11434` unless you change it (`hydra/providers.py`).

Turn it on:

```bash
hydra setup --mode local --model '<your-model>' --non-interactive
```

That writes `OLLAMA_ENDPOINT` and, when `--model` is set, `OLLAMA_MODEL` to `~/.hydraAgent/workspace/.env.ollama`. Leave the model empty and the rung resolver chooses one. The same keys can be exported in the process. `OLLAMA_BASE_URL` is accepted as an alias of `OLLAMA_ENDPOINT`. `OLLAMA_NUM_CTX` is the context window a keyless client requests. This setup path does not take an API key.

Check:

```bash
hydra models --provider ollama
hydra ask "Explain this project's entry point" --provider ollama --model '<your-model>' --approval-policy deny
```

### Needle / Kev decision service and a fleet ladder

Two separate pieces.

**Local Needle** selects tools. Hydra still executes them. `hydra/needle_client.py` takes a model string and does not read an environment variable. The `needle` provider passes `NEEDLE_MODEL` from the process environment or from `~/.hydraAgent/workspace/.env.needle`. If that variable is unset, the provider uses the fallback name compiled in `hydra/providers.py`.

```bash
python -m pip install -e ".[needle]"
hydra ask "Read README.md" --provider needle --model '<your-needle-model>' --approval-policy deny
```

A pipx install uses `pipx install --force "hydraagent[needle] @ git+https://github.com/Tcuzzo/HydraAgent_public.git"`. If the extra is missing, the client raises `Needle is optional; install hydraagent[needle] to use provider 'needle'`. Needle does not write a conversational final answer. The selector tool reads `HYDRA_NEEDLE_MODEL` (`hydra/decision_tools.py`). Typed decisions are a different extra, `hydraagent[laya]`, configured with `HYDRA_LAYA_MODEL` and `HYDRA_LAYA_DEVICE` (`hydra/decision_models.py`). See [Models](docs/MODELS.md).

**The decision service** resolves the rung names in `hydra/model_routing.yaml` at call time. `hydra/rung_resolver.py` reads the base URL and does not store a model list. `hydra setup` has no `backs` mode. `hydra/cli/cmd_setup.py` accepts `--mode` `interactive`, `local`, `cloud`, or `codex` only, and `hydra/setup.py` does not write this URL. Any other mode is rejected by that choice list.

Set the origin in the process environment. This wins over the file:

```bash
export HYDRA_BACKS_URL='http://<backs-host>:<port>'
```

If `HYDRA_BACKS_URL` is empty, `backs_url()` reads `BACKS_ENDPOINT` from `~/.hydraAgent/workspace/.env.backs`. The routing loader calls the resolver without a directory argument, so the ladder uses that file. A caller that passes `env_dir` reads `.env.backs` in that directory. `hydra/providers.py` also accepts `BACKS_ENDPOINT` from the process environment when it builds a `backs` client. `backs_url()` reads that key only from the file:

```
BACKS_ENDPOINT=http://<backs-host>:<port>
```

`http://<backs-host>:<port>` is a placeholder for the origin only, with no path. Keep the quotes in the shell so the angle brackets are not redirection. The resolver appends:

- `GET /api/system/needle_engineer_rungs` for the fleet ladder. A local GPU rung uses the row whose `identity` matches the rung and reads that row's `model`.
- `POST /api/router/resolve`, `POST /api/router/complete`, and `POST /api/router/outcome` for cloud rungs (`backs.router.v1`).
- `GET /v1/models` when it needs a capability id for a rung.

If neither value is set, `backs_url()` raises `RungResolutionError` with `BACKS rung resolver location is not set`. Check:

```bash
python -c "from hydra.rung_resolver import backs_url; print(backs_url())"
```

That prints the origin, or that error. A cloud turn prints a route line on stderr from `RungGatewayClient`, starting with `hydra: rung`. The endpoint contract is [docs/BACKS_ROUTER_CONTRACT.md](docs/BACKS_ROUTER_CONTRACT.md).

`skills/backs-aios-skills-pub/fleet-ladder/ladder.example.yaml` shows one ladder shape. `needle_client.py`, `rung_resolver.py`, and `model_router.py` do not open that file. The live ladder is the JSON from the GET above. `ModelRouter` also reads `HYDRA_GPU_LOCK` when it checks whether the local GPU is busy. That value is a lock-file path.

**Role file.** `hydra/model_router.py` loads `provider`, `model`, `family`, and optional `base_url`, `api_key_env`, and `fallbacks` from a YAML file. With no path argument it reads `$HYDRA_CONFIG` if set, otherwise `~/.hydra/hydra.yaml`. Copy [`.hydraAgent/hydra.example.yaml`](.hydraAgent/hydra.example.yaml) and replace every placeholder:

```bash
cp .hydraAgent/hydra.example.yaml .hydraAgent/hydra.yaml
```

The example file itself is not loaded. In a source checkout the constant `HYDRA_CONFIG` in `hydra/cli/cmd_runtime.py`, `hydra/cli/cmd_execution.py`, and `hydra/cli/cmd_chat.py` points at `.hydraAgent/hydra.yaml` next to the repository root. `hydra roles` and `hydra execute` pass that path to `resolve_roles`. `hydra ask` does too, only when `--auto-route` is set. `subagents.spawn_model` is read from `.hydraAgent/hydra.yaml` in the current working directory (`hydra/cli/tool_binding.py`).

**When `hydra.yaml` is missing.** The readers do not share one fallback.

- `ModelRouter`, when its path does not exist, or when `agentic.roles` is empty, loads the rung pairs shipped in `hydra/model_routing.yaml`. An empty or comment-only file logs a warning and uses those same pairs. That is the built-in default.
- `resolve_roles` raises. A missing `hydra.yaml` makes `core.config.load` raise `ConfigError` (`config file not found`). `resolve_roles` then raises `RoleError`: `role config invalid: <path>: config file not found`. `hydra roles` and `hydra execute` print `role error: ...` and exit 2.
- The subagent string, when `./.hydraAgent/hydra.yaml` is absent or `subagents.spawn_model` is unset, uses the planner pair from `hydra/model_routing.yaml`, formatted as `provider/model`.
- `hydra/test_model_config_ssot.py` skips with `.hydraAgent/hydra.yaml not present` when the live file is absent. That skip is the test suite. The CLI reports the missing file as the role error above.

**Gateway 401, 402, and 403.** Hydra does not send a key on these calls. A refusal is a `RungResolutionError` (a retryable `LlmError`). Routing then tries the next rung (`hydra/model_routing.py`). The text depends on which call failed:

- `POST /api/router/resolve` with any status other than 200 or 404, including 401, 402, and 403: `BACKS router refused task '<task>': HTTP <status>` plus the first 200 characters of the body. A 404 is not this error. The resolver logs that the router is not deployed and tries `GET /v1/models`.
- `POST /api/router/complete` with a non-200 status: `BACKS router answered nothing for task '<task>':` plus the JSON `message` or `error`, or `HTTP <status>` when neither is set. HTTP 503 also marks the service down for 60 seconds. 401, 402, and 403 do not.
- A chat or catalog read through `RungGatewayClient._read_json`: `HTTP <code> from <url>:` plus the first 300 characters of the body.
- `GET` for the ladder or `/v1/models`: `BACKS rung resolver at <url> failed:` plus the HTTP error. An HTTP status does not mark the host down. A connection failure does, for 60 seconds.
- `POST /api/router/outcome` does not raise. A non-200 is a warning, `router outcome not recorded: HTTP <status>`, and the report returns false.

`OllamaClient._read_json` (not the gateway override) classifies the status in `hydra/provider_health.http_failure`. 401 is kind `authentication`. 403 is kind `permission`. The message is `HTTP <status>: <body>`. A 402 is kind `budget` when the body matches a credit or budget phrase, kind `rate_limit` when the error metadata names an in-flight budget limit, and otherwise kind `request`. The gateway override does not call that classifier, so its `RungResolutionError` keeps kind `unknown`. `classify_provider_error` then treats a message that contains `401` or `403` as `auth`. A 402 stays `other` unless the message also says unauthorized, forbidden, or authentication. If the chat health wrapper sees budget or retirement phrases in that text, it promotes the kind to `budget` or `retired`.

### Sandbox (Bubblewrap)

Worker verification runs commands through `hydra.exec_backend.run_sandboxed`. On Linux it uses `bwrap` when `bwrap` is on `PATH` and the probe succeeds: unprivileged user namespaces are allowed, and a no-op sandbox run works. There is no environment variable that enables Bubblewrap. If confinement is unavailable, the command is not executed. The result is return code 126 and the text `bwrap sandbox unavailable; command not executed.`

`HYDRA_EXEC_SANDBOX` is a different switch. It opts the shell tool into a container image (`skills/bash.py` and `hydra/container_sandbox.py`). It is off unless set, and it is not Bubblewrap.

Turn Bubblewrap on (Ubuntu):

```bash
sudo apt-get install bubblewrap
```

Native Windows and macOS do not provide this confinement. Ordinary local CLI tools still run there.

Check:

```bash
bwrap --version
python -c "from pathlib import Path; from hydra.exec_backend import run_sandboxed; r = run_sandboxed(['/bin/true'], workspace=Path('.'), timeout=10); print(r.sandboxed, r.returncode)"
```

`True 0` means the command ran inside Bubblewrap. `False 126` means it was refused. On a host where `/bin/true` is absent, the probe inside Hydra tries `/usr/bin/true`.

### MCP

Serve Hydra tools over stdio, or call servers you list. The server is read-only unless you pass `--approval-policy allow`. `hydra ask` attaches MCP tools only when `HYDRA_MCP_CONFIG` is set (`hydra/cli/tool_binding.py`).

Turn it on:

```bash
python -m pip install -e ".[mcp]"
```

The extra is `mcp>=2.3.0,<3`. A missing import fails when `hydra mcp serve` loads `hydra/mcp_server.py`.

```bash
hydra mcp serve --root .
```

A client file must be a JSON object whose only key is `servers`. Each server chooses one of `command`, `url`, or `url_env`. Remote `url` values must be `https`, except `http` on `localhost`, `127.0.0.1`, or `::1`. `read_only_tools` is your list of tool names. `timeout` is optional and defaults to 30 seconds, maximum 300. The fields `hydra/mcp_bridge.py` accepts are `command`, `args`, `env`, `url`, `url_env`, `bearer_token_env`, `read_only_tools`, and `timeout`.

```json
{
  "servers": {
    "workspace": {
      "command": "hydra",
      "args": ["mcp", "serve", "--root", "<your-project>"],
      "read_only_tools": ["fs_read", "list_directory"]
    }
  }
}
```

Check:

```bash
python -c "import mcp; print('mcp ok')"
hydra mcp tools workspace --config '<path-to-mcp.json>'
```

Set `HYDRA_MCP_CONFIG` to that absolute path before `hydra ask` if the turn should see MCP tools. More host and Zapier steps are in [Personal orchestration](docs/PERSONAL-ORCHESTRATION.md).

### Memory embeddings

Vector recall next to keyword search. The index is SQLite. The optional `vec0` extension is sqlite-vec. Embeddings are produced by `hydra/semantic_recall.py`, which delegates to `hydra/embeddings.py` when you set a config file.

Turn the extension on:

```bash
python -m pip install -e ".[memory]"
```

Linux x86-64 can also load the vendored extension. `HYDRA_VEC0_PATH` overrides the extension path. The database file defaults under `~/.hydra-memory/`.

With `HYDRA_EMBEDDING_CONFIG` unset, memory posts to `http://localhost:11434/api/embeddings` and sends the model id compiled in `embed_nomic` (`nomic-embed-text`). That id is the code fallback, not a required model. Pull it only if you leave the config unset. To choose the model yourself, point `HYDRA_EMBEDDING_CONFIG` at a JSON file. `backend` must be `ollama`, `openai`, or `openvino`. `model` and `revision` are required. `endpoint` is required unless `backend` is `openvino`. An `ollama` endpoint is called at `<endpoint>/api/embeddings`. An `openai` endpoint is called at `<endpoint>/embeddings`. Remote endpoints must be `https`. `http` is accepted only for `localhost`, `127.0.0.1`, and `::1`. `api_key_env` names a variable that holds the bearer token. It is not the token.

```json
{
  "backend": "ollama",
  "endpoint": "http://127.0.0.1:11434",
  "model": "<your-embedding-model>",
  "revision": "<your-weights-revision>"
}
```

For OpenVINO, install `hydraagent[igpu]`, set `"backend": "openvino"`, and set `device` (`CPU` when omitted). The adapter checks OpenVINO's device list and fails if the device is missing.

Source-map embeddings are the same config. `hydra index embed` raises `set HYDRA_EMBEDDING_CONFIG before embedding the source map` until the variable is set. `HYDRA_SOURCE_SEMANTIC=1` includes those matches in `source_lookup`.

Check:

```bash
python -c "import sqlite3; print(hasattr(sqlite3.Connection, 'enable_load_extension'))"
python -c "from hydra.semantic_recall import embed_nomic; print(len(embed_nomic('ping')))"
```

The first line must print `True` or vec0 cannot load. The second prints a vector length when the embedder answers. Chat prints `Hydra memory: vector lane OFF —` and the reason when the vector lane cannot start, and keyword recall still works.

## Develop from source

```bash
git clone https://github.com/Tcuzzo/HydraAgent_public.git
cd HydraAgent_public
python -m venv .venv
```

Activate it with `source .venv/bin/activate` on Linux/macOS or
`.venv\Scripts\Activate.ps1` in Windows PowerShell. Then:

```bash
python -m pip install -e ".[test]" -c constraints.txt
python -m hydra --help
python -m hydra tools
python -m pytest -q
```

Optional local models: `python -m pip install -e ".[needle,laya]"`.
Training: `python -m pip install -e ".[training]"` in a separate environment.
The pinned core constraints do not lock the optional ML dependency trees.
See [model configuration](docs/MODELS.md) and [training](docs/TRAINING.md).

## More

See the [command reference](docs/CLI-REFERENCE.md) for the complete CLI guide.
