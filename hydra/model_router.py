#!/usr/bin/env python3
"""hydra.model_router — Phase 4: Model routing.

Don't use one model for everything. Route by task complexity:
• Fast cheap model: classification/routing/simple cleanup
• Strong reasoning model: planning/debugging/synthesis  
• Verifier/judge model: critique/rubric scoring
• Vision model (optional): UI/screenshot/file inspection
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from hydra.llm import LlmError, OllamaClient, ChatMessage
from hydra.providers import ProviderError, make_client
from hydra.model_routing import load_routing


# Same lock the local GPU job holds while a GPU job runs
# (hydra/studio/adapters/gpu_job.py: _DEFAULT_GPU_LOCK). Kept in sync by path,
# not import, so the router has no dependency on the studio adapter.
_GPU_LOCK_PATH = os.path.expanduser("~/.cache/hydra/gpu_job.lock")


def _gpu_busy() -> bool:
    """True if a GPU job (e.g. a local GPU job) currently holds the
    cross-process GPU lock.

    The single local GPU can only hold one model at a time. When a video job has
    the GPU, the local-GPU reader (qwen2.5-coder on the GPU) must yield and the
    read/classify call routes to cloud instead of contending (fall back to cloud
    when the GPU is busy). Non-blocking and
    side-effect free: it test-acquires the lock and immediately releases it.
    """
    lock_path = os.environ.get("HYDRA_GPU_LOCK") or _GPU_LOCK_PATH
    if not os.path.exists(lock_path):
        return False  # no lock file -> no GPU job has ever run -> not busy
    try:
        import fcntl  # POSIX-only; lazy so non-POSIX imports of this module work
    except ImportError:
        return False
    try:
        fd = open(lock_path, "a+")
    except OSError:
        return False
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)  # acquired -> free; release at once
        return False
    except OSError:
        return True  # could not acquire -> held by a GPU job -> busy
    finally:
        fd.close()


class TaskComplexity(Enum):
    SIMPLE = "simple"  # Classification, lookup, simple transforms
    MODERATE = "moderate"  # Standard coding, research, analysis
    COMPLEX = "complex"  # Long-horizon, ambiguous, high-stakes
    CRITICAL = "critical"  # Irreversible actions, security, money


@dataclass
class ModelConfig:
    """Configuration for one model in the routing stack."""
    name: str
    provider: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    cost_per_1m_tokens: float = 0.0  # For cost tracking
    latency_target_ms: int = 2000  # Expected latency
    
    # Capabilities
    supports_tools: bool = True
    supports_vision: bool = False
    supports_json_mode: bool = True


@dataclass 
class RoutingDecision:
    """Result of task routing classification."""
    complexity: TaskComplexity
    recommended_model: str
    reasoning: str
    estimated_cost_usd: float
    estimated_latency_ms: int
    requires_verifier: bool = True
    requires_human_approval: bool = False


class ModelRouter:
    """Routes tasks to appropriate models based on complexity."""
    
    # Cloud providers tried (in order) before any local downgrade, so a single
    # misconfigured cloud provider never silently pins the agent to local. Sourced
    # from hydra/model_routing.yaml (cloud_fallback_ladder) — editing the ladder
    # there changes it here, no Python edit. The ladder/downgrade LOGIC in
    # _create_client (last_substitution + WARNING on downgrade) is unchanged; only
    # this data moved to YAML.
    CLOUD_FALLBACKS: tuple[str, ...] = tuple(load_routing().cloud_fallback_ladder)

    def __init__(self, config_path: Path | None = None, env_dir: str | Path | None = None):
        _default_config = Path(os.environ.get("HYDRA_CONFIG", "")).expanduser().resolve() if os.environ.get("HYDRA_CONFIG") else Path.home() / ".hydra" / "hydra.yaml"
        self.config_path = config_path or _default_config
        self.env_dir = env_dir
        self.models: dict[str, ModelConfig] = {}
        # Records the most recent provider resolution so callers (and the
        # operator) can see whether cloud was used or it downgraded to local —
        # the downgrade is never silent (bug #11).
        self.last_substitution: dict[str, Any] = {
            "requested": None, "used": None, "downgraded_to_local": False, "note": "",
        }
        self._load_config()
    
    def _load_config(self):
        """Load model configs from hydra.yaml."""
        if not self.config_path.exists():
            self._load_defaults()
            return
        
        config = yaml.safe_load(self.config_path.read_text())
        agentic = config.get("agentic", {})
        roles = agentic.get("roles", {})

        # When a role omits provider/model, fall back to the single source of
        # truth (hydra/model_routing.yaml) instead of bare literals, so the
        # defaults can never drift from the rest of the routing policy.
        routing = load_routing()
        worker_provider, worker_model = routing.role_pair("worker")
        router_provider, router_model = routing.role_pair("router")

        # hydra.yaml may define planner/chat/doer/worker but omit router. If we
        # then classify with "first configured role", simple/read turns burn a
        # cloud planner call just to decide they are simple. Keep the router lane
        # explicit and local from the SSOT even when the config file is partial.
        if "router" not in roles:
            router_entry = routing.role_entry("router")
            self.models["router"] = ModelConfig(
                name="router",
                provider=router_provider,
                model=router_model,
                base_url=router_entry.base_url or "http://127.0.0.1:11434",
                max_tokens=512,
                temperature=0.0,
            )

        # Load role-based models
        for role_name, role_config in roles.items():
            provider = role_config.get("provider", worker_provider)
            model = role_config.get("model", worker_model)

            self.models[role_name] = ModelConfig(
                name=role_name,
                provider=provider,
                model=model,
                base_url=role_config.get("base_url"),
                api_key_env=role_config.get("api_key_env"),
            )

        # Add verifier model (auditor role or default — default from the YAML).
        if "auditor" not in self.models:
            auditor_provider, auditor_model = routing.role_pair("auditor")
            self.models["auditor"] = ModelConfig(
                name="auditor",
                provider=auditor_provider,
                model=auditor_model,
                base_url="https://api.ollama.cloud",
            )
    
    def _load_defaults(self):
        """Load default model stack.

        The (provider, model) for each role is the single source of truth in
        hydra/model_routing.yaml (routing.roles) — editing a model name there
        changes it here, no Python edit. The per-role runtime KNOBS (base_url,
        max_tokens, temperature) are router tuning, not routing policy, so they
        stay in code. Falls back to the loader's frozen DEFAULT if the YAML is
        missing/invalid, so this never crashes and never picks a different model.
        """
        routing = load_routing()

        def _pm(role: str) -> tuple[str, str]:
            return routing.role_pair(role)

        router_provider, router_model = _pm("router")
        planner_provider, planner_model = _pm("planner")
        doer_provider, doer_model = _pm("doer")
        worker_provider, worker_model = _pm("worker")
        auditor_provider, auditor_model = _pm("auditor")
        self.models = {
            "router": ModelConfig(
                name="router",
                provider=router_provider,
                model=router_model,
                base_url="http://127.0.0.1:11434",
                max_tokens=512,
                temperature=0.0,
            ),
            "planner": ModelConfig(
                name="planner",
                provider=planner_provider,
                model=planner_model,
                base_url="https://api.ollama.cloud",
                max_tokens=8192,
                temperature=0.2,
            ),
            "doer": ModelConfig(
                name="doer",
                provider=doer_provider,
                model=doer_model,
                base_url="https://api.ollama.cloud",
                max_tokens=4096,
                temperature=0.0,
            ),
            "worker": ModelConfig(
                name="worker",
                provider=worker_provider,
                model=worker_model,
                base_url="http://127.0.0.1:11434",  # SSOT: hydra/model_routing.yaml (unified 2026-06-11)
                max_tokens=2048,
                temperature=0.0,
            ),
            "auditor": ModelConfig(
                name="auditor",
                provider=auditor_provider,
                model=auditor_model,
                base_url="https://api.ollama.cloud",
                max_tokens=2048,
                temperature=0.0,
            ),
        }
    
    def classify_task(self, task_description: str) -> RoutingDecision:
        """Classify task complexity and recommend model."""
        if not self.models:
            complexity = self._heuristic_classification(task_description)
            return RoutingDecision(
                complexity=complexity,
                recommended_model=self._select_model(complexity),
                reasoning="Heuristic fallback (no models configured)",
                estimated_cost_usd=0.0,
                estimated_latency_ms=0,
                requires_verifier=complexity != TaskComplexity.SIMPLE,
                requires_human_approval=complexity == TaskComplexity.CRITICAL,
            )

        # GPU-busy: a local job holds the GPU. Skip the local 'router' model
        # and let _select_model reroute the read to cloud so nothing contends.
        if _gpu_busy():
            complexity = self._heuristic_classification(task_description)
            recommended = self._select_model(complexity)  # cloud-rerouted
            selected = self.models.get(recommended) or next(iter(self.models.values()))
            return RoutingDecision(
                complexity=complexity,
                recommended_model=recommended,
                reasoning="Heuristic classify (GPU busy — yielding the GPU to the "
                          "video job; read routed to cloud).",
                estimated_cost_usd=self._estimate_cost(recommended, complexity),
                estimated_latency_ms=getattr(selected, "latency_target_ms", 0),
                requires_verifier=complexity != TaskComplexity.SIMPLE,
                requires_human_approval=complexity == TaskComplexity.CRITICAL,
            )

        # Use fast router model for classification
        router_model = self.models.get("router") or list(self.models.values())[0]
        client = self._create_client(router_model)
        
        prompt = f"""Classify this task's complexity.

