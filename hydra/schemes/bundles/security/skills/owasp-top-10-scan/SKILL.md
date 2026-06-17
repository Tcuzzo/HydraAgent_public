---
name: owasp-top-10-scan
description: Comprehensive security scan for OWASP Top 10 vulnerabilities including SQL injection, XSS, broken authentication, and more using industry-standard tools
---

# OWASP Top 10 Security Scan

**Skill ID:** hydra-security-owasp-top-10-scan  
**Version:** 1.0.0  
**Category:** security  
**Dependencies:** OWASP ZAP, Nuclei, Burp Suite API  

---

## When to Activate

Use this skill when:
- Security audit of web application is needed
- Pre-release security validation required
- Compliance requires OWASP Top 10 coverage
- Penetration testing support is needed
- After major feature releases

---

## Instructions

### Step 1: Reconnaissance
1. Crawl target application to map all endpoints
2. Identify technologies in use (Wappalyzer)
3. Document authentication mechanisms
4. Map API endpoints and GraphQL schemas

### Step 2: Automated Scanning
1. Run OWASP ZAP active scan
2. Execute Nuclei templates for OWASP Top 10
3. Test for injection vulnerabilities (SQL, NoSQL, OS command)
4. Check for XSS in all input vectors
5. Validate authentication and session management

### Step 3: Manual Testing Support
1. Generate test cases for manual verification
2. Provide payloads for common vulnerabilities
3. Document steps to reproduce findings
4. Suggest exploitation proof-of-concepts

### Step 4: Reporting
1. Categorize findings by OWASP Top 10 category
2. Assign CVSS scores and severity ratings
3. Provide remediation guidance per finding
4. Generate executive and technical reports

---

## Examples

### Example 1: Full OWASP Top 10 Scan
```
User: Scan our staging app at https://staging.example.com
Assistant: Running comprehensive OWASP Top 10 scan...

Results Summary:
  - A01 Broken Access Control: 2 MEDIUM issues
  - A03 Injection: 1 HIGH (SQL injection in search)
  - A05 Security Misconfiguration: 3 LOW issues
  - A07 Auth Failures: 1 MEDIUM (weak password policy)
  
Full report: owasp-scan-20260527.pdf
Remediation PR: security/owasp-fixes
```

---

## Evaluation Framework

```yaml
eval:
  name: owasp-top-10-scan-eval
  version: 1.0
  accuracy_threshold: 0.96
  test_cases:
    - name: sqli-detection
      count: 10
      pass_rate: 0.98
    - name: xss-detection
      count: 15
      pass_rate: 0.96
    - name: auth-testing
      count: 10
      pass_rate: 0.95
    - name: false-positives
      count: 10
      pass_rate: 0.94
  performance:
    max_latency_ms: 300000
    avg_latency_ms: 120000
  quality:
    detection_rate: 0.96
    false_positive_rate: 0.04
```

---

## License

Apache 2.0
