"""扫描 outputs/matrix/** 的每个非 static 训练单元，输出「机制决策日志分析」。

只读扫描（唯一写入目标是 reports/cam_analysis.md 与 reports/cam_analysis.json）。
容忍「正在跑」的单元：history.json / cam_gate_events.json 可能半截、
summary.json 缺失、部分 step 目录只有 rollout —— 一律取不到记 None
（markdown 显示 —，不编数），坏文件跳过，不崩。

判定依据（见 docs/CAM_EVIDENCE_MAP.md 的机制→证据映射）：
  - history.json：list，每条一个 step；**被评步**（action ∈
    accept/accept_new_best/reject/cam_re_evaluate）才有 cam_gate_used，
    skip 步无该键属正常，不算矛盾
  - cam_gate_events.json：全量重写的门控决策事件，仅 strict_cam 臂产生
    （cam_full / cam_no_adaptive_budget / cam_no_memory）；
    baseline / cam_no_bootstrap 不应产生任何 cam 门控文件
  - cam_rejected_memory.json：仅在第一个 reject 步才落盘，冷启动缺失属正常
  - patches/**/memory_retrieval.json：**所有臂都写**，开关看 enabled 字段
  - cam_no_memory 臂永不产生 cam_rejected_memory.json / memory_write.json
  - 非门控臂不可能出现 cam_re_evaluate 动作（走标量 evaluate_gate）

Usage:
    python -X utf8 scripts/analyze_cam.py                  # 全矩阵扫描
    python -X utf8 scripts/analyze_cam.py --cell <path>     # 只分析一个单元
    python -X utf8 scripts/analyze_cam.py --json-only       # 只写 JSON
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "outputs" / "matrix"
MD_PATH = ROOT / "reports" / "cam_analysis.md"
JSON_PATH = ROOT / "reports" / "cam_analysis.json"

# 最近 1 小时内有产物更新 => 视为运行中（与 collect_matrix 的宽限一致但更短）
RUNNING_GRACE_S = 60 * 60

# 臂分类（决定一致性校验的期望值）；未知臂回落到 config 的 cam_* 平坦键推导
#   gate   = strict_cam（bootstrap 门控 + cam_gate_events.json 应存在）
#   memory = cam_rejected_memory.json / memory_write.json 可能产生
ARM_PROFILE = {
    "baseline":               {"gate": False, "memory": False},
    "cam_full":               {"gate": True,  "memory": True},
    "cam_no_bootstrap":       {"gate": False, "memory": True},
    "cam_no_adaptive_budget": {"gate": True,  "memory": True},
    "cam_no_memory":          {"gate": True,  "memory": False},
}
CAM_ARM_NAMES = ("cam_full", "cam_no_bootstrap", "cam_no_adaptive_budget", "cam_no_memory")

# 被评步（这些 action 才经过门控/评估，才要求有 cam_gate_used）
EVALUATED_ACTIONS = {"accept", "accept_new_best", "soft_accept", "reject", "cam_re_evaluate"}
# 动作分布的固定顺序：skip 桶吸收 skip / skip_no_patches，其余未识别归「其他」
SKIP_ACTIONS = {"skip", "skip_no_patches"}
# 「发生了更新」= 候选被接受（current 技能真的被换掉）
UPDATE_ACTIONS = {"accept", "accept_new_best", "soft_accept"}
ACTION_BUCKETS = ("accept", "accept_new_best", "soft_accept", "reject", "skip", "cam_re_evaluate", "other")

# 臂不应该存在的 cam 产物（存在即告警）；分两组：门控组 / 记忆组
GATE_FILES = ("cam_gate_events.json", "cam_gate_state.json", "final_cam_gate.json")
MEM_FILES = ("cam_rejected_memory.json",)


def read_json(p: Path):
    """读 JSON 文件；失败（不存在 / 半截 / 编码问题）一律返回 None。"""
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def as_int(v):
    """仅接受真正的 int（排除 bool），否则返回 None。"""
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    return None


def parse_arm_seed(unit_name: str):
    """目录名形如 baseline_seed42 / cam_full_seed42，拆出 (arm, seed)。"""
    m = re.match(r"^(.+?)_seed(\d+)$", unit_name)
    if m:
        return m.group(1), int(m.group(2))
    return unit_name, None


def mean_of(values):
    """数值列表的均值；空列表或无 None 时返回 None。"""
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return None
    return sum(nums) / len(nums)


def fmt_ratio(num, den):
    """num/den 的百分比；分母为 0/None 时返回 '—'。"""
    if not den:
        return "—"
    return "%.1f%%" % (100.0 * num / den)


def fmt_num(v, spec="%.3f"):
    """数值按格式化串输出；None 输出 '—'。"""
    return "—" if v is None else spec % v


def fmt_seq(values, max_len=6):
    """短序列用 → 连接；超长截头尾（预算轨迹展示用）。"""
    if values is None:
        return "—"
    if not values:
        return "—"
    if len(values) <= max_len:
        return "→".join(str(v) for v in values)
    head = "→".join(str(v) for v in values[:3])
    tail = "→".join(str(v) for v in values[-2:])
    return "%s→…→%s" % (head, tail)


def profile_for(arm, cfg):
    """取臂期望行为：已知臂用固定表，未知臂按 config 的 cam_* 平坦键推导。

    返回 (gate_expected, memory_possible)；无法判定时返回 (None, None)。
    """
    if arm in ARM_PROFILE:
        prof = ARM_PROFILE[arm]
        return prof["gate"], prof["memory"]
    if not isinstance(cfg, dict) or not cfg:
        return None, None
    if cfg.get("cam_enabled") is None:
        return None, None
    gate = bool(cfg.get("cam_enabled")) and bool(cfg.get("cam_bootstrap_gate", True))
    memory = bool(cfg.get("cam_enabled")) and bool(cfg.get("cam_memory_enabled"))
    return gate, memory


def run_state_of(unit_dir: Path):
    """单元状态（简化版）：done / running / stale / not_started。

    done        summary.json 已写出
    running     无 summary 且最近 RUNNING_GRACE_S 秒内有产物更新
    stale       无 summary 且超时无更新
    not_started 目录为空
    """
    try:
        if not any(unit_dir.iterdir()):
            return "not_started"
    except OSError:
        return "not_started"
    if (unit_dir / "summary.json").exists():
        return "done"
    newest = None
    for path in unit_dir.rglob("*"):
        try:
            if not path.is_file():
                continue
            m = path.stat().st_mtime
        except OSError:
            continue
        if newest is None or m > newest:
            newest = m
    if newest is not None and (time.time() - newest) <= RUNNING_GRACE_S:
        return "running"
    return "stale"


def expected_steps_of(cfg):
    """期望 step 数 = num_epochs * ceil(train_size / batch_size)（按单元自己的 config 反推）。"""
    if not isinstance(cfg, dict):
        return None
    ne = as_int(cfg.get("num_epochs"))
    tsz = as_int(cfg.get("train_size"))
    bs = as_int(cfg.get("batch_size"))
    if ne is None or tsz is None or bs is None or bs <= 0:
        return None
    return ne * math.ceil(tsz / bs)


def history_stats(history):
    """从 history.json（list，每条一个 step）取进度 / 动作分布 / 预算轨迹。

    返回 dict；history 缺失或不可解析时 total=None，其余量为 None。
    """
    out = {
        "total": None, "evaluated": None,
        "action_counts": None, "other_actions": None,
        "update_steps": None, "update_ratio": None,
        "edit_budgets": None, "lr_control_modes": None,
        "gate_used_true": None, "gate_used_false": None, "gate_used_missing": None,
        "persistent_memory_enabled": None,
    }
    if not isinstance(history, list):
        return out
    steps = [h for h in history if isinstance(h, dict)]
    counts = Counter()
    other = set()
    budgets, lr_modes, pm_enabled = [], set(), set()
    eval_true = eval_false = eval_missing = 0
    evaluated = 0
    for h in steps:
        action = str(h.get("action"))
        if action in SKIP_ACTIONS:
            counts["skip"] += 1
        elif action in EVALUATED_ACTIONS:
            counts[action] += 1
        else:
            counts["other"] += 1
            other.add(action)
        if as_int(h.get("edit_budget")) is not None:
            budgets.append(h["edit_budget"])
        if h.get("lr_control_mode") is not None:
            lr_modes.add(str(h["lr_control_mode"]))
        pm = h.get("persistent_memory")
        if isinstance(pm, dict) and "enabled" in pm:
            pm_enabled.add(bool(pm["enabled"]))
        if action in EVALUATED_ACTIONS:
            evaluated += 1
            used = h.get("cam_gate_used")
            if used is True:
                eval_true += 1
            elif used is False:
                eval_false += 1
            else:
                eval_missing += 1
    total = len(steps)
    update = sum(counts.get(action, 0) for action in UPDATE_ACTIONS)
    out.update({
        "total": total,
        "evaluated": evaluated,
        "action_counts": {k: counts.get(k, 0) for k in ACTION_BUCKETS},
        "other_actions": sorted(other) if other else [],
        "update_steps": update,
        "update_ratio": (update / total) if total else None,
        "edit_budgets": budgets,
        "lr_control_modes": sorted(lr_modes),
        "gate_used_true": eval_true,
        "gate_used_false": eval_false,
        "gate_used_missing": eval_missing,
        "persistent_memory_enabled": sorted(pm_enabled) if pm_enabled else None,
    })
    return out


def gate_stats(unit_dir: Path):
    """读 cam_gate_events.json（仅 strict_cam 臂产生），算门控统计。

    返回 dict；exists=False 时其余量全 None。文件半截不可解析时
    count=None 且 unparseable=True（不崩）。
    """
    out = {
        "exists": False, "unparseable": False, "count": None,
        "promotion_true": None, "promotion_ratio": None,
        "pending_true": None, "pending_ratio": None,
        "n_pairs_values": None, "lcb_mean": None, "ucb_mean": None,
        "cross_zero": None, "cross_zero_ratio": None,
        "by_action": None,
    }
    path = unit_dir / "cam_gate_events.json"
    if not path.exists():
        return out
    out["exists"] = True
    events = read_json(path)
    if not isinstance(events, list):
        out["unparseable"] = True
        return out
    out["count"] = len(events)
    evs = [e for e in events if isinstance(e, dict)]
    n = len(evs)
    if n:
        promo = sum(1 for e in evs if e.get("promotion_authorized") is True)
        pend = sum(1 for e in evs if e.get("pending_additional_paired_evidence") is True)
        lcbs = [e.get("lower_confidence_bound") for e in evs]
        ucbs = [e.get("upper_confidence_bound") for e in evs]
        cross = sum(
            1 for e in evs
            if isinstance(e.get("lower_confidence_bound"), (int, float))
            and isinstance(e.get("upper_confidence_bound"), (int, float))
            and e["lower_confidence_bound"] < 0 < e["upper_confidence_bound"])
        # 按 action 分组：条数 + 候选/当前分数差（candidate_score - current_score）均值
        groups = {}
        for e in evs:
            key = str(e.get("action"))
            g = groups.setdefault(key, {"count": 0, "score_diffs": [],
                                        "improvements": []})
            g["count"] += 1
            cand, cur = e.get("candidate_score"), e.get("current_score")
            if isinstance(cand, (int, float)) and isinstance(cur, (int, float)):
                g["score_diffs"].append(cand - cur)
            if isinstance(e.get("mean_improvement"), (int, float)):
                g["improvements"].append(e["mean_improvement"])
        by_action = {}
        for key, g in groups.items():
            by_action[key] = {
                "count": g["count"],
                "mean_score_diff": mean_of(g["score_diffs"]),
                "mean_improvement": mean_of(g["improvements"]),
            }
        out.update({
            "promotion_true": promo, "promotion_ratio": promo / n,
            "pending_true": pend, "pending_ratio": pend / n,
            "n_pairs_values": sorted({e.get("n_pairs") for e in evs
                                      if e.get("n_pairs") is not None},
                                     key=lambda v: (isinstance(v, str), v)),
            "lcb_mean": mean_of(lcbs), "ucb_mean": mean_of(ucbs),
            "cross_zero": cross, "cross_zero_ratio": cross / n,
            "by_action": by_action,
        })
    return out


def memory_stats(unit_dir: Path):
    """记忆统计：cam_rejected_memory.json + 全部 memory_retrieval.json。

    retrieval 扫描容忍坏文件（半截跳过并计数）。
    """
    out = {
        "rejected_memory_exists": False, "rejected_items": None, "kind_distribution": None,
        "retrieval_files": 0, "retrieval_enabled_true": None,
        "retrieval_hit_gt0": None, "status_distribution": None,
        "retrieval_unparseable": 0,
    }
    mem_p = unit_dir / "cam_rejected_memory.json"
    if mem_p.exists():
        out["rejected_memory_exists"] = True
        data = read_json(mem_p)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            out["rejected_items"] = len(data["items"])
            out["kind_distribution"] = dict(Counter(item.get("kind", "rejected") for item in data["items"]))
    total = enabled = hits = unread = 0
    statuses = Counter()
    for path in sorted(unit_dir.rglob("memory_retrieval.json")):
        total += 1
        row = read_json(path)
        if not isinstance(row, dict):
            unread += 1  # 半截 / 坏文件：跳过不崩
            continue
        if row.get("enabled") is True:
            enabled += 1
        if as_int(row.get("hit_count")) and row["hit_count"] > 0:
            hits += 1
        if row.get("status") is not None:
            statuses[str(row["status"])] += 1
    if total:
        out.update({
            "retrieval_files": total,
            "retrieval_enabled_true": enabled,
            "retrieval_hit_gt0": hits,
            "status_distribution": dict(sorted(statuses.items())),
            "retrieval_unparseable": unread,
        })
    return out


def consistency_checks(unit_dir: Path, gate_expected, mem_possible,
                       hst, gate, action_other_has_reval):
    """一致性校验（重点）：返回 (ok, alarms[])，每条告警是 {type, message}。

    检查项（docs/CAM_EVIDENCE_MAP.md §3/§4 的边界情况已考虑）：
      1. cam_gate_used 与臂期望矛盾（只看被评步；skip 步无键属正常）
      2. 门控臂缺失 cam_gate_events.json（已有被评步还不正常；零被评步
         可能仍在首个 rollout，属冷启动，不算告警）
      3. 非门控臂（baseline / cam_no_bootstrap）出现 cam 门控文件
      4. cam_no_memory / baseline 出现 cam_rejected_memory.json（永不产生）
      5. 非门控臂出现 cam_re_evaluate 动作（标量路径不可能有）
    """
    alarms = []
    # 1. cam_gate_used 一致性（仅被评步）
    if hst["evaluated"]:
        if gate_expected is True:
            bad = (hst["gate_used_false"] or 0) + (hst["gate_used_missing"] or 0)
            if bad:
                alarms.append({
                    "type": "gate_used_mismatch",
                    "message": "门控臂被评步 cam_gate_used 应全为 true，"
                               "实际 true %d / false %d / 缺键 %d（共 %d 被评步）"
                               % (hst["gate_used_true"], hst["gate_used_false"],
                                  hst["gate_used_missing"], hst["evaluated"]),
                })
        elif gate_expected is False:
            if hst["gate_used_true"]:
                alarms.append({
                    "type": "gate_used_mismatch",
                    "message": "非门控臂被评步 cam_gate_used 应全为 false，"
                               "实际出现 true %d 步（共 %d 被评步）"
                               % (hst["gate_used_true"], hst["evaluated"]),
                })
    # 2. 门控臂缺失 cam_gate_events.json
    if gate_expected is True and not gate["exists"]:
        if hst["evaluated"]:
            alarms.append({
                "type": "gate_events_missing",
                "message": "CAM 门控臂缺失 cam_gate_events.json（已有 %d 个被评步，不应缺失）"
                           % hst["evaluated"],
            })
        # 零被评步：可能仍在首个门控决策前（冷启动），不算告警
    # 3. 非门控臂出现 cam 门控文件（baseline / cam_no_bootstrap）
    if gate_expected is False:
        present = [f for f in GATE_FILES if (unit_dir / f).exists()]
        present += [p.relative_to(unit_dir).as_posix()
                    for p in sorted(unit_dir.glob("steps/step_*/cam_gate.json"))]
        present += [p.relative_to(unit_dir).as_posix() for p in
                    sorted(unit_dir.glob("steps/step_*/pending_re_evaluation.json"))]
        if present:
            alarms.append({
                "type": "unexpected_cam_files",
                "message": "非门控臂不应产生 cam 门控文件，却存在：%s"
                           % ", ".join(present[:8]),
            })
    # 4. cam_no_memory / baseline 出现记忆文件（永不产生）
    if mem_possible is False:
        present = [f for f in MEM_FILES if (unit_dir / f).exists()]
        present += [p.relative_to(unit_dir).as_posix() for p in
                    sorted(unit_dir.glob("steps/step_*/memory_write.json"))]
        if present:
            alarms.append({
                "type": "unexpected_memory_files",
                "message": "该臂不应产生持久记忆文件，却存在：%s"
                           % ", ".join(present[:8]),
            })
    # 5. 非门控臂出现 cam_re_evaluate（标量 evaluate_gate 路径不可能产生）
    if gate_expected is False and action_other_has_reval:
        alarms.append({
            "type": "cam_re_evaluate_without_gate",
            "message": "非门控臂出现 cam_re_evaluate 动作（只有 bootstrap 门控才可能产生）",
        })
    return (len(alarms) == 0), alarms


def analyze_unit(unit_dir: Path, benchmark, target) -> dict:
    """分析一个训练单元：进度 / 动作分布 / 预算轨迹 / 门控 / 记忆 / 一致性。"""
    arm, seed = parse_arm_seed(unit_dir.name)
    cfg = read_json(unit_dir / "config.json")
    if not isinstance(cfg, dict):
        cfg = {}
    if seed is None:
        seed = as_int(cfg.get("seed"))

    expected_steps = expected_steps_of(cfg)
    history = read_json(unit_dir / "history.json")
    hst = history_stats(history)
    gate = gate_stats(unit_dir)
    mem = memory_stats(unit_dir)
    gate_expected, mem_possible = profile_for(arm, cfg)

    # cam_re_evaluate 计数（供一致性第 5 项）：history 缺失时不可判（None）
    n_reval = hst["action_counts"]["cam_re_evaluate"] if hst["action_counts"] else None
    ok, alarms = consistency_checks(
        unit_dir, gate_expected, mem_possible, hst, gate,
        bool(n_reval))

    return {
        "unit": unit_dir.relative_to(MATRIX).as_posix()
        if MATRIX in unit_dir.parents else unit_dir.name,
        "benchmark": benchmark,
        "target": target,
        "arm": arm,
        "seed": seed,
        "run_state": run_state_of(unit_dir),
        # 1. 进度
        "completed_steps": hst["total"],
        "expected_steps": expected_steps,
        # 2. 动作分布（顺序 accept/accept_new_best/reject/skip/cam_re_evaluate/other）
        "action_counts": hst["action_counts"],
        "other_actions": hst["other_actions"],
        "evaluated_steps": hst["evaluated"],
        "update_steps": hst["update_steps"],
        "update_ratio": hst["update_ratio"],
        # 3. 预算轨迹
        "edit_budgets": hst["edit_budgets"],
        "lr_control_modes": hst["lr_control_modes"],
        # 4. 门控统计（仅 CAM 臂有 cam_gate_events.json）
        "gate_expected": gate_expected,
        "gate": gate,
        # 5. 记忆统计
        "memory": mem,
        "persistent_memory_enabled": hst["persistent_memory_enabled"],
        # 6. 一致性
        "memory_possible": mem_possible,
        "gate_used_true": hst["gate_used_true"],
        "gate_used_false": hst["gate_used_false"],
        "gate_used_missing": hst["gate_used_missing"],
        "cam_re_evaluate_steps": n_reval,
        "consistency_ok": ok,
        "alarms": alarms,
    }


def action_dist_cell(rec) -> str:
    """动作分布紧凑串：accept/new_best/reject/skip/re_evaluate/其他。"""
    c = rec["action_counts"]
    if c is None:
        return "—"
    return "/".join(str(c[k]) for k in ACTION_BUCKETS)


def budget_cell(rec) -> str:
    """预算轨迹串：edit_budget 序列 + lr_control_mode 取值集合。"""
    if rec["edit_budgets"] is None and rec["lr_control_modes"] is None:
        return "—"
    seq = fmt_seq(rec["edit_budgets"])
    modes = "·".join(rec["lr_control_modes"]) if rec["lr_control_modes"] else "—"
    return "%s｜%s" % (seq, modes)


def progress_cell(rec) -> str:
    a = rec["completed_steps"]
    b = rec["expected_steps"]
    if a is None and b is None:
        return "—"
    return "%s/%s" % ("—" if a is None else a, "—" if b is None else b)


def update_cell(rec) -> str:
    if rec["update_steps"] is None:
        return "—"
    return "%d/%d（%s）" % (rec["update_steps"], rec["completed_steps"],
                           fmt_ratio(rec["update_steps"], rec["completed_steps"]))


def gate_cell(rec) -> str:
    """门控事件列：条数（门控臂）或 —（非门控臂）。"""
    if rec["gate_expected"] is False:
        return "—"
    g = rec["gate"]
    if not g["exists"]:
        return "0 条" if rec["gate_expected"] else "—"
    if g["count"] is None:
        return "不可解析"
    return "%d 条" % g["count"]


def memory_cell(rec) -> str:
    """记忆列：拒忆条数 + 检索文件统计。"""
    m = rec["memory"]
    parts = []
    if m["rejected_memory_exists"]:
        parts.append("拒忆 %s 条" % ("?" if m["rejected_items"] is None
                                    else m["rejected_items"]))
    else:
        parts.append("无拒忆")
    if m["retrieval_files"]:
        parts.append("检索 %d（开 %d·hit>0 %d）"
                     % (m["retrieval_files"], m["retrieval_enabled_true"],
                        m["retrieval_hit_gt0"]))
    else:
        parts.append("无检索文件")
    return " · ".join(parts)


def md_table(records) -> str:
    """总表：单元 | arm | 进度 | 更新步数 | 动作分布 | 预算轨迹 | 门控事件 | 记忆 | 一致性。"""
    headers = ["单元", "arm", "进度", "更新步数", "动作分布",
               "预算轨迹", "门控事件", "记忆", "一致性"]
    rows = []
    for rec in records:
        rows.append([
            rec["unit"], rec["arm"], progress_cell(rec), update_cell(rec),
            action_dist_cell(rec), budget_cell(rec), gate_cell(rec),
            memory_cell(rec),
            "OK" if rec["consistency_ok"] else "告警 %d 项" % len(rec["alarms"]),
        ])
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i])
              for i in range(len(headers))]
    aligns = ["-", "-", ":", ":", ":", ":", ":", "-", "-"]

    def render(cells):
        return "| " + " | ".join(
            cells[i].ljust(widths[i]) if aligns[i] == "-" else cells[i].rjust(widths[i])
            for i in range(len(cells))
        ) + " |"

    lines = [render(headers)]
    lines.append("|" + "|".join(
        (":" if aligns[i] == "-" else "-:") + "-" * (widths[i] + 1)
        for i in range(len(headers))
    ) + "|")
    for r in rows:
        lines.append(render(r))
    lines.append("")
    lines.append("动作分布 = accept/accept_new_best/soft_accept/reject/skip/cam_re_evaluate/其他；"
                 "预算轨迹 = 逐步 edit_budget｜lr_control_mode 取值；"
                 "— = 该量当前取不到（单元仍在跑或产物缺失），不编数。")
    return "\n".join(lines)


def md_unit_section(rec) -> str:
    """每个单元一小节：3–6 行关键数字。"""
    lines = ["### %s（%s，%s）" % (rec["unit"], rec["arm"], rec["run_state"])]
    # 行 1：进度 + 动作 + 更新占比
    c = rec["action_counts"]
    if c is None:
        dist = "history 不可读"
    else:
        seg = ["%s×%d" % (k, c[k]) for k in ACTION_BUCKETS if c[k]]
        dist = "、".join(seg) if seg else "无被记录动作"
    if rec["other_actions"]:
        dist += "（其他取值：%s）" % ", ".join(rec["other_actions"])
    lines.append("- 进度：%s 步（期望 %s）；动作分布 %s；更新步数 %s"
                 % (progress_cell(rec), rec["expected_steps"], dist, update_cell(rec)))
    # 行 2：预算轨迹
    lines.append("- 预算：edit_budget %s；lr_control_mode = %s"
                 % (fmt_seq(rec["edit_budgets"]),
                    "·".join(rec["lr_control_modes"]) if rec["lr_control_modes"] else "—"))
    # 行 3–4：门控统计（仅 CAM 臂）
    g = rec["gate"]
    if rec["gate_expected"]:
        if not g["exists"]:
            note = "（尚无被评步 = 冷启动正常；已有被评步见一致性告警）"
            lines.append("- 门控：cam_gate_events.json 不存在%s" % note)
        elif g["count"] is None:
            lines.append("- 门控：cam_gate_events.json 存在但不可解析（可能正在写入）")
        else:
            lines.append(
                "- 门控：事件 %d 条；promotion_authorized %d/%d（%s）；"
                "pending_additional_paired_evidence %d/%d（%s）；n_pairs=%s"
                % (g["count"], g["promotion_true"], g["count"],
                   fmt_ratio(g["promotion_true"], g["count"]),
                   g["pending_true"], g["count"], fmt_ratio(g["pending_true"], g["count"]),
                   "、".join(str(v) for v in g["n_pairs_values"])
                   if g["n_pairs_values"] else "—"))
            if g["by_action"]:
                seg = ["%s=%s（n=%d）" % (a, fmt_num(v["mean_score_diff"], "%+.3f"),
                                         v["count"])
                       for a, v in sorted(g["by_action"].items())]
                lines.append(
                    "- 区间：LCB 均值 %s，UCB 均值 %s，跨零 %d/%d（%s）；"
                    "分差（候选−当前）按 action：%s"
                    % (fmt_num(g["lcb_mean"]), fmt_num(g["ucb_mean"]),
                       g["cross_zero"], g["count"], fmt_ratio(g["cross_zero"], g["count"]),
                       "、".join(seg)))
    # 行 5：记忆
    m = rec["memory"]
    if m["rejected_memory_exists"]:
        rej = "存在，items %s 条" % ("?" if m["rejected_items"] is None
                                     else m["rejected_items"])
    else:
        n_reject = (rec["action_counts"] or {}).get("reject", 0) \
            if rec["action_counts"] else None
        if rec["memory_possible"] is False:
            rej = "不存在（该臂永不产生，正常）"
        elif n_reject:
            rej = "不存在（有 %d 个 reject 步却不落盘，需检查）" % n_reject
        elif n_reject == 0:
            rej = "不存在（尚无 reject 步，冷启动正常）"
        else:
            rej = "不存在（history 不可读）"
    if m["retrieval_files"]:
        st = "、".join("%s×%d" % (k, v) for k, v in m["status_distribution"].items())
        lines.append(
            "- 记忆：cam_rejected_memory %s；memory_retrieval %d 个"
            "（enabled=true %d，hit_count>0 %d%s；status：%s）"
            % (rej, m["retrieval_files"], m["retrieval_enabled_true"],
               m["retrieval_hit_gt0"],
               "，不可解析 %d 个" % m["retrieval_unparseable"]
               if m["retrieval_unparseable"] else "", st))
    else:
        lines.append("- 记忆：cam_rejected_memory %s；无 memory_retrieval 文件" % rej)
    # 行 6：一致性小结
    if rec["consistency_ok"]:
        gnote = ""
        if rec["gate_used_true"] is not None or rec["gate_used_false"] is not None:
            gnote = "；被评步 cam_gate_used：true %s / false %s / 缺键 %s" % (
                rec["gate_used_true"], rec["gate_used_false"], rec["gate_used_missing"])
        lines.append("- 一致性：无矛盾%s" % gnote)
    else:
        lines.append("- 一致性：**%d 项告警**（见文末）" % len(rec["alarms"]))
    return "\n".join(lines)


def build_md(records) -> str:
    """组装 reports/cam_analysis.md：总表 + 每单元小节 + 一致性告警。"""
    lines = ["# CAM 机制决策日志分析", ""]
    lines.append("扫描范围：`outputs/matrix/**` 下全部非 static 单元（%d 个）；"
                 "数据源只读，未跑任何训练/评测。" % len(records))
    lines.append("")
    lines.append("## 总表")
    lines.append("")
    lines.append(md_table(records))
    lines.append("")
    lines.append("## 各单元关键数字")
    lines.append("")
    for rec in records:
        lines.append(md_unit_section(rec))
        lines.append("")
    lines.append("## 一致性告警")
    lines.append("")
    any_alarm = False
    for rec in records:
        for al in rec["alarms"]:
            any_alarm = True
            lines.append("- **%s**（%s）：%s" % (rec["unit"], al["type"], al["message"]))
    if not any_alarm:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    global MATRIX, MD_PATH, JSON_PATH
    ap = argparse.ArgumentParser(
        description="分析 outputs/matrix/** 各单元的 CAM 机制决策日志"
                    "（只读，写 reports/cam_analysis.md + .json）")
    ap.add_argument("--cell", metavar="<path>", default=None,
                    help="只分析该单元目录（相对仓库根或绝对路径）")
    ap.add_argument("--json-only", action="store_true",
                    help="只写 reports/cam_analysis.json，不写 markdown")
    ap.add_argument("--matrix-root", type=Path, default=MATRIX)
    ap.add_argument("--report-dir", type=Path, default=MD_PATH.parent)
    args = ap.parse_args()
    MATRIX = args.matrix_root.resolve()
    MD_PATH = args.report_dir.resolve() / "cam_analysis.md"
    JSON_PATH = args.report_dir.resolve() / "cam_analysis.json"

    records = []
    if args.cell:
        cell = Path(args.cell)
        if not cell.is_absolute():
            cell = ROOT / cell
        cell = cell.resolve()
        if not cell.is_dir():
            print("cell not found: %s" % cell, file=sys.stderr)
            return 1
        # 从路径里尽量还原 benchmark / target（--cell 时不强求）
        rel = cell.relative_to(MATRIX) if MATRIX in cell.parents else None
        if rel is not None and len(rel.parts) == 3:
            bench, target_dir, _name = rel.parts
        else:
            bench, target_dir = "?", "?"
        records.append(analyze_unit(cell, bench, target_dir))
    else:
        if not MATRIX.is_dir():
            print("matrix root not found: %s" % MATRIX, file=sys.stderr)
            return 1
        for bench in sorted(p for p in MATRIX.iterdir() if p.is_dir()):
            for target_dir in sorted(p for p in bench.iterdir() if p.is_dir()):
                for unit in sorted(p for p in target_dir.iterdir() if p.is_dir()):
                    arm, _ = parse_arm_seed(unit.name)
                    if arm == "static":
                        continue  # 静态单元只评测不训练，不适用本分析
                    records.append(analyze_unit(unit, bench.name, target_dir.name))

    alarm_count = sum(len(r["alarms"]) for r in records)
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "unit_count": len(records),
        "alarm_count": alarm_count,
        "units": records,
    }
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JSON_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    if not args.json_only:
        md = build_md(records)
        with MD_PATH.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(md)
        print(md)
    print("wrote %s%s (%d units, %d alarms)"
          % (JSON_PATH, "" if args.json_only else " + " + str(MD_PATH),
             len(records), alarm_count), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
