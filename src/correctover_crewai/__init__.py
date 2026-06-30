"""Correctover CrewAI Adapter - Recomputable verification for CrewAI agents."""

from correctover_crewai.adapter import CorrectoverCrewAIAdapter
from correctover_crewai.models import (
    VerificationRequest,
    VerificationVerdict,
    VerifierDepth,
)
from correctover_crewai.verifier import SixDimVerifier
from correctover_crewai.recompute import RecomputeEngine

__version__ = "0.1.0"
__all__ = [
    "CorrectoverCrewAIAdapter",
    "VerificationRequest",
    "VerificationVerdict",
    "VerifierDepth",
    "SixDimVerifier",
    "RecomputeEngine",
]
