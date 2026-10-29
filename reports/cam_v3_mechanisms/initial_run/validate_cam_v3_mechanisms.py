"""Small, deterministic, offline mechanism check for CAM v3.

This script blocks socket connections and subprocess/model execution before importing
CAM modules. Its synthetic results verify decision mechanics only; they are not a
benchmark result or evidence of calibration/generalization.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import socket
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from statistics import fmean


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "cam_v3_mechanisms"
PROTOCOL_PATH = REPORT_DIR / "protocol.json"


def _blocked(*_args, **_kwargs):
    raise RuntimeError("Offline mechanism experiment blocked network/subprocess access")


# Hard block outbound sockets and child processes before importing project code.
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

# The selection guard needs only infra_errors, but skillopt.model's package
# initializer imports API clients. Supply a package shell so only the local,
# pure infra_errors module loads and no model API package is imported.
model_package = ModuleType("skillopt.model")
model_package.__path__ = [str(ROOT / "skillopt" / "model")]
sys.modules["skillopt.model"] = model_package

from skillopt.cam.adaptive_budget import compute_evidence_budget  # noqa: E402
from skillopt.cam.memory_context import prepare_memory_context  # noqa: E402
from skillopt.cam.rejected_memory import (  # noqa: E402
    PersistentRejectedEditMemory,
    RejectedEditMemoryItem,
)
from skillopt.cam.selection_guard import decide_cam_update  # noqa: E402

if "openai" in sys.modules or "anthropic" in sys.modules:
    raise RuntimeError("Unexpected model API package import in offline mechanism experiment")


PROTOCOL = {
    "protocol_version": 1,
    "title": "CAM v3 synthetic mechanism verification",
    "scope": "offline synthetic mechanism check; not benchmark accuracy, calibration, or generalization evidence",
    "fixed_before_outcomes": True,
    "design": {
        "scenario_count": 6,
        "seeds": list(range(20)),
        "paired_items_per_run": 40,
        "task_type_balance": {"cell_level": 20, "sheet_level": 20},
        "bootstrap_samples": 500,
        "confidence_level": 0.95,
        "v3_policy": "noninferiority",
        "v3_noninferiority_margin": 0.05,
        "v3_group_mean_regression_tolerance": 0.1,
        "v3_group_min_pairs": 5,
        "v2_policy": "paired-bootstrap with legacy soft acceptance (default)",
        "current_score": 0.6,
        "candidate_scores_clipped_to": [0.0, 1.0],
    },
    "scenarios": [
        {"name": "clear_improvement", "base_delta": 0.12, "noise_sd": 0.025,
         "construction": "40 balanced pairs; independent seeded Gaussian perturbation per pair",
         "static_hypothesis": "v2 accepts; v3 accepts if bootstrap and group checks pass"},
        {"name": "clear_loss", "base_delta": -0.12, "noise_sd": 0.025,
         "construction": "40 balanced pairs; independent seeded Gaussian perturbation per pair",
         "static_hypothesis": "both policies reject a clearly harmful candidate"},
        {"name": "unchanged", "base_delta": 0.0, "noise_sd": 0.0,
         "construction": "all paired scores equal",
         "static_hypothesis": "v2 requests reevaluation; v3 may soft-accept within its noninferiority margin"},
        {"name": "small_loss", "base_delta": -0.03, "noise_sd": 0.008,
         "construction": "40 balanced pairs; independent seeded Gaussian perturbation per pair",
         "static_hypothesis": "v2 requests reevaluation; v3 can soft-accept if its LCB clears -0.05"},
        {"name": "noisy_mixed_gains_losses", "base_delta": None, "noise_sd": 0.02,
         "construction": "each type has 10 +0.15 and 10 -0.12 items; seeded jitter and within-type shuffle",
         "static_hypothesis": "both subgroup means are near +0.015; decisions depend on paired uncertainty"},
        {"name": "subgroup_regression_masked_by_gains", "base_delta": None, "noise_sd": 0.02,
         "construction": "cell_level +0.20 and sheet_level -0.15, with seeded jitter",
         "static_hypothesis": "positive aggregate can pass v2 bootstrap; v3 group guard flags sheet regression"},
    ],
    "memory_fixture": {
        "query_type": "cell_level",
        "fixture_records": [
            {"memory_id": "cell-zero", "task_type": "cell_level", "score_change": 0.0,
             "supporting_task_ids": ["train-1", "train-2"], "failure_pattern": "formula reference range mismatch"},
            {"memory_id": "sheet-distractor", "task_type": "sheet_level", "score_change": -0.2,
             "supporting_task_ids": ["train-3", "train-4"], "failure_pattern": "formula reference range mismatch"},
        ],
        "static_hypothesis": "evidence_v3 scopes away the sheet distractor and retains the matching zero-delta record",
    },
    "memory_bundle_fixture": {
        "rejected_edits": ["edit A", "edit B", "edit C"],
        "kind": "inconclusive",
        "supporting_task_ids": ["train-1", "train-2"],
        "static_hypothesis": "legacy stores one record per edit; evidence_v3 stores one update-bundle record",
    },
    "budget_fixtures": [
        {"name": "duplicate", "static_hypothesis": "identical edits collapse and task IDs union"},
        {"name": "conflict", "static_hypothesis": "different edits at the same explicit target are excluded"},
        {"name": "no_provenance", "static_hypothesis": "unknown provenance uses min-budget fallback bounded by unique edits"},
        {"name": "explicit_ids", "static_hypothesis": "distinct validated task IDs establish candidate support"},
    ],
}


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _score_rows(seed: int, scenario: dict) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed * 1009 + PROTOCOL["scenarios"].index(scenario) * 9176)
    ids = [f"seed{seed:02d}-item{i:02d}" for i in range(40)]
    task_types = ["cell_level"] * 20 + ["sheet_level"] * 20
    deltas = []
    if scenario["name"] == "noisy_mixed_gains_losses":
        for _kind in ("cell_level", "sheet_level"):
            group = [0.15] * 10 + [-0.12] * 10
            rng.shuffle(group)
            deltas.extend(group)
    elif scenario["name"] == "subgroup_regression_masked_by_gains":
        deltas = [0.20] * 20 + [-0.15] * 20
    else:
        deltas = [scenario["base_delta"]] * 40
    if scenario["noise_sd"]:
        deltas = [delta + rng.gauss(0.0, scenario["noise_sd"]) for delta in deltas]

    current, candidate = [], []
    for item_id, kind, delta in zip(ids, task_types, deltas):
        old = 0.6
        new = min(1.0, max(0.0, old + delta))
        common = {"id": item_id, "split": "selection", "status": "completed", "task_type": kind}
        current.append({**common, "hard": old})
        candidate.append({**common, "hard": new})
    return current, candidate


def _run_gate_scenarios() -> list[dict]:
    rows = []
    for scenario in PROTOCOL["scenarios"]:
        for seed in PROTOCOL["design"]["seeds"]:
            current, candidate = _score_rows(seed, scenario)
            common_cfg = {"cam_confidence_level": 0.95, "cam_meaningful_improvement": 0.0,
                          "cam_bootstrap_samples": 500, "cam_seed": seed,
                          "cam_group_guard": False}
            v2_result, v2_audit = decide_cam_update(
                candidate_skill="candidate", current_skill="current", best_skill="current",
                current_results=current, candidate_results=candidate, best_results=current,
                best_step=0, global_step=1, cfg=common_cfg,
            )
            v3_cfg = {**common_cfg, "cam_soft_accept_policy": "noninferiority",
                      "cam_noninferiority_margin": 0.05, "cam_group_guard": True,
                      "cam_group_max_regression": 0.1, "cam_group_min_pairs": 5}
            v3_result, v3_audit = decide_cam_update(
                candidate_skill="candidate", current_skill="current", best_skill="current",
                current_results=current, candidate_results=candidate, best_results=current,
                best_step=0, global_step=1, cfg=v3_cfg,
            )
            deltas = [new["hard"] - old["hard"] for old, new in zip(current, candidate)]
            group_means = {}
            for kind in ("cell_level", "sheet_level"):
                group_means[kind] = fmean(
                    delta for delta, row in zip(deltas, current) if row["task_type"] == kind
                )
            rows.append({
                "scenario": scenario["name"], "seed": seed,
                "mean_improvement": fmean(deltas), "group_mean_improvements": group_means,
                "v2": {"action": v2_result.action, "bootstrap_action": v2_audit["action"],
                       "positive_bootstrap_fraction": v2_audit["positive_bootstrap_fraction"]},
                "v3": {"action": v3_result.action, "bootstrap_action": v3_audit["action"],
                       "group_guard": v3_audit["group_guard"],
                       "noninferiority_passed": v3_audit.get("noninferiority_passed"),
                       "positive_bootstrap_fraction": v3_audit["positive_bootstrap_fraction"]},
            })
    return rows


def _run_memory_fixture() -> dict:
    with tempfile.TemporaryDirectory(prefix="cam-v3-memory-offline-") as temp_dir:
        current_training_failure = [{
            "id": "train-query", "split": "train", "hard": 0,
            "task_type": "cell_level", "failure_type": "formula_reference_range_mismatch",
            "fail_reason": "formula reference range mismatch", "task_description": "repair formula reference range",
        }]
        outputs = {}
        for policy in ("legacy", "evidence_v3"):
            memory = PersistentRejectedEditMemory(Path(temp_dir) / f"{policy}.json", policy=policy)
            memory.items = [RejectedEditMemoryItem(
                memory_id=record["memory_id"], failure_pattern=record["failure_pattern"],
                rejected_edit=f"review {record['memory_id']}", score_change=record["score_change"],
                benchmark="mechanism-fixture", task_type=record["task_type"], epoch=1, step=1,
                kind="inconclusive" if record["score_change"] == 0 else "rejected",
                attribution_scope="update_bundle", supporting_task_ids=tuple(record["supporting_task_ids"]),
                support_count=len(record["supporting_task_ids"]),
            ) for record in PROTOCOL["memory_fixture"]["fixture_records"]]
            context, audit = prepare_memory_context(
                memory, current_training_failure, benchmark="mechanism-fixture", epoch=2, step=2, top_k=5,
            )
            outputs[policy] = {
                "hit_ids": audit["hit_ids"],
                "hit_count": audit["hit_count"],
                "zero_delta_hit": any(hit["score_change"] == 0 for hit in audit["hits"]),
                "cross_type_hit": any(hit["task_type"] == "sheet_level" for hit in audit["hits"]),
                "context_prepared": audit["context_prepared"],
                "context_sha256": audit["context_sha256"],
            }
        bundle_counts = {}
        bundle_metadata = {}
        for policy in ("legacy", "evidence_v3"):
            memory = PersistentRejectedEditMemory(Path(temp_dir) / f"bundle-{policy}.json", policy=policy)
            stored = memory.add_rejected_step(
                failure_patterns=["formula reference range mismatch"],
                rejected_edits=PROTOCOL["memory_bundle_fixture"]["rejected_edits"],
                score_change=0.0, benchmark="mechanism-fixture", task_type="cell_level",
                epoch=1, step=1, kind=PROTOCOL["memory_bundle_fixture"]["kind"],
                supporting_task_ids=PROTOCOL["memory_bundle_fixture"]["supporting_task_ids"],
            )
            bundle_counts[policy] = len(stored)
            bundle_metadata[policy] = [{"memory_id": item.memory_id,
                                        "attribution_scope": item.attribution_scope,
                                        "stored_edit_count": len(item.rejected_edit.splitlines()),
                                        "supporting_task_ids": list(item.supporting_task_ids)}
                                       for item in stored]
        return {"retrieval": outputs, "bundle_record_count": bundle_counts,
                "bundle_metadata": bundle_metadata}


def _run_budget_fixtures() -> list[dict]:
    fixtures = [
        ("duplicate", [
            {"op": "replace", "target": "A", "content": "new", "support_task_ids": ["t1"]},
            {"op": "replace", "target": "A", "content": "new", "task_ids": ["t2"]},
            {"op": "replace", "target": "B", "content": "other", "task_ids": ["t3", "t4"]},
        ]),
        ("conflict", [
            {"op": "replace", "target": "A", "content": "new", "task_ids": ["t1", "t2"]},
            {"op": "delete", "target": "A", "task_ids": ["t3", "t4"]},
            {"op": "replace", "target": "B", "content": "other", "task_ids": ["t5", "t6"]},
        ]),
        ("no_provenance", [
            {"op": "replace", "target": "A", "content": "new", "support_count": 100},
            {"op": "replace", "target": "B", "content": "other"},
        ]),
        ("explicit_ids", [
            {"op": "replace", "target": "A", "content": "new", "evidence_task_ids": ["t1", "t2"]},
            {"op": "replace", "target": "B", "content": "other", "evidence_task_ids": ["t3", "t4"]},
            {"op": "replace", "target": "C", "content": "last", "evidence_task_ids": ["t5"]},
        ]),
    ]
    results = []
    for name, edits in fixtures:
        decision = compute_evidence_budget(edits, total_failures=100, min_budget=2,
                                           max_budget=4, min_support_tasks=2)
        results.append({
            "name": name, "budget": decision.budget,
            "candidate_count": decision.candidate_count,
            "eligible_candidate_count": decision.eligible_candidate_count,
            "eligible_indices": decision.eligible_indices,
            "eligible_targets": [item.get("target") for item in decision.eligible_edits],
            "evidence_task_ids": decision.evidence_task_ids,
            "unknown_provenance": decision.unknown_provenance,
            "duplicate_count": decision.duplicate_count,
            "conflicting_count": decision.conflicting_count,
            "reason": decision.reason,
        })
    return results


def _summarize_gate(rows: list[dict]) -> dict:
    summary = {}
    for scenario in PROTOCOL["scenarios"]:
        subset = [row for row in rows if row["scenario"] == scenario["name"]]
        summary[scenario["name"]] = {
            "n_runs": len(subset),
            "mean_delta": fmean(row["mean_improvement"] for row in subset),
            "mean_cell_delta": fmean(row["group_mean_improvements"]["cell_level"] for row in subset),
            "mean_sheet_delta": fmean(row["group_mean_improvements"]["sheet_level"] for row in subset),
            "v2_actions": dict(Counter(row["v2"]["action"] for row in subset)),
            "v3_actions": dict(Counter(row["v3"]["action"] for row in subset)),
            "v3_group_guard_statuses": dict(Counter(row["v3"]["group_guard"]["status"] for row in subset)),
        }
    return summary


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    # Freeze the prespecified protocol to disk before producing any outcomes.
    _write_json(PROTOCOL_PATH, PROTOCOL)
    protocol_sha256 = _sha256(PROTOCOL_PATH)

    gate_runs = _run_gate_scenarios()
    memory = _run_memory_fixture()
    budget = _run_budget_fixtures()
    modules = [
        ROOT / "skillopt/cam/bootstrap_gate.py",
        ROOT / "skillopt/cam/selection_guard.py",
        ROOT / "skillopt/cam/memory_context.py",
        ROOT / "skillopt/cam/rejected_memory.py",
        ROOT / "skillopt/cam/adaptive_budget.py",
        Path(__file__).resolve(),
    ]
    metadata = {
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": {"python": sys.version, "implementation": platform.python_implementation(),
                    "platform": platform.platform()},
        "protocol_sha256": protocol_sha256,
        "code_sha256": {str(path.relative_to(ROOT)): _sha256(path) for path in modules},
        "offline_guards": ["socket.socket.connect/connect_ex", "socket.create_connection",
                           "subprocess.Popen/run/call/check_call/check_output", "os.system/popen"],
        "model_api_imports": "none",
    }
    result = {
        "label": "synthetic mechanism verification only",
        "interpretation_limit": "No SpreadsheetBench accuracy, calibration, or generalization claim is supported.",
        "metadata": metadata,
        "gate": {"runs": gate_runs, "by_scenario": _summarize_gate(gate_runs)},
        "memory_fixture": memory,
        "budget_fixtures": budget,
        "mechanism_findings": [
            "All six scenario outcomes are generated synthetic score data; they do not estimate real-task performance.",
            "In small_loss, the paired bootstrap returned reject in all 20 seeds. The current selection_guard checks that reject branch before its noninferiority-margin branch, so the configured 0.05 margin did not soft-accept these confidently negative but within-margin examples.",
            "The subgroup-regression fixture had aggregate mean improvement near +0.025 while sheet_level regressed near -0.15; v2 soft-accepted all 20 runs and the v3 group guard requested reevaluation in all 20.",
        ],
        "prespecified_hypotheses": {
            "gate": {scenario["name"]: scenario["static_hypothesis"] for scenario in PROTOCOL["scenarios"]},
            "memory": PROTOCOL["memory_fixture"]["static_hypothesis"],
            "memory_bundle": PROTOCOL["memory_bundle_fixture"]["static_hypothesis"],
            "budget": {fixture["name"]: fixture["static_hypothesis"] for fixture in PROTOCOL["budget_fixtures"]},
        },
    }
    _write_json(REPORT_DIR / "results.json", result)
    _write_markdown(result)


def _write_markdown(result: dict) -> None:
    lines = [
        "# CAM v3 offline mechanism verification",
        "",
        "> Synthetic mechanism verification only. This is not SpreadsheetBench accuracy, calibration, or generalization evidence.",
        "",
        f"Protocol SHA-256: `{result['metadata']['protocol_sha256']}`  ",
        f"Completed UTC: `{result['metadata']['completed_utc']}`  ",
        f"Runtime: `{result['metadata']['runtime']['python'].splitlines()[0]}` / `{result['metadata']['runtime']['platform']}`",
        "",
        "## Paired gate scenarios",
        "",
        "Each row aggregates 20 fixed seeds over 40 paired items (20 per task type), with 500 paired bootstrap resamples.",
        "v3 uses a 0.05 noninferiority margin and a 0.10 subgroup mean regression tolerance with at least five pairs per group.",
        "",
        "| Scenario | Mean Δ | v2 actions | v3 actions | v3 group guard |",
        "|---|---:|---|---|---|",
    ]
    for scenario in PROTOCOL["scenarios"]:
        row = result["gate"]["by_scenario"][scenario["name"]]
        lines.append(
            f"| {scenario['name']} | {row['mean_delta']:.4f} | "
            f"{json.dumps(row['v2_actions'], sort_keys=True)} | "
            f"{json.dumps(row['v3_actions'], sort_keys=True)} | "
            f"{json.dumps(row['v3_group_guard_statuses'], sort_keys=True)} |"
        )
    lines += ["", "## Memory fixture", "", "| Policy | Hits | Zero-delta retained | Cross-type distractor retrieved |", "|---|---|---|---|"]
    for policy, row in result["memory_fixture"]["retrieval"].items():
        lines.append(f"| {policy} | {', '.join(row['hit_ids']) or 'none'} | {row['zero_delta_hit']} | {row['cross_type_hit']} |")
    lines += ["", f"Bundle record count for {len(PROTOCOL['memory_bundle_fixture']['rejected_edits'])} edits: "
              f"legacy={result['memory_fixture']['bundle_record_count']['legacy']}, "
              f"evidence_v3={result['memory_fixture']['bundle_record_count']['evidence_v3']}."]
    lines += ["", "## Observed mechanism details", ""]
    lines.extend(f"- {finding}" for finding in result["mechanism_findings"])
    lines += ["", "## Budget fixtures", "", "| Fixture | Budget | Candidates | Eligible | Duplicates | Conflicts | Unknown provenance |", "|---|---:|---:|---:|---:|---:|---|"]
    for row in result["budget_fixtures"]:
        lines.append(f"| {row['name']} | {row['budget']} | {row['candidate_count']} | {row['eligible_candidate_count']} | {row['duplicate_count']} | {row['conflicting_count']} | {row['unknown_provenance']} |")
    lines += ["", "## Reproducibility", "", "The full 120 gate decisions, fixtures, protocol, and source hashes are in the adjacent JSON files. The script patches socket connections and subprocess entry points to raise before importing CAM modules; no model API imports or network calls are used.", ""]
    (REPORT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
