"""Recomputable verification engine.

The core insight: instead of signing a verdict (tamper-evident),
we compute a deterministic proof hash from inputs + rules + output.
Anyone can recompute the same hash independently — no trust required.

This module enables third parties to:
1. Take the original inputs, rules, and tool output
2. Re-run the same verification logic
3. Confirm the proof_hash matches — proving the verdict was correct
4. Or detect if the verdict was wrong — proving the verifier lied
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from correctover_crewai.models import (
    VerificationRequest,
    VerificationVerdict,
    VerifierDepth,
)
from correctover_crewai.verifier import SixDimVerifier


class RecomputeEngine:
    """
    Engine for recomputing verification verdicts from portable proof data.

    This is the core of recomputable verification:
    - Anyone can take a proof package and recompute the verdict
    - No trust in the original verifier is required
    - If the recomputed proof_hash matches, the verdict is valid
    """

    def recompute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: str,
        agent_role: str,
        task_description: str,
        provider_name: str,
        model_name: str,
        latency_ms: float,
        token_usage: dict[str, int],
        timestamp: str,
        rules: dict[str, Any],
        expected_proof_hash: str | None = None,
        crew_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Recompute a verification verdict from raw inputs.

        This allows third parties to independently verify that a verdict
        was computed correctly, without trusting the original verifier.

        Args:
            tool_name: Name of the tool that was called
            tool_input: Input parameters to the tool
            tool_output: Output from the tool
            agent_role: Role of the agent executing the tool
            task_description: Description of the task
            provider_name: LLM provider used
            model_name: Model name used
            latency_ms: Latency of the tool call
            token_usage: Token consumption dict
            timestamp: ISO 8601 timestamp of the call
            rules: Verification rules used (structure, schema, latency, etc.)
            expected_proof_hash: Optional expected proof hash to compare against
            crew_id: Optional crew identifier

        Returns:
            dict with verdict, confidence, proof_hash, and comparison result
        """
        # Reconstruct the request
        request = VerificationRequest(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            agent_role=agent_role,
            task_description=task_description,
            crew_id=crew_id,
            provider_name=provider_name,
            model_name=model_name,
            latency_ms=latency_ms,
            token_usage=token_usage,
            timestamp=timestamp,
        )

        # Re-run verification with the same rules
        verifier = SixDimVerifier(
            structure_rules=rules.get("structure", {}),
            schema_rules=rules.get("schema", {}),
            latency_rules=rules.get("latency", {}),
            cost_rules=rules.get("cost", {}),
            identity_rules=rules.get("identity", {}),
            integrity_rules=rules.get("integrity", {}),
        )

        verdict = verifier.verify(request, depth=VerifierDepth.D0)

        result = {
            "verdict": verdict.verdict,
            "confidence": verdict.confidence,
            "drift_score": verdict.drift_score,
            "proof_hash": verdict.proof_hash,
            "input_hash": verdict.input_hash,
            "dimensions": {
                name: {"passed": d.passed, "score": d.score}
                for name, d in verdict.dimensions.items()
            },
        }

        # Check if recomputed proof matches original
        if expected_proof_hash is not None:
            result["proof_matches"] = verdict.proof_hash == expected_proof_hash

        return result

    def recompute_from_proof(
        self,
        proof_package: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Recompute from a complete proof package.

        The proof package contains everything needed to independently
        verify the verdict: inputs, rules, expected outputs.

        Args:
            proof_package: Complete proof package exported from verification

        Returns:
            Recomputation result with proof_matches boolean
        """
        return self.recompute(
            tool_name=proof_package["tool_name"],
            tool_input=proof_package["tool_input"],
            tool_output=proof_package["tool_output"],
            agent_role=proof_package.get("agent_role", ""),
            task_description=proof_package.get("task_description", ""),
            provider_name=proof_package.get("provider_name", "unknown"),
            model_name=proof_package.get("model_name", "unknown"),
            latency_ms=proof_package.get("latency_ms", 0),
            token_usage=proof_package.get("token_usage", {"prompt": 0, "completion": 0, "total": 0}),
            timestamp=proof_package.get("timestamp", ""),
            rules=proof_package.get("rules", {}),
            expected_proof_hash=proof_package.get("expected_proof_hash"),
            crew_id=proof_package.get("crew_id"),
        )

    def verify_proof_package(
        self,
        proof_package: dict[str, Any],
        confidence_tolerance: float = 1e-6,
    ) -> dict[str, Any]:
        """
        Quick verification: recompute and check if results match.

        Validates:
        1. proof_hash matches (deterministic recomputation)
        2. expected_verdict matches recomputed verdict (plaintext consistency)
        3. expected_confidence matches recomputed confidence within tolerance

        Returns a summary indicating whether the proof is valid.
        """
        result = self.recompute_from_proof(proof_package)

        hash_matches = result.get("proof_matches", False)
        expected_verdict = proof_package.get("expected_verdict")
        expected_confidence = proof_package.get("expected_confidence")
        recomputed_verdict = result["verdict"]
        recomputed_confidence = result["confidence"]

        # Guard against plaintext field tampering:
        # valid requires hash match AND plaintext consistency
        verdict_matches = (
            expected_verdict is None or expected_verdict == recomputed_verdict
        )
        confidence_matches = (
            expected_confidence is None
            or abs(expected_confidence - recomputed_confidence) <= confidence_tolerance
        )
        valid = hash_matches and verdict_matches and confidence_matches

        return {
            "valid": valid,
            "recomputed_verdict": recomputed_verdict,
            "recomputed_confidence": recomputed_confidence,
            "recomputed_proof_hash": result["proof_hash"],
            "expected_verdict": expected_verdict,
            "expected_confidence": expected_confidence,
            "expected_proof_hash": proof_package.get("expected_proof_hash"),
            "dimensions": result["dimensions"],
        }

    @staticmethod
    def export_proof_package(
        request: VerificationRequest,
        verdict: VerificationVerdict,
        rules: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Export a complete proof package for third-party recomputation.

        The proof package is self-contained: anyone with this data can
        independently recompute the verdict and verify the proof hash.
        """
        return {
            # Input data
            "tool_name": request.tool_name,
            "tool_input": request.tool_input,
            "tool_output": request.tool_output,
            "agent_role": request.agent_role,
            "task_description": request.task_description,
            "crew_id": request.crew_id,
            "provider_name": request.provider_name,
            "model_name": request.model_name,
            "latency_ms": request.latency_ms,
            "token_usage": request.token_usage,
            "timestamp": request.timestamp,

            # Verification rules used
            "rules": rules,

            # Expected results
            "expected_proof_hash": verdict.proof_hash,
            "expected_verdict": verdict.verdict,
            "expected_confidence": verdict.confidence,

            # Metadata
            "proof_version": "1.0",
            "exported_at": request.timestamp,
        }
