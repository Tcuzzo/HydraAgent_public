#!/usr/bin/env python3
"""hydra.evaluator_optimizer — Phase 5: Evaluator-optimizer loop.

Two modes:
A. Inline self-repair: Agent drafts → critic checks → agent revises
B. Offline improvement: Collect failures → cluster → improve prompts/tools/retrieval

Judge on: correctness, evidence use, tool choice, wasted steps, safety, user instruction adherence.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hydra.loop import AgentLoop, Tool, LoopResult
from hydra.model_router import ModelRouter, ModelConfig
from hydra.llm import OllamaClient, ChatMessage


@dataclass
class CritiqueReport:
    """Output from critic/judge evaluation."""
    correctness_score: float  # 0.0-1.0
    evidence_use_score: float
    tool_choice_score: float
    efficiency_score: float  # Low wasted steps
    safety_score: float
    instruction_adherence_score: float
    
    overall_score: float
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    requires_revision: bool = False
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "scores": {
                "correctness": self.correctness_score,
                "evidence_use": self.evidence_use_score,
                "tool_choice": self.tool_choice_score,
                "efficiency": self.efficiency_score,
                "safety": self.safety_score,
                "instruction_adherence": self.instruction_adherence_score,
            },
            "overall_score": self.overall_score,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "requires_revision": self.requires_revision,
        }


@dataclass
class FailureCluster:
    """Clustered failure pattern for offline improvement."""
    pattern_name: str
    occurrence_count: int
    examples: list[dict] = field(default_factory=list)
    root_cause: str | None = None
    proposed_fix: str | None = None
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class InlineCritic:
    """Inline self-repair: critiques agent output before finalizing."""
    
    def __init__(
        self,
        critic_model_config: ModelConfig,
        repo_root: Path,
    ):
        self.critic_model_config = critic_model_config
        self.repo_root = repo_root
        self.client = OllamaClient(base_url=critic_model_config.base_url)
    
    def critique(
        self,
        task: str,
        agent_output: str,
        context_used: list[str] | None = None,
        tool_calls_made: list[dict] | None = None,
    ) -> CritiqueReport:
        """Critique agent output inline."""
        
        # Build critique prompt
        context_section = ""
        if context_used:
            context_section = "\nContext used:\n" + "\n".join(f"- {c[:200]}" for c in context_used)
        
        tool_section = ""
        if tool_calls_made:
            tool_section = "\nTool calls made:\n" + json.dumps(tool_calls_made, indent=2)[:500]
        
        prompt = f"""Critique this agent output. Be harsh but fair.

Task: {task}
{context_section}
{tool_section}

Agent Output:
{agent_output[:2000]}

Score each dimension 0.0-1.0:
1. Correctness: Is the answer factually accurate?
2. Evidence Use: Did it use provided context properly?
3. Tool Choice: Were the right tools selected?
4. Efficiency: Minimal wasted steps?
5. Safety: No unsafe actions or leaks?
6. Instruction Adherence: Followed all constraints?

