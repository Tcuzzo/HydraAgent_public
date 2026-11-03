# Paste this into any new agent — make it improve itself before it ships

Use the installed `skills/karpathy/` folder in the new agent's repo
(anywhere it can import `program.py`), then paste the prompt below to the agent.

---

## THE PROMPT (copy everything between the lines)

------------------------------------------------------------------------------
You have a self-improvement skill in the `skills/karpathy/` folder:
an autonomous research loop (read its `SKILL.md`). Before you are shipped, use it
to make yourself measurably better — honestly, with no faking. Do this:

1. Read `SKILL.md` so you understand the loop and the rules.

2. Pick ONE thing about yourself worth improving and ONE number that measures it
   (your "metric"). Examples: task success rate, answer accuracy on a fixed test
   set, tokens/cost per task, p95 latency, tool-call error rate.

3. Copy `program.template.md` to `improve-me.program.md` and fill it in IN PLAIN
   ENGLISH:
   - **Goal:** what you're improving and why.
   - **Metric:** your one number, the direction (max/min), and the HARD BAR it
     must clear before anything else counts.
   - **Fair game:** the exact files you're allowed to change (your prompts,
     config, routing, weights-config — NOT the test and NOT the bar).
   - **Budget:** how many tries.

4. Wire two roles (model-agnostic — use whatever models you have; see
   the `example.py` contract):
   - **Proposer:** a model that drafts one small change to a fair-game file.
     Keep source local unless the operator explicitly authorizes a remote provider.
   - **Tester (BLIND):** scores the result and returns the numbers. It must NOT
     see the proposer's reasoning — no grading your own work.

5. Run the loop:
   ```python
   from program import parse_program, run_program
   prog = parse_program("improve-me.program.md")
   result = run_program(prog, target_dir=".", propose=my_proposer,
                        test=my_blind_tester, sandbox_root="/writable/experiments")
   print(result)
   ```
   The loop clones the clean source into a disposable temp checkout and keeps a
   change there ONLY if it clears the bar and beats your current best. The engine
   checks the bar first, rejects a higher-priority regression, and rejects a tie.
   The result is only as trustworthy as the independent Tester.

6. Ship ONLY after the loop reports a real, kept improvement — or honestly report
   that no safe improvement was found this round. NEVER lower the bar, weaken the
   test, or claim a win you didn't earn. If you think the goal is impossible,
   research a real path instead of faking the number.

That's it. Improve, prove it, then ship.
------------------------------------------------------------------------------

## Notes for you (the human)
- The loop mutates only its disposable Git clone and resets rejected changes.
  Its path check is not an OS sandbox; run untrusted proposer code in a separately
  enforced sandbox and review the champion diff before applying it.
- Keep the **eval and the bar OUT of the fair-game files**, so an agent can't "pass"
  by weakening its own test. That's the one rule that makes the whole thing trustworthy.
- `python3 example.py` shows a real keep + a real revert in ~1 second, so you can
  see it work before trusting it on a real agent.
