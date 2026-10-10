# Quickstart

Install Hydra, connect a model, and run your first task.

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
