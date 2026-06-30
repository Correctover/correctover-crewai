# correctover-crewai

Recomputable verification adapter for CrewAI agents.

## What This Does

`correctover-crewai` plugs into CrewAI's tool-call hook system to provide:

- **6-dimension verification** — structure, schema, latency, cost, identity, integrity
- **Recomputable proof hashes** — anyone can re-derive the verification result from the raw bytes, no trust required
- **Drift detection** — catch when output quality degrades across providers or over time
- **Verified failover** — automatic fallback to backup providers when verification fails

## Quick Start

```bash
pip install correctover-crewai
```

```python
from crewai import Agent, Crew, Task
from correctover_crewai import CorrectoverCrewAIAdapter, VerifierDepth

# Initialize with your Correctover engine
adapter = CorrectoverCrewAIAdapter(
    api_key="your-correctover-key",
    verifier_depth=VerifierDepth.D2,
    enable_failover=True,
)

# Register hooks globally
adapter.register()

# Use CrewAI normally — verification happens automatically
agent = Agent(role="Researcher", goal="Find accurate info")
task = Task(description="Research AI safety", agent=agent)
crew = Crew(agents=[agent], tasks=[task])
result = crew.kickoff()

# Inspect verdicts
for v in adapter.get_verdicts():
    print(f"{v.tool_name}: {v.verdict} (confidence={v.confidence:.2f})")
```

## Recomputable vs Tamper-Evident

| Feature | correctover-crewai | asqav-crewai |
|---------|-------------------|--------------|
| Verification | Recomputable from bytes | Tamper-evident signatures |
| Trust model | No trust needed | Trust the signer |
| Drift detection | Built-in | Not available |
| Failover | Automatic | Not available |
| 6-dim verification | Structure, Schema, Latency, Cost, Identity, Integrity | Audit logging only |

## Verifier Depth Levels

- **D0 (audit_only)**: Record events, no intervention
- **D1 (bounded_repair)**: Low-risk automatic retries
- **D2 (structured_replan)**: Replan with structured output constraints
- **D3 (public_conformance)**: Generate compliance reports

## License

MIT