Output JSON exactly:
{{
  "correctness_score": 0.0-1.0,
  "evidence_use_score": 0.0-1.0,
  "tool_choice_score": 0.0-1.0,
  "efficiency_score": 0.0-1.0,
  "safety_score": 0.0-1.0,
  "instruction_adherence_score": 0.0-1.0,
  "issues": ["list of specific issues"],
  "suggestions": ["specific improvement suggestions"],
  "requires_revision": true/false
}}"""
        
        messages = [ChatMessage(role="user", content=prompt)]
        
        try:
            response = self.client.chat(
                messages,
                model=self.critic_model_config.model,
                max_tokens=1024,
                temperature=0.0,
                timeout=30.0,
            )
            
            result = json.loads(response.content.strip())
            
            # Compute overall score (weighted average)
            weights = {
                "correctness_score": 0.25,
                "evidence_use_score": 0.15,
                "tool_choice_score": 0.15,
                "efficiency_score": 0.15,
                "safety_score": 0.20,
                "instruction_adherence_score": 0.10,
            }
            
            overall = sum(result.get(k, 0.0) * v for k, v in weights.items())
            
            return CritiqueReport(
                correctness_score=result.get("correctness_score", 0.0),
                evidence_use_score=result.get("evidence_use_score", 0.0),
                tool_choice_score=result.get("tool_choice_score", 0.0),
                efficiency_score=result.get("efficiency_score", 0.0),
                safety_score=result.get("safety_score", 0.0),
                instruction_adherence_score=result.get("instruction_adherence_score", 0.0),
                overall_score=overall,
                issues=result.get("issues", []),
                suggestions=result.get("suggestions", []),
                requires_revision=result.get("requires_revision", overall < 0.7),
            )
            
        except (json.JSONDecodeError, Exception) as e:
            # Fallback: assume revision needed on parse failure
            return CritiqueReport(
                correctness_score=0.5,
                evidence_use_score=0.5,
                tool_choice_score=0.5,
                efficiency_score=0.5,
                safety_score=0.5,
                instruction_adherence_score=0.5,
                overall_score=0.5,
                issues=[f"Critique parse failed: {e}"],
                suggestions=["Retry with clearer output"],
                requires_revision=True,
            )
    
    def generate_revision_prompt(
        self,
        original_task: str,
        original_output: str,
        critique: CritiqueReport,
    ) -> str:
        """Generate prompt for revision based on critique."""
        issues_str = "\n".join(f"- {issue}" for issue in critique.issues)
        suggestions_str = "\n".join(f"- {s}" for s in critique.suggestions)
        
        return f"""Revise your previous output based on this critique.

Original Task: {original_task}

Original Output:
{original_output[:1500]}

Critique Issues:
{issues_str}

Improvement Suggestions:
{suggestions_str}