Task: {task_description}

Complexity levels:
- simple: Lookup, classification, simple transform, single tool call
- moderate: Standard coding, research, multi-step but clear
- complex: Long-horizon, ambiguous, requires planning, multiple files
- critical: Irreversible actions, security-sensitive, financial, legal

Output JSON exactly:
{{
  "complexity": "simple|moderate|complex|critical",
  "reasoning": "brief explanation",
  "requires_verifier": true/false,
  "requires_human_approval": true/false
}}"""
        
        messages = [ChatMessage(role="user", content=prompt)]
        try:
            response = client.chat(
                messages,
                model=router_model.model,
                max_tokens=256,
                temperature=0.0,
                timeout=10.0,
            )
            result = json.loads(response.content.strip())
            complexity = TaskComplexity(result.get("complexity", "moderate"))
            requires_verifier = result.get("requires_verifier", True)
            requires_human_approval = result.get("requires_human_approval", False)
            reasoning = result.get("reasoning", "Auto-classified")
        except (json.JSONDecodeError, KeyError, ValueError, LlmError, ProviderError):
            complexity = self._heuristic_classification(task_description)
            requires_verifier = complexity != TaskComplexity.SIMPLE
            requires_human_approval = complexity == TaskComplexity.CRITICAL
            reasoning = "Heuristic fallback (router unavailable or invalid response)"
        
        # Select recommended model
        recommended = self._select_model(complexity)
        
        # Estimate cost and latency
        estimated_cost = self._estimate_cost(recommended, complexity)
        selected = self.models.get(recommended) or next(iter(self.models.values()))
        if recommended not in self.models:
            recommended = selected.name
        estimated_latency = selected.latency_target_ms
        
        return RoutingDecision(
            complexity=complexity,
            recommended_model=recommended,
            reasoning=reasoning,
            estimated_cost_usd=estimated_cost,
            estimated_latency_ms=estimated_latency,
            requires_verifier=requires_verifier,
            requires_human_approval=requires_human_approval,
        )
    
    def _heuristic_classification(self, task: str) -> TaskComplexity:
        """Fallback heuristic classification."""
        task_lower = task.lower()
        
        # Critical keywords
        critical_keywords = ["delete", "drop", "destroy", "rm -rf", "security", "password", "secret", "payment", "money"]
        if any(kw in task_lower for kw in critical_keywords):
            return TaskComplexity.CRITICAL
        
        # Complex keywords
        complex_keywords = ["refactor", "architecture", "debug", "investigate", "analyze", "research", "synthesize"]
        if any(kw in task_lower for kw in complex_keywords):
            return TaskComplexity.COMPLEX
        
        # Simple keywords
        simple_keywords = ["read", "list", "show", "what", "where", "find"]
        if any(kw in task_lower for kw in simple_keywords):
            return TaskComplexity.SIMPLE
        
        return TaskComplexity.MODERATE
    
    def _role_is_local(self, role: str) -> bool:
        """True if a role resolves to a model on the local GPU (127.0.0.1)."""
        cfg = self.models.get(role)
        base = (getattr(cfg, "base_url", "") or "") if cfg else ""
        return "127.0.0.1" in base or "localhost" in base

    def _cloud_substitute_role(self) -> str:
        """A cloud role to stand in for a local role when the GPU is busy.
        Prefer the cloud 'doer' (qwen3.5:cloud); else any configured non-local
        role; else 'doer' as a last-resort label."""
        for role in ("doer", "planner", "auditor"):
            if role in self.models and not self._role_is_local(role):
                return role
        return "doer"

    def _gpu_busy_reroute(self, role: str) -> str:
        """If `role` is local-GPU and a video job holds the GPU, reroute to cloud.

        The local runner can only hold one model at a time, so a local read must
        yield when the GPU is busy and go to cloud instead of contending. Never
        silent — logged at WARNING and recorded in ``last_substitution``.
        """
        if self._role_is_local(role) and _gpu_busy():
            cloud = self._cloud_substitute_role()
            if cloud != role:
                logging.getLogger(__name__).warning(
                    "GPU busy (video job holds the lock) — rerouting local '%s' "
                    "read to cloud '%s' instead of contending on the GPU.",
                    role, cloud,
                )
                self.last_substitution = {
                    "requested": role, "used": cloud,
                    "downgraded_to_local": False,
                    "note": "gpu_busy_cloud_fallback",
                }
                return cloud
        return role

    def _select_model(self, complexity: TaskComplexity) -> str:
        """Select best model for complexity level (cloud-rerouted if GPU busy)."""
        if complexity == TaskComplexity.SIMPLE:
            role = "worker"  # Fast, cheap, local GPU
        elif complexity == TaskComplexity.MODERATE:
            role = "doer"  # Balanced
        elif complexity == TaskComplexity.COMPLEX:
            role = "planner"  # Strong reasoning
        else:  # CRITICAL
            role = "planner"  # Strongest available
        return self._gpu_busy_reroute(role)
    
    def _estimate_cost(self, model_name: str, complexity: TaskComplexity) -> float:
        """Estimate cost in USD."""
        model = self.models.get(model_name)
        if not model:
            return 0.0
        
        # Rough token estimates by complexity
        token_estimates = {
            TaskComplexity.SIMPLE: 500,
            TaskComplexity.MODERATE: 2000,
            TaskComplexity.COMPLEX: 8000,
            TaskComplexity.CRITICAL: 10000,
        }
        
        tokens = token_estimates.get(complexity, 2000)
        return (tokens / 1_000_000) * model.cost_per_1m_tokens
    
    def _create_client(self, model_config: ModelConfig) -> Any:
        """Create an LLM client for a model config, cloud-first.

        Returns whatever ``make_client`` builds for the resolved provider: an
        ``OllamaClient`` for HTTP providers, or a duck-typed ``CodexClient``
        (local CLI) for the ``codex`` provider. The annotation is ``Any``
        because both share only the ``.chat()``/``.list_models()`` surface the
        caller depends on, not a common base class.

        Ladder: the configured provider → other configured CLOUD providers →
        local ollama (last resort). A downgrade to local is recorded in
        ``self.last_substitution`` and logged at WARNING — it is never silent,
        so the operator always knows when the agent is on local vs cloud.
        """
        log = logging.getLogger(__name__)
        requested = model_config.provider

        def _mk(provider: str):
            # Pass env_dir only when set, so callers/mocks with a strict
            # make_client(provider) signature keep working.
            if self.env_dir is None:
                return make_client(provider)
            return make_client(provider, env_dir=self.env_dir)

        # 1. The configured provider. For mundane/read tasks this is already a
        #    local model (free, reliable) — we return it as-is, no cloud forced.
        first_err: Exception | None = None
        try:
            client, cfg = _mk(requested)
            self.last_substitution = {
                "requested": requested, "used": getattr(cfg, "name", requested),
                "downgraded_to_local": False, "note": "",
            }
            return client
        except Exception as exc:  # ProviderError or any construction error
            first_err = exc

        # 2. Other CLOUD providers (by provider name) before touching local.
        #    This loop handles provider-level fallbacks (e.g. a second cloud provider).
        for candidate in self.CLOUD_FALLBACKS:
            if candidate == requested or candidate == "ollama":
                continue
            try:
                client, cfg = _mk(candidate)
            except Exception:
                continue
            log.warning(
                "provider %r unavailable (%s); using cloud provider %r instead",
                requested, first_err, getattr(cfg, "name", candidate),
            )
            self.last_substitution = {
                "requested": requested, "used": getattr(cfg, "name", candidate),
                "downgraded_to_local": False,
                "note": f"cloud substitution for {requested}",
            }
            return client

        # 3. FREE cloud model tier — tried before any local downgrade.
        #    These models live on the same ollama-cloud provider but carry no quota.
        #    We swap the model name on the same provider so we stay cloud.
        #    Each step logs WARNING — NEVER silent (no silent downgrade).
        routing = load_routing()
        free_models = list(routing.free_fallback_models)
        # The primary model name — used to skip it if it appears in the free list.
        primary_model = model_config.model
        for free_model in free_models:
            if free_model == primary_model:
                continue
            # Build a temporary ModelConfig pointing at the free model on the same
            # ollama-cloud provider (base_url comes from the roster when available).
            free_entry = None
            for entry in routing.roster:
                if entry.model == free_model and entry.provider == "ollama-cloud":
                    free_entry = entry
                    break
            free_provider = "ollama-cloud"
            try:
                client, cfg = _mk(free_provider)
            except Exception as free_err:
                log.warning(
                    "free cloud model %r on provider %r unavailable (%s); trying next",
                    free_model, free_provider, free_err,
                )
                continue
            log.warning(
                "paid model %r unavailable (%s); downgrading to FREE cloud model %r on %r",
                primary_model, first_err, free_model, free_provider,
            )
            self.last_substitution = {
                "requested": requested,
                "used": free_model,
                "downgraded_to_local": False,
                "note": f"free-cloud fallback: {primary_model} -> {free_model}",
            }
            return client

        # 4. Local ollama — last resort, surfaced loudly (never silent).
        log.warning(
            "no cloud provider available for %r (%s); DOWNGRADING to local ollama",
            requested, first_err,
        )
        try:
            client, cfg = _mk("ollama")
            used = getattr(cfg, "name", "ollama")
        except Exception:
            client = OllamaClient(endpoint="http://localhost:11434")
            used = "ollama"
        self.last_substitution = {
            "requested": requested, "used": used,
            "downgraded_to_local": True,
            "note": f"all cloud providers unavailable ({first_err}); local fallback",
        }
        return client
    
    def get_client_for_task(self, task_description: str) -> tuple[OllamaClient, RoutingDecision]:
        """Get appropriate client for a task."""
        decision = self.classify_task(task_description)
        model_config = self.models.get(decision.recommended_model)
        if model_config is None:
            worker_provider, worker_model = load_routing().role_pair("worker")
            model_config = next(
                iter(self.models.values()),
                ModelConfig(name="worker", provider=worker_provider, model=worker_model),
            )
        client = self._create_client(model_config)
        return client, decision
    
    def create_verification_stack(self, generator_model: str) -> str:
        """Select verifier model (different from generator)."""
        # Verifier should be different from generator when possible
        verifier_candidates = ["auditor", "planner", "doer"]
        
        for candidate in verifier_candidates:
            if candidate != generator_model and candidate in self.models:
                return candidate
        
        # Fallback to auditor or any available
        return "auditor" if "auditor" in self.models else list(self.models.keys())[0]


def route_and_execute(
    task: str,
    agent_loop_factory: callable,
    tools: list,
    router: ModelRouter | None = None,
) -> dict[str, Any]:
    """Route task to appropriate model and execute with verification.
    
    This is the main entry point for Phase 4 + Phase 3 integration.
    """
    router = router or ModelRouter()
    
    # Phase 1: Route. Prefer the public router API so tests and alternate
    # router implementations do not need to expose private construction hooks.
    if hasattr(router, "get_client_for_task"):
        client, decision = router.get_client_for_task(task)
    else:
        decision = router.classify_task(task)
        selected_for_client = router.models.get(decision.recommended_model)
        if selected_for_client is None:
            return {
                "error": f"role {decision.recommended_model!r} not configured in hydra.yaml",
                "routing_decision": {
                    "complexity": decision.complexity.value,
                    "role": decision.recommended_model,
                },
            }
        client = router._create_client(selected_for_client)

    # Phase 2: Execute with appropriate model
    selected_model = router.models.get(decision.recommended_model)
    if selected_model is None:
        return {
            "error": f"role {decision.recommended_model!r} not configured in hydra.yaml",
            "routing_decision": {
                "complexity": decision.complexity.value,
                "role": decision.recommended_model,
            },
        }
    agent_loop = agent_loop_factory(client, selected_model.model)
    
    # Phase 3: Verify (if required)
    if decision.requires_verifier:
        pass  # verification logic not yet implemented
    
    return {
        "routing_decision": {
            "complexity": decision.complexity.value,
            "role": decision.recommended_model,
            "model": selected_model.model,
            "provider": selected_model.provider,
            "reasoning": decision.reasoning,
            "estimated_cost_usd": decision.estimated_cost_usd,
            "estimated_latency_ms": decision.estimated_latency_ms,
        },
        "requires_verifier": decision.requires_verifier,
        "requires_human_approval": decision.requires_human_approval,
    }
