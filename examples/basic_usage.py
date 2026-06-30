"""
Example: Using correctover-crewai adapter with CrewAI.

This example demonstrates how to integrate Correctover verification
into a CrewAI agent workflow.
"""

from crewai import Agent, Task, Crew, Process
from correctover_crewai import CorrectoverCrewAIAdapter, VerifierDepth

# Initialize Correctover adapter
adapter = CorrectoverCrewAIAdapter(
    verifier_depth=VerifierDepth.D2,  # Structured replan on failure
    enable_failover=True,              # Auto-switch provider on failure
    verification_rules={
        "structure": {"format": "json", "required_keys": ["result", "confidence"]},
        "schema": {
            "fields": {
                "result": {"type": "string", "required": True},
                "confidence": {"type": "number", "required": True},
            }
        },
        "latency": {"max_ms": 5000},
        "cost": {"max_tokens": 2000},
        "identity": {"min_similarity": 0.5},
        "integrity": {"forbidden_patterns": ["ERROR", "FAILED", "exception"]},
    },
    backup_providers=["anthropic", "deepseek"],  # Failover chain
)

# Register hooks with CrewAI
adapter.register()

# Define agent
researcher = Agent(
    role="Senior Research Analyst",
    goal="Uncover cutting-edge developments in AI and technology",
    backstory="""You work at a leading tech think tank.
    Your expertise is identifying emerging trends and breakthrough technologies.""",
    verbose=True,
)

# Define task
research_task = Task(
    description="""Research the latest developments in AI agents and 
    multi-agent systems. Focus on verification, reliability, and 
    recomputable proof mechanisms. Provide a JSON response with 
    'result' and 'confidence' fields.""",
    expected_output="JSON object with 'result' (string) and 'confidence' (0-1)",
    agent=researcher,
)

# Create crew and execute
crew = Crew(
    agents=[researcher],
    tasks=[research_task],
    process=Process.sequential,
    verbose=True,
)

result = crew.kickoff()

# Inspect verification results
print("\n" + "="*60)
print("VERIFICATION RESULTS")
print("="*60)

verdicts = adapter.get_verdicts()
for i, v in enumerate(verdicts, 1):
    print(f"\nTool Call #{i}:")
    print(f"  Verdict: {v.verdict}")
    print(f"  Confidence: {v.confidence:.2f}")
    print(f"  Drift Score: {v.drift_score:.2f}")
    print(f"  Dimensions:")
    for dim_name, dim_result in v.dimensions.items():
        status = "✓" if dim_result.passed else "✗"
        print(f"    {status} {dim_name}: {dim_result.detail}")
    print(f"  Proof Hash: {v.proof_hash[:16]}...")
    print(f"  Should Failover: {v.should_failover}")

# Demonstrate recomputability
print("\n" + "="*60)
print("RECOMPUTABLE VERIFICATION DEMO")
print("="*60)

proof_packages = adapter.get_proof_packages()
if proof_packages:
    print(f"\nTotal proof packages: {len(proof_packages)}")
    print("\nThird party can recompute verdict from proof package:")
    
    # Simulate third-party verification
    result = adapter.recompute_verdict(proof_packages[0])
    print(f"  Valid: {result['valid']}")
    print(f"  Recomputed verdict: {result['recomputed_verdict']}")
    print(f"  Recomputed confidence: {result['recomputed_confidence']:.2f}")
    print(f"  Proof matches: {result['expected_proof_hash'] == result['recomputed_proof_hash']}")

print("\n" + "="*60)
print("KEY INSIGHT: Recomputable > Tamper-Evident")
print("="*60)
print("Asqav: 'Nobody tampered with my records'")
print("Correctover: 'You don't need my records, recompute from bytes yourself'")
print("="*60)
