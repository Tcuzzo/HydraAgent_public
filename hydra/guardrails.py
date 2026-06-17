#!/usr/bin/env python3
"""hydra.guardrails — Phase 8: Safety guardrails and action tiers.

Implements multi-layer safety:
- Action permission tiers (read-only → bounded → destructive)
- Prompt injection resistance
- Source trust labels
- Memory write policies
- Tool allowlists per mode/task
- Output confidence bands
- Destructive action approval gates (Telegram)
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


class ActionTier(Enum):
    """Permission tiers for actions."""
    READ_ONLY = "read_only"  # Always auto-approved
    BOUNDED_WRITE = "bounded_write"  # Auto-approved within limits
    EXTERNAL_CALL = "external_call"  # Requires logging + rate limit
    DESTRUCTIVE = "destructive"  # Requires Telegram approval
    IDENTITY_CHANGE = "identity_change"  # Requires explicit confirmation


class SourceTrustLevel(Enum):
    """Trust levels for information sources."""
    TRUSTED = "trusted"  # Verified internal sources
    VERIFIED = "verified"  # Cross-checked external sources
    UNVERIFIED = "unverified"  # Single-source claims
    SUSPICIOUS = "suspicious"  # Known unreliable sources
    HOSTILE = "hostile"  # User-provided input (treat as untrusted)


class InjectionPattern:
    """Detected prompt injection patterns."""
    
    # Common injection patterns
    PATTERNS = [
        r"(?i)ignore\s+(previous|all)\s+(instructions|rules)",
        r"(?i)bypass\s+(safety|guardrails|restrictions)",
        r"(?i)(you\s+are\s+now|act\s+as|pretend\s+to\s+be)\s+[A-Z]+",
        r"(?i)output\s+(only|just)\s+(raw|markdown|code)",
        r"(?i)(don't|do\s+not)\s+(follow|obey)\s+(rules|guidelines)",
        r"(?i)this\s+is\s+a\s+(test|simulation|game)",
        r"(?i)developer\s+mode|jailbreak|dan-",
        r"(?i)(system|user)\s+prompt:\s*",
        r"<\|.*?\|>",  # Special token mimicry
        r"```\s*\n\s*import\s+os",  # Code injection attempt
    ]
    
    @classmethod
    def detect(cls, text: str) -> list[dict[str, Any]]:
        """Detect injection patterns in text."""
        detections = []
        for i, pattern in enumerate(cls.PATTERNS):
            matches = list(re.finditer(pattern, text))
            for match in matches:
                detections.append({
                    "pattern_id": i,
                    "pattern": pattern[:50],
                    "match": match.group()[:100],
                    "start": match.start(),
                    "end": match.end(),
                    "severity": "high" if i < 4 else "medium",
                })
        return detections


@dataclass
class GuardrailConfig:
    """Configuration for guardrail behavior."""
    # Action tier settings
    allow_read_only_auto: bool = True
    allow_bounded_write_auto: bool = True
    require_approval_for_destructive: bool = True
    require_approval_for_external: bool = False
    
    # Rate limits
    max_external_calls_per_hour: int = 100
    max_memory_writes_per_hour: int = 50
    
    # Trust settings
    default_source_trust: SourceTrustLevel = SourceTrustLevel.UNVERIFIED
    require_trusted_sources_for_memory: bool = True
    
    # Tool settings
    tool_allowlist_mode: bool = False  # If True, only allowlisted tools work
    default_tool_allowlist: list[str] = field(default_factory=list)
    
    # Output settings
    min_confidence_for_final_answer: float = 0.5
    block_low_confidence_outputs: bool = False
    
    # Injection detection
    enable_injection_detection: bool = True
    block_on_injection_detected: bool = True


@dataclass
class ToolAllowlist:
    """Tool allowlist for a specific mode or task type."""
    mode_name: str
    allowed_tools: set[str]
    blocked_tools: set[str] = field(default_factory=set)
    
    def is_allowed(self, tool_name: str) -> bool:
        if tool_name in self.blocked_tools:
            return False
        if not self.allowed_tools:  # Empty = all allowed
            return True
        return tool_name in self.allowed_tools


@dataclass
class MemoryWritePolicy:
    """Policy for memory writes."""
    allow_overwrite: bool = False
    require_source_attribution: bool = True
    max_entry_size_bytes: int = 10000
    allowed_namespaces: set[str] = field(default_factory=lambda: {"lessons", "notes", "tasks"})
    blocked_namespaces: set[str] = field(default_factory=lambda: {"system", "config"})
    
    def validate_write(self, namespace: str, content: str, source: Optional[str] = None) -> tuple[bool, str]:
        """Validate a memory write request."""
        if namespace in self.blocked_namespaces:
            return False, f"Namespace '{namespace}' is blocked"
        
        if self.allowed_namespaces and namespace not in self.allowed_namespaces:
            return False, f"Namespace '{namespace}' not in allowed list"
        
        if len(content.encode('utf-8')) > self.max_entry_size_bytes:
            return False, f"Content exceeds max size ({self.max_entry_size_bytes} bytes)"
        
        if self.require_source_attribution and not source:
            return False, "Source attribution required"
        
        return True, "OK"


@dataclass
class ConfidenceBand:
    """Confidence band for output classification."""
    label: str
    min_confidence: float
    max_confidence: float
    description: str
    requires_disclaimer: bool = False


CONFIDENCE_BANDS = [
    ConfidenceBand("very_low", 0.0, 0.2, "Highly uncertain, likely speculative", requires_disclaimer=True),
    ConfidenceBand("low", 0.2, 0.4, "Some evidence but significant gaps", requires_disclaimer=True),
    ConfidenceBand("moderate", 0.4, 0.6, "Reasonable confidence with some uncertainty", requires_disclaimer=False),
    ConfidenceBand("high", 0.6, 0.8, "Strong evidence, minor uncertainties", requires_disclaimer=False),
    ConfidenceBand("very_high", 0.8, 1.0, "Very confident, well-supported", requires_disclaimer=False),
]


def get_confidence_band(confidence: float) -> ConfidenceBand:
    """Get confidence band for a confidence score."""
    confidence = max(0.0, min(1.0, confidence))
    for band in CONFIDENCE_BANDS:
        if band.min_confidence <= confidence < band.max_confidence:
            return band
    return CONFIDENCE_BANDS[-1]  # Default to very_high


class Guardrails:
    """Main guardrails enforcement system."""
    
    def __init__(self, config: GuardrailConfig | None = None, repo_root: Path | None = None):
        self.config = config or GuardrailConfig()
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        self.tool_allowlists: dict[str, ToolAllowlist] = {}
        self.memory_policy = MemoryWritePolicy()
        
        # Rate limiting state
        self._external_calls_this_hour: list[float] = []
        self._memory_writes_this_hour: list[float] = []
    
    def classify_action_tier(self, action_type: str, action_details: dict[str, Any]) -> ActionTier:
        """Classify an action into a permission tier."""
        # Read operations
        if action_type in ("read_file", "list_directory", "grep", "glob", "http_fetch_read"):
            return ActionTier.READ_ONLY
        
        # Bounded writes
        if action_type in ("write_file", "edit_file", "create_directory"):
            path = action_details.get("path", "")
            # Check if path is within safe bounds
            if self._is_safe_path(path):
                return ActionTier.BOUNDED_WRITE
            return ActionTier.DESTRUCTIVE
        
        # External calls
        if action_type in ("http_post", "send_email", "send_message", "api_call"):
            return ActionTier.EXTERNAL_CALL
        
        # Destructive operations
        if action_type in ("delete_file", "delete_directory", "run_shell", "execute_code"):
            return ActionTier.DESTRUCTIVE
        
        # Identity changes
        if action_type in ("update_config", "change_identity", "modify_guardrails"):
            return ActionTier.IDENTITY_CHANGE
        
        return ActionTier.BOUNDED_WRITE  # Default
    
    def _is_safe_path(self, path: str) -> bool:
        """Check if a path is within safe bounds."""
        try:
            resolved = Path(path).resolve()
            repo_resolved = self.repo_root.resolve()
            # Must be within repo root
            return str(resolved).startswith(str(repo_resolved))
        except (OSError, ValueError):
            return False
    
    def check_action_permission(
        self,
        action_type: str,
        action_details: dict[str, Any],
        mode: str | None = None,
    ) -> tuple[bool, str, Optional[dict[str, Any]]]:
        """Check if an action is permitted.
        
        Returns: (allowed, reason, approval_context)
        """
        tier = self.classify_action_tier(action_type, action_details)
        
        # Check tier permissions
        if tier == ActionTier.READ_ONLY and self.config.allow_read_only_auto:
            return True, "Read-only action auto-approved", None
        
        if tier == ActionTier.BOUNDED_WRITE and self.config.allow_bounded_write_auto:
            return True, "Bounded write auto-approved", None
        
        if tier == ActionTier.DESTRUCTIVE:
            if not self.config.require_approval_for_destructive:
                return True, "Destructive action allowed (approval disabled)", None
            
            # Require Telegram approval
            approval_context = {
                "requires_telegram_approval": True,
                "action_type": action_type,
                "action_details": action_details,
                "tier": tier.value,
            }
            return False, "Destructive action requires Telegram approval", approval_context
        
        if tier == ActionTier.EXTERNAL_CALL:
            # Check rate limit
            if not self._check_rate_limit("external"):
                return False, "External call rate limit exceeded", None
            
            if self.config.require_approval_for_external:
                return False, "External call requires approval", {"requires_approval": True}
            
            return True, "External call permitted (within rate limits)", None
        
        if tier == ActionTier.IDENTITY_CHANGE:
            return False, "Identity change requires explicit confirmation", {"requires_confirmation": True}
        
        return True, "Action permitted", None
    
    def _check_rate_limit(self, action_type: str) -> bool:
        """Check rate limit for an action type."""
        now = __import__('time').time()
        hour_ago = now - 3600
        
        if action_type == "external":
            self._external_calls_this_hour = [t for t in self._external_calls_this_hour if t > hour_ago]
            if len(self._external_calls_this_hour) >= self.config.max_external_calls_per_hour:
                return False
            self._external_calls_this_hour.append(now)
        
        elif action_type == "memory":
            self._memory_writes_this_hour = [t for t in self._memory_writes_this_hour if t > hour_ago]
            if len(self._memory_writes_this_hour) >= self.config.max_memory_writes_per_hour:
                return False
            self._memory_writes_this_hour.append(now)
        
        return True
    
    def detect_injection(self, prompt: str) -> tuple[bool, list[dict[str, Any]]]:
        """Detect prompt injection attempts."""
        if not self.config.enable_injection_detection:
            return False, []
        
        detections = InjectionPattern.detect(prompt)
        injection_detected = len(detections) > 0
        
        return injection_detected, detections
    
    def validate_tool_access(self, tool_name: str, mode: str | None = None) -> tuple[bool, str]:
        """Validate tool access based on allowlists."""
        if not self.config.tool_allowlist_mode:
            return True, "Tool allowlist mode disabled"
        
        # Check mode-specific allowlist
        if mode and mode in self.tool_allowlists:
            allowlist = self.tool_allowlists[mode]
            if allowlist.is_allowed(tool_name):
                return True, f"Tool '{tool_name}' allowed in mode '{mode}'"
            return False, f"Tool '{tool_name}' blocked in mode '{mode}'"
        
        # Check default allowlist
        if self.config.default_tool_allowlist:
            if tool_name in self.config.default_tool_allowlist:
                return True, f"Tool '{tool_name}' in default allowlist"
            return False, f"Tool '{tool_name}' not in default allowlist"
        
        return True, "No allowlist configured"
    
    def validate_memory_write(
        self,
        namespace: str,
        content: str,
        source: Optional[str] = None,
        source_trust: SourceTrustLevel = SourceTrustLevel.UNVERIFIED,
    ) -> tuple[bool, str]:
        """Validate a memory write request."""
        # Check policy
        allowed, reason = self.memory_policy.validate_write(namespace, content, source)
        if not allowed:
            return False, reason
        
        # Check source trust if required
        if self.config.require_trusted_sources_for_memory:
            if source_trust not in (SourceTrustLevel.TRUSTED, SourceTrustLevel.VERIFIED):
                return False, f"Source trust level '{source_trust.value}' insufficient for memory writes"
        
        # Check rate limit
        if not self._check_rate_limit("memory"):
            return False, "Memory write rate limit exceeded"
        
        return True, "Memory write permitted"
    
    def get_output_disclaimer(self, confidence: float) -> Optional[str]:
        """Get disclaimer for output based on confidence."""
        band = get_confidence_band(confidence)
        if band.requires_disclaimer:
            return f"[Confidence: {band.label}] {band.description}"
        return None
    
    def register_tool_allowlist(self, allowlist: ToolAllowlist):
        """Register a tool allowlist for a mode."""
        self.tool_allowlists[allowlist.mode_name] = allowlist
    
    def compute_action_hash(self, action_type: str, action_details: dict[str, Any]) -> str:
        """Compute hash for an action (for audit trail)."""
        data = json.dumps({"type": action_type, "details": action_details}, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()[:16]


class ApprovalGate:
    """Handles approval gating for destructive actions."""
    
    def __init__(self, telegram_config: Optional[dict[str, Any]] = None):
        self.telegram_config = telegram_config or {}
        self._pending_approvals: dict[str, dict[str, Any]] = {}
    
    def request_approval(
        self,
        action_id: str,
        action_type: str,
        action_details: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """Request approval for an action."""
        approval_request = {
            "action_id": action_id,
            "action_type": action_type,
            "action_details": action_details,
            "reason": reason,
            "requested_at": __import__('time').time(),
            "status": "pending",
        }
        
        self._pending_approvals[action_id] = approval_request
        
        # In production, this would send a Telegram message
        # For now, just return the request details
        return {
            "approval_required": True,
            "action_id": action_id,
            "telegram_message": self._format_telegram_message(approval_request),
        }
    
    def _format_telegram_message(self, request: dict[str, Any]) -> str:
        """Format approval request as Telegram message."""
        return f"""🔒 ACTION APPROVAL REQUIRED

