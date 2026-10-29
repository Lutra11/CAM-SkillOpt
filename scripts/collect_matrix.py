"""扫描 outputs/matrix/** 的每个实验单元，汇总为 reports/matrix_summary.json。

只读扫描（唯一写入目标是 reports/matrix_summary.json 与可选的 --md-out 文件）。
容忍"正在跑"（history.json / summary.json 尚未写出）与"半截"
（results.jsonl 末行被截断）的单元：缺失记 null，坏行跳过并计数，不崩。

产物结构差异（实测）：
  - 训练单元：config.json（扁平键）+ history.json（每条一个 step，步数与
    A/R/S 在这里）+ summary.json（test_hard / best_selection_hard /
    token_summary 等；正常完成的 summary 没有 status 字段，infra 中止时才有）
  - 静态单元（--action static）：没有 summary.json / history.json，只有
    eval_summary.json（完成时只有 hard/soft，infra 中止时才有 status）；
    test_hard 兜底取其 hard。注意：被 run_eval_robust 重试的静态单元会留着
    旧的 infra eval_summary.json，但 results.jsonl / stage_stats.json 仍在
    更新 —— 这种"汇总之后仍有新写入"按 running 处理，不按 done
  - spreadsheetbench 的 results.jsonl 行有 llm_ok 字段；
    livemathematicianbench / officeqa 的行没有 llm_ok（记 null）
  - Windows 目录名不允许冒号：target gemma4:26b 的单元目录写作 gemma4-26b

Usage:
    python -X utf8 scripts/collect_matrix.py [--json-only] [--md-out <path>]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "outputs" / "matrix"
REPORT_PATH = ROOT / "reports" / "matrix_summary.json"

# 密钥泄漏扫描只查文本产物；xlsx 等二进制跳过
TEXT_EXTS = {".json", ".jsonl", ".md", ".txt", ".log", ".py", ".yaml", ".yml", ".csv"}
# OpenAI 风格密钥形态：sk- 开头 + 32 位以上字母数字（只报文件路径，不报内容）
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{32,}")
# run_state 判定阈值（秒）：最近 RUNNING_GRACE_S 内有产物更新算"在写"；
# 最终 summary 写出后 SUMMARY_FRESH_S 内的后续写入（收尾日志等）不算重试
RUNNING_GRACE_S = 60 * 60
SUMMARY_FRESH_S = 120

# markdown 主表 Method 列的显示名（目录名 arm -> 方案第 2 节的方法名）
ARM_LABELS = {
    "static": "Static",
    "baseline": "SkillOpt",
    "cam_full": "CAM-Full",
    "cam_no_bootstrap": "CAM-No-Bootstrap",
    "cam_no_adaptive_budget": "CAM-No-Adaptive-Budget",
    "cam_no_memory": "CAM-No-Memory",
}

# 单元目录名 -> 真实 target（Windows 目录名把冒号换成了连字符）
TARGET_DIR_MAP = {"gemma4-26b": "gemma4:26b"}

# markdown 表的 状态 / 验收 列显示名
RUN_STATE_LABELS = {
    "done": "完成",
    "running": "运行中",
    "stale": "卡住",
    "not_started": "未开始",
}


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
    """目录名形如 baseline_seed42 / static_seed42，拆出 (arm, seed)。"""
    m = re.match(r"^(.+?)_seed(\d+)$", unit_name)
    if m:
        return m.group(1), int(m.group(2))
    return unit_name, None


def scan_results_jsonl(unit_dir: Path):
    """遍历单元内所有 results.jsonl（顶层 + 各阶段目录）。

    返回 (rows_total, rows_bad, rows_llm_ok_true)：
      rows_total       非空行总数（含坏行）
      rows_bad         解析失败（截断/半截）的行数，跳过不崩
      rows_llm_ok_true llm_ok 为 true 的行数；若所有行都没有 llm_ok 字段
                       （livemathematicianbench / officeqa），返回 None
    """
    rows_total = rows_bad = llm_ok_true = 0
    has_llm_key = False
    for path in sorted(unit_dir.rglob("results.jsonl")):
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if not line.strip():
                    continue
                rows_total += 1
                try:
                    row = json.loads(line)
                except Exception:
                    rows_bad += 1  # 半截行：正在写的最后一行，跳过
                    continue
                if not isinstance(row, dict):
                    rows_bad += 1
                    continue
                if "llm_ok" in row:
                    has_llm_key = True
                    if row.get("llm_ok") is True:
                        llm_ok_true += 1
    return rows_total, rows_bad, (llm_ok_true if has_llm_key else None)


def step_stats_from_history(unit_dir: Path):
    """从 history.json（list，每条一个 step）统计步数与 A/R/S。

    accepts = action in {accept, accept_new_best}；rejects = action == reject；
    skips = action in {skip, skip_no_patches}。实际出现过的 action 取值单独
    列出（可能有 cam_re_evaluate 之类）。cam_gate_used=true 的步数用于佐证
    CAM 是否真的生效（cam_full 应全 true，baseline 应全 false）。
    history.json 缺失/不可解析（训练尚未结束）时全部返回 None。
    """
    history = read_json(unit_dir / "history.json")
    if not isinstance(history, list):
        return None, None, None, None, None, None
    accepts = rejects = skips = cam_gate_true = 0
    actions = set()
    for h in history:
        if not isinstance(h, dict):
            continue
        action = str(h.get("action"))
        actions.add(action)
        if action in ("accept", "accept_new_best"):
            accepts += 1
        elif action == "reject":
            rejects += 1
        elif action in ("skip", "skip_no_patches"):
            skips += 1
        if h.get("cam_gate_used") is True:
            cam_gate_true += 1
    return len(history), accepts, rejects, skips, sorted(actions), cam_gate_true


def sum_token_total(summary):
    """取 token_summary["_total"]["total_tokens"]；_total 缺失时按角色求和。

    实测结构：{"analyst": {...}, "merge": {...}, "ranking": {...},
    "rollout": {...}, "_total": {..., "total_tokens": N}}，"_total" 即各角色
    之和。取不到（无 summary / 无 token_summary）返回 None。
    """
    if not isinstance(summary, dict):
        return None
    ts = summary.get("token_summary")
    if not isinstance(ts, dict):
        return None
    grand = ts.get("_total")
    if isinstance(grand, dict) and isinstance(grand.get("total_tokens"), (int, float)):
        return grand["total_tokens"]
    total, seen = 0, False
    for role, usage in ts.items():
        if role.startswith("_") or not isinstance(usage, dict):
            continue
        value = usage.get("total_tokens")
        if isinstance(value, (int, float)):
            total += value
            seen = True
    return total if seen else None


def scan_unit_files(unit_dir: Path):
    """单次遍历：收集文本产物路径 + 全部文件的最新 mtime（用于判定 run_state）。"""
    text_paths, newest = [], None
    for path in unit_dir.rglob("*"):
        try:
            if not path.is_file():
                continue
            st = path.stat()
        except OSError:
            continue
        if newest is None or st.st_mtime > newest:
            newest = st.st_mtime
        if path.suffix.lower() in TEXT_EXTS:
            text_paths.append(path)
    return text_paths, newest


def scan_secret_leak(text_paths, unit_dir: Path):
    """在文本产物里找 sk- 开头 32+ 位的疑似密钥，只返回命中的相对路径列表。"""
    hits = []
    for path in text_paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if SECRET_RE.search(text):
            hits.append(path.relative_to(unit_dir).as_posix())
    return sorted(hits)


def cam_evidence_of(unit_dir: Path):
    """CAM 证据：cam_gate_events.json 是否存在且非空、cam_rejected_memory.json 是否存在。"""
    gate = unit_dir / "cam_gate_events.json"
    mem = unit_dir / "cam_rejected_memory.json"
    gate_nonempty, gate_count = False, None
    if gate.exists() and gate.stat().st_size > 0:
        data = read_json(gate)
        if isinstance(data, list):
            gate_count = len(data)
            gate_nonempty = len(data) > 0
    return {
        "gate_events_nonempty": gate_nonempty,
        "gate_events_count": gate_count,
        "rejected_memory_exists": mem.exists(),
    }


def run_state_of(unit_dir: Path, is_static: bool, newest_mtime):
    """单元状态：done / running / stale / not_started。

    done        有最终 summary（训练 summary.json / 静态 eval_summary.json），
                且它是（几乎）最后写入的文件
    running     最终 summary 缺失，或汇总之后仍有新写入（run_eval_robust 重试
                会留着旧的 infra eval_summary.json 继续跑），且最近
                RUNNING_GRACE_S 秒内有产物更新
    stale       无最终汇总且最近 RUNNING_GRACE_S 秒无任何写入
    not_started 单元目录为空
    """
    try:
        if not any(unit_dir.iterdir()):
            return "not_started"
    except OSError:
        return "not_started"
    final_p = unit_dir / ("eval_summary.json" if is_static else "summary.json")
    has_final = final_p.exists()
    if newest_mtime is None:
        return "done" if has_final else "stale"
    recent = (time.time() - newest_mtime) <= RUNNING_GRACE_S
    if recent:
        if not has_final:
            return "running"
        try:
            fs = final_p.stat().st_mtime
        except OSError:
            return "running"
        # 汇总写出后超过 SUMMARY_FRESH_S 仍有新写入 => 是重试在跑，不是收尾
        if (newest_mtime - fs) > SUMMARY_FRESH_S:
            return "running"
        return "done"
    return "done" if has_final else "stale"


def progress_of(unit_dir: Path, is_static: bool, expected_steps, rows_total):
    """人能读的进度：训练 'step 1/8 · rollout 20/40'；静态 '134/280 题'。

    训练单元取 steps/step_*/*/stage_stats.json 中最新（mtime）的一个；
    还没有 step 目录时读 selection_eval_baseline/stage_stats.json（基线选评）。
    """
    if is_static:
        ss = read_json(unit_dir / "stage_stats.json")
        if isinstance(ss, dict) and ss.get("n_recorded") is not None:
            n_exp = ss.get("n_expected")
            return "%s/%s 题" % (ss["n_recorded"], n_exp if n_exp is not None else "?")
        return "%d 题" % rows_total if rows_total else None
    candidates = list(unit_dir.glob("steps/step_*/*/stage_stats.json"))
    if candidates:
        def mtime(p):
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0
        latest = max(candidates, key=mtime)
        step_dir = latest.parent.parent.name  # step_0001
        m = re.match(r"step_(\d+)", step_dir)
        step_no = str(int(m.group(1))) if m else step_dir  # 去前导零：0001 -> 1
        total = expected_steps if expected_steps is not None else "?"
        ss = read_json(latest) or {}
        stage = ss.get("stage") or latest.parent.name
        n, e = ss.get("n_recorded"), ss.get("n_expected")
        counts = " %s/%s" % (n, e) if n is not None else ""
        return "step %s/%s · %s%s" % (step_no, total, stage, counts)
    ss = read_json(unit_dir / "selection_eval_baseline" / "stage_stats.json")
    if isinstance(ss, dict) and ss.get("n_recorded") is not None:
        n, e = ss.get("n_recorded"), ss.get("n_expected")
        return "基线选评 %s/%s" % (n, e if e is not None else "?")
    return None


