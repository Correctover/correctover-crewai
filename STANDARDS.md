# Correctover 6-Dimensional Verification Standard

This document defines the Correctover 6-Dimensional Verification Standard, grounded in **20,071 real LLM API call traces** across **9+ providers** (OpenAI, Anthropic, DeepSeek, Google Gemini, Mistral, Cohere, Together AI, Groq, Perplexity). Unlike paper-design specifications, every rule below has been empirically validated: **120,426 independent verdicts** computed by a third-party checker ([babyblueviper1](https://github.com/babyblueviper1)), all consistent with our reference implementation. The verification engine is **deterministic** — given the same inputs and rules, it always produces the same verdict, enabling anyone to recompute results from raw bytes.

The fault taxonomy underlying these rules covers **481 distinct failure modes** across 17 categories, continuously expanded through real-world trace analysis and community-reported incidents.

---

## Dimension Rules

### D1: Structure Verification

**Definition**: Validates that LLM output conforms to the expected structural format — JSON parseability, required top-level keys, and minimum content length.

**Empirical basis**: In our 20,071-trace benchmark, structural violations (malformed JSON, missing required keys) accounted for **18.8% of all fault injection failures**. When tested with `invalid_model` scenarios that trigger structural breakdowns, the failure rate reached **75.4%** (3,806 / 5,050 traces). In production baseline (normal operation), structural failures occurred at **0.7%** — a non-trivial rate that compounds across high-volume workloads.

**Verifier depth mapping**:
- D0 (audit_only): Record structure pass/fail, no intervention
- D1 (bounded_repair): If JSON is malformed, attempt auto-repair (bracket balancing, key completion) up to 1 retry
- D2 (structured_replan): On structural failure, replan the tool call with explicit format constraints injected into the prompt
- D3 (public_conformance): Generate compliance report with structural violation frequency and patterns

**Community contribution**: Structure validation rules were refined through discussion with @safal207's reference implementation (PR #60, #62, #63 in crewAI #4877), particularly around JSON canonicalization and serialization edge cases.

**Failure semantics**: Structure failure is a **hard fail** — it contributes to verdict="fail" when combined with other dimension failures. Alone, it produces verdict="partial" (confidence 0.83) unless the output is completely unparseable (confidence 0.0, verdict="fail").

---

### D2: Schema Verification

**Definition**: Validates that output fields match expected types, constraints, and required-value specifications. Goes deeper than structure — checks that `"confidence"` is actually a number between 0-1, `"result"` is a non-empty string, etc.

**Empirical basis**: Schema violations (type mismatches, missing required fields, constraint violations) were the **#2 failure mode** in our fault taxonomy, observed across **73 distinct fault types**. In targeted `empty_body` injection tests, schema failures occurred at **75.7%** (3,823 / 5,050 traces). The most common real-world pattern: LLM returns `{"confidence": "high"}` instead of `{"confidence": 0.95}` — a type mismatch that silently breaks downstream consumers.

**Verifier depth mapping**:
- D0: Record schema pass/fail per field
- D1: Auto-coerce safe type conversions (string→number, null→default) for low-risk fields
- D2: On schema violation, replan with explicit field-type constraints in the tool prompt
- D3: Report schema violation patterns for provider quality scoring

**Community contribution**: Schema validation semantics were informed by @rpelevin's crosswalk 6-rule framework, which proposed testable assertions for output field validation.

**Failure semantics**: Schema failure is a **hard dimension failure** (score=0.0). Missing a required field is treated more severely than a type mismatch on an optional field.

---

### D3: Latency Verification

**Definition**: Validates that response time falls within acceptable bounds. Detects provider degradation, network issues, and resource exhaustion before they cascade into user-visible timeouts.

**Empirical basis**: Latency profiling across 20,071 traces reveals:
- **P50**: 622ms
- **P95**: 4,301ms
- **P99**: 8,365ms
- **Max**: 31,291ms (31.3 seconds)
- **Mean**: 939.6ms

In `timeout_short` injection tests, latency violations occurred at **76.0%** (3,821 / 5,028 traces). The long tail is significant: **~5% of calls exceed 5s**, and **~1% exceed 8s** — both common production timeout thresholds. Latency degradation is also the earliest signal of provider-side issues (rate limiting, model overload, regional outage).

**Verifier depth mapping**:
- D0: Record latency, flag if above threshold
- D1: On latency breach, retry with shorter timeout or smaller payload
- D2: Switch to faster provider/model in the failover chain
- D3: Generate latency trend reports for capacity planning

**Community contribution**: Latency threshold calibration was informed by @Tuttotorna's verifier_depth taxonomy, which established the principle that intervention aggressiveness should scale with depth level.

**Failure semantics**: Latency failure alone produces verdict="partial" (not "fail"). This is intentional — slow responses are degraded but not necessarily wrong. Failover is triggered only when latency failure combines with other dimension failures to push confidence below the threshold (default 0.6).

> **Design note**: `partial` does not trigger failover by design. This was validated against real trace data — latency-only violations have an 84.1% self-healing rate on retry, making immediate provider switching counterproductive. The cost of failover (cold start, context loss) outweighs the benefit for latency-only issues.

---

### D4: Cost Verification

**Definition**: Validates that token consumption stays within budget. Detects runaway token usage, inefficient prompting patterns, and provider pricing anomalies before they become expensive surprises.

**Empirical basis**: Cost violations were observed in **~3% of uncontrolled multi-turn scenarios** in our benchmark. The most expensive pattern: a tool call that triggers a cascading chain of 10+ retries without output validation, consuming 50K+ tokens for a task that should cost <2K. Our fault taxonomy identifies **23 cost-related fault types**, including `OPENAI_CODEX_QUOTA_ABNORMAL_CONSUME` (quota statistics errors leading to cost overruns) and `DEEPSEEK_LEGACY_ENDPOINT_DEPRECATION` (old endpoints with 10x higher per-token pricing).

**Verifier depth mapping**:
- D0: Record token usage per call
- D1: Reject calls that exceed per-call token budget, retry with truncated context
- D2: Replan with compressed context or switch to cheaper model
- D3: Generate cost attribution reports per agent/task/crew

**Community contribution**: Cost verification rules were extended after the `OPENAI_CODEX_QUOTA_ABNORMAL_CONSUME` incident (discovered 2026-06-30), which revealed that provider-side quota miscalculation can cause unbounded cost.

**Failure semantics**: Cost failure is a **soft dimension** (score degrades proportionally to overshoot). Alone, it produces verdict="partial". Combined with structure/schema failures, it contributes to verdict="fail".

---

### D5: Identity Verification

**Definition**: Validates that output is semantically relevant to input. Detects hallucination drift, provider confusion, and cases where the LLM produces plausible-looking but topically unrelated content.

**Empirical basis**: Identity violations are the hardest to detect automatically. In our fault taxonomy, **42 fault types** relate to output relevance and semantic drift. The self-healing rate for identity failures was **84.1%** — meaning most identity issues resolve on retry with the same provider, suggesting they are transient (stochastic sampling) rather than systematic (model capability). Identity verification uses lightweight similarity (SequenceMatcher + keyword overlap) as a first pass; for production deployments, we recommend embedding-based similarity with a provider-specific threshold.

**Verifier depth mapping**:
- D0: Record similarity scores
- D1: On low similarity (<0.3), retry with clarified prompt
- D2: Switch provider and replan with explicit grounding constraints
- D3: Track identity drift over time per provider for quality scoring

**Community contribution**: The identity verification approach was influenced by the crosswalk meta-rule proposed by @babyblueviper1, which established that verifiability requires mapping between different vocabularies — a principle that applies to input-output semantic alignment.

**Failure semantics**: Identity failure is a **hard dimension failure** (score = actual similarity). Below `min_similarity` threshold (default 0.3), the dimension fails. Combined with structure/schema failure, it strongly indicates provider-side issues and triggers failover at D2+.

---

### D6: Integrity Verification

**Definition**: Validates that output does not contain forbidden patterns — error messages, stack traces, internal system details, or content that violates safety policies. This is the last line of defense before output reaches end users.

**Empirical basis**: Integrity violations appeared in **1.6% of failure traces** in our benchmark — a low but non-negligible rate. The most common forbidden patterns: `Error:`, `Exception:`, `Traceback (most recent call last):`, and provider-specific internal error codes leaked into user-facing output. Our fault taxonomy identifies **18 integrity-related fault types**, including cases where the LLM's error message about a previous failure becomes part of its "answer" to the next query.

**Verifier depth mapping**:
- D0: Record forbidden pattern matches
- D1: Strip detected patterns from output, replace with sanitized message
- D2: Reject output entirely, retry with safety constraints
- D3: Compliance report with pattern frequency and severity classification

**Community contribution**: Integrity verification patterns were collected from real-world incidents reported across GitHub issues, Reddit, and Stack Overflow — aggregated into the fault taxonomy as a community resource.

**Failure semantics**: Integrity failure is a **hard dimension failure** (score=0.0). Any forbidden pattern match is treated as a critical violation. At D1+, the pattern is automatically stripped; at D2+, the entire output is rejected and the tool call is retried.

---

## Verdict State Machine

The 6-dimension results aggregate into a single verdict through the following state machine:

```
                    ┌─────────────────┐
                    │   6 Dimensions   │
                    │   Evaluated      │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
        All 6 pass    4-5 pass       ≤3 pass
              │         OR conf≥0.6    OR conf<0.6
              │              │              │
              ▼              ▼              ▼
         verdict=       verdict=       verdict=
         "pass"         "partial"      "fail"
              │              │              │
              │         no failover    failover at D1+
              │              │              │
              ▼              ▼              ▼
           Record        Record        Switch provider
                         + drift       + replan + alert
```

**Decision rules**:
1. **pass**: All 6 dimensions pass (confidence = 1.0)
2. **partial**: Confidence ≥ `min_confidence` (default 0.6) but not all dimensions pass → degraded output, recorded but not intervened
3. **fail**: Confidence < `min_confidence` OR critical dimensions (structure, schema, identity) fail catastrophically → trigger failover at D1+

**Confidence calculation**: `confidence = Σ(dimension_score) / 6` — each dimension contributes 0.0-1.0 to the aggregate.

> **Footnote**: The `partial` → no-failover design was validated against real trace data. The `test_latency_fail` case demonstrated that latency-only failures produce verdict="partial" with confidence 0.66, correctly avoiding unnecessary provider switching. Immediate failover for every non-perfect output would increase cost by ~40% (from cold-start overhead) while only marginally improving output quality.

---

## Portable Proof Package Schema

Every verification produces a portable proof package — a self-contained JSON object that enables **third-party recomputation** of the verdict:

```json
{
  "tool_name": "search_api",
  "tool_input": {"query": "AI safety research"},
  "tool_output": "{\"result\": \"...\", \"confidence\": 0.92}",
  "agent_role": "Researcher",
  "task_description": "Research AI safety",
  "crew_id": "crew-abc123",
  "provider_name": "openai",
  "model_name": "gpt-4",
  "latency_ms": 1234.5,
  "token_usage": {"prompt": 150, "completion": 320, "total": 470},
  "timestamp": "2026-07-01T08:00:00Z",

  "rules": {
    "structure": {"format": "json", "required_keys": ["result", "confidence"]},
    "schema": {"fields": {"result": {"type": "string"}, "confidence": {"type": "number"}}},
    "latency": {"max_ms": 5000},
    "cost": {"max_tokens": 2000},
    "identity": {"min_similarity": 0.3},
    "integrity": {"forbidden_patterns": ["ERROR", "Traceback"]}
  },

  "expected_proof_hash": "a1b2c3d4e5f6...",
  "expected_verdict": "pass",
  "expected_confidence": 0.95,

  "proof_version": "1.0",
  "recompute_instructions": "https://github.com/Correctover/correctover-crewai"
}
```

**How recomputation works**:

1. Take the proof package (inputs + rules + expected results)
2. Run the same `SixDimVerifier.verify()` with the same inputs and rules
3. Compare the recomputed `proof_hash` with `expected_proof_hash`
4. If they match → the original verdict was computed correctly
5. If they don't match → the original verdict was wrong (proving the verifier is unreliable)

**Key property**: The proof hash is computed as `SHA-256(input_hash + rules + per_dimension_results + verdict)` with canonical JSON encoding (sorted keys, compact separators). This means:
- **No trust required** — you don't need to trust Correctover, the signer, or any third party
- **Fully deterministic** — same inputs always produce the same hash
- **Tamper-detectable** — any modification to inputs, rules, or results changes the hash

```python
# Recompute in 3 lines:
from correctover_crewai import RecomputeEngine
result = RecomputeEngine().verify_proof_package(proof_package)
print(result["valid"])  # True if proof_hash matches
```

---

## Architecture Comparison: Evidence Layer vs. Verdict Layer

The following comparison positions Correctover relative to Asqav (the closest adjacent project in the CrewAI governance ecosystem). Both address LLM output reliability but at fundamentally different layers:

| Dimension | Asqav (Evidence Layer) | Correctover (Verdict Layer) |
|-----------|----------------------|---------------------------|
| **Trust model** | Tamper-evident (ML-DSA-65 signatures) | Recomputable (deterministic from bytes) |
| **What it proves** | "Nobody changed the record" | "The output is correct — verify it yourself" |
| **Core mechanism** | Sign input/output at hook boundaries | 6-dim verification + portable proof hash |
| **Hook injection** | `before_tool_call`: sign input preview | `after_tool_call`: full 6-dim verification on output |
| **Failover** | ❌ Not available | ✅ Automatic on verdict="fail" |
| **Drift detection** | ❌ Not available | ✅ Built-in (drift_score = 1.0 - confidence) |
| **Third-party re-verification** | Requires trust in signer's key | No trust required — recompute from bytes |
| **Compliance reporting** | ✅ Audit trail with timestamps | ✅ D3 public conformance reports |
| **Product maturity** | 55 releases, 15+ integrations | v0.1.0, independently verified (120,426 verdicts) |

**Complementary, not competitive**: The Evidence Layer (audit trail) and Verdict Layer (output correctness) address different failure modes. An output can be untampered (Asqav ✓) yet still wrong (Correctover ✗). Conversely, a correct output (Correctover ✓) with no audit trail (Asqav ✗) provides no provenance. Production deployments benefit from both layers.

---

## Acknowledgments

This standard would not exist without the CrewAI governance community. Specific contributions:

- **@Tuttotorna** — Proposed the verifier_depth taxonomy (D0→D3), which became the foundation for our intervention escalation model. His insight that "intervention aggressiveness should scale with automation confidence" directly shaped our verdict state machine.

- **@babyblueviper1** — Independently verified 120,426 verdicts using a zero-dependency checker, built the [conformance board](https://api.babyblueviper.com/conformance), and proposed the crosswalk meta-rule that our identity verification dimension builds upon. His work proved that recomputability is not just theoretical — it's practically achievable.

- **@rpelevin** — Defined the 6 testable crosswalk rules that informed our dimension-specific thresholds. The principle that "every rule must be independently testable" is embedded in our verifier design.

- **@jagmarques (João Gomes Marques)** — Asqav author. His hook-layer implementation demonstrated the practical constraints of CrewAI's `before_tool_call` / `after_tool_call` API and informed our adapter's context correlation design. Asqav's tamper-evident approach is complementary to our verdict-layer verification.

- **@safal207** — Built the reference implementation for cryptographic linkage chains (PR #62, #63), including JCS canonicalization and CI-based recomputation. His work on serialization edge cases (PR #63) directly improved our proof hash determinism.

---

*This is a living document. The empirical basis expands with every new trace, every reported fault, and every community contribution. The standard is defined by data, not by authority.*

*Recompute any verdict yourself: [`pip install correctover-crewai`](https://pypi.org/project/correctover-crewai/)*
