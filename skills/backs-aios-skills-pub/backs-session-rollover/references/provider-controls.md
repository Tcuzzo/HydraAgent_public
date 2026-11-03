# Provider controls

Verified against official documentation on 2026-10-17. Inspect the installed
CLI/SDK capabilities again during integration. Preserve the operator's selected
model, authentication route, tool permissions, and existing configuration.
Configuration, an observed hook run, and a bound handoff controller are separate
installation results. A native compaction setting does not itself launch a
successor or resume the BACKS task.

## Claude Code

Merge this field into the applicable settings object, preserving other keys:

```json
{
  "env": {
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "125000"
  }
}
```

For a supported running interactive session, /autocompact 125k sets its compact
window. For a supported new launch, claude --autocompact 125000 sets it for that
launch. The environment value accepts a plain integer and overrides those
controls. The CLI flag requires v2.1.221 or later. Detect effective support;
do not assume that an unknown environment variable took effect.

Current Claude Code watches its user, project, local, and managed settings
files and reloads most changes, including hooks. Changes to settings.env are
reapplied to a running process. An external shell export reaches only later
launches; removing an env key from the file also requires a relaunch to unset
it. The docs exempt features that cache variables at startup, with telemetry
initialization as an example. They do not document a compaction-variable
exception or promise a per-dispatch reread. After a live install, use the
running session's /autocompact output to confirm the effective window or
environment override. /status confirms loaded sources, not each effective key.
Existing ConfigChange hooks can reject a live settings update.

Since v2.1.288, /autocompact saves the current model's window in modelSettings;
older versions save top-level autoCompactWindow. A settings-level value can be
overridden by a higher scope or a per-model entry. Inspect those existing
values rather than rewriting unrelated model configuration.

Subagents compact against their own context. On v2.1.296+, their optional
autoCompactWindow frontmatter can lower the inherited window, but a positive
CLAUDE_CODE_AUTO_COMPACT_WINDOW overrides that field. Observe each worker
separately; the coordinator's token count is not its workers' count.

The smaller compaction window does not change the status line's denominator:
used_percentage still refers to the model's full window. A native compaction
setting also does not create a new model session or a verified task handoff.

