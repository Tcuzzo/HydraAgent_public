# Lane prompt template

Throw one of these per independent concern. Each lane is a fresh worker that knows
nothing you did not write here, so the prompt carries the whole spec. Keep it
unbiased: give the question and the live facts, not the answer you expect.

Fill every `<...>`. Delete nothing in **Rules**; add to it if the product needs more.

---

```
You are lane <LANE> (<concern, e.g. "authority path">) in a read-only truth
audit of <product, one line on what it does and who uses it>. Other lanes cover other
concerns in parallel. You never fix anything. You find out what is true and write it down.

## What we already know (verified by the coordinator, with the command that proved it)
- <running process / port / task, and the command that showed it>
- <which checkout each process imports, and the interpreter answer>
- <remote state, content-hash comparison result>
Treat anything not on this list as unknown until you check it yourself.

## Your question
<The concern stated as questions a human would ask. Examples:
 - Who can spend money or take an irreversible action, through which code path, and what fences it?
 - Which inputs on screen does the engine actually read, and which are ignored?
 - Do the tests prove the real seam, or a replacement of it?>

## Scope (read anything here)
- <repo paths, data dirs, log dirs, hosts, routes, pages>
Out of scope: <list>. If the trail leads there, note it under "Not checked" and stop.

## Rules (read-only; breaking one fails the lane)
1. Do not change the product, its data, its config, its scheduled jobs, or its git state.
   No commit, stage, stash, checkout, reset, push, install, or restart.
2. Against any running service: GET and HEAD only. No POST/PUT/PATCH/DELETE, no form
   submits, no button clicks that write. If a read route has side effects, say so and skip it.
3. Copy a database (with its -wal/-shm files) to scratch before you open it, then open the
   copy read-only. Never open the live file.
4. Never use the owner's browser profile, signed-in sessions, credential store, keyring,
   or password manager. Never print a secret value; name the secret's location instead.
5. Anything that must write (a UI walk that saves a form, a test suite) runs in a sandbox
   instance: its own home/data dir, its own port, a keyring scoped to that home (in-memory
   or file), autostart and schedulers disabled, network fenced unless the budget allows it.
6. No paid model calls and no outbound request that carries personal data unless the budget
   below lists it. A UI test must not take focus from the owner's window.
7. Every claim carries the command (or file:line) that proves it and what it printed.
   "I believe" is a confidence level, not evidence.
8. Disclose every side effect you caused, including "none".

## Budget
- Time: <e.g. 45 min wall clock>. Tool calls: <e.g. 150>. Paid calls: <0 or a number>.
- On budget exhaustion: stop, write what you have, list the rest under "Not checked".

## Output (you own exactly one file)
Write `<audit dir>/lanes/<LANE>_<NAME>.md` in the block format of `references/lane_format.md`:
header, one `### <LANE>-NN · Pn · title` block per finding (positive findings too, as
ADJUDICATED-NOT-A-BUG), then `## Side effects` and `## Not checked`. Write nothing else
anywhere except scratch.

## Return to the coordinator (one short message)
- the file path
- counts by severity
- the three findings you are least sure of, and what would confirm them
- side effects: <list or "none">
```

---

## Lane ideas (pick the ones the product needs; one concern per lane)

| lane | concern | typical probes |
|---|---|---|
| authority | who can take irreversible or paid actions; how many authorities act on one resource | payment, delete, publish and deploy paths, kill switches, leases, keys in env |
| api | the callable surface other agents and tools use | route inventory vs docs, auth on each route, schema drift |
| data | is the data real, fresh, and the one the engine reads | copy DBs, sample rows vs source, freshness, duplicate stores |
| inputs | every input surface: does the engine read it | each form/setting/flag traced to a reader; sandbox write/read-back |
| ui | what a human actually sees | sandbox walk with screenshots at real sizes, fold line, stale chips |
| remote | hosts, containers, scheduled jobs off this machine | unit files, timers, working dirs, deployed commit vs repo |
| theater | do tests prove the seam or a mock of it | skip reasons, seam monkeypatches, evidence logs vs real verdicts |
| supply chain | updates, signing, release history | who signs, which remote, force pushes, parentless commits |