def collect_unit(unit_dir: Path, benchmark: str, target: str, target_dir: str) -> dict:
    arm, seed = parse_arm_seed(unit_dir.name)
    is_static = arm == "static"
    cfg = read_json(unit_dir / "config.json")
    if not isinstance(cfg, dict):
        cfg = {}  # 静态单元没有 config.json
    if seed is None:
        seed = as_int(cfg.get("seed"))
    summary = read_json(unit_dir / "summary.json")
    if not isinstance(summary, dict):
        summary = None
    eval_summary = read_json(unit_dir / "eval_summary.json")
    if not isinstance(eval_summary, dict):
        eval_summary = None

    def sget(key):
        return summary.get(key) if summary is not None else None

    # 步数与 A/R/S 以 history.json 为准；缺失时（旧版 summary 兼容）兜底 summary.json
    h_steps, h_accepts, h_rejects, h_skips, h_actions, h_cam_true = (
        step_stats_from_history(unit_dir))
    history = read_json(unit_dir / "history.json")
    total_soft_accepts = (sum(h.get("action") == "soft_accept" for h in history if isinstance(h, dict))
                          if isinstance(history, list) else sget("total_soft_accepts"))
    if is_static:
        total_steps = 0  # 静态单元只评测不训练
    elif h_steps is not None:
        total_steps = h_steps
    else:
        total_steps = sget("total_steps")
    total_accepts = h_accepts if h_accepts is not None else (
        None if is_static else sget("total_accepts"))
    total_rejects = h_rejects if h_rejects is not None else (
        None if is_static else sget("total_rejects"))
    total_skips = h_skips if h_skips is not None else (
        None if is_static else sget("total_skips"))

    # status / test_hard：训练单元取 summary.json；静态单元兜底 eval_summary.json
    status = None
    if summary is not None and "status" in summary:
        status = summary.get("status")
    elif is_static and eval_summary is not None and "status" in eval_summary:
        status = eval_summary.get("status")
    test_hard = None
    if summary is not None and summary.get("test_hard") is not None:
        test_hard = summary["test_hard"]
    elif is_static and eval_summary is not None:
        test_hard = eval_summary.get("hard")  # 静态单元：hard 即 test hard

    # expected_steps = num_epochs * ceil(train_size / batch_size)，一律按该单元
    # 自己的 config.json 反推（历史目录 batch_size 可能是 20）；静态单元记 0
    if is_static:
        expected_steps = 0
    else:
        ne, tsz, bs = (as_int(cfg.get(k)) for k in ("num_epochs", "train_size", "batch_size"))
        if ne is not None and tsz is not None and bs is not None and bs > 0:
            expected_steps = ne * math.ceil(tsz / bs)
        else:
            expected_steps = None

    rows_total, rows_bad, rows_llm_ok_true = scan_results_jsonl(unit_dir)
    token_total = sum_token_total(summary)
    cost_summary = read_json(unit_dir / "cost_summary.json")
    if isinstance(cost_summary, dict):
        token_total = sum_token_total(cost_summary)
    cam_ev = cam_evidence_of(unit_dir)

    text_paths, newest_mtime = scan_unit_files(unit_dir)
    leak_hits = scan_secret_leak(text_paths, unit_dir)
    run_state = run_state_of(unit_dir, is_static, newest_mtime)
    progress = progress_of(unit_dir, is_static, expected_steps, rows_total)

    # ---- 阶段 B 验收判据：三项式 true / false / null（尚不可判）----
    def judge(ok, reason):
        return {"ok": ok, "reason": reason}

    # cam_gate_used 佐证片段（history 可得时拼进 CAM 证据的理由）
    gate_used_note = (
        "；history 中 cam_gate_used=true 的步数 %d/%d" % (h_cam_true, h_steps)
        if h_steps is not None else "")
    # 依赖最终产物的检查项在 done 之外的判词
    pending_word = {
        "running": "运行中（尚不可判）",
        "stale": "卡住：超过 60 分钟无产物更新且无汇总文件",
        "not_started": "单元未开始",
    }[run_state] if run_state != "done" else ""

    acceptance = {}
    if not is_static and run_state == "done":
        try:
            from scripts.experiment_integrity import validate_cell
        except ModuleNotFoundError:
            from experiment_integrity import validate_cell
        verified, reason = validate_cell(unit_dir)
        acceptance["complete_evidence"] = judge(verified, reason)
    # 训练完整度
    if is_static:
        acceptance["train_completeness"] = judge(
            None, "静态单元只评测不训练，不适用")
    elif run_state != "done":
        acceptance["train_completeness"] = judge(
            None, pending_word + "：history.json/summary.json 尚未落盘")
    elif total_steps is None:
        acceptance["train_completeness"] = judge(
            None, "history.json/summary.json 均未产出，无法判定")
    elif expected_steps is None:
        acceptance["train_completeness"] = judge(
            None, "config 缺 num_epochs/train_size/batch_size，无法推导 expected_steps")
    else:
        ok = total_steps == expected_steps
        acceptance["train_completeness"] = judge(
            ok, "total_steps=%s，expected_steps=%s" % (total_steps, expected_steps))
    # 有真实编辑
    if is_static:
        acceptance["real_edits"] = judge(None, "静态单元无技能编辑，不适用")
    elif run_state != "done":
        acceptance["real_edits"] = judge(
            None, pending_word + "：history.json 尚未落盘")
    elif total_steps is None:
        acceptance["real_edits"] = judge(
            None, "history.json 未产出，无法判定")
    else:
        n_edit = (total_accepts or 0) + (total_soft_accepts or 0) + (total_rejects or 0)
        acceptance["real_edits"] = judge(
            n_edit > 0, "accepts+soft_accepts+rejects=%d（actions=%s）" % (n_edit, h_actions))
    # 有真实调用（随时可扫的产物，不 gating 于 run_state）
    if (token_total is not None and token_total > 0) or (
        rows_llm_ok_true is not None and rows_llm_ok_true > 0
    ):
        acceptance["real_calls"] = judge(
            True, "token_total=%s，llm_ok=true 行数=%s" % (token_total, rows_llm_ok_true))
    elif token_total == 0 and rows_llm_ok_true == 0:
        acceptance["real_calls"] = judge(
            False, "token_total=0 且 llm_ok=true 行数=0，无任何真实调用")
    else:
        acceptance["real_calls"] = judge(
            None, "token 汇总与 llm_ok 均不可得（该 benchmark 行无 llm_ok 字段），无法判定")
    # 有效性
    if run_state == "running":
        acceptance["validity"] = judge(None, pending_word + "：最终 summary 尚未写出")
    elif run_state == "stale":
        acceptance["validity"] = judge(None, pending_word)
    elif run_state == "not_started":
        acceptance["validity"] = judge(None, pending_word)
    elif status is None:
        acceptance["validity"] = judge(
            True, "summary 无 status 字段（正常完成，未标记 invalid/infra_error）")
    else:
        ok = not str(status).startswith("invalid") and status != "infra_error"
        acceptance["validity"] = judge(ok, "status=%s" % status)
    # CAM 证据
    cam_enabled = cfg.get("cam_enabled")
    if cam_enabled is None:
        acceptance["cam_evidence"] = judge(
            None, "无 config.json（静态单元），cam_enabled 未知，不适用")
    elif not (cam_enabled is True and cfg.get("cam_bootstrap_gate", True)):
        acceptance["cam_evidence"] = judge(
            None, "cam_enabled=%s（未启用），不要求 cam_gate_events%s"
            % (cam_enabled, gate_used_note))
    elif run_state != "done":
        acceptance["cam_evidence"] = judge(
            None, pending_word + "：cam_gate_events.json 在首个选评完成后才写")
    else:
        ok = bool(cam_ev["gate_events_nonempty"])
        if ok:
            acceptance["cam_evidence"] = judge(
                True, "cam_gate_events.json 非空（%s 条）%s"
                % (cam_ev["gate_events_count"], gate_used_note))
        else:
            acceptance["cam_evidence"] = judge(
                False, "cam_enabled=true 但 cam_gate_events.json 不存在或为空%s"
                % gate_used_note)
    if cam_enabled is not None and not (cam_enabled and cfg.get("cam_bootstrap_gate", True)):
        forbidden = [name for name in ("cam_gate_events.json", "cam_gate_state.json", "final_cam_gate.json")
                     if (unit_dir / name).exists()]
        acceptance["no_unexpected_gate"] = judge(not forbidden, "unexpected gate files: %s" % forbidden)
    # 无密钥泄漏（只报命中路径，不打印内容）
    if leak_hits:
        acceptance["no_secret_leak"] = judge(
            False, "疑似密钥命中 %d 个文件: %s" % (len(leak_hits), ", ".join(leak_hits)))
    elif run_state == "not_started":
        acceptance["no_secret_leak"] = judge(None, "单元未开始，无文本产物")
    else:
        acceptance["no_secret_leak"] = judge(
            True, "全部文本产物未发现 sk- 开头的 32+ 位字符串")

    # acceptance_all_ok：done 才允许 true；stale 一律 false；running/not_started 为 null
    oks = [item["ok"] for item in acceptance.values()]
    if run_state == "stale":
        acceptance_all_ok = False
    elif run_state in ("running", "not_started"):
        acceptance_all_ok = None
    elif any(v is False for v in oks):
        acceptance_all_ok = False
    elif all(v is None for v in oks):
        acceptance_all_ok = None
    else:
        acceptance_all_ok = True

    return {
        "benchmark": benchmark,
        "target": target,
        "target_dir": target_dir,  # 单元目录名（gemma4:26b 的目录写作 gemma4-26b）
        "arm": arm,
        "seed": seed,
        "out_root": unit_dir.relative_to(ROOT).as_posix(),
        "run_state": run_state,  # done / running / stale / not_started
        "progress": progress,    # 人能读的进度（如 "step 1/8 · rollout 20/40"）
        # 以下 5 项来自 config.json（扁平键）；静态单元无 config.json 记 null
        "batch_size": cfg.get("batch_size"),
        "num_epochs": cfg.get("num_epochs"),
        "cam_enabled": cfg.get("cam_enabled"),
        "sel_env_num": cfg.get("sel_env_num"),
        "train_size": cfg.get("train_size"),
        # 以下来自 summary.json（缺失记 null；静态单元 status/test_hard 兜底 eval_summary.json）
        "status": status,
        # 步数与 A/R/S 来自 history.json（缺失记 null；静态单元 total_steps 记 0）
        "total_steps": total_steps,
        "total_accepts": total_accepts,
        "total_soft_accepts": total_soft_accepts,
        "total_rejects": total_rejects,
        "total_skips": total_skips,
        "actions": h_actions,  # history.json 中实际出现过的 action 取值
        "cam_gate_used_steps": h_cam_true,  # cam_gate_used=true 的步数（CAM 生效佐证）
        "test_hard": test_hard,
        "final_test_hard": sget("final_test_hard"),
        "best_selection_hard": sget("best_selection_hard"),
        "expected_steps": expected_steps,
        "rows_total": rows_total,
        "rows_llm_ok_true": rows_llm_ok_true,
        "rows_parse_failed": rows_bad,  # 额外：截断/坏行计数（容忍性证据）
        "token_total": token_total,
        "cam_evidence": cam_ev,
        "acceptance": acceptance,
        "acceptance_all_ok": acceptance_all_ok,
        "secret_leak_files": leak_hits,  # 额外：密钥命中文件（仅路径）
    }


