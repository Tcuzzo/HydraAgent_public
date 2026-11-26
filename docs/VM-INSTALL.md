# Install and verify Hydra on a VM

Use Ubuntu 24.04 with Python 3.11 or 3.12. The automated installed-package job
uses a fresh Ubuntu 24.04 VM, builds a wheel, installs it in a new environment,
and runs the CLI from an empty workspace outside the checkout.

Hydra itself does not need a GPU. A model can run on the VM, on another machine
you configure, or through your chosen provider. Model memory and disk needs are
separate from the CLI installation. No model or account credentials ship with it.

## Clean installation

These commands assume a new checkout. Change the three paths or the Git ref at
the top if needed. The package installation is non-editable.

```bash
HYDRA_SOURCE="$HOME/src/HydraAgent_public"
HYDRA_VENV="$HOME/.local/share/hydra/venv"
HYDRA_RECEIPT="$HOME/.local/state/hydra/install-verification.json"
HYDRA_REF="main"

set -e
sudo apt-get update
sudo apt-get install -y git python3-venv bubblewrap
git clone --depth 1 --branch "$HYDRA_REF" https://github.com/Tcuzzo/HydraAgent_public.git "$HYDRA_SOURCE"
bash "$HYDRA_SOURCE/.github/scripts/setup-bwrap.sh"
export PATH="/usr/local/libexec/hydra-ci:$PATH"
python3 -m venv "$HYDRA_VENV"
"$HYDRA_VENV/bin/python" -m pip install -c "$HYDRA_SOURCE/constraints.txt" "$HYDRA_SOURCE"
"$HYDRA_VENV/bin/python" -m pip check
"$HYDRA_VENV/bin/python" "$HYDRA_SOURCE/tools/verify_install.py" \
  --require-sandbox --output "$HYDRA_RECEIPT"
source "$HYDRA_VENV/bin/activate"
hydra --version
```

The verifier launches real CLI processes and a temporary HTTP model fixture. It
checks installed package discovery, setup, both local model transports, a file
edit, a real verification command, memory across process restarts, undo, approval
denial, path confinement, and failure exit codes. The model replies are scripted;
this check proves the installation and execution path, not a model's reasoning.
It uses temporary workspace and state directories and does not need an API key.

The receipt separates application checks from OS sandbox availability. The clean
installation above requires positive Bubblewrap isolation. To repeat that check:

```bash
"$HYDRA_VENV/bin/python" "$HYDRA_SOURCE/tools/verify_install.py" \
  --require-sandbox --output "$HYDRA_RECEIPT"
```

That is the command CI runs. A host that cannot create network namespaces fails
this strict check. Hydra refuses offline worker verification on that host; it
does not silently run the requested verification on the host. Filesystem shell
confinement and network isolation are probed separately. The setup helper used
above also runs in CI. On Ubuntu hosts with AppArmor namespace restrictions it
uses `sudo` to install a scoped profile for the administrator-owned Bubblewrap
helper, while leaving global restrictions enabled. The `PATH` export selects
that helper in the current terminal; repeat it when opening a new terminal.

## Connect your model and complete a task

For an Ollama server already running on the VM:

```bash
HYDRA_MODEL='<your-installed-model>'
HYDRA_PROJECT="$HOME/hydra-first-project"

mkdir -p "$HYDRA_PROJECT"
hydra setup --mode local --model "$HYDRA_MODEL" --non-interactive
hydra models --provider ollama
hydra ask "Create hello.py that prints hello, then run it and report the output" \
  --provider ollama --root "$HYDRA_PROJECT" --approval-policy allow \
  --trace-out "$HYDRA_PROJECT/first-task.json"
```

`allow` explicitly authorizes this task's edits and commands. Use `deny` when you
only want to read and inspect. For a server elsewhere, add `--endpoint` to setup.
The [Quickstart](../QUICKSTART.md) covers provider keys and Codex sign-in.

