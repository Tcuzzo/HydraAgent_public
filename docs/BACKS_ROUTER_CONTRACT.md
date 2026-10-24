# BACKS router client contract (`backs.router.v1`)

Vendorable. Copy this file into any client (Hydra, the POWERS PLAYBOOK app, a script). It is the
whole contract between a client and the BACKS router. BACKS owns the server side
(`backend/services/backs_router.py`, `backend/routes/backs_router_routes.py`); the authority for
which rungs exist and in what order is `config/fleet_ladder.yaml` (`roles:` plus the `router:` block).

## What the router promises

1. **The app keeps working.** A call walks the chain until a rung answers. A failed key, used-up
   tokens or credit, a retired model, a server error, a timeout, or a cut-off answer puts that rung
   on cooldown and the call moves on. The local GPU tier is always last. You get a failure only
   when no rung could answer, with a plain message naming each rung and why.
2. **The essence travels.** Every rung gets the same pack: BACKS core laws, harness rules, the
   picked-up skill, the tool protocol, the output contract, and the rung's own working card. It is
   sized to each rung's context window. Core laws, tool protocol, and contract are never dropped.
3. **Decisions get better.** Every attempt writes one outcome. A bandit per task type, seeded from
   ladder order, reorders rungs inside each tier and explores at most `exploration_cap` of the time.
   An operator ladder edit always wins: a changed ladder reseeds that task from the new order.
4. **No literals.** Responses name rungs. Model, provider, and endpoint come from the ladder.
   Credentials appear only as a reference (`{"kind": "env", "env_key": "<NAME>"}`), never a value.

## Endpoints

LAN callers only (loopback or the house LAN). Base: the BACKS backend, port 8000.

### `POST /api/router/resolve` - chain plus essence, no model call

Request:

```json
{
  "task_type": "answer",
  "task": "what the user asked",
  "tools": [{"name": "file.read_range", "description": "Read lines.", "parameters": {"type": "object"}}],
  "contract": {"type": "json", "required": ["answer"]},
  "skill_text": null
}
```

- `task_type`: a key of `router.tasks` in the ladder (`answer`, `code`, `plan`, `grade`, `default`).
- `tools`: tools the client grants for this task. Only these are offered and run.
- `contract`: `{"type": "text"}` (default), `{"type": "json", "required": [...]}`, or
  `{"type": "regex", "pattern": "..."}`.
- `skill_text`: optional override; when null BACKS picks the skill from the task.

Response 200:

```json
{
  "contract": "backs.router.v1",
  "task_type": "answer",
  "roles": ["worker"],
  "ladder_fp": "16 hex",
  "reseeded": false,
  "explored": false,
  "essence": {"text": "...", "chars": 0, "budget_chars": 0, "over_budget": false,
              "core_intact": true, "sha": "12 hex", "sections": [{"name": "core_laws", "level": "full", "chars": 0}]},
  "chain": [
    {"rung": "<identity>", "role": "worker", "provider": "<from ladder>", "model": "<from ladder>",
     "transport": "<from ladder>", "wire": "chat_completions | messages_api | native_chat | null",
     "tier": "cloud | local", "endpoint": "<from ladder>",
     "credential_ref": {"kind": "env", "env_key": "<NAME>"},
     "available": true, "state": "available | unknown | unavailable", "reason": "",
     "cooldown": null, "usable": true, "score": 0.7, "posterior_mean": 0.7, "ladder_index": 0,
     "context_tokens": 8192, "essence_budget_chars": 12902, "max_tokens": 4096, "timeout_s": 120,
     "essence": {"text": "...", "core_intact": true, "...": "..."}}
  ]
}
```

A client that calls models itself walks `chain` in order, skips rows where `usable` is false,
sends `essence.text` of that row as the system message, resolves `credential_ref` from its own
vault, and reports each attempt to `/api/router/outcome`.

### `POST /api/router/complete` - BACKS runs the whole chain

Same request as resolve. BACKS sends the task down the chain, runs granted read-only tools that a
model writes as text, checks the contract (one repair try), and records an outcome per attempt.

Response 200 (answered):

