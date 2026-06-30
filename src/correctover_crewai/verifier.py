"""6-dimension verification engine.

Each dimension checks a different aspect of LLM output quality:
1. Structure  — Does output match expected JSON/text structure?
2. Schema     — Do fields have correct types and required values?
3. Latency    — Was response time within acceptable bounds?
4. Cost       — Did token consumption stay within budget?
5. Identity   — Is output semantically relevant to input?
6. Integrity  — Does output contain forbidden patterns?
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from difflib import SequenceMatcher
from typing import Any

from correctover_crewai.models import (
    DimensionResult,
    VerificationRequest,
    VerificationVerdict,
    VerifierDepth,
)


class SixDimVerifier:
    """Runs 6-dimension verification on tool call results.

    The verifier is deterministic: given the same request + rules,
    it always produces the same verdict. This is what makes it
    recomputable — no external state, no randomness.
    """

    def __init__(
        self,
        structure_rules: dict[str, Any] | None = None,
        schema_rules: dict[str, Any] | None = None,
        latency_rules: dict[str, Any] | None = None,
        cost_rules: dict[str, Any] | None = None,
        identity_rules: dict[str, Any] | None = None,
        integrity_rules: dict[str, Any] | None = None,
        min_confidence: float = 0.6,
    ):
        self.structure_rules = structure_rules or {}
        self.schema_rules = schema_rules or {}
        self.latency_rules = latency_rules or {}
        self.cost_rules = cost_rules or {}
        self.identity_rules = identity_rules or {}
        self.integrity_rules = integrity_rules or {}
        self.min_confidence = min_confidence

    def verify(
        self,
        request: VerificationRequest,
        depth: VerifierDepth = VerifierDepth.D2,
    ) -> VerificationVerdict:
        """Execute all 6 verification dimensions.

        Returns a VerificationVerdict with per-dimension results and
        a recomputable proof hash.
        """
        t0 = time.monotonic()

        # Run all 6 dimensions
        dimensions: dict[str, DimensionResult] = {}
        dimensions["structure"] = self._check_structure(request)
        dimensions["schema"] = self._check_schema(request)
        dimensions["latency"] = self._check_latency(request)
        dimensions["cost"] = self._check_cost(request)
        dimensions["identity"] = self._check_identity(request)
        dimensions["integrity"] = self._check_integrity(request)

        # Aggregate
        passed_count = sum(1 for d in dimensions.values() if d.passed)
        total_count = len(dimensions)
        confidence = sum(d.score for d in dimensions.values()) / total_count
        drift_score = 1.0 - confidence

        # Determine verdict
        if passed_count == total_count:
            verdict = "pass"
        elif confidence >= self.min_confidence:
            verdict = "partial"
        else:
            verdict = "fail"

        # Compute proof hash (recomputable)
        input_hash = request.compute_input_hash()
        proof_hash = self._compute_proof_hash(request, dimensions, verdict)

        # Failover decision
        should_failover = False
        failover_reason = None
        if verdict == "fail" and depth.value >= VerifierDepth.D1.value:
            should_failover = True
            failover_reason = self._determine_failover_reason(dimensions)

        verification_latency = (time.monotonic() - t0) * 1000

        return VerificationVerdict(
            verdict=verdict,
            confidence=confidence,
            drift_score=drift_score,
            verifier_depth=depth,
            dimensions=dimensions,
            input_hash=input_hash,
            proof_hash=proof_hash,
            should_failover=should_failover,
            failover_reason=failover_reason,
            verification_latency_ms=verification_latency,
            timestamp=request.timestamp,
        )

    # ── Dimension 1: Structure ──────────────────────────────────

    def _check_structure(self, request: VerificationRequest) -> DimensionResult:
        """Verify output matches expected structure (JSON/text)."""
        output = request.tool_output.strip()

        # If expected schema specifies JSON, check parseability
        if self.structure_rules.get("format") == "json":
            try:
                parsed = json.loads(output)
                if self.structure_rules.get("required_keys"):
                    missing = set(self.structure_rules["required_keys"]) - set(parsed.keys())
                    if missing:
                        return DimensionResult(
                            "structure", False, 0.0,
                            f"Missing required keys: {missing}"
                        )
                return DimensionResult("structure", True, 1.0, "Valid JSON with required keys")
            except json.JSONDecodeError as e:
                return DimensionResult("structure", False, 0.0, f"Invalid JSON: {e}")

        # Check for minimum length
        min_length = self.structure_rules.get("min_length", 0)
        if len(output) < min_length:
            return DimensionResult(
                "structure", False, 0.0,
                f"Output too short: {len(output)} < {min_length}"
            )

        # Check for non-empty
        if not output:
            return DimensionResult("structure", False, 0.0, "Empty output")

        return DimensionResult("structure", True, 1.0, "Structure check passed")

    # ── Dimension 2: Schema ─────────────────────────────────────

    def _check_schema(self, request: VerificationRequest) -> DimensionResult:
        """Verify output fields match expected types and constraints."""
        if not self.schema_rules:
            return DimensionResult("schema", True, 1.0, "No schema rules configured")

        output = request.tool_output.strip()

        # Try to parse as JSON for field-level validation
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            # If schema rules exist but output isn't JSON, fail
            if self.schema_rules.get("require_json", False):
                return DimensionResult(
                    "schema", False, 0.0,
                    "Schema rules require JSON but output is not valid JSON"
                )
            return DimensionResult("schema", True, 0.8, "Non-JSON output, limited schema check")

        violations = []
        checks_passed = 0
        total_checks = 0

        for field_name, field_rules in self.schema_rules.get("fields", {}).items():
            total_checks += 1
            if field_name not in parsed:
                if field_rules.get("required", True):
                    violations.append(f"Missing required field: {field_name}")
                continue

            value = parsed[field_name]
            expected_type = field_rules.get("type")

            if expected_type == "string" and not isinstance(value, str):
                violations.append(f"Field '{field_name}': expected string, got {type(value).__name__}")
            elif expected_type == "number" and not isinstance(value, (int, float)):
                violations.append(f"Field '{field_name}': expected number, got {type(value).__name__}")
            elif expected_type == "array" and not isinstance(value, list):
                violations.append(f"Field '{field_name}': expected array, got {type(value).__name__}")
            elif expected_type == "object" and not isinstance(value, dict):
                violations.append(f"Field '{field_name}': expected object, got {type(value).__name__}")
            else:
                checks_passed += 1

        score = checks_passed / max(total_checks, 1)
        passed = len(violations) == 0
        detail = "; ".join(violations) if violations else "All schema checks passed"

        return DimensionResult("schema", passed, score, detail)

    # ── Dimension 3: Latency ────────────────────────────────────

    def _check_latency(self, request: VerificationRequest) -> DimensionResult:
        """Verify response time is within acceptable bounds."""
        max_ms = self.latency_rules.get("max_ms", request.max_allowed_latency_ms)
        actual_ms = request.latency_ms

        if actual_ms <= 0:
            return DimensionResult("latency", True, 1.0, "Latency not measured")

        if actual_ms <= max_ms:
            # Score based on how close to limit (closer = lower score)
            ratio = actual_ms / max_ms
            score = max(0.5, 1.0 - ratio * 0.3)  # 1.0 at 0ms, 0.7 at limit
            return DimensionResult("latency", True, score, f"{actual_ms:.0f}ms <= {max_ms:.0f}ms")
        else:
            overshoot = (actual_ms - max_ms) / max_ms
            score = max(0.0, 1.0 - overshoot)
            return DimensionResult(
                "latency", False, score,
                f"{actual_ms:.0f}ms > {max_ms:.0f}ms (overshoot: {overshoot:.0%})"
            )

    # ── Dimension 4: Cost ───────────────────────────────────────

    def _check_cost(self, request: VerificationRequest) -> DimensionResult:
        """Verify token consumption within budget."""
        max_tokens = self.cost_rules.get("max_tokens", request.max_allowed_tokens)
        total_tokens = request.token_usage.get("total", 0)

        if total_tokens == 0:
            return DimensionResult("cost", True, 1.0, "Token usage not reported")

        if total_tokens <= max_tokens:
            ratio = total_tokens / max_tokens
            score = max(0.5, 1.0 - ratio * 0.3)
            return DimensionResult("cost", True, score, f"{total_tokens} tokens <= {max_tokens}")
        else:
            overshoot = (total_tokens - max_tokens) / max_tokens
            score = max(0.0, 1.0 - overshoot)
            return DimensionResult(
                "cost", False, score,
                f"{total_tokens} tokens > {max_tokens} (overshoot: {overshoot:.0%})"
            )

    # ── Dimension 5: Identity ───────────────────────────────────

    def _check_identity(self, request: VerificationRequest) -> DimensionResult:
        """Verify output is semantically relevant to input.

        Uses SequenceMatcher for lightweight similarity check.
        For production use, can be replaced with embedding-based similarity.
        """
        min_similarity = self.identity_rules.get("min_similarity", 0.3)

        input_text = json.dumps(request.tool_input, ensure_ascii=False)
        output_text = request.tool_output

        if not input_text or not output_text:
            return DimensionResult("identity", True, 1.0, "Empty input/output, skipping")

        # Lightweight similarity via SequenceMatcher
        similarity = SequenceMatcher(None, input_text[:1000], output_text[:1000]).ratio()

        # Boost: check for keyword overlap
        input_words = set(re.findall(r'\w+', input_text.lower()))
        output_words = set(re.findall(r'\w+', output_text.lower()))
        if input_words:
            overlap = len(input_words & output_words) / len(input_words)
            combined = (similarity + overlap) / 2
        else:
            combined = similarity

        if combined >= min_similarity:
            return DimensionResult("identity", True, combined, f"Similarity: {combined:.2f}")
        else:
            return DimensionResult(
                "identity", False, combined,
                f"Low similarity: {combined:.2f} < {min_similarity}"
            )

    # ── Dimension 6: Integrity ──────────────────────────────────

    def _check_integrity(self, request: VerificationRequest) -> DimensionResult:
        """Verify output doesn't contain forbidden patterns."""
        patterns = self.integrity_rules.get("forbidden_patterns", [])
        if not patterns:
            patterns = request.forbidden_patterns
        if not patterns:
            return DimensionResult("integrity", True, 1.0, "No forbidden patterns configured")

        output = request.tool_output
        violations = []

        for pattern in patterns:
            if re.search(pattern, output, re.IGNORECASE):
                violations.append(pattern)

        if violations:
            return DimensionResult(
                "integrity", False, 0.0,
                f"Forbidden patterns found: {violations}"
            )

        return DimensionResult("integrity", True, 1.0, "No forbidden patterns detected")

    # ── Proof computation ───────────────────────────────────────

    def _compute_proof_hash(
        self,
        request: VerificationRequest,
        dimensions: dict[str, DimensionResult],
        verdict: str,
    ) -> str:
        """Compute recomputable proof hash.

        The proof hash binds: input + verification rules + per-dimension results + verdict.
        Anyone with the same inputs and rules can recompute this hash independently.
        """
        proof_data = {
            "input_hash": request.compute_input_hash(),
            "rules": {
                "structure": self.structure_rules,
                "schema": self.schema_rules,
                "latency": self.latency_rules,
                "cost": self.cost_rules,
                "identity": self.identity_rules,
                "integrity": self.integrity_rules,
            },
            "dimensions": {
                name: {"passed": r.passed, "score": r.score}
                for name, r in sorted(dimensions.items())
            },
            "verdict": verdict,
        }
        proof_bytes = json.dumps(proof_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(proof_bytes).hexdigest()

    def _determine_failover_reason(self, dimensions: dict[str, DimensionResult]) -> str:
        """Determine reason for failover based on failed dimensions."""
        failed = [name for name, d in dimensions.items() if not d.passed]
        if "structure" in failed or "schema" in failed:
            return "Output format/schema violation — trying alternative provider"
        if "identity" in failed:
            return "Output semantically unrelated to input — possible provider issue"
        if "latency" in failed:
            return "Response too slow — switching to faster provider"
        if "cost" in failed:
            return "Token usage exceeded budget — switching to cheaper provider"
        if "integrity" in failed:
            return "Forbidden content detected — switching provider"
        return "Verification failed — attempting failover"
