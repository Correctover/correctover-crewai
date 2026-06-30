"""Core data models for correctover-crewai."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class VerifierDepth(str, Enum):
    """Verifier depth levels controlling intervention aggressiveness."""
    D0 = "audit_only"          # Record only, no intervention
    D1 = "bounded_repair"      # Low-risk auto-retries
    D2 = "structured_replan"   # Structured replan with constraints
    D3 = "public_conformance"  # Full compliance reporting


@dataclass(frozen=True)
class VerificationRequest:
    """Input to the 6-dim verification engine.

    Captures everything needed to reproduce a verdict from raw bytes:
    tool identity, input/output, agent context, provider metadata, timing.
    """
    tool_name: str
    tool_input: dict[str, Any]
    tool_output: str
    agent_role: str
    task_description: str
    crew_id: str | None
    provider_name: str
    model_name: str
    latency_ms: float
    token_usage: dict[str, int]  # {"prompt": N, "completion": M, "total": X}
    timestamp: str               # ISO 8601

    # Optional metadata
    expected_output_schema: dict[str, Any] | None = None
    max_allowed_latency_ms: float = 30_000.0
    max_allowed_tokens: int = 10_000
    forbidden_patterns: list[str] = field(default_factory=list)

    def to_canonical_bytes(self) -> bytes:
        """Serialize to canonical byte representation for hashing.

        Uses sorted keys + compact separators for deterministic output.
        This is the foundation of recomputable verification:
        anyone with the same inputs gets the same hash.
        """
        canonical = {
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_output": self.tool_output,
            "agent_role": self.agent_role,
            "task_description": self.task_description,
            "crew_id": self.crew_id,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "latency_ms": round(self.latency_ms, 3),
            "token_usage": self.token_usage,
            "timestamp": self.timestamp,
        }
        return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def compute_input_hash(self) -> str:
        """SHA-256 hash of the canonical input representation."""
        return hashlib.sha256(self.to_canonical_bytes()).hexdigest()


@dataclass
class DimensionResult:
    """Result of a single verification dimension."""
    name: str
    passed: bool
    score: float          # 0.0 (fail) to 1.0 (pass)
    detail: str = ""


@dataclass
class VerificationVerdict:
    """Output of the verification engine.

    Contains per-dimension results, aggregate verdict, and a portable
    proof hash that enables third-party recomputation.
    """
    verdict: str                          # "pass" | "fail" | "partial"
    confidence: float                     # 0.0 - 1.0
    drift_score: float                    # 0.0 - 1.0, output drift magnitude
    verifier_depth: VerifierDepth

    # 6-dim results
    dimensions: dict[str, DimensionResult] = field(default_factory=dict)

    # Recomputable proof
    input_hash: str = ""                  # SHA-256 of canonical input
    proof_hash: str = ""                  # SHA-256 of (input + rules + result)

    # Failover
    should_failover: bool = False
    failover_reason: str | None = None

    # Timing
    verification_latency_ms: float = 0.0
    timestamp: str = ""

    @property
    def structure_pass(self) -> bool:
        return self.dimensions.get("structure", DimensionResult("structure", True, 1.0)).passed

    @property
    def schema_pass(self) -> bool:
        return self.dimensions.get("schema", DimensionResult("schema", True, 1.0)).passed

    @property
    def latency_pass(self) -> bool:
        return self.dimensions.get("latency", DimensionResult("latency", True, 1.0)).passed

    @property
    def cost_pass(self) -> bool:
        return self.dimensions.get("cost", DimensionResult("cost", True, 1.0)).passed

    @property
    def identity_pass(self) -> bool:
        return self.dimensions.get("identity", DimensionResult("identity", True, 1.0)).passed

    @property
    def integrity_pass(self) -> bool:
        return self.dimensions.get("integrity", DimensionResult("integrity", True, 1.0)).passed

    def to_dict(self) -> dict[str, Any]:
        """Serialize verdict for logging/export."""
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "drift_score": self.drift_score,
            "verifier_depth": self.verifier_depth.value,
            "input_hash": self.input_hash,
            "proof_hash": self.proof_hash,
            "should_failover": self.should_failover,
            "verification_latency_ms": self.verification_latency_ms,
            "timestamp": self.timestamp,
            "dimensions": {
                name: {"passed": r.passed, "score": r.score, "detail": r.detail}
                for name, r in self.dimensions.items()
            },
        }