def verdict_label(rec) -> str:
    """验收列：OK / FAIL / 待定 / —（未开始）。"""
    if rec["acceptance_all_ok"] is True:
        return "OK"
    if rec["acceptance_all_ok"] is False:
        return "FAIL"
    return "—" if rec["run_state"] == "not_started" else "待定"


def md_table(records) -> str:
    """主表：Task | Target | Method | Seed | Test hard | A/R/S | Steps | Tokens | 进度 | 状态 | 验收"""
    headers = ["Task", "Target", "Method", "Seed", "Test hard",
               "A/Soft/R/Skip", "Steps", "Tokens", "进度", "状态", "验收"]
    dash = "—"
    rows = []
    for rec in records:
        arm = rec["arm"]
        th = dash if rec["test_hard"] is None else "%.4f" % rec["test_hard"]
        if all(rec[k] is None for k in ("total_accepts", "total_rejects", "total_skips")):
            ars = dash
        else:
            fmt = lambda v: dash if v is None else str(v)
            ars = "/".join(fmt(rec[k]) for k in
                           ("total_accepts", "total_soft_accepts", "total_rejects", "total_skips"))
        steps = dash if rec["total_steps"] is None else str(rec["total_steps"])
        tokens = dash if rec["token_total"] is None else "{:,}".format(rec["token_total"])
        rows.append([
            rec["benchmark"], rec["target"], ARM_LABELS.get(arm, arm),
            dash if rec["seed"] is None else str(rec["seed"]),
            th, ars, steps, tokens,
            dash if rec["progress"] is None else rec["progress"],
            RUN_STATE_LABELS.get(rec["run_state"], rec["run_state"]),
            verdict_label(rec),
        ])
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i])
              for i in range(len(headers))]
    aligns = ["-", "-", "-", ":", ":", ":", ":", ":", "-", "-", "-"]  # 左文本右数值

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
    lines.append("状态：完成=有最终 summary；运行中=最近 60 分钟内有产物更新（含重试）；"
                 "卡住=无汇总且超 60 分钟无更新；— = 数值缺失，详见 reports/matrix_summary.json")
    return "\n".join(lines)


