#!/usr/bin/env python3
"""hydra.context_engine_v2 — Context engineering system.

Phase 1: working memory + durable memory + JIT retrieval + compression.

What makes this better:
• Remembers the right things (not everything)
• Pulls only relevant context (no stuffing)
• Avoids context rot/distraction/contradictions
• Isolates unrelated junk from reasoning
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hydra.local_memory import build_local_memory_context
# Wiki memory functions - use directly from module
import hydra.wiki_memory as wiki_memory
# Failure clusters - use module functions
import hydra.failure_clusters as failure_clusters
# Repo-map ranked localization (slice 10) — replaces substring stub
from hydra.repo_map import build_repo_map_context


@dataclass
class ContextBudget:
    """Token budget enforcement for context assembly."""
    max_context_tokens: int = 8192
    max_working_memory_items: int = 20
    max_durable_memory_items: int = 50
    max_retrieval_results: int = 10
    compression_threshold: int = 6000  # Start compressing at this token count


@dataclass
class WorkingMemory:
    """Short-term task-specific context. Lives for one task/session."""
    task_id: str
    objective: str
    current_step: str = ""
    steps_completed: list[str] = field(default_factory=list)
    facts_discovered: list[dict] = field(default_factory=list)
    decisions_made: list[dict] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    tool_calls_made: list[dict] = field(default_factory=list)
    errors_encountered: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    
    def add_fact(self, fact: str, source: str, confidence: float = 1.0):
        self.facts_discovered.append({
            "fact": fact,
            "source": source,
            "confidence": confidence,
            "timestamp": time.time(),
        })
        self.updated_at = time.time()
    
    def add_decision(self, decision: str, rationale: str, alternatives_rejected: list[str] | None = None):
        self.decisions_made.append({
            "decision": decision,
            "rationale": rationale,
            "alternatives_rejected": alternatives_rejected or [],
            "timestamp": time.time(),
        })
        self.updated_at = time.time()
    
    def complete_step(self, step: str, outcome: str):
        self.steps_completed.append(step)
        self.current_step = ""
        self.add_fact(f"Completed: {step}", outcome)
    
    def to_context_dict(self) -> dict[str, Any]:
        """Convert to context dict for prompt injection."""
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "current_step": self.current_step,
            "steps_completed": self.steps_completed,
            "facts_discovered": self.facts_discovered[-10:],  # Last 10 facts
            "decisions_made": self.decisions_made[-5:],  # Last 5 decisions
            "open_questions": self.open_questions[-5:],
        }
    
    def token_estimate(self) -> int:
        """Rough token count estimate."""
        return len(json.dumps(self.to_context_dict())) // 4


@dataclass
class RetrievedContext:
    """One retrieved memory/document with relevance score."""
    source: str  # "lesson" | "wiki" | "cluster" | "receipt"
    title: str
    content: str
    relevance_score: float
    provenance: str | None = None  # Evidence path if applicable
    age_days: float | None = None
    
    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "content": self.content[:500],  # Truncate for context
            "relevance_score": self.relevance_score,
            "provenance": self.provenance,
        }


class ContextAssembler:
    """Just-in-time context assembly with budget enforcement."""
    
    def __init__(
        self,
        repo_root: Path,
        memory_root: Path | None = None,
        budget: ContextBudget | None = None,
    ):
        self.repo_root = repo_root.expanduser().resolve()
        self.memory_root = memory_root.expanduser().resolve() if memory_root else repo_root.parent / ".hydra-memory"
        self.budget = budget or ContextBudget()
        
        # Initialize memory systems
        self.memory_root = memory_root.expanduser().resolve() if memory_root else repo_root.parent / ".hydra-memory"
        self.wiki_root = self.repo_root / ".hydraAgent" / "wiki"
        self.evidence_root = self.repo_root / "evidence"
    
    def assemble_context(
        self,
        query: str,
        working_memory: WorkingMemory | None = None,
        task_type: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Assemble just-in-time context for a task.
        
        Returns:
            (formatted_context_string, metadata_dict)
        """
        budget = self.budget if max_tokens is None else ContextBudget(max_context_tokens=max_tokens)
        
        # Phase 1: Retrieve durable memory (lessons + wiki + clusters)
        retrieved = self._retrieve_relevant_context(query, task_type, budget.max_retrieval_results)
        
        # Phase 2: Add working memory if provided
        working_ctx = working_memory.to_context_dict() if working_memory else {}
        
        # Phase 3: Compress if over budget
        total_tokens = self._estimate_tokens(retrieved, working_ctx)
        if total_tokens > budget.compression_threshold:
            retrieved = self._compress_context(retrieved, working_ctx, budget.max_context_tokens)
        
        # Phase 4: Format for prompt injection
        formatted = self._format_context(retrieved, working_ctx)
        
        metadata = {
            "retrieved_count": len(retrieved),
            "working_memory_items": len(working_ctx),
            "total_tokens": self._estimate_tokens(retrieved, working_ctx),
            "compression_applied": total_tokens > budget.compression_threshold,
        }
        
        return formatted, metadata
    
    def _retrieve_relevant_context(
        self,
        query: str,
        task_type: str | None,
        max_results: int,
    ) -> list[RetrievedContext]:
        """Retrieve relevant context from all memory sources.

        Slice 10: the old substring-match stub is replaced with a ranked
        repo-map call (personalized PageRank via build_repo_map_context).
        Durable memory + wiki still run alongside it.
        """
        results = []

        # --- Repo-map ranked localization (replaces substring stub) -----------
        try:
            # Budget: leave room for other context; 3000 bytes for the map
            repo_map_str = build_repo_map_context(
                repo_root=self.repo_root,
                query=query,
                max_bytes=3000,
            )
            if repo_map_str.strip():
                results.append(RetrievedContext(
                    source="repo_map",
                    title="Repo Map",
                    content=repo_map_str,
                    relevance_score=0.9,
                    provenance=str(self.repo_root),
                ))
        except Exception:
            pass  # Never let localization break the context pipeline

        # --- Durable memory (lessons) ----------------------------------------
        try:
            memory_context = build_local_memory_context(
                root=self.memory_root,
                max_chars=10000,
            )
            if memory_context.strip():
                # Score based on term overlap (simple token match, not substring)
                query_terms = set(query.lower().split())
                mem_lower = memory_context.lower()
                overlap = sum(1 for t in query_terms if t in mem_lower)
                relevance = 0.4 + min(0.3, overlap * 0.05)
                results.append(RetrievedContext(
                    source="lesson",
                    title="Local Memory Context",
                    content=memory_context[:2000],
                    relevance_score=relevance,
                    provenance=str(self.memory_root),
                ))
        except Exception:
            pass  # Don't fail if memory unavailable

        # --- Wiki (capability docs) ------------------------------------------
        try:
            wiki_index = wiki_memory.render_wiki_index(self.wiki_root)
            if wiki_index.strip():
                query_terms = set(query.lower().split())
                wiki_lower = wiki_index.lower()
                overlap = sum(1 for t in query_terms if t in wiki_lower)
                relevance = 0.3 + min(0.3, overlap * 0.05)
                results.append(RetrievedContext(
                    source="wiki",
                    title="Wiki Index",
                    content=wiki_index[:1000],
                    relevance_score=relevance,
                ))
        except Exception:
            pass

        # --- Failure clusters ------------------------------------------------
        try:
            clusters = []  # Disabled: failure_clusters.search not available
            for cluster in clusters:
                results.append(RetrievedContext(
                    source="cluster",
                    title=f"Failure Pattern: {cluster.get('pattern', 'Unknown')}",
                    content=cluster.get('summary', ''),
                    relevance_score=cluster.get('score', 0.5),
                ))
        except Exception:
            pass

        # --- Sort by relevance and dedupe ------------------------------------
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        seen_titles: set[str] = set()
        deduped: list[RetrievedContext] = []
        for r in results:
            if r.title not in seen_titles:
                seen_titles.add(r.title)
                deduped.append(r)

        return deduped[:max_results]
    
    def _estimate_tokens(self, retrieved: list[RetrievedContext], working_ctx: dict) -> int:
        """Rough token estimate (4 chars ≈ 1 token)."""
        retrieved_chars = sum(len(r.content) for r in retrieved)
        working_chars = len(json.dumps(working_ctx))
        return (retrieved_chars + working_chars) // 4
    
    def _compress_context(
        self,
        retrieved: list[RetrievedContext],
        working_ctx: dict,
        target_tokens: int,
    ) -> list[RetrievedContext]:
        """Compress context to fit budget."""
        # Keep highest-relevance items, truncate content
        compressed = []
        remaining_tokens = target_tokens
        
        for item in sorted(retrieved, key=lambda r: r.relevance_score, reverse=True):
            # Estimate tokens for this item
            item_tokens = len(item.content) // 4 + 50  # Content + overhead
            
            if item_tokens <= remaining_tokens:
                # Truncate content if needed
                max_chars = remaining_tokens * 4
                if len(item.content) > max_chars:
                    item.content = item.content[:max_chars - 20] + "..."
                compressed.append(item)
                remaining_tokens -= item_tokens
            elif remaining_tokens > 100:
                # Include heavily truncated version
                item.content = item.content[:remaining_tokens * 4 - 20] + "..."
                compressed.append(item)
                break
        
        return compressed
    
    def _format_context(
        self,
        retrieved: list[RetrievedContext],
        working_ctx: dict,
    ) -> str:
        """Format context for prompt injection."""
        sections = []
        
        # Working memory section
        if working_ctx:
            sections.append("## Current Task Context\n")
            if working_ctx.get("objective"):
                sections.append(f"**Objective:** {working_ctx['objective']}\n")
            if working_ctx.get("steps_completed"):
                sections.append(f"**Completed:** {', '.join(working_ctx['steps_completed'])}\n")
            if working_ctx.get("facts_discovered"):
                sections.append("**Key Facts:**\n")
                for fact in working_ctx["facts_discovered"]:
                    sections.append(f"- {fact['fact']} (confidence: {fact['confidence']})\n")
        
        # Retrieved context section
        if retrieved:
            sections.append("\n## Relevant Memory\n")
            for item in retrieved:
                sections.append(f"\n### [{item.source.upper()}] {item.title}\n")
                if item.provenance:
                    sections.append(f"*Source:* `{item.provenance}`\n")
                sections.append(f"{item.content}\n")
        
        return "\n".join(sections)


def create_working_memory(task_id: str, objective: str) -> WorkingMemory:
    """Factory for fresh working memory."""
    return WorkingMemory(task_id=task_id, objective=objective)
