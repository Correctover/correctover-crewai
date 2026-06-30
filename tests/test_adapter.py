"""Tests for correctover-crewai adapter."""

import pytest
from correctover_crewai import (
    CorrectoverCrewAIAdapter,
    VerificationRequest,
    VerifierDepth,
    SixDimVerifier,
    RecomputeEngine,
)


class TestSixDimVerifier:
    """Test 6-dimension verification logic."""

    def test_structure_pass_json(self):
        """Test structure verification with valid JSON."""
        verifier = SixDimVerifier(
            structure_rules={"format": "json", "required_keys": ["result"]}
        )
        request = VerificationRequest(
            tool_name="test_tool",
            tool_input={"query": "test"},
            tool_output='{"result": "success"}',
            agent_role="tester",
            task_description="test task",
            crew_id="crew1",
            provider_name="openai",
            model_name="gpt-4",
            latency_ms=100.0,
            token_usage={"prompt": 10, "completion": 20, "total": 30},
            timestamp="2026-07-01T00:00:00Z",
        )
        verdict = verifier.verify(request)
        assert verdict.structure_pass
        assert verdict.verdict in ["pass", "partial"]

    def test_structure_fail_invalid_json(self):
        """Test structure verification with invalid JSON."""
        verifier = SixDimVerifier(
            structure_rules={"format": "json", "required_keys": ["result"]}
        )
        request = VerificationRequest(
            tool_name="test_tool",
            tool_input={"query": "test"},
            tool_output="not json",
            agent_role="tester",
            task_description="test task",
            crew_id="crew1",
            provider_name="openai",
            model_name="gpt-4",
            latency_ms=100.0,
            token_usage={"prompt": 10, "completion": 20, "total": 30},
            timestamp="2026-07-01T00:00:00Z",
        )
        verdict = verifier.verify(request)
        assert not verdict.structure_pass

    def test_latency_pass(self):
        """Test latency verification within limit."""
        verifier = SixDimVerifier(
            latency_rules={"max_ms": 5000}
        )
        request = VerificationRequest(
            tool_name="test_tool",
            tool_input={},
            tool_output="result",
            agent_role="tester",
            task_description="test",
            crew_id="crew1",
            provider_name="openai",
            model_name="gpt-4",
            latency_ms=1000.0,
            token_usage={"prompt": 0, "completion": 0, "total": 0},
            timestamp="2026-07-01T00:00:00Z",
        )
        verdict = verifier.verify(request)
        assert verdict.latency_pass

    def test_latency_fail(self):
        """Test latency verification exceeding limit."""
        verifier = SixDimVerifier(
            latency_rules={"max_ms": 500}
        )
        request = VerificationRequest(
            tool_name="test_tool",
            tool_input={},
            tool_output="result",
            agent_role="tester",
            task_description="test",
            crew_id="crew1",
            provider_name="openai",
            model_name="gpt-4",
            latency_ms=1000.0,
            token_usage={"prompt": 0, "completion": 0, "total": 0},
            timestamp="2026-07-01T00:00:00Z",
        )
        verdict = verifier.verify(request)
        assert not verdict.latency_pass
        assert verdict.should_failover

    def test_cost_pass(self):
        """Test cost verification within budget."""
        verifier = SixDimVerifier(
            cost_rules={"max_tokens": 1000}
        )
        request = VerificationRequest(
            tool_name="test_tool",
            tool_input={},
            tool_output="result",
            agent_role="tester",
            task_description="test",
            crew_id="crew1",
            provider_name="openai",
            model_name="gpt-4",
            latency_ms=100.0,
            token_usage={"prompt": 100, "completion": 200, "total": 300},
            timestamp="2026-07-01T00:00:00Z",
        )
        verdict = verifier.verify(request)
        assert verdict.cost_pass

    def test_integrity_fail(self):
        """Test integrity verification with forbidden pattern."""
        verifier = SixDimVerifier(
            integrity_rules={"forbidden_patterns": ["ERROR", "FAILED"]}
        )
        request = VerificationRequest(
            tool_name="test_tool",
            tool_input={},
            tool_output="Operation completed with ERROR",
            agent_role="tester",
            task_description="test",
            crew_id="crew1",
            provider_name="openai",
            model_name="gpt-4",
            latency_ms=100.0,
            token_usage={"prompt": 0, "completion": 0, "total": 0},
            timestamp="2026-07-01T00:00:00Z",
        )
        verdict = verifier.verify(request)
        assert not verdict.integrity_pass


class TestRecomputeEngine:
    """Test recomputable verification."""

    def test_recompute_matches_original(self):
        """Test that recomputed verdict matches original."""
        verifier = SixDimVerifier(
            latency_rules={"max_ms": 5000},
            cost_rules={"max_tokens": 1000},
        )
        request = VerificationRequest(
            tool_name="test_tool",
            tool_input={"query": "test"},
            tool_output="valid output",
            agent_role="tester",
            task_description="test task",
            crew_id="crew1",
            provider_name="openai",
            model_name="gpt-4",
            latency_ms=100.0,
            token_usage={"prompt": 10, "completion": 20, "total": 30},
            timestamp="2026-07-01T00:00:00Z",
        )

        # Original verification
        original_verdict = verifier.verify(request)
        rules = {
            "structure": verifier.structure_rules,
            "schema": verifier.schema_rules,
            "latency": verifier.latency_rules,
            "cost": verifier.cost_rules,
            "identity": verifier.identity_rules,
            "integrity": verifier.integrity_rules,
        }

        # Export proof package
        proof_package = RecomputeEngine.export_proof_package(
            request=request,
            verdict=original_verdict,
            rules=rules,
        )

        # Recompute
        engine = RecomputeEngine()
        result = engine.verify_proof_package(proof_package)

        # Verify
        assert result["valid"]
        assert result["recomputed_proof_hash"] == original_verdict.proof_hash
        assert result["recomputed_verdict"] == original_verdict.verdict


class TestCorrectoverCrewAIAdapter:
    """Test adapter integration."""

    def test_adapter_initialization(self):
        """Test adapter can be initialized."""
        adapter = CorrectoverCrewAIAdapter(
            api_key="test_key",
            verifier_depth=VerifierDepth.D2,
            enable_failover=True,
        )
        assert adapter.verifier_depth == VerifierDepth.D2
        assert adapter.enable_failover is True

    def test_adapter_verdict_storage(self):
        """Test adapter stores verdicts correctly."""
        adapter = CorrectoverCrewAIAdapter()
        # Simulate verdicts (in real usage, these come from hooks)
        assert len(adapter.get_verdicts()) == 0
        assert len(adapter.get_proof_packages()) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
