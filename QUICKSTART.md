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