```json
{"ok": true, "contract": "backs.router.v1", "rung": "<identity>", "tier": "cloud | local",
 "answer": "raw text", "parsed": {"answer": "..."}, "contract_pass": true,
 "first_token_s": 0.8, "latency_s": 3.1, "total_s": 4.0,
 "tool_calls": [{"name": "file.read_range", "arguments": {}, "refused": false, "ok": true}],
 "essence": {"chars": 0, "budget_chars": 0, "core_intact": true, "sha": "...", "sections": []},
 "attempts": [{"rung": "...", "ok": false, "error_class": "quota", "status": 429, "cooldown_s": 900}],
 "skipped": [{"rung": "...", "reason": "cooldown | unavailable | no_wire", "error_class": "auth"}],
 "chain": ["..."], "explored": false, "last_resort": false}
```

Response 503 (nothing could answer):

```json
{"ok": false, "error": "no_rung_reachable",
 "message": "No rung could answer. Tried and skipped: <rung>: auth; <rung>: server.",
 "attempts": [], "skipped": [], "chain": []}
```

Tool calls the router reads from model text:

```
<tool_call>{"name": "<tool id>", "arguments": {...}}</tool_call>
```

also `<function=name>{...}</function>`, a fenced JSON block, or bare JSON with `name` and
`arguments` that names a granted tool. Results go back as
`<tool_result name="<tool id>">...</tool_result>`. A tool the client did not grant is refused.
The router itself runs only enabled read-only registry tools; anything else comes back refused, so
the client runs side-effecting tools under its own gates.

### `POST /api/router/outcome` - report a call you made yourself

```json
{"task_type": "answer", "rung": "<identity>", "success": true,
 "error_class": null, "latency_s": 2.4, "first_token_s": 0.6, "truncated": false,
 "contract_pass": true, "grader_pass": null, "cost": 0.0012,
 "tokens_in": 1200, "tokens_out": 300, "source": "hydra"}
```

- `success` (bool), `task_type`, and `rung` are required. The rung must be on the ladder for that
  task, otherwise 400.
- `error_class` on failure: `auth`, `quota`, `retired`, `server`, `timeout`, `truncation`,
  `unreachable`, `empty`, `contract`, `config`, `transport`.

Response 200: `{"ok": true, "recorded": true, "id": "...", "reward": 0.98, "arm": {...}}`.

### `GET /api/router/state?tail=20` - inspect the learning

Returns `bandit` (per task type: `ladder_fp`, `arms` with `alpha`, `beta`, `mean`, `n`,
`successes`, `failures`, `prior_mean`, `latency_ewma_s`; plus `reseeds`, `picks`, `explored`),
`cooldowns.entries` (key, `until`, `error_class`, `status`), `outcomes_total`, and
`outcomes_recent`.

## Failure classes and what happens

| class | from | cooldown scope |
|---|---|---|
| auth | 401/403 without a credit word, bad key | provider credential |
| quota | 402, 429, 401/403 with a credit, quota, or billing word | provider credential (Retry-After honored) |
| retired | 410, model-not-found text | rung |
| server | 5xx | rung (Retry-After honored) |
| timeout | no answer inside the rung's `timeout_s` | rung |
| truncation | the answer hit the token limit | none (0s), tried again next call |
| unreachable | connection refused or reset | rung |
| empty | no text came back | rung |
| contract | still outside the contract after one repair | none |

Durations live in `router.cooldown_s` in the ladder. Before giving up, the router makes one last
pass over rungs skipped for soft reasons (unavailable, server, timeout), local first. It never
retries a rung that is down for auth, quota, retirement, or config.

## State on the BACKS host

`backend/state/backs_router/` (override with `BACKS_ROUTER_STATE`): `bandit.json`,
`cooldowns.json`, `outcomes.jsonl`. Plain JSON, safe to read, safe to delete (the router reseeds
from the ladder).

## CLI on the BACKS host

```
cd backend && python -m services.backs_router resolve answer "say hi"
cd backend && python -m services.backs_router complete answer "say hi" --contract '{"type":"json","required":["answer"]}'
cd backend && python -m services.backs_router state --tail 5
```
