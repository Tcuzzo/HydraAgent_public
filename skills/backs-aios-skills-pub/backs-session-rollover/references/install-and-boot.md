# Local installation, existing sessions, and startup

The standalone `install-backs-rollover.sh` embeds a pinned public source pack
and its Python installer. It does not fetch a mutable branch, install packages,
change provider credentials, start model calls, or require sudo. Use Bash and
Python 3.11 or later on Linux, macOS, or the Linux side of WSL. Run it as the
normal account that runs your coding tools.

## Run

```bash
bash install-backs-rollover.sh --all --boot auto --enable-linger
```

The default is installation with project discovery and automatic scheduler
selection. `--enable-linger` asks Linux loginctl to start the user manager at
boot before login; the operating system may require administrator
authorization. The report distinguishes boot loading from login loading.
macOS uses a LaunchAgent at user login, including login after a reboot.
No claim of a pre-login macOS LaunchDaemon is made.

Preview the same plan without changing configuration:

```bash
bash install-backs-rollover.sh --all --dry-run
```

Add a checkout or discovery root outside the standard locations:

```bash
bash install-backs-rollover.sh --project "$PWD" --scan-root "$HOME/work"
```

Project and scan-root registrations persist across reconciliation and later
installs. `--no-scan` limits work to explicitly registered projects and global
configuration; `--all` enables standard discovery again. A standard scan is
bounded to five directory levels and 20000 visited directories under common
project roots, and reports when that bound is reached. It does not read source
file contents to discover a project. Symlink directories are not traversed.

## What installation applies

| Surface | Applied behavior |
| --- | --- |
| Claude Code | Merge `env.CLAUDE_CODE_AUTO_COMPACT_WINDOW` into user and discovered existing project settings. Preserve valid stricter windows. |
| Codex | Merge `model_auto_compact_token_limit` at the TOML top level and reduce larger existing profile overrides. Preserve stricter limits, unrelated values and comments. |
| Global skills | Register missing skills for Claude, Codex, Cursor, OpenCode and portable `.agents` discovery. Preserve existing native skill bodies and foreign pointers. |
| Existing project skill roots | Add a portable rollover loader without putting a machine-specific absolute link into source control. |
| Session grounding | Add a bounded managed block to existing project instruction files and the applicable global Claude/Codex/OpenCode instruction files. Preserve precedence and surrounding text. |
| Existing Cursor project surface | Add or refresh an `alwaysApply` rule, preserving content outside its managed block. |
| Shell startup | Add a managed PATH/bootstrap block to applicable existing Bash/Zsh startup files and `.profile`. |
| OS startup | Reconcile at user-manager startup or login and every 15 minutes. Use Linux systemd user timers, macOS LaunchAgents, or cron when selected/available. |

Native configuration and instruction registration are distinct from enforced
fresh-session rollover. The installer reports the bundled controller's live
provider/store/writer integration as **UNBOUND**. It has no production adapter
for an unknown BACKS host and does not silently claim one exists.

The global source snapshot contains the complete public pack so relative
references remain valid. Existing native BACKS grounding remains authoritative.
The installer retargets only links it owns. Foreign directories, foreign links,
malformed settings, and unrelated commands with colliding names are preserved
and reported. An unresolved conflict produces a partial configuration result.

Existing project configuration and instruction files can be tracked by Git;
changes are left for normal review. The installer does not commit, stash,
reset, delete or restart the user's working tree or coder processes.

## Existing and remote sessions

Claude documents settings and hook hot reload, including reapplication of
settings environment values. Some features cache values at startup. Obtain an
effective `/autocompact` window receipt on the installed version before
claiming an already-running session adopted the native 125k setting. A large
already-running operation can overshoot a sampled scheduling boundary.

There is no blanket Codex config hot-reload claim. Its merged files apply
through the installed surface's supported configuration lifecycle. A fresh
session and the host controller remain the reliable transition route.

Updating configuration cannot restore instructions that an earlier session
already discarded. Preserve available original transcripts and task records,
then use the checkpoint protocol to reconstruct and verify the continuation.
The installer does not rewrite those historical transcripts or kill an active
task to manufacture a clean status.

Local configuration reaches only processes that read these local paths.
Run the installer separately on remote hosts, container images, or cloud
worker images that you control. Windows-native tools outside WSL and managed
organization settings are separate surfaces and are not overwritten.

## Daily commands

Open a new shell, or invoke the installed command by its complete path:

```bash
"$HOME/.local/bin/backs-rollover" status
"$HOME/.local/bin/backs-rollover" reconcile
"$HOME/.local/bin/backs-claude"
"$HOME/.local/bin/backs-codex"
```

The provider wrappers reconcile configuration, then run the existing provider
CLI with the original arguments and permissions. A configuration conflict is
shown without disabling the operator's CLI. These wrappers do not manage
provider session replacement; the real host must bind the rollover controller.

`status` shows the most recent reconciliation report and its timestamp, not a
new provider acceptance trace. `reconcile` checks the local files again.
Default private state is under `$HOME/.local/share/backs-rollover`; the
installer also respects `BACKS_ROLLOVER_HOME`, `XDG_DATA_HOME`, `CODEX_HOME`,
`CLAUDE_CONFIG_DIR` and `XDG_CONFIG_HOME` where relevant.

## Disable and restore

```bash
"$HOME/.local/bin/backs-rollover" disable-boot
"$HOME/.local/bin/backs-rollover" rollback --manifest "/path/from/the/install/report/manifest.json"
```

`disable-boot` stops the installer's named jobs and records a disabled startup
state. Native compaction configuration remains. The next normal install can
enable startup again.

Each changed file or link has a private transaction receipt with its original
bytes/mode or target. Rollback restores only a file/link that still matches
the installed version; later user changes remain and produce a conflict.
The command prints the concrete receipt path after installation. Restore
multiple transactions newest first if several reconciliations changed files.

Rollback stops the startup jobs and does not automatically re-enable a
previous scheduler configuration. Source snapshots and receipts are retained
for inspection. Run the installer again when ready to re-enable the policy.

## Build and validation

The repository script `scripts/rollover/build_installer.py` creates the single
Bash release from the tracked worktree plus the installer Python modules. Its
archive is checksummed, and extraction rejects absolute paths, path traversal,
links, duplicate entries, sparse members and non-regular files.

```bash
python3 scripts/rollover/build_installer.py --output install-backs-rollover.sh
python3 -m unittest discover -s scripts/rollover -p 'test_*.py' -v
python3 -m unittest discover -s skills/backs-session-rollover/scripts -p 'test_*.py' -v
```

These checks establish local installer and controller behavior. Complete the
separate real-provider acceptance sequence in runtime-integration.md before
reporting automatic fresh-session continuation as active.

Provider references:

- [Claude settings and reload](https://code.claude.com/docs/en/settings)
- [Claude auto-compaction window](https://code.claude.com/docs/en/model-config#context-window-and-auto-compaction)
- [Codex configuration](https://developers.openai.com/codex/config-reference/)
- [Codex hooks and trust](https://developers.openai.com/codex/hooks/)
- [Cursor skill locations](https://cursor.com/docs/skills)
- [OpenCode instruction precedence](https://opencode.ai/docs/rules/)