def main() -> int:
    global MATRIX, REPORT_PATH
    ap = argparse.ArgumentParser(
        description="收集 outputs/matrix/** 每个单元的汇总（只读，写 reports/matrix_summary.json）")
    ap.add_argument("--json-only", action="store_true",
                    help="只写 reports/matrix_summary.json，不在 stdout 打 markdown 表")
    ap.add_argument("--matrix-root", type=Path, default=MATRIX)
    ap.add_argument("--report", type=Path, default=REPORT_PATH)
    ap.add_argument("--md-out", metavar="<path>", default=None,
                    help="把 markdown 主表另存到该文件（无 BOM、LF）")
    args = ap.parse_args()
    MATRIX = args.matrix_root.resolve()
    REPORT_PATH = args.report.resolve()

    if not MATRIX.is_dir():
        print("matrix root not found: %s" % MATRIX, file=sys.stderr)
        return 1
    records = []
    for bench in sorted(p for p in MATRIX.iterdir() if p.is_dir()):
        for target_dir in sorted(p for p in bench.iterdir() if p.is_dir()):
            target = TARGET_DIR_MAP.get(target_dir.name, target_dir.name)
            for unit in sorted(p for p in target_dir.iterdir() if p.is_dir()):
                records.append(collect_unit(unit, bench.name, target, target_dir.name))

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    table = None
    if not args.json_only:
        table = md_table(records)
        print(table)
    if args.md_out:
        if table is None:
            table = md_table(records)
        out = Path(args.md_out)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(table + "\n")
    print("wrote %s (%d units)" % (REPORT_PATH, len(records)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
