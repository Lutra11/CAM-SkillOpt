"""Frozen, offline binary-hard-score supplement for CAM v3 gate mechanisms."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import socket
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "cam_v3_mechanisms" / "binary"
PROTOCOL_PATH = REPORT_DIR / "protocol.json"


def _blocked(*_args, **_kwargs):
    raise RuntimeError("Offline binary mechanism experiment blocked network/subprocess access")


socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
subprocess.Popen.__init__ = _blocked
subprocess.run = _blocked
subprocess.call = _blocked
subprocess.check_call = _blocked
subprocess.check_output = _blocked
os.system = _blocked
os.popen = _blocked

sys.path.insert(0, str(ROOT))
model_package = ModuleType("skillopt.model")
model_package.__path__ = [str(ROOT / "skillopt" / "model")]
sys.modules["skillopt.model"] = model_package

from skillopt.cam.selection_guard import decide_cam_update  # noqa: E402

if "openai" in sys.modules or "anthropic" in sys.modules:
    raise RuntimeError("Unexpected model API package import in offline binary experiment")


SCENARIOS = [
    {"name": "clear_improvement", "construction": "flip 5 current failures to passes in each type",
     "expected_delta": 0.25, "hypothesis": "both arms accept"},
    {"name": "clear_loss", "construction": "flip 5 current passes to failures in each type",
     "expected_delta": -0.25, "hypothesis": "both arms reject"},
    {"name": "unchanged", "construction": "candidate equals current",
     "expected_delta": 0.0, "hypothesis": "v2 soft-accepts; v3 may soft-accept under noninferiority"},
    {"name": "sparse_loss", "construction": "flip 1 current pass to failure overall",
     "expected_delta": -0.025, "hypothesis": "tests v3 noninferiority hold on a sparse negative change"},
    {"name": "noisy_exchange", "construction": "per type flip 5 failures to passes and 5 passes to failures",
     "expected_delta": 0.0, "hypothesis": "v2 and v3 should treat wide uncertainty as inconclusive"},
    {"name": "masked_group_loss", "construction": "cell: flip 10 failures to passes; sheet: flip 3 passes to failures",
     "expected_delta": 0.175, "hypothesis": "v3 group guard blocks subgroup regression hidden by aggregate gain"},
]

PROTOCOL = {
    "protocol_version": 1,
    "title": "CAM v3 binary hard-score gate mechanism supplement",
    "relationship_to_continuous_run": "independent supplement; prior continuous-score report remains unchanged and is a fractional-score diagnostic, not an actual hard-task score distribution",
    "scope": "offline synthetic mechanism verification only; not SpreadsheetBench accuracy, calibration, or generalization evidence",
    "design": {
        "seeds": list(range(20)), "paired_items_per_run": 40,
        "task_type_balance": {"cell_level": 20, "sheet_level": 20},
        "current_passes_per_type": 10, "score_domain": [0, 1],
        "bootstrap_samples": 500, "confidence_level": 0.95,
        "v3_noninferiority_margin": 0.05,
        "v3_group_mean_regression_tolerance": 0.1,
        "v3_group_min_pairs": 5,
        "id_randomization": "one per-seed permutation of 40 fixed IDs; type assignment is first 20 cell_level and next 20 sheet_level; within-type order determines current pass IDs and scenario flip IDs",
        "same_pairs_for_both_arms": True,
    },
    "scenarios": SCENARIOS,
    "analysis": "Report per-arm decisions and observed subgroup means for all 120 paired runs; do not tune thresholds or reinterpret the continuous-score fixture.",
}


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_rows(seed: int, scenario_name: str) -> tuple[list[dict], list[dict]]:
    rng = random.Random(73019 + seed)
    ids = [f"binary-seed{seed:02d}-item{i:02d}" for i in range(40)]
    rng.shuffle(ids)
    typed_ids = {"cell_level": ids[:20], "sheet_level": ids[20:]}
    current_by_id: dict[str, tuple[str, int]] = {}
    candidate_by_id: dict[str, int] = {}
    shuffled_within_type = {}
    for kind, group_ids in typed_ids.items():
        shuffled_within_type[kind] = list(group_ids)
        for index, item_id in enumerate(group_ids):
            current_by_id[item_id] = (kind, 1 if index < 10 else 0)
            candidate_by_id[item_id] = current_by_id[item_id][1]

    def flip(group_ids, from_score: int, to_score: int, count: int) -> None:
        eligible = [item_id for item_id in shuffled_within_type[group_ids]
                    if current_by_id[item_id][1] == from_score]
        # Choices are reproducible and disjoint by construction within a scenario.
        for item_id in eligible[:count]:
            candidate_by_id[item_id] = to_score

    if scenario_name == "clear_improvement":
        for kind in typed_ids:
            flip(kind, 0, 1, 5)
    elif scenario_name == "clear_loss":
        for kind in typed_ids:
            flip(kind, 1, 0, 5)
    elif scenario_name == "sparse_loss":
        flip("cell_level", 1, 0, 1)
    elif scenario_name == "noisy_exchange":
        for kind in typed_ids:
            flip(kind, 0, 1, 5)
            flip(kind, 1, 0, 5)
    elif scenario_name == "masked_group_loss":
        flip("cell_level", 0, 1, 10)
        flip("sheet_level", 1, 0, 3)
    elif scenario_name != "unchanged":
        raise ValueError(f"Unexpected scenario: {scenario_name}")

    current, candidate = [], []
    for item_id in ids:
        kind, old_score = current_by_id[item_id]
        common = {"id": item_id, "split": "selection", "status": "completed", "task_type": kind}
        current.append({**common, "hard": old_score})
        candidate.append({**common, "hard": candidate_by_id[item_id]})
    return current, candidate


def _gate_decision(current, candidate, seed: int, *, v3: bool):
    cfg = {"cam_confidence_level": 0.95, "cam_meaningful_improvement": 0.0,
           "cam_bootstrap_samples": 500, "cam_seed": seed,
           "cam_group_guard": v3}
    if v3:
        cfg.update({"cam_soft_accept_policy": "noninferiority",
                    "cam_noninferiority_margin": 0.05,
                    "cam_group_max_regression": 0.1,
                    "cam_group_min_pairs": 5})
    result, audit = decide_cam_update(
        candidate_skill="candidate", current_skill="current", best_skill="current",
        current_results=current, candidate_results=candidate, best_results=current,
        best_step=0, global_step=1, cfg=cfg,
    )
    return result, audit


def _run() -> dict:
    decisions = []
    for scenario in SCENARIOS:
        for seed in PROTOCOL["design"]["seeds"]:
            current, candidate = _make_rows(seed, scenario["name"])
            v2_result, v2_audit = _gate_decision(current, candidate, seed, v3=False)
            v3_result, v3_audit = _gate_decision(current, candidate, seed, v3=True)
            changes = [new["hard"] - old["hard"] for old, new in zip(current, candidate)]
            groups = {}
            for kind in ("cell_level", "sheet_level"):
                group_changes = [delta for delta, row in zip(changes, current) if row["task_type"] == kind]
                groups[kind] = {"mean_delta": fmean(group_changes), "n_pairs": len(group_changes),
                                "current_passes": sum(row["hard"] == 1 for row in current
                                                      if row["task_type"] == kind),
                                "candidate_passes": sum(row["hard"] == 1 for row in candidate
                                                        if row["task_type"] == kind)}
            decisions.append({
                "scenario": scenario["name"], "seed": seed,
                "mean_delta": fmean(changes), "group_details": groups,
                "v2": {"action": v2_result.action, "bootstrap_action": v2_audit["action"],
                       "mean_improvement": v2_audit["mean_improvement"],
                       "lower_confidence_bound": v2_audit["lower_confidence_bound"],
                       "upper_confidence_bound": v2_audit["upper_confidence_bound"]},
                "v3": {"action": v3_result.action, "bootstrap_action": v3_audit["action"],
                       "mean_improvement": v3_audit["mean_improvement"],
                       "lower_confidence_bound": v3_audit["lower_confidence_bound"],
                       "upper_confidence_bound": v3_audit["upper_confidence_bound"],
                       "group_guard": v3_audit["group_guard"],
                       "noninferiority_passed": v3_audit.get("noninferiority_passed")},
            })
    summary = {}
    for scenario in SCENARIOS:
        subset = [row for row in decisions if row["scenario"] == scenario["name"]]
        summary[scenario["name"]] = {
            "n_runs": len(subset), "mean_delta": fmean(row["mean_delta"] for row in subset),
            "mean_cell_delta": fmean(row["group_details"]["cell_level"]["mean_delta"] for row in subset),
            "mean_sheet_delta": fmean(row["group_details"]["sheet_level"]["mean_delta"] for row in subset),
            "v2_actions": dict(Counter(row["v2"]["action"] for row in subset)),
            "v3_actions": dict(Counter(row["v3"]["action"] for row in subset)),
            "v3_group_guard_statuses": dict(Counter(row["v3"]["group_guard"]["status"] for row in subset)),
        }
    return {"runs": decisions, "by_scenario": summary}


def _write_markdown(result: dict) -> None:
    lines = [
        "# CAM v3 binary-score offline mechanism supplement",
        "",
        "> Synthetic mechanism verification only. This is not SpreadsheetBench accuracy, calibration, or generalization evidence.",
        "> The earlier continuous-score fixture is retained separately as a fractional-score diagnostic, not an actual hard-task score distribution.",
        "",
        f"Protocol SHA-256: `{result['metadata']['protocol_sha256']}`  ",
        f"Completed UTC: `{result['metadata']['completed_utc']}`  ",
        f"Runtime: `{result['metadata']['runtime']['python'].splitlines()[0]}` / `{result['metadata']['runtime']['platform']}`",
        "",
        "The frozen design uses seeds 0–19, 40 paired binary items per run (20 per type, 10 current passes per type), and 500 paired bootstrap resamples. Both arms receive the same ID-aligned pairs.",
        "v3 uses noninferiority margin 0.05 and subgroup mean regression tolerance 0.10 with at least five pairs per group.",
        "",
        "| Scenario | Mean Δ | Cell Δ | Sheet Δ | v2 actions | v3 actions | v3 group guard |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for scenario in SCENARIOS:
        row = result["gate"]["by_scenario"][scenario["name"]]
        lines.append(f"| {scenario['name']} | {row['mean_delta']:.3f} | {row['mean_cell_delta']:.3f} | {row['mean_sheet_delta']:.3f} | "
                     f"{json.dumps(row['v2_actions'], sort_keys=True)} | {json.dumps(row['v3_actions'], sort_keys=True)} | "
                     f"{json.dumps(row['v3_group_guard_statuses'], sort_keys=True)} |")
    lines += ["", "The full per-seed decisions, confidence bounds, counts, hypotheses, and hashes are in `results.json`. Offline guards reject socket connections and subprocess/model execution before importing the selection module; no model API packages or environment files are read.", ""]
    (REPORT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    # Freeze the binary supplement protocol on disk before computing outcomes.
    _write_json(PROTOCOL_PATH, PROTOCOL)
    protocol_sha256 = _sha256(PROTOCOL_PATH)
    gate = _run()
    modules = [ROOT / "skillopt/cam/bootstrap_gate.py",
               ROOT / "skillopt/cam/selection_guard.py", Path(__file__).resolve()]
    result = {
        "label": "binary synthetic mechanism verification only",
        "interpretation_limit": "No SpreadsheetBench accuracy, calibration, or generalization claim is supported.",
        "metadata": {
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "runtime": {"python": sys.version, "implementation": platform.python_implementation(),
                        "platform": platform.platform()},
            "protocol_sha256": protocol_sha256,
            "code_sha256": {str(path.relative_to(ROOT)): _sha256(path) for path in modules},
            "offline_guards": ["socket.socket.connect/connect_ex", "socket.create_connection",
                               "subprocess.Popen/run/call/check_call/check_output", "os.system/popen"],
            "model_api_imports": "none",
            "environment_files_read": False,
        },
        "gate": gate,
        "prespecified_hypotheses": {row["name"]: row["hypothesis"] for row in SCENARIOS},
    }
    _write_json(REPORT_DIR / "results.json", result)
    _write_markdown(result)


if __name__ == "__main__":
    main()
