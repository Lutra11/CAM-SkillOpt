"""Offline diagnosis of completed CAM v3 training/validation artifacts.

No model backend, environment-file loader, subprocess, or evaluation is imported.
Held-out predictions are forbidden inputs. Old reports are hashed only to check
preservation; their scores never enter the diagnostic calculations.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVISION = "cam_v3r1_runtime_diagnostics"
OLD_STUDY = "continuous_adaptation_v3"
ARTIFACTS = ROOT / "outputs/studies" / REVISION
REPORTS = ROOT / "reports" / REVISION
FORBIDDEN_COMPONENTS = {"test_eval", "test_eval_baseline", "test_eval_final", "test"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_training_json(path: Path) -> object:
    if any(part.lower() in FORBIDDEN_COMPONENTS for part in path.parts):
        raise ValueError("Held-out data or predictions cannot enter mechanism diagnosis")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolve_inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Artifact path leaves the registered workspace")
    return path


def preserve_checks(workspace: Path, registration: Path) -> list[dict]:
    manifest = read_training_json(registration)
    checks = []
    for row in manifest["preserved_old_evidence"]:
        path = resolve_inside(workspace, row["path"])
        checks.append({"path": row["path"], "unchanged": digest(path) == row["sha256"]})
    if not all(row["unchanged"] for row in checks):
        raise ValueError("Registered old evidence changed; refuse to reinterpret it")
    return checks


def diagnose(workspace: Path, study_root: Path) -> dict:
    freeze = read_training_json(study_root / "freeze.json")
    cells = []
    for cell in freeze["cells"]:
        if cell["method"] != "cam_v3":
            continue
        folder = resolve_inside(workspace, cell["out_root"])
        history = read_training_json(folder / "history.json")
        runtime = read_training_json(folder / "runtime_state.json")
        journal = read_training_json(folder / "cam_gate_events.json")
        by_origin = {row["origin"]: row for row in journal}
        plan = freeze["task_orders"][cell["cell_id"]]
        batches = {row["global_step"]: row for row in plan["batches"]}
        seen_types = set()
        steps = []
        for row in history:
            step = row["step"]
            task_type = batches[step]["task_type"]
            recurrence = task_type in seen_types
            seen_types.add(task_type)
            directory = folder / "steps" / f"step_{step:04d}"
            budget = read_training_json(directory / "evidence_budget.json")
            gate = by_origin[f"step_{step:04d}"]
            memory = row.get("persistent_memory", {})
            steps.append({
                "step": step, "task_type": task_type,
                "recurring_task_type": recurrence, "action": row["action"],
                "candidate_validation_score": row.get("selection_hard"),
                "best_validation_score": row.get("best_score"),
                "budget": budget["budget"], "budget_reason": budget["reason"],
                "unknown_provenance": budget["unknown_provenance"],
                "supported_training_ids": len(budget.get("evidence_task_ids", [])),
                "memory_retrieval_calls": memory.get("retrieval_calls", 0),
                "memory_injected_items": memory.get("injected_items", 0),
                "memory_records_added": memory.get("items_added", 0),
                "gate_lower_bound": gate.get("lower_confidence_bound"),
                "gate_upper_bound": gate.get("upper_confidence_bound"),
                "promotion_authorized": gate.get("promotion_authorized", False),
                "group_guard": gate.get("group_guard"),
                "noninferiority_passed": gate.get("noninferiority_passed"),
            })
        cells.append({
            "cell_id": cell["cell_id"], "seed": cell["seed"], "steps": steps,
            "final_current_validation_score": runtime["current_score"],
            "final_best_validation_score": runtime["best_score"],
            "best_origin": runtime["best_origin"], "current_origin": runtime["current_origin"],
            "best_skill_sha256": digest(folder / "best_skill.md"),
        })
        # Compare bytes, not distinct filenames that may contain identical skills.
        cells[-1]["same_current_and_best"] = (
            digest(resolve_inside(workspace, runtime["current_skill_path"])) == cells[-1]["best_skill_sha256"]
        )

    auxiliary = []
    for cell in freeze["cells"]:
        folder = resolve_inside(workspace, cell["out_root"])
        for family, filename in (("slow_update", "slow_result.json"),
                                 ("meta_skill", "meta_skill_result.json")):
            for path in sorted((folder / family).glob(f"epoch_*/{filename}")):
                outcome = read_training_json(path)
                if outcome.get("action") == "no_content":
                    auxiliary.append({"cell_id": cell["cell_id"], "family": family,
                                      "epoch": path.parent.name,
                                      "time_s": outcome.get("time_s"),
                                      "legacy_action": "no_content",
                                      "path": path.relative_to(workspace).as_posix()})
    rows = [row for cell in cells for row in cell["steps"]]
    recurring = [row for row in rows if row["recurring_task_type"]]
    return {
        "revision": REVISION, "source_study": OLD_STUDY,
        "scope": "Training and validation mechanism behavior, not a new accuracy experiment",
        "model_api_calls": 0, "heldout_predictions_read": False,
        "cam_cells": cells,
        "totals": {
            "training_steps": len(rows),
            "budget_unknown_provenance_steps": sum(row["unknown_provenance"] for row in rows),
            "budget_reasons": dict(Counter(row["budget_reason"] for row in rows)),
            "memory_injected_steps": sum(row["memory_injected_items"] > 0 for row in rows),
            "memory_injected_items": sum(row["memory_injected_items"] for row in rows),
            "recurrence_opportunities": len(recurring),
            "recurrence_steps_with_memory": sum(row["memory_injected_items"] > 0 for row in recurring),
            "main_gate_actions": dict(Counter(row["action"] for row in rows)),
            "legacy_auxiliary_no_content": len(auxiliary),
            "auxiliary_no_content_by_family": dict(Counter(row["family"] for row in auxiliary)),
        },
        "legacy_auxiliary_outcomes": auxiliary,
        "interpretation_limits": [
            "Unknown-provenance fallback is not evidence that task-support adaptation helped.",
            "Memory warm-up steps must not be counted as missed recurrence retrieval opportunities.",
            "A higher current validation score does not establish a better held-out checkpoint.",
            "Cached gate decisions cannot establish counterfactual training trajectories or accuracy.",
            "Legacy no_content conflates outcomes; known failure attribution uses the preserved incident evidence.",
            "No gate threshold, budget, retrieval policy, model, or test subset is tuned by this diagnosis.",
        ],
    }


def markdown(result: dict) -> str:
    totals = result["totals"]
    lines = ["# CAM v3r1：零模型请求机制诊断", "",
             "本报告只分析旧实验的训练和验证工件。旧评估报告仅做保全哈希核对，不按测试成绩挑选参数或版本。", "",
             f"- 预算来源缺失：{totals['budget_unknown_provenance_steps']}/{totals['training_steps']} 步。",
             f"- 类型重现机会：{totals['recurrence_opportunities']} 步，其中 {totals['recurrence_steps_with_memory']} 步有记忆注入。",
             f"- 实际记忆注入：{totals['memory_injected_items']} 条，分布于 {totals['memory_injected_steps']} 步。",
             f"- 旧辅助 no_content 记录：{totals['legacy_auxiliary_no_content']} 条，类型分布 {totals['auxiliary_no_content_by_family']}。", "",
             "| 单元 | 步 | 类型重现 | 动作 | 预算 | 来源未知 | 注入记忆条数 | 验证候选分 | 验证 best 分 |", "|---|---:|---|---|---:|---|---:|---:|---:|"]
    for cell in result["cam_cells"]:
        for row in cell["steps"]:
            lines.append(f"| {cell['cell_id']} | {row['step']} | {row['recurring_task_type']} | {row['action']} | {row['budget']} | {row['unknown_provenance']} | {row['memory_injected_items']} | {row['candidate_validation_score']} | {row['best_validation_score']} |")
    lines += ["", "## 解释", "",
              "预算模块运行了，但所有步骤都走来源未知回退；不能说已验证基于独立训练任务支持数的自适应预算。",
              "记忆在类型重现时确实有注入，不能把前两步的正常预热当作检索失败；是否改善编辑仍需单组件对照。",
              "两个 CAM 的最终 best 都来自第一步。seed43 的 current 验证分高于 best，但现有规则没有授权晋升。这是待诊断的选择权衡，不证明后期技能测试表现更好。",
              "辅助生成异常、响应类型丢失及提示词冻结缺口需要先修复；本报告不改变任何 CAM 决策阈值或实验轨迹。", "",
              "原始字段、门控区间、分组检查和逐步预算原因见 diagnostics.json。旧实验及其结果不作为本修订的新实验结果。", ""]
    return "\n".join(lines)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()  # Intentionally no online/authentication execution mode.
    checks = preserve_checks(ROOT, ARTIFACTS / "prechange_manifest.json")
    protocol = {"revision": REVISION, "script_sha256": digest(Path(__file__)),
                "source_study": OLD_STUDY, "model_api_budget": 0,
                "inputs": "frozen task-order metadata, train histories, gate/budget/memory records, auxiliary outcomes",
                "heldout_predictions": "forbidden", "old_report_access": "preservation hashes only"}
    protocol_path = REPORTS / "diagnostic_protocol.json"
    if protocol_path.exists() and read_training_json(protocol_path) != protocol:
        raise ValueError("Diagnostic protocol changed; preserve evidence and register a new revision")
    write_json(protocol_path, protocol)
    result = diagnose(ROOT, ROOT / "outputs/studies" / OLD_STUDY)
    result["observed_at_utc"] = datetime.now(timezone.utc).isoformat()
    result["old_evidence_preservation"] = checks
    preserve_checks(ROOT, ARTIFACTS / "prechange_manifest.json")
    write_json(REPORTS / "diagnostics.json", result)
    (REPORTS / "DIAGNOSIS.md").write_text(markdown(result), encoding="utf-8")
    print(f"Offline diagnosis complete: {result['totals']['training_steps']} CAM steps; model API calls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