Inspect both the produced file and the trace. A successful model response alone
does not establish that the requested change happened. The trace records tool
results, iteration counts, errors, and the reason the run stopped. Model quality
still affects whether it chooses the right tools and arguments.

### A tested CPU example

A file write/read/undo task completed with an installed wheel, Ollama 0.40.18,
and [qwen3:4b-instruct-2507-q4_K_M](https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M)
on a CPU-only Linux host. The first model response took about 200 seconds and
the complete ask took about 226 seconds. The example below allows 420 seconds
per model request and 900 seconds overall. This is one tested task; other
models, hardware, and tasks can change both timing and tool behavior.

`OLLAMA_NUM_CTX=12288` makes Hydra's keyless Ollama client request a 12,288-token
context window. The exported value takes precedence over the provider's
`.env.ollama` file. Add the same setting to that file to retain it across
terminals. A larger context also increases model memory use.

With Ollama already running and the Hydra environment activated:

```bash
set -e
HYDRA_MODEL='qwen3:4b-instruct-2507-q4_K_M'
export OLLAMA_NUM_CTX=12288
ollama pull "$HYDRA_MODEL"
hydra setup --mode local --model "$HYDRA_MODEL" --non-interactive

HYDRA_PROOF_RUN="$(mktemp -d)"
HYDRA_PROOF_ROOT="$HYDRA_PROOF_RUN/workspace"
mkdir "$HYDRA_PROOF_ROOT"

timeout 900s hydra ask \
  "Use fs_write to write proof.txt in the current workspace with the single line hydra-live-model-ok followed by one newline. Then use fs_read to read proof.txt and verify its contents. Do not run shell commands. When both tools succeed, say LIVE_PROOF_OK. /no_think" \
  --provider ollama --model "$HYDRA_MODEL" --root "$HYDRA_PROOF_ROOT" \
  --memory-root "$HYDRA_PROOF_RUN/memory" --approval-policy allow \
  --max-iterations 4 --timeout 420 --trace-out "$HYDRA_PROOF_RUN/trace.json"

printf 'hydra-live-model-ok\n' | cmp - "$HYDRA_PROOF_ROOT/proof.txt"
hydra undo --root "$HYDRA_PROOF_ROOT" --list
hydra undo --root "$HYDRA_PROOF_ROOT"
test ! -e "$HYDRA_PROOF_ROOT/proof.txt"
hydra undo --root "$HYDRA_PROOF_ROOT" --list
```

The byte comparison must succeed. Inspect `trace.json` for successful `fs_write`
and `fs_read` results, a natural stop, and the final `LIVE_PROOF_OK` response.
For this single-write task, undo must remove `proof.txt` and the final checkpoint
list must be empty. The trace is saved outside the workspace and remains
available after undo.

## Restart, configuration, and recovery

Provider settings live under `~/.hydraAgent/workspace/`, or `HYDRA_ENV_DIR` when
set. They remain available after closing the terminal or restarting the VM.
Reopen a terminal and activate the environment before running `hydra` again.

`hydra roles` works without a private checkout configuration. Role selection uses
an explicit `--config` where supported, then `HYDRA_CONFIG`, the active workspace's
`.hydraAgent/hydra.yaml` or `hydra.yaml`, `~/.hydra/hydra.yaml`, and finally the
packaged routing defaults. A specified missing or invalid file fails visibly.
`execute --root` and `ask --root --auto-route` use that requested workspace.

Hydra's `fs_edit` and `fs_write` tools save undo snapshots before publication.
If the snapshot cannot be stored, the file change is refused. Undo restores exact
bytes for an edit or removes a file created by those tools:

```bash
hydra undo --root "$HYDRA_PROJECT" --list
hydra undo --root "$HYDRA_PROJECT"
```

Shell commands and native CLI providers have their own side effects; these are
outside Hydra's file snapshot stack. A native CLI completion is recorded
separately with `verification: not_run`. Hydra does not count that as one of its
own tool calls or repeat the task merely because its local tool count is zero.