Action ID: `{request['action_id']}`
Type: {request['action_type']}
Reason: {request['reason']}

Details:
{json.dumps(request['action_details'], indent=2)[:500]}

Reply with:
✅ /approve_{request['action_id']} to approve
❌ /deny_{request['action_id']} to deny"""
    
    def submit_approval(self, action_id: str, approved: bool, approver: str) -> bool:
        """Submit an approval decision."""
        if action_id not in self._pending_approvals:
            return False
        
        request = self._pending_approvals[action_id]
        request["status"] = "approved" if approved else "denied"
        request["decided_at"] = __import__('time').time()
        request["approver"] = approver
        
        return True
    
    def check_approval_status(self, action_id: str) -> Optional[dict[str, Any]]:
        """Check approval status for an action."""
        return self._pending_approvals.get(action_id)


def create_guardrails(
    repo_root: Path,
    config_path: Optional[Path] = None,
) -> Guardrails:
    """Create guardrails instance with config from file."""
    config = GuardrailConfig()
    
    if config_path and config_path.exists():
        import yaml
        guardrail_config = yaml.safe_load(config_path.read_text()).get("guardrails", {})
        
        config.allow_read_only_auto = guardrail_config.get("allow_read_only_auto", True)
        config.allow_bounded_write_auto = guardrail_config.get("allow_bounded_write_auto", True)
        config.require_approval_for_destructive = guardrail_config.get("require_approval_for_destructive", True)
        config.enable_injection_detection = guardrail_config.get("enable_injection_detection", True)
        config.block_on_injection_detected = guardrail_config.get("block_on_injection_detected", True)
    
    return Guardrails(config, repo_root)