Sources: [model configuration](https://code.claude.com/docs/en/model-config#context-window-and-auto-compaction),
[environment variables](https://code.claude.com/docs/en/env-vars),
[CLI reference](https://code.claude.com/docs/en/cli-reference),
[settings reload and precedence](https://code.claude.com/docs/en/settings#when-edits-take-effect),
[ConfigChange](https://code.claude.com/docs/en/hooks#configchange),
[subagent compaction](https://code.claude.com/docs/en/sub-agents#auto-compaction).

### Active context telemetry

For status-line current_usage, add input_tokens, cache_creation_input_tokens,
and cache_read_input_tokens. Account for current output and pending additions
separately. Cached input still occupies context. A null current_usage is unknown,
including just after compaction; it is not zero. Status-line callbacks can be
cancelled or delayed, so enforce at the controller's synchronous dispatch boundary.

For the Agent SDK, read per-step assistant-message usage, deduplicate repeated
message IDs, and distinguish root work from subagents. Stream deltas supply
output growth; initial output usage may be incomplete. Result usage and broader
modelUsage are spend accounting, not a current-context meter.

Sources: [status line](https://code.claude.com/docs/en/statusline#context-window-fields),
[SDK usage](https://code.claude.com/docs/en/agent-sdk/cost-tracking).

### Native lifecycle

Hooks receive JSON on stdin with session_id, cwd, transcript_path, and
hook_event_name. The transcript can lag current in-memory state. Keep task
state in the journal; do not reconstruct a ready checkpoint solely from that
file.

| Event | Additional input | Control relevant to rollover |
| --- | --- | --- |
| PreCompact | trigger: manual or auto; custom_instructions: string or null | Persist the latch. decision: block or exit 2 blocks compaction, but proactive processing can continue uncompacted. continue and systemMessage are ignored. |
| PostCompact | trigger; compact_summary | Record completion; no decision control. |
| SessionStart | source: startup, resume, clear, compact, or fork | additionalContext injects grounding; cannot block startup. |

Use a nonblocking latch until the supervisor owns continuation. Retain the
existing BACKS grounding hook. CLI and SDK event support can differ, so
feature-detect both. SessionStart source fork requires v2.1.214; earlier
versions report resume. On v2.1.251+, resumed/forked sessions with prior
responses can provide context_tokens and seconds_since_last_response.

Return bounded grounding pointers through:

```json
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Load the current verified task record and its original intent before continuing."}}
```

For noninteractive -p, SessionStart output's hookSpecificOutput.initialUserMessage
can supply the first turn; any CLI prompt then follows as another turn. Prefer
the wrapper's explicit prompt when available, avoiding two accidental
continuation turns.
Large hook strings spill after 10000 characters; pointers must explicitly
instruct the successor to load the referenced state.

Send /compact to the session controlled by the host and confirm compact_boundary.
A success result alone does not prove compaction happened. For a fresh session,
start a new SDK query without resume/continue, or use documented /clear in a
streaming-input session and verify the new session ID. A new claude -p process
without resume/continue is another fresh boundary. Fork copies history.

Launching claude -p with /clear from an old agent's shell controls the child
process, not its parent session. Only the owner of the session can perform the
transition correctly. If interrupting a streaming SDK client, drain the old
turn's result before consuming the next query.

An external wrapper can feed the verified continuation prompt to a new -p
process automatically after the source finishes cleanly. --session-id accepts
a UUID, but that alone is not documented launch idempotency: the wrapper must
persist its launch identity and reconcile any uncertain outcome. Keep the
existing permission and tool configuration on each launch.

For existing background sessions, claude agents --json --all is the supported
state inventory. Inspect id, sessionId, state, status, and waitingFor.
claude --bg starts background work; /background moves an interactive
conversation there. claude attach reattaches its UI, while claude respawn
preserves conversation history. None of those history-preserving operations
counts as a fresh-context handoff. Do not edit internal jobs/state.json files
or kill a supervisor's unrelated workers.

Sources: [hooks](https://code.claude.com/docs/en/hooks),
[SDK commands](https://code.claude.com/docs/en/agent-sdk/skills#commands-in-agent-sdk-sessions),
[SDK sessions](https://code.claude.com/docs/en/agent-sdk/sessions),
[Python client](https://code.claude.com/docs/en/agent-sdk/python#claudesdkclient),
[background session control](https://code.claude.com/docs/en/agent-view#manage-sessions-from-the-shell).

## Codex

Set the real compaction threshold in the active configuration scope:

```toml
model_auto_compact_token_limit = 125000
```

On versions that expose model_auto_compact_token_limit_scope, use total to
count the complete active context. Do not set model_context_window to an
invented capacity just to change compaction timing. Confirm the effective
configuration and installed version before adding version-specific keys.

```toml
# Add only when the installed schema exposes this key.
model_auto_compact_token_limit_scope = "total"
```

Codex CLI and the local IDE extension share configuration layers. The user
file defaults to $CODEX_HOME/config.toml, normally ~/.codex/config.toml;
trusted project configuration and launch overrides can supersede it. Current
named profiles use $CODEX_HOME/profile-name.config.toml. Inspect the installed
format and actual selected profile before changing it.

The current docs do not guarantee that changing this threshold on disk
updates every already-loaded thread. app-server config/read reports resolved
disk configuration; that is not an active-thread receipt. Apply overrides to
owned fresh launches, or adopt an existing task at a verified clean boundary,
then verify the new thread's behavior. Do not restart unrelated sessions to
make a configuration report appear complete.

Sources: [configuration reference](https://developers.openai.com/codex/config-reference/),
[configuration layers](https://developers.openai.com/codex/config-basic/),
[app-server configuration methods](https://developers.openai.com/codex/app-server/).

### Hooks and trust

Current Codex discovers hooks.json beside active config layers, inline hooks
in config.toml, and enabled plugin hooks. Matching sources accumulate. Hooks
are enabled by default; the feature key is hooks, with codex_hooks deprecated.
Project hooks also require project trust.

New or changed non-managed hook definitions are skipped until their exact
hash is trusted through /hooks. Installation is not a trust receipt. Never
silently add --dangerously-bypass-hook-trust, edit trust records, or impersonate
managed policy. That flag is documented for an invocation whose caller has
already vetted all enabled hook sources, not for a general installer.

PreCompact and PostCompact receive trigger: manual|auto and turn_id. Shared
fields include session_id, cwd, model, hook_event_name, and nullable
transcript_path. Subagent hooks use the parent's session_id; preserve agent
identity separately. Transcript formats are not a stable hook interface.

PreCompact continue:false stops before compaction; PostCompact stops after it.
SessionStart with source compact can stop before the immediate continuation.
SessionStart accepts the additionalContext shape shown above. Async hooks cannot enforce
these stops. Use short synchronous, nonblocking latch hooks until ownership
is bound. Local hooks do not cover Work cloud orchestration.

Source: [Codex hooks](https://developers.openai.com/codex/hooks/).

### Owned sessions and continuation

For a supervisor, the app-server exposes thread/tokenUsage/updated,
thread/compact/start, and thread/start. Compaction returns immediately; wait
for the contextCompaction item lifecycle to complete. thread/start makes a
new conversation. thread/resume continues history; thread/fork branches it.
The SDK similarly distinguishes startThread() from resumeThread(). Preserve
the configured sandbox and approvals rather than copying documentation defaults.

The notification name alone does not define a portable token formula. Bind the
installed protocol schema and distinguish its latest active-window usage from
cumulative usage. The bundled guard therefore requires normalized active tokens
for Codex rather than guessing from an arbitrary total field.

Use an owned app-server connection for turn/interrupt, and wait for
turn/completed before advancing quiescence. Generate the protocol schema from
the installed binary with app-server generate-json-schema --out <directory>.
Inspect --help before using newer options such as --strict-config. An observed
hook/started and hook/completed pair verifies execution; hooks/list alone only
shows discovery. Treat app-server version changes as an adapter compatibility
check, since the CLI reference labels that interface experimental.

For a CLI wrapper, codex exec --json - reads the next task prompt from stdin
and emits thread.started plus turn completion/error events. A new exec run
without resume or fork gives the wrapper a fresh conversation. --ephemeral
only changes transcript persistence; it is not required for fresh context.
Do not discard transcripts needed for recovery.

Current Codex also supports codex queue --thread <UUID> --message <text>,
including on a selected --remote server. This can request a checkpoint in a
reachable existing session without a manually typed prompt. It queues text;
it does not prove the source is paused or clear its history. Reconcile source
work, confirm quiescence, then launch the successor under the existing writer
lease. codex resume, exec resume, and fork retain history.

Sources: [app-server](https://developers.openai.com/codex/app-server/),
[SDK](https://developers.openai.com/codex/sdk/),
[CLI reference](https://developers.openai.com/codex/cli/reference/),
[noninteractive execution](https://developers.openai.com/codex/noninteractive/).

## Installation coverage

Native settings persist for later local launches without a daemon. Claude's
settings are shared by its local terminal, IDE, and Desktop surfaces, but
local files do not automatically reach a cloud execution machine. Shell
startup exports likewise do not update already-running processes or every GUI
launcher. Configure the existing launcher or service explicitly.

A boot/login worker is useful only when it is bound to the real BACKS journal,
provider adapter, and writer lease. A service that merely scans configuration
cannot promise automatic fresh-session continuation. Preserve existing
controller ownership and startup services; report configured, observed,
trusted, and bound coverage separately. Keep installation hooks nonblocking
when no supported controller owns the session.

Source: [Claude settings by surface](https://code.claude.com/docs/en/settings#settings-in-cloud-sessions).

## Other frontier coders

Implement the same capability contract: reliable active-context count or a
labeled conservative estimate; safe quiescence; empty-history launch;
instruction/evidence hydration; and observable acknowledgement. Explicitly
record unsupported operations. Keep policy shared and transport-specific
parsing in the provider adapter. Never disguise history resume as a fresh start.
