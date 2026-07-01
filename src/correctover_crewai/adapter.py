"""Correctover CrewAI Adapter - Main adapter class."""

import json
import time
from datetime import datetime
from typing import Any

from correctover_crewai.models import (
    VerificationRequest,
    VerificationVerdict,
    VerifierDepth,
)
from correctover_crewai.recompute import RecomputeEngine
from correctover_crewai.verifier import SixDimVerifier


class CorrectoverCrewAIAdapter:
    """
    Recomputable verification adapter for CrewAI agents.

    Hooks into CrewAI's tool-call lifecycle to provide:
    - 6-dimension verification (structure, schema, latency, cost, identity, integrity)
    - Recomputable proof hashes (third-party verifiable)
    - Drift detection (track output quality over time)
    - Verified failover (automatic provider switching on verification failure)
    """

    def __init__(
        self,
        api_key: str | None = None,
        verifier_depth: VerifierDepth = VerifierDepth.D2,
        enable_failover: bool = True,
        verification_rules: dict[str, Any] | None = None,
        backup_providers: list[str] | None = None,
    ):
        """
        Initialize the Correctover adapter.

        Args:
            api_key: Optional Correctover API key (for cloud features)
            verifier_depth: Depth of verification (D0-D3)
            enable_failover: Whether to enable automatic failover on verification failure
            verification_rules: Custom verification rules for 6 dimensions
            backup_providers: List of backup LLM providers for failover
        """
        self.api_key = api_key
        self.verifier_depth = verifier_depth
        self.enable_failover = enable_failover
        self.backup_providers = backup_providers or []

        # Initialize verifier with rules
        rules = verification_rules or {}
        self.verifier = SixDimVerifier(
            structure_rules=rules.get("structure", {}),
            schema_rules=rules.get("schema", {}),
            latency_rules=rules.get("latency", {}),
            cost_rules=rules.get("cost", {}),
            identity_rules=rules.get("identity", {}),
            integrity_rules=rules.get("integrity", {}),
        )

        # Recompute engine for third-party verification
        self.recompute_engine = RecomputeEngine()

        # Storage for verdicts
        self._verdicts: list[VerificationVerdict] = []
        self._proof_packages: list[dict] = []

        # Context tracking for before/after hook correlation (FIFO queue)
        self._pending_contexts: list[dict] = []

    def register(self):
        """
        Register hooks with CrewAI.

        This should be called before running the crew.
        """
        try:
            from crewai.hooks.tool_hooks import (
                register_before_tool_call_hook,
                register_after_tool_call_hook,
            )

            register_before_tool_call_hook(self._before_tool_call)
            register_after_tool_call_hook(self._after_tool_call)
        except ImportError:
            raise ImportError(
                "CrewAI 1.9.1+ is required for tool-call hooks. "
                "Please upgrade: pip install --upgrade crewai"
            )

    def _before_tool_call(self, context) -> bool | None:
        """
        Before tool call hook: capture context for later verification.

        Args:
            context: CrewAI ToolCallHookContext

        Returns:
            None to allow execution, False to block
        """
        import hashlib
        input_hash = hashlib.sha256(
            json.dumps(context.tool_input, sort_keys=True).encode()
        ).hexdigest()[:12]

        # Capture full context for after_tool_call verification (FIFO queue)
        self._pending_contexts.append({
            "match_key": f"{context.tool_name}:{input_hash}",
            "tool_name": context.tool_name,
            "tool_input": context.tool_input,
            "agent_role": getattr(context.agent, "role", None) if context.agent else None,
            "task_description": getattr(context.task, "description", None) if context.task else None,
            "crew_id": getattr(context.crew, "id", None) if context.crew else None,
            "start_time": time.time(),
        })

        # Optional: fail-closed mode based on policy
        # For now, we allow all tool calls and verify after
        return None

    def _after_tool_call(self, context) -> None:
        """
        After tool call hook: perform 6-dim verification.

        Args:
            context: CrewAI ToolCallHookContext
        """
        import hashlib
        input_hash = hashlib.sha256(
            json.dumps(context.tool_input, sort_keys=True).encode()
        ).hexdigest()[:12]
        match_key = f"{context.tool_name}:{input_hash}"

        # Find and remove the first matching pending context (FIFO)
        call_context = None
        for i, ctx in enumerate(self._pending_contexts):
            if ctx["match_key"] == match_key:
                call_context = self._pending_contexts.pop(i)
                break

        if not call_context:
            return  # Context not found, skip verification

        # Build verification request
        latency_ms = (time.time() - call_context.get("start_time", 0)) * 1000
        token_usage = self._extract_token_usage(context)

        request = VerificationRequest(
            tool_name=call_context["tool_name"],
            tool_input=call_context["tool_input"],
            tool_output=str(context.tool_result) if context.tool_result else "",
            agent_role=call_context["agent_role"] or "unknown",
            task_description=call_context["task_description"] or "",
            crew_id=call_context["crew_id"],
            provider_name=self._get_current_provider(),
            model_name=self._get_current_model(),
            latency_ms=latency_ms,
            token_usage=token_usage,
            timestamp=datetime.utcnow().isoformat(),
        )

        # Execute 6-dim verification
        verdict = self.verifier.verify(request, depth=self.verifier_depth)
        self._verdicts.append(verdict)

        # Export proof package for recomputability
        proof_package = self.recompute_engine.export_proof_package(
            request=request,
            verdict=verdict,
            rules={
                "structure": self.verifier.structure_rules,
                "schema": self.verifier.schema_rules,
                "latency": self.verifier.latency_rules,
                "cost": self.verifier.cost_rules,
                "identity": self.verifier.identity_rules,
                "integrity": self.verifier.integrity_rules,
            },
        )
        self._proof_packages.append(proof_package)

        # Handle failover if needed
        if self.enable_failover and verdict.should_failover:
            self._trigger_failover(context, verdict)

    def get_verdicts(self) -> list[VerificationVerdict]:
        """Get all verification verdicts."""
        return self._verdicts

    def get_proof_packages(self) -> list[dict]:
        """Get all proof packages for third-party verification."""
        return self._proof_packages

    def recompute_verdict(self, proof_package: dict) -> dict:
        """
        Recompute a verdict from a proof package.

        This allows third parties to verify the correctness of our verdicts.
        """
        return self.recompute_engine.verify_proof_package(proof_package)

    def _extract_token_usage(self, context) -> dict:
        """Extract token usage from tool result."""
        # CrewAI doesn't always expose token usage in tool_result
        # This is a placeholder - in production, extract from LLM response metadata
        return {"prompt": 0, "completion": 0, "total": 0}

    def _get_current_provider(self) -> str:
        """Get current LLM provider name."""
        import os
        return os.getenv("CORRECTOVER_PROVIDER", "openai")

    def _get_current_model(self) -> str:
        """Get current model name."""
        import os
        return os.getenv("CORRECTOVER_MODEL", "gpt-4")

    def _trigger_failover(self, context, verdict: VerificationVerdict):
        """
        Trigger failover to backup provider.

        Note: CrewAI doesn't support mid-execution provider switching,
        so this logs the failover event for the next tool call.
        """
        import os
        if self.backup_providers:
            current = self._get_current_provider()
            for backup in self.backup_providers:
                if backup != current:
                    os.environ["CORRECTOVER_PROVIDER"] = backup
                    break
