---
name: understanding
description: Score code against its intent and ask Hydra's routed auditor to refute unsupported claims before delivery.
license: MIT
version: "1.0"
allowed-tools:
  - fs_read
  - shell
---
# Understanding

Use `hydra.understanding.check_candidate(candidate_code, original_intent)` to collect advisory evidence about code before delivery.

The result contains five deterministic `dimension_scores`: Spec adherence, Architectural fit, Type safety, Testability, and Security. Each dimension is scored from 0 through 4. `total_score` scales their 20 available points to 0–100, and `verdict` is `APPROVED` at 80 or above, `REVISE` from 60 through 79, and `REJECT` below 60.

The same call routes a grounded refutation request through Hydra's configured `auditor` role and provider factory. It accepts only the auditor's strict JSON verdict. The model call requests 2048 output tokens. When routing, provider setup, the call, or JSON parsing is unavailable, the result has `passed=False`, `status="model_unavailable"`, a named failure, and a recovery action describing how to configure Hydra's auditor path.

Read `dimension_scores`, `failures`, `recovery_actions`, and `confidence`, not only the headline verdict. A grounded refutation has `passed=False` and `status="refuted"`. A non-refuted result passes only when its deterministic verdict is `APPROVED`.

This API is advisory. It returns evidence to its caller; it does not run automatically, authorize execution, block another workflow, or create an approval path.

## Design → Plan → Build → Test → Ship

1. **Design:** Keep the original intent as the stable `original_intent` input. State concrete behavior and constraints so Spec adherence has evidence to compare.
2. **Plan:** Identify how the candidate will fit existing structure, retain type boundaries, remain testable, and avoid security hazards represented by the five dimensions.
3. **Build:** Implement the candidate, then call `check_candidate(candidate_code, original_intent)`. Inspect every dimension and any grounded refutation.
4. **Test:** Run the project's real tests. Apply returned recovery actions and rerun `check_candidate()` when the code or intent changes. Understanding evidence does not replace executable tests.
5. **Ship:** Report the score, verdict, status, confidence, failures, and recovery actions alongside test evidence. Treat the result as advice, never as an approval or gate.
