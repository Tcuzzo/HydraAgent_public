# Quickstart

Run Hydra in about a minute.

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
hydra ask "summarize this folder"
```

Run it from the folder you want Hydra to see. It reads, searches, and edits files within that folder, and (with your approval) can run shell commands on your machine.

## Safety

Risky tools ask first; in scripts or CI they are blocked unless you opt in with `--approval-policy allow`.

## More

See [README.md](README.md#command-reference) for the full command reference.
