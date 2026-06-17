#!/usr/bin/env python3
"""hydra.traces — Phase 7: Observability and trace capture.

Captures complete execution traces for every task:
- Prompt/context snapshot
- Retrieved memory/docs
- Tool calls + outputs
- Verifier results
- Retries
- Final answer quality

Stores traces in evidence/traces/YYYY-MM-DD/ for later analysis and dashboard generation.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from enum import Enum


class TraceStatus(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    REPAIRED = "repaired"


@dataclass
class ToolCallTrace:
    """Trace of a single tool call."""
    tool_name: str
    arguments: dict[str, Any]
    output: Any
    error: Optional[str]
    duration_ms: int
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "output": self.output if isinstance(self.output, (str, int, float, bool, type(None))) else str(self.output)[:500],
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class VerificationTrace:
    """Trace of a verification check."""
    check_name: str
    passed: bool
    evidence: str
    error: Optional[str]
    duration_ms: int
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "check_name": self.check_name,
            "passed": self.passed,
            "evidence": self.evidence[:500] if self.evidence else "",
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class ContextSnapshot:
    """Snapshot of context used in a task."""
    lessons_retrieved: list[str] = field(default_factory=list)
    clusters_retrieved: list[str] = field(default_factory=list)
    promotions_retrieved: list[str] = field(default_factory=list)
    wiki_slices: list[str] = field(default_factory=list)
    token_budget: int = 0
    tokens_used: int = 0
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskTrace:
    """Complete trace for a single task execution."""
    task_id: str
    objective: str
    status: TraceStatus = TraceStatus.PARTIAL
    
    # Timing
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    total_duration_ms: int = 0
    
    # Model routing
    routing_decision: Optional[dict[str, Any]] = None
    model_used: str = ""
    
    # Context
    context_snapshot: Optional[ContextSnapshot] = None
    
    # Execution
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    verification_checks: list[VerificationTrace] = field(default_factory=list)
    
    # Quality
    repair_cycles: int = 0
    critique_scores: Optional[dict[str, float]] = None
    
    # Output
    final_answer: str = ""
    confidence: float = 0.0
    
    # Metadata
    repo_root: str = ""
    evidence_root: str = ""
    
    def add_tool_call(self, tool_call: ToolCallTrace):
        self.tool_calls.append(tool_call)
    
    def add_verification(self, verification: VerificationTrace):
        self.verification_checks.append(verification)
    
    def finalize(self, status: TraceStatus, final_answer: str, confidence: float = 0.0):
        self.end_time = time.time()
        self.total_duration_ms = int((self.end_time - self.start_time) * 1000)
        self.status = status
        self.final_answer = final_answer[:2000]
        self.confidence = confidence
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "status": self.status.value,
            "timing": {
                "start_time": self.start_time,
                "end_time": self.end_time,
                "total_duration_ms": self.total_duration_ms,
            },
            "routing": self.routing_decision,
            "model_used": self.model_used,
            "context": self.context_snapshot.to_dict() if self.context_snapshot else None,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "verification_checks": [vc.to_dict() for vc in self.verification_checks],
            "quality": {
                "repair_cycles": self.repair_cycles,
                "critique_scores": self.critique_scores,
                "confidence": self.confidence,
            },
            "final_answer": self.final_answer,
            "metadata": {
                "repo_root": self.repo_root,
                "evidence_root": self.evidence_root,
            },
        }


class TraceCapture:
    """Captures and stores execution traces."""
    
    def __init__(self, evidence_root: Path, repo_root: Path):
        self.evidence_root = Path(evidence_root)
        self.repo_root = Path(repo_root)
        self.traces_dir = self.evidence_root / "traces"
        self._ensure_dirs()
    
    def _ensure_dirs(self):
        """Ensure trace directories exist."""
        today = datetime.now().strftime("%Y-%m-%d")
        self.today_traces_dir = self.traces_dir / today
        self.today_traces_dir.mkdir(parents=True, exist_ok=True)
    
    def capture_task_start(self, task_id: str, objective: str) -> TaskTrace:
        """Start capturing a task trace."""
        self._ensure_dirs()
        
        trace = TaskTrace(
            task_id=task_id,
            objective=objective,
            repo_root=str(self.repo_root),
            evidence_root=str(self.evidence_root),
        )
        
        # Save initial trace
        self._save_trace(trace)
        
        return trace
    
    def _save_trace(self, trace: TaskTrace):
        """Save trace to disk."""
        today = datetime.now().strftime("%Y-%m-%d")
        trace_file = self.traces_dir / today / f"{trace.task_id}.json"
        
        with open(trace_file, 'w') as f:
            json.dump(trace.to_dict(), f, indent=2, default=str)
    
    def finalize_trace(self, trace: TaskTrace, status: TraceStatus, final_answer: str, confidence: float = 0.0):
        """Finalize and save a task trace."""
        trace.finalize(status, final_answer, confidence)
        self._save_trace(trace)


class TraceDashboard:
    """Generates dashboards from historical traces."""
    
    def __init__(self, traces_root: Path):
        self.traces_root = Path(traces_root)
    
    def load_traces(self, date_range: tuple[str, str] | None = None) -> list[dict[str, Any]]:
        """Load all traces within optional date range."""
        traces = []
        
        if not self.traces_root.exists():
            return traces
        
        # Get date directories
        date_dirs = sorted([d for d in self.traces_root.iterdir() if d.is_dir()])
        
        for date_dir in date_dirs:
            if date_range:
                start_date, end_date = date_range
                if not (start_date <= date_dir.name <= end_date):
                    continue
            
            # Load all trace files
            for trace_file in date_dir.glob("*.json"):
                try:
                    with open(trace_file, 'r') as f:
                        trace_data = json.load(f)
                        traces.append(trace_data)
                except (json.JSONDecodeError, OSError):
                    continue
        
        return traces
    
    def generate_summary(self, traces: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate summary dashboard from traces."""
        if not traces:
            return {"error": "No traces to analyze"}
        
        total = len(traces)
        success = sum(1 for t in traces if t.get("status") == "success")
        failure = sum(1 for t in traces if t.get("status") == "failure")
        repaired = sum(1 for t in traces if t.get("status") == "repaired")
        partial = sum(1 for t in traces if t.get("status") == "partial")
        
        total_duration = sum(t.get("timing", {}).get("total_duration_ms", 0) for t in traces)
        avg_duration = total_duration / total if total > 0 else 0
        
        total_repairs = sum(t.get("quality", {}).get("repair_cycles", 0) for t in traces)
        
        # Tool confusion analysis
        tool_calls_by_name: dict[str, int] = {}
        tool_errors: dict[str, int] = {}
        for trace in traces:
            for tc in trace.get("tool_calls", []):
                name = tc.get("tool_name", "unknown")
                tool_calls_by_name[name] = tool_calls_by_name.get(name, 0) + 1
                if tc.get("error"):
                    tool_errors[name] = tool_errors.get(name, 0) + 1
        
        # Model usage
        model_usage: dict[str, int] = {}
        for trace in traces:
            model = trace.get("model_used", "unknown")
            model_usage[model] = model_usage.get(model, 0) + 1
        
        # Confidence distribution
        confidences = [t.get("quality", {}).get("confidence", 0) for t in traces if t.get("quality", {}).get("confidence")]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0
        
        return {
            "summary": {
                "total_tasks": total,
                "success_rate": success / total if total > 0 else 0,
                "success": success,
                "failure": failure,
                "repaired": repaired,
                "partial": partial,
            },
            "performance": {
                "total_duration_ms": total_duration,
                "avg_duration_ms": avg_duration,
                "total_repairs": total_repairs,
                "avg_repairs_per_task": total_repairs / total if total > 0 else 0,
            },
            "tool_confusion_map": {
                "calls_by_tool": tool_calls_by_name,
                "errors_by_tool": tool_errors,
                "error_rate_by_tool": {k: tool_errors.get(k, 0) / tool_calls_by_name[k] for k in tool_calls_by_name},
            },
            "model_usage": model_usage,
            "quality": {
                "avg_confidence": avg_confidence,
                "confidence_distribution": self._bucket_confidences(confidences),
            },
        }
    
    def _bucket_confidences(self, confidences: list[float]) -> dict[str, int]:
        """Bucket confidences into ranges."""
        buckets = {"0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0, "0.6-0.8": 0, "0.8-1.0": 0}
        for c in confidences:
            if c < 0.2:
                buckets["0.0-0.2"] += 1
            elif c < 0.4:
                buckets["0.2-0.4"] += 1
            elif c < 0.6:
                buckets["0.4-0.6"] += 1
            elif c < 0.8:
                buckets["0.6-0.8"] += 1
            else:
                buckets["0.8-1.0"] += 1
        return buckets
    
    def generate_retry_hotspots(self, traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Identify retry hotspots from traces."""
        hotspots: dict[str, dict[str, Any]] = {}
        
        for trace in traces:
            repair_cycles = trace.get("quality", {}).get("repair_cycles", 0)
            if repair_cycles == 0:
                continue
            
            objective = trace.get("objective", "unknown")[:100]
            
            if objective not in hotspots:
                hotspots[objective] = {
                    "objective": objective,
                    "occurrences": 0,
                    "total_repairs": 0,
                    "examples": [],
                }
            
            hotspot = hotspots[objective]
            hotspot["occurrences"] += 1
            hotspot["total_repairs"] += repair_cycles
            if len(hotspot["examples"]) < 3:
                hotspot["examples"].append({
                    "task_id": trace.get("task_id"),
                    "repairs": repair_cycles,
                    "status": trace.get("status"),
                })
        
        # Sort by total repairs descending
        sorted_hotspots = sorted(hotspots.values(), key=lambda x: x["total_repairs"], reverse=True)
        
        return sorted_hotspots[:20]  # Top 20 hotspots
    
    def export_dashboard(self, output_path: Path | str):
        """Export full dashboard to JSON file."""
        traces = self.load_traces()
        summary = self.generate_summary(traces)
        hotspots = self.generate_retry_hotspots(traces)
        
        dashboard = {
            "generated_at": time.time(),
            "trace_count": len(traces),
            "summary": summary,
            "retry_hotspots": hotspots,
        }
        
        output_path = Path(output_path)
        with open(output_path, 'w') as f:
            json.dump(dashboard, f, indent=2)
        
        return dashboard


def capture_trace_for_task(
    task_id: str,
    objective: str,
    repo_root: Path,
    evidence_root: Path,
) -> tuple[TaskTrace, TraceCapture]:
    """Convenience function to start trace capture for a task."""
    capture = TraceCapture(evidence_root, repo_root)
    trace = capture.capture_task_start(task_id, objective)
    return trace, capture
