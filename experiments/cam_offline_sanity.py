"""Small, offline verification experiment for the three CAM-SkillOpt modules.

This is not a benchmark score.  It uses fixed synthetic paired outcomes to
verify the mathematical contracts before model-backed training is attempted.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from skillopt.cam import (
    PersistentRejectedEditMemory,
    compute_adaptive_budget,
    estimate_failure_confidence,
    paired_bootstrap_gate,
)


def main() -> None:
    out_dir = Path("outputs/cam_offline_sanity")
    out_dir.mkdir(parents=True, exist_ok=True)

    gate_cases = {
        "reliable_improvement": paired_bootstrap_gate(
            [0.0] * 20,
            [1.0] * 20,
            bootstrap_samples=2_000,
        ),
        "uncertain_change": paired_bootstrap_gate(
            [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            bootstrap_samples=2_000,
        ),
        "reliable_regression": paired_bootstrap_gate(
            [1.0] * 20,
            [0.0] * 20,
            bootstrap_samples=2_000,
        ),
    }

    concentrated = estimate_failure_confidence({"tool_selection": 40})
    diffuse = estimate_failure_confidence(
        {
            "tool_selection": 8,
            "formatting": 8,
            "verification": 8,
            "search": 8,
            "reasoning": 8,
        }
    )

    memory_path = out_dir / "rejected_memory.json"
    if memory_path.exists():
        memory_path.unlink()
    memory = PersistentRejectedEditMemory(memory_path)
    memory.add_rejected_step(
        failure_patterns=["tool selection error", "incorrect spreadsheet operation"],
        rejected_edits=["Always retry every tool with a different command."],
        score_change=-0.25,
        benchmark="synthetic_spreadsheet",
        task_type="tool_use",
        epoch=1,
        step=1,
    )
    memory.add_rejected_step(
        failure_patterns=["output formatting error"],
        rejected_edits=["Use free-form prose regardless of the required schema."],
        score_change=-0.10,
        benchmark="synthetic_spreadsheet",
        task_type="tool_use",
        epoch=1,
        step=2,
    )
    retrieved = memory.retrieve("spreadsheet tool selection failure", top_k=2)

    report = {
        "report_type": "offline_module_verification_not_benchmark_result",
        "bootstrap_gate": {name: asdict(result) for name, result in gate_cases.items()},
        "adaptive_budget": {
            "concentrated": {**asdict(concentrated), "edit_budget": compute_adaptive_budget(concentrated)},
            "diffuse": {**asdict(diffuse), "edit_budget": compute_adaptive_budget(diffuse)},
        },
        "persistent_memory": {
            "stored_items": len(memory.items),
            "query": "spreadsheet tool selection failure",
            "retrieved": [
                {
                    "memory_id": result.item.memory_id,
                    "failure_pattern": result.item.failure_pattern,
                    "rejected_edit": result.item.rejected_edit,
                    "score_change": result.item.score_change,
                    "similarity": result.similarity,
                    "retrieval_score": result.retrieval_score,
                }
                for result in retrieved
            ],
        },
    }
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWrote {report_path}")


if __name__ == "__main__":
    main()
