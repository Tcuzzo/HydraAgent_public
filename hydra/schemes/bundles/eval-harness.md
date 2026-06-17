# Hydra Skills Evaluation Harness

**Version:** 1.0.0  
**Purpose:** Standardized evaluation framework for all Hydra skills  

---

## Overview

Every skill in the Hydra bundle includes built-in evaluation to ensure:
- **Reliability:** Consistent performance across runs
- **Accuracy:** Correct outputs for given inputs
- **Safety:** No harmful or unintended behavior
- **Efficiency:** Reasonable token and time usage

---

## Evaluation Components

### 1. Automated Test Suite

```yaml
test_suite:
  name: {skill-name}-tests
  framework: jest|pytest|go-test
  
  # Unit tests for core functionality
  unit_tests:
    - test_basic_functionality
    - test_edge_cases
    - test_error_conditions
  
  # Integration tests with dependencies
  integration_tests:
    - test_api_integrations
    - test_file_operations
    - test_network_calls
  
  # Regression tests
  regression_tests:
    - test_previously_fixed_bugs
    - test_performance_regression
```

### 2. Quality Metrics

```yaml
metrics:
  # Accuracy metrics
  accuracy:
    method: "comparison with ground truth"
    threshold: 0.95
    
  precision:
    method: "true positives / (true positives + false positives)"
    threshold: 0.90
    
  recall:
    method: "true positives / (true positives + false negatives)"
    threshold: 0.90
    
  f1_score:
    method: "harmonic mean of precision and recall"
    threshold: 0.92

  # Performance metrics
  latency:
    p50: "< 500ms"
    p95: "< 1500ms"
    p99: "< 3000ms"
    
  throughput:
    requests_per_second: "> 10"
    
  token_efficiency:
    avg_tokens_per_task: "< 4000"
```

### 3. Safety Checks

```yaml
safety:
  # Content safety
  content_filter:
    - no_harmful_instructions
    - no_pii_leakage
    - no_secret_exposure
    
  # Behavioral safety
  behavior:
    - no_infinite_loops
    - no_resource_exhaustion
    - graceful_error_handling
    
  # Compliance
  compliance:
    - gdpr_compatible
    - soc2_controls
    - iso27001_alignment
```

---

## Running Evaluations

### For Document Processing Skills

```bash
# Run all document processing evals
hydra-pi.mjs eval --bundle document-processing

# Run specific skill eval
hydra-pi.mjs eval --skill pdf-extract-tables

# Run with custom test data
hydra-pi.mjs eval --skill docx-create --testdata ./my-docs/
```

### For Web Testing Skills

```bash
# Run web testing evals against test app
hydra-pi.mjs eval --bundle web-testing --target http://localhost:3000

# Run cross-browser tests
hydra-pi.mjs eval --bundle web-testing --browsers chromium,firefox,webkit

# Run performance tests
hydra-pi.mjs eval --bundle web-testing --suite performance
```

### For Code Review Skills

```bash
# Run code review evals on test repos
hydra-pi.mjs eval --bundle code-review --repos ./test-repos/*

# Run security scanning evals
hydra-pi.mjs eval --bundle code-review --suite security

# Run with custom ruleset
hydra-pi.mjs eval --bundle code-review --rules ./my-rules.yaml
```

### For CI/CD Skills

```bash
# Run CI/CD evals in sandbox
hydra-pi.mjs eval --bundle ci-cd --platform github

# Run deployment simulation
hydra-pi.mjs eval --bundle ci-cd --suite deployment --dry-run

# Run pipeline optimization evals
hydra-pi.mjs eval --bundle ci-cd --suite optimization
```

### For Security Skills

```bash
# Run security skill evals (sandboxed)
hydra-pi.mjs eval --bundle security --target http://test-vuln-app.local

# Run compliance audits
hydra-pi.mjs eval --bundle security --suite compliance --framework gdpr

# Run vulnerability detection evals
hydra-pi.mjs eval --bundle security --suite vulnerability-detection
```

### For Research Skills

```bash
# Run research skill evals
hydra-pi.mjs eval --bundle research --topic "climate change"

# Run fact-checking evals
hydra-pi.mjs eval --bundle research --suite fact-checking

# Run synthesis quality evals
hydra-pi.mjs eval --bundle research --suite synthesis
```

---

## Evaluation Report Format

```markdown
# Skill Evaluation Report

## Skill: {skill-name}
## Date: {evaluation-date}
## Version: {skill-version}

### Summary
- **Overall Score:** {score}/100
- **Status:** PASS | FAIL | NEEDS_IMPROVEMENT

### Test Results
| Category | Tests | Passed | Pass Rate |
|----------|-------|--------|-----------|
| Functionality | 20 | 19 | 95% |
| Edge Cases | 10 | 8 | 80% |
| Error Handling | 5 | 5 | 100% |
| Performance | 5 | 4 | 80% |

### Metrics
- Accuracy: {value} (target: 0.95)
- Precision: {value} (target: 0.90)
- Recall: {value} (target: 0.90)
- Avg Latency: {value}ms (target: 500ms)

### Issues Found
1. {issue description}
   - Severity: HIGH | MEDIUM | LOW
   - Recommendation: {fix suggestion}

### Recommendations
- {recommendation 1}
- {recommendation 2}

### Next Steps
- [ ] Fix identified issues
- [ ] Re-run evaluation
- [ ] Update documentation
```

---

## Continuous Evaluation

Skills are continuously evaluated through:

1. **Pre-commit Checks:** Run on every skill modification
2. **CI Pipeline:** Full eval suite on PR
3. **Nightly Runs:** Comprehensive testing against latest dependencies
4. **Canary Testing:** New skills tested on subset before full rollout
5. **User Feedback Loop:** Real-world performance tracking

---

## Certification Levels

| Level | Requirements | Badge |
|-------|--------------|-------|
| Bronze | 90%+ pass rate, basic evals | 🥉 |
| Silver | 95%+ pass rate, full evals | 🥈 |
| Gold | 98%+ pass rate, stress tested | 🥇 |
| Platinum | 99%+ pass rate, production proven | ⭐ |

---

## License

Apache 2.0
