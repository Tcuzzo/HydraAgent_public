# One execution packet

Keep this record in the task's existing authorized state surface. For a strict
read-only review, return it in the response. A handoff does not widen permissions.

```yaml
objective: requested result and observable acceptance
task_mode: build_fix | read_only_review | investigate | resume_deliver
integration_owner: current writer or coordinator
target: verified repo/worktree/runtime and relevant caller/consumer path
base_sha: verified commit before task changes
candidate_sha: verified current commit; list pending work separately
preserve: unrelated edits, sealed RED files, compatibility requirements
authorization: scope already granted; actual unresolved external boundary
operator_awareness:
  source: private essence path or supplied evidence reference
  status: grounded | provisional | unavailable
  slice: relevant observation, evidence, adaptation, uncertainty
mission:
  mission_id: persistent identity across workers and resumes
  deadline: explicit wall-clock stop point
  attempts_remaining: shared remaining allowance
  inner_round_limit: declared before the current episode
  resources_remaining: measured tokens/cost/calls or explicitly unavailable
skills: relevant body/source, purpose, evidence to return, exit condition
evidence:
  red: failing behavior command, revision, output, sealed file hash
  green: scoped lint/type/test commands, revision, results
  quality: measured gauntlet evidence and remaining gates
  delivery: actual artifact/runtime/revision verification
unknowns: decision-relevant gaps and unavailable tooling
next_action: concrete action and owner
```

Do not paste full histories, credentials, or other operators' profiles. A skill
receipt proves delivery of a body, not execution of its method. Revalidate stale
revision/runtime facts rather than rebuilding all context. The receiving worker
acknowledges remaining budget and ownership; it cannot silently replenish either.