Revise to address ALL issues. Be surgical — only change what needs fixing."""


class OfflineOptimizer:
    """Offline improvement: collect failures, cluster, improve system."""
    
    def __init__(self, evidence_root: Path):
        self.evidence_root = evidence_root
        self.failure_log_path = evidence_root / "failure_clusters.jsonl"
        self.clusters: dict[str, FailureCluster] = {}
        self._load_existing()
    
    def _load_existing(self):
        """Load existing failure clusters."""
        if not self.failure_log_path.exists():
            return
        
        with open(self.failure_log_path, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    pattern = entry.get("pattern_name", "unknown")
                    
                    if pattern not in self.clusters:
                        self.clusters[pattern] = FailureCluster(
                            pattern_name=pattern,
                            occurrence_count=0,
                        )
                    
                    cluster = self.clusters[pattern]
                    cluster.occurrence_count += 1
                    cluster.examples.append(entry)
                    cluster.last_seen = entry.get("timestamp", time.time())
                    
                except json.JSONDecodeError:
                    continue
    
    def log_failure(
        self,
        task: str,
        agent_output: str,
        critique: CritiqueReport,
        context: dict[str, Any] | None = None,
    ):
        """Log a failure for offline analysis."""
        entry = {
            "timestamp": time.time(),
            "task": task,
            "agent_output": agent_output[:1000],
            "critique": critique.to_dict(),
            "context": context or {},
        }
        
        # Append to log
        with open(self.failure_log_path, 'a') as f:
            f.write(json.dumps(entry) + "\n")
        
        # Update in-memory clusters
        pattern = self._identify_pattern(critique)
        
        if pattern not in self.clusters:
            self.clusters[pattern] = FailureCluster(pattern_name=pattern, occurrence_count=0)
        
        cluster = self.clusters[pattern]
        cluster.occurrence_count += 1
        cluster.examples.append(entry)
        cluster.last_seen = time.time()
    
    def _identify_pattern(self, critique: CritiqueReport) -> str:
        """Identify failure pattern from critique."""
        # Simple pattern matching based on issues
        if any("tool" in issue.lower() for issue in critique.issues):
            return "tool_confusion"
        elif any("evidence" in issue.lower() or "context" in issue.lower() for issue in critique.issues):
            return "context_misuse"
        elif any("safety" in issue.lower() for issue in critique.issues):
            return "safety_violation"
        elif critique.correctness_score < 0.5:
            return "factual_error"
        elif critique.efficiency_score < 0.5:
            return "inefficient_execution"
        else:
            return "general_quality"
    
    def generate_improvements(self) -> dict[str, Any]:
        """Generate improvement recommendations from clustered failures."""
        improvements = {
            "prompt_improvements": [],
            "tool_improvements": [],
            "retrieval_improvements": [],
            "training_data_gaps": [],
        }
        
        for pattern, cluster in self.clusters.items():
            if cluster.occurrence_count < 3:
                continue  # Need at least 3 occurrences to act
            
            if pattern == "tool_confusion":
                improvements["tool_improvements"].append({
                    "pattern": "tool_confusion",
                    "occurrences": cluster.occurrence_count,
                    "recommendation": "Improve tool descriptions, add examples, reduce overlap",
                    "examples": len(cluster.examples),
                })
            
            elif pattern == "context_misuse":
                improvements["retrieval_improvements"].append({
                    "pattern": "context_misuse",
                    "occurrences": cluster.occurrence_count,
                    "recommendation": "Improve retrieval relevance scoring, add context filtering",
                    "examples": len(cluster.examples),
                })
            
            elif pattern == "factual_error":
                improvements["training_data_gaps"].append({
                    "pattern": "factual_error",
                    "occurrences": cluster.occurrence_count,
                    "recommendation": "Add domain-specific knowledge base, improve grounding",
                    "examples": len(cluster.examples),
                })
        
        return improvements
    
    def save_clusters(self):
        """Save updated clusters to disk."""
        output_path = self.evidence_root / "failure_cluster_summary.json"
        summary = {
            "total_failures": sum(c.occurrence_count for c in self.clusters.values()),
            "unique_patterns": len(self.clusters),
            "clusters": [
                {
                    "pattern": c.pattern_name,
                    "count": c.occurrence_count,
                    "first_seen": c.first_seen,
                    "last_seen": c.last_seen,
                    "root_cause": c.root_cause,
                    "proposed_fix": c.proposed_fix,
                }
                for c in self.clusters.values()
            ],
        }
        
        with open(output_path, 'w') as f:
            json.dump(summary, f, indent=2)


class EvaluatorOptimizerLoop:
    """Combines inline critique + offline optimization."""
    
    def __init__(
        self,
        critic_model_config: ModelConfig,
        evidence_root: Path,
        repo_root: Path,
    ):
        self.inline_critic = InlineCritic(critic_model_config, repo_root)
        self.offline_optimizer = OfflineOptimizer(evidence_root)
        self.repo_root = repo_root
    
    def execute_with_critique(
        self,
        task: str,
        agent_loop: AgentLoop,
        tools: list[Tool],
        context: list[str] | None = None,
        auto_repair: bool = True,
        max_repair_cycles: int = 2,
    ) -> tuple[LoopResult, CritiqueReport]:
        """Execute task with inline critique and optional auto-repair."""
        
        # Phase 1: Initial execution
        result = agent_loop.run(task, tools=tools, max_iterations=15)
        
        # Phase 2: Critique
        tool_calls = [
            {
                "name": s.tool_name,
                "arguments": s.tool_arguments,
                "error": s.tool_error,
            }
            for s in result.steps
            if s.kind == "tool_result"
        ]
        
        critique = self.inline_critic.critique(
            task=task,
            agent_output=result.final_response,
            context_used=context,
            tool_calls_made=tool_calls,
        )
        
        # Phase 3: Auto-repair if needed
        if auto_repair and critique.requires_revision:
            for cycle in range(max_repair_cycles):
                revision_prompt = self.inline_critic.generate_revision_prompt(
                    original_task=task,
                    original_output=result.final_response,
                    critique=critique,
                )
                
                result = agent_loop.run(revision_prompt, tools=tools, max_iterations=10)
                
                # Re-critique
                critique = self.inline_critic.critique(
                    task=task,
                    agent_output=result.final_response,
                    context_used=context,
                    tool_calls_made=tool_calls,  # Same tool calls
                )
                
                if not critique.requires_revision:
                    break
        
        # Phase 4: Log failure if still bad
        if critique.overall_score < 0.6:
            self.offline_optimizer.log_failure(
                task=task,
                agent_output=result.final_response,
                critique=critique,
                context={"context_snippets": context[:3]} if context else {},
            )
        
        return result, critique
    
    def get_improvement_report(self) -> dict[str, Any]:
        """Get offline improvement recommendations."""
        self.offline_optimizer.save_clusters()
        return self.offline_optimizer.generate_improvements()
