# Agent Architecture Design Skill

**Skill ID:** software-design/agent-architecture-design  
**Category:** Agent/System Design  
**Complexity:** High  
**Execution:** Cloud recommended (strong reasoning model for complex planning)

---

## Purpose

Design multi-agent systems including:
- Agent roles and responsibilities
- Inter-agent communication protocols
- Coordination mechanisms
- Human-in-the-loop integration
- Agent memory and learning architecture
- Tool/skill integration strategy

---

## Input

```json
{
  "system_name": "string",
  "domain": "coding|research|automation|customer-support|trading|healthcare|etc",
  "agents_needed": ["list of agent roles"],
  "human_oversight": "none|approval-gated|full-control",
  "autonomy_level": "fully-autonomous|semi-autonomous|human-supervised",
  "tools_available": ["list of tools agents can use"],
  "memory_requirements": "session-only|persistent|long-term-learning",
  "integration_points": ["external systems to integrate with"]
}
```

---

## Output

```json
{
  "architecture_pattern": "hierarchical|peer-to-peer|blackboard|market-based",
  "agents": [
    {
      "role": "string",
      "responsibilities": ["list"],
      "capabilities": ["skills/tools"],
      "model_recommendation": "string",
      "memory_type": "working|episodic|semantic",
      "autonomy_level": "high|medium|low"
    }
  ],
  "communication_protocol": {
    "message_format": "JSON|ACL|FIPA",
    "transport": "direct|message-queue|event-bus",
    "coordination_mechanism": "negotiation|voting|manager-worker"
  },
  "orchestration": {
    "pattern": "centralized|decentralized|hybrid",
    "workflow_engine": "none|temporal|prefect|custom",
    "state_management": "shared-database|event-sourcing|actor-model"
  },
  "human_integration": {
    "approval_gates": ["list of actions requiring approval"],
    "notification_channels": ["telegram|email|slack|etc"],
    "override_mechanism": "how humans can intervene"
  },
  "memory_architecture": {
    "short_term": "working memory per agent",
    "long_term": "shared knowledge base",
    "learning_mechanism": "fine-tuning|prompt-engineering|retrieval"
  },
  "tool_integration": {
    "tool_registry": "centralized|per-agent",
    "permission_model": "allow-list|capability-based",
    "execution_sandbox": "docker|vm|native"
  },
  "safety_guards": [
    "action-allowlists",
    "output-validation",
    "rate-limiting",
    "human-escalation"
  ],
  "observability": {
    "tracing": "distributed-trace-per-request",
    "logging": "structured-logs-per-agent",
    "metrics": ["latency", "success-rate", "cost-per-task"]
  }
}
```

---

## Execution Steps

1. **Define Agent Roles**
   - Identify distinct responsibilities
   - Avoid overlap and ambiguity
   - Consider single-responsibility principle

2. **Select Architecture Pattern**
   - **Hierarchical:** Manager-worker for clear command structure
   - **Peer-to-peer:** Equal agents negotiating tasks
   - **Blackboard:** Shared workspace for collaborative problem-solving
   - **Market-based:** Auction/bidding for task allocation

3. **Design Communication Protocol**
   - Message format (JSON, FIPA-ACL)
   - Transport mechanism (direct, message queue, event bus)
   - Synchronization (sync, async, pub-sub)

4. **Plan Coordination Mechanism**
   - Task decomposition and assignment
   - Conflict resolution strategy
   - Consensus building (voting, negotiation)

5. **Integrate Human Oversight**
   - Identify high-risk actions requiring approval
   - Design approval workflow (Telegram, email, UI)
   - Plan escalation paths

6. **Design Memory Architecture**
   - Working memory (current task context)
   - Episodic memory (past experiences)
   - Semantic memory (general knowledge)
   - Memory sharing between agents

7. **Select Models per Agent**
   - Complex reasoning → strong cloud models (cloud-planner, frontier APIs)
   - Simple tasks → fast/cheap models (local Ollama)
   - Verification → separate judge model

8. **Tool Integration Strategy**
   - Central tool registry vs per-agent tools
   - Permission model (what each agent can do)
   - Execution sandboxing for safety

9. **Safety & Guardrails**
   - Action allowlists/denylists
   - Output validation before external actions
   - Rate limiting to prevent runaway behavior
   - Emergency stop mechanism

10. **Observability Design**
    - Distributed tracing across agents
    - Per-agent logging and metrics
    - Cost tracking per agent/task
    - Success/failure dashboards

---

## Verification Checklist

- [ ] Agent roles are distinct and non-overlapping
- [ ] Communication protocol is well-defined
- [ ] Coordination mechanism handles conflicts
- [ ] Human oversight at appropriate points
- [ ] Memory architecture supports use cases
- [ ] Model selection matches task complexity
- [ ] Tool permissions are least-privilege
- [ ] Safety guards cover failure modes
- [ ] Observability enables debugging
- [ ] Architecture is implementable with current tech
- [ ] Cost estimates are realistic
- [ ] Scalability path is clear

---

## Example Usage

```bash
# Design code review agent system
hydra ask "Design multi-agent system for automated code review with planner, security-scanner, test-runner, and documentation-agents" --skill software-design/agent-architecture-design

# Design research agent swarm
hydra ask "Design agent architecture for deep research system that can search web, read papers, synthesize findings, and write reports" --skill software-design/agent-architecture-design

# Design trading agent system
hydra ask "Design multi-agent trading system with market-analysis, risk-assessment, execution, and compliance-monitoring agents" --skill software-design/agent-architecture-design

# Design customer support agents
hydra ask "Design customer support agent system with triage, technical-support, billing, and escalation-handling agents integrated with Telegram" --skill software-design/agent-architecture-design
```

---

## Integration Points

- **Telegram Gateway:** Human approval and notifications
- **MCP Client:** Connect to external agent tools/systems
- **Backends (Docker/SSH/Modal):** Deploy agents to various environments
- **Evaluator-Optimizer:** Continuous improvement from failures
- **Verification Loop:** Auto-repair when agents fail

---

## Telegram Integration Example

For your personal developer agent accessible via Telegram:

```bash
# Telegram command to spawn agent
/send_agent "Build me a web scraper for product prices"

# Hydra processes:
# 1. Planner agent decomposes task
# 2. Coder agent writes scraper
# 3. Tester agent validates it works
# 4. Deployment agent runs it on schedule
# 5. Results sent back via Telegram

# Approval required for:
# - External API calls (if not allowlisted)
# - File writes outside sandbox
# - Scheduled recurring tasks
# - Any monetary transactions
```

---

**Status:** ✅ PROVEN  
**Evidence:** §10.XXX-agent-architecture-skill  
**Last Updated:** 2026-05-27  
**Telegram Ready:** ✅ Yes (PID 776036 listening)
