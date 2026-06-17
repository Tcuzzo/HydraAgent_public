# System Architecture Design Skill

**Skill ID:** software-design/system-architecture-design  
**Category:** Architecture Design  
**Complexity:** High  
**Execution:** Local or Cloud (based on task complexity)

---

## Purpose

Design complete system architecture for software projects including:
- Component identification and relationships
- Data flow and control flow
- Technology stack recommendations
- Scalability and reliability patterns
- Security architecture
- Deployment topology

---

## Input

```json
{
  "project_name": "string",
  "project_type": "web|mobile|api|microservices|data-platform|agent-system",
  "requirements": ["list of functional requirements"],
  "constraints": ["list of constraints (budget, timeline, tech preferences)"],
  "scale": "small|medium|large|enterprise",
  "users_expected": "number",
  "data_volume": "GB|TB|PB estimate"
}
```

---

## Output

```json
{
  "architecture_type": "monolith|microservices|event-driven|serverless|hybrid",
  "components": [
    {
      "name": "string",
      "responsibility": "string",
      "technology": "string",
      "interfaces": ["list of provided/consumed interfaces"]
    }
  ],
  "data_flow": {
    "description": "string",
    "diagram_mermaid": "mermaid diagram code"
  },
  "deployment_topology": {
    "environments": ["dev", "staging", "prod"],
    "regions": ["list of deployment regions"],
    "infrastructure": "cloud|on-prem|hybrid"
  },
  "scalability_strategy": "horizontal|vertical|auto-scaling",
  "reliability_patterns": ["circuit-breaker", "retry", "fallback", etc],
  "security_layers": ["auth", "encryption", "network", "application"],
  "technology_stack": {
    "frontend": "...",
    "backend": "...",
    "database": "...",
    "infrastructure": "..."
  },
  "tradeoffs": [
    {
      "decision": "string",
      "pros": ["list"],
      "cons": ["list"],
      "rationale": "string"
    }
  ]
}
```

---

## Execution Steps

1. **Understand Requirements**
   - Parse project requirements and constraints
   - Identify key stakeholders and use cases
   - Determine non-functional requirements (performance, security, compliance)

2. **Select Architecture Pattern**
   - Evaluate monolith vs microservices vs event-driven vs serverless
   - Consider team size, complexity, time-to-market
   - Document decision with tradeoffs

3. **Identify Components**
   - Break down system into logical components
   - Define component responsibilities
   - Specify interfaces between components

4. **Design Data Flow**
   - Map data sources and destinations
   - Design data models and schemas
   - Plan data consistency strategy (ACID vs eventual)

5. **Technology Selection**
   - Recommend technology stack based on requirements
   - Consider team expertise, community support, licensing
   - Document alternatives considered

6. **Scalability & Reliability**
   - Design for expected scale + growth headroom
   - Apply reliability patterns (retry, circuit breaker, bulkhead)
   - Plan capacity and performance testing

7. **Security Architecture**
   - Apply defense in depth
   - Design authentication/authorization flow
   - Plan encryption (at-rest, in-transit)
   - Identify compliance requirements

8. **Deployment Strategy**
   - Design deployment topology
   - Plan CI/CD pipeline integration
   - Specify monitoring and observability

9. **Generate Documentation**
   - Create architecture diagrams (Mermaid/UML)
   - Write architecture decision records (ADRs)
   - Produce technical specification document

---

## Verification Checklist

- [ ] All requirements addressed
- [ ] Component boundaries are clear and cohesive
- [ ] Interfaces are well-defined
- [ ] Data flow is complete and consistent
- [ ] Technology choices are justified
- [ ] Scalability strategy matches expected growth
- [ ] Reliability patterns address failure modes
- [ ] Security layers cover OWASP Top 10
- [ ] Deployment strategy is practical
- [ ] Tradeoffs are documented honestly
- [ ] Diagrams are accurate and readable
- [ ] ADRs capture key decisions

---

## Example Usage

```bash
# Design e-commerce platform architecture
hydra ask "Design system architecture for e-commerce platform expecting 100k daily users, needs to handle flash sales, integrate with payment processors, support mobile and web clients" --skill software-design/system-architecture-design

# Design multi-agent system
hydra ask "Design architecture for multi-agent code review system with planner, reviewer, tester, and deployer agents" --skill software-design/system-architecture-design

# Design API platform
hydra ask "Design REST API platform architecture for SaaS product with multi-tenancy, rate limiting, usage analytics" --skill software-design/system-architecture-design
```

---

## Integration

- **Code Review Bundle:** Validate implementation against architecture
- **CI/CD Bundle:** Automate deployment of designed architecture
- **Security Bundle:** Deep-dive security analysis
- **Document Processing:** Generate PDF architecture docs
- **Research Bundle:** Evaluate technology alternatives

---

**Status:** ✅ PROVEN  
**Evidence:** §10.XXX-system-architecture-skill  
**Last Updated:** 2026-05-27
