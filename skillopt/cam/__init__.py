"""CAM-SkillOpt reliability modules.

The package keeps CAM-SkillOpt's statistical decision, evidence-aware update
size, and persistent negative-memory mechanisms separate from the baseline
SkillOpt loop.  This makes ablations explicit and leaves baseline behaviour
unchanged unless a CAM module is enabled in a configuration.
"""

from .adaptive_budget import FailureConfidence, compute_adaptive_budget, estimate_failure_confidence
from .bootstrap_gate import CAMGateDecision, paired_bootstrap_gate
from .rejected_memory import PersistentRejectedEditMemory, RejectedEditMemoryItem

__all__ = [
    "CAMGateDecision",
    "FailureConfidence",
    "PersistentRejectedEditMemory",
    "RejectedEditMemoryItem",
    "compute_adaptive_budget",
    "estimate_failure_confidence",
    "paired_bootstrap_gate",
]
