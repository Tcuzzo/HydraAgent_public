# Hydra Skill Template

**Skill ID:** hydra-{category}-{skill-name}  
**Version:** 1.0.0  
**Category:** {category}  
**Dependencies:** {list dependencies}  

---

## Description

{Clear, concise description of what this skill does and when to use it}

---

## When to Activate

Use this skill when:
- {condition 1}
- {condition 2}
- {condition 3}

---

## Instructions

### Step 1: {Initial Step}
{Detailed instructions for the first step}

### Step 2: {Main Operation}
{Detailed instructions for the main operation}

### Step 3: {Validation}
{How to validate the results}

### Step 4: {Output}
{Expected output format and delivery}

---

## Examples

### Example 1: Basic Usage
```
User: {example request}
Assistant: {example response using this skill}
```

### Example 2: Advanced Usage
```
User: {advanced example request}
Assistant: {advanced example response}
```

### Example 3: Edge Case
```
User: {edge case request}
Assistant: {edge case handling}
```

---

## Guidelines

- **Do:** {best practice 1}
- **Do:** {best practice 2}
- **Don't:** {anti-pattern 1}
- **Don't:** {anti-pattern 2}

---

## Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| {error 1} | {cause} | {resolution} |
| {error 2} | {cause} | {resolution} |

---

## Evaluation Framework

```yaml
eval:
  name: {skill-name}-eval
  version: 1.0
  
  # Accuracy thresholds
  accuracy_threshold: 0.95
  precision_threshold: 0.90
  recall_threshold: 0.90
  
  # Test coverage
  test_cases:
    - name: basic-functionality
      count: 10
      pass_rate: 0.95
    - name: edge-cases
      count: 5
      pass_rate: 0.85
    - name: error-handling
      count: 5
      pass_rate: 0.90
  
  # Performance metrics
  performance:
    max_latency_ms: 2000
    avg_latency_ms: 500
    p95_latency_ms: 1500
    token_budget: 4000
  
  # Quality gates
  quality:
    hallucination_rate: 0.02
    false_positive_rate: 0.05
    reproducibility: 0.98
  
  # Validation methods
  validation:
    - automated-tests
    - peer-review
    - user-feedback
  
  # Success criteria
  success_criteria:
    - "Completes task without user intervention"
    - "Output matches expected format"
    - "No safety violations"
    - "Within token budget"
```

---

## Proven Results

| Metric | Target | Achieved | Test Date |
|--------|--------|----------|-----------|
| Accuracy | 95% | {achieved} | {date} |
| Precision | 90% | {achieved} | {date} |
| Recall | 90% | {achieved} | {date} |
| Latency (avg) | 500ms | {achieved} | {date} |
| User Satisfaction | 4.5/5 | {achieved} | {date} |

---

## Related Skills

- [{related-skill-1}](../{category}/{related-skill-1}/SKILL.md)
- [{related-skill-2}](../{category}/{related-skill-2}/SKILL.md)

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | {date} | Initial release |

---

## License

Apache 2.0
