#!/usr/bin/env python3
"""无人值守阶段队列驱动器：按固定顺序跑完剩余实验单元（阶段 b_rest/c/d/e）。

本脚本只负责排队、并发调度、幂等跳过、状态落盘与汇总；每个单元仍由
scripts/run_cell_robust.py 启动（它自带 infra_error 清理重试与 llm_ok 哨兵）。

要点
----
  * 幂等可续跑：单元 out_root 下已有完整 summary.json（total_steps 非空、
    status 不以 invalid 开头）则跳过并标 skipped_done。判断目录的规则与
    run_cell_robust.py 完全一致（target 里的 ':' 换成 '-'，即 gemma4:26b
    -> gemma4-26b）；另外向 run_cell_robust.py 透传 --only-if-incomplete
    作双保险——即使两边的目录规则将来漂移，robust runner 自身也会跳过。
    不解析子进程 stdout 来获取 out_root：那必须捕获输出（本脚本明确
    不捕获，输出量很大），且 dry-run 时没有进程可解析。
  * 并发：--lanes 个 worker 线程共享一个线程安全队列，每个线程串行地
    取单元 -> 启动 -> 等待结束 -> 记录 -> 取下一个。
  * 子进程 stdout/stderr 直接重定向到日志文件（磁盘落地，不进内存）。
  * 状态文件 reports/queue_state.json：每次状态变化先写临时文件再
    os.replace 原子替换；状态只会是
    pending/running/done/failed/skipped_done 之一。
  * 不打印、不写入任何 API key（本脚本从不读取环境变量，只透传参数）。

用法
----
    python -X utf8 scripts/run_phase_queue.py --phases b_rest,c,d,e --lanes 2
    python -X utf8 scripts/run_phase_queue.py --phases c --dry-run
    python -X utf8 scripts/run_phase_queue.py --start-from qwen_cam_no_bootstrap
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_ROBUST_RUNNER = _SCRIPT_DIR / "run_cell_robust.py"
_STATE_FILE = _PROJECT_ROOT / "reports" / "queue_state.json"
_SEED = 42

# 阶段的规范执行顺序（--phases 无论怎么给，都按这个顺序执行）。
PHASE_ORDER = ["b_rest", "c", "d", "e"]

# 全量有序队列：(phase, cell_id, benchmark, target, method)。
# cell_id 只是队列内部的名字；真实 target 原样保留（如 gemma4:26b），
# 只有目录名/文件名里的非法字符会被替换（见 cell_dir_name / log_file_of）。
CELLS = [
    # ---- phase b_rest：阶段 B 剩余两格（SpreadsheetBench, qwen）----
    ("b_rest", "glm_cam_full", "spreadsheetbench", "glm", "cam_full"),
    ("b_rest", "qwen_baseline", "spreadsheetbench", "qwen", "baseline"),
    ("b_rest", "qwen_cam_full", "spreadsheetbench", "qwen", "cam_full"),
    # ---- phase c：能力梯度补齐（SpreadsheetBench）----
    # gemma4:26b 已移除（2026-10-17 冒烟实测）：4 题里 3 题返回空回复（各重试 5 次），
    # 单题耗时 487–1194 s，hard=0/1 无有效信号；按方案 §7 风险条款"单格 > 2 h 则砍掉该格"处理。
    # 证据：outputs/preflight/smoke_gemma4/
    ("c", "qwen_nothink_baseline", "spreadsheetbench", "qwen_nothink", "baseline"),
    ("c", "qwen_nothink_cam_full", "spreadsheetbench", "qwen_nothink", "cam_full"),
    # ---- phase d：消融（SpreadsheetBench, qwen）----
    ("d", "qwen_cam_no_bootstrap", "spreadsheetbench", "qwen", "cam_no_bootstrap"),
    # no_adaptive_budget 本轮不跑（2026-10-18 二次裁剪）：自适应预算是否生效不需要一整格证明——
    # history.lr_control_mode 为 cam_adaptive 且 edit_budget 随失败集中度变化即可（已在 v1 观察过）。
    # ("d", "qwen_cam_no_adaptive_budget", "spreadsheetbench", "qwen", "cam_no_adaptive_budget"),
    ("d", "qwen_cam_no_memory", "spreadsheetbench", "qwen", "cam_no_memory"),
    # ---- phase e：副 benchmark（glm）----
    # OfficeQA 训练对被裁掉（2026-10-18，用户要求规模降 30–50%）：
    # 它是剩余单元里最贵的一对（长 prompt，单格 4–5 h，两格约 10 h 单元时间），
    # 而其静态基线 0.3081 已经跑完、可照常报告。跨任务泛化的证据由 LMB 一对承担。
    # 若要恢复：把下面两行取消注释即可（不会影响其它单元）。
    ("e", "lmb_baseline", "livemathematicianbench", "glm", "baseline"),
    ("e", "lmb_cam_full", "livemathematicianbench", "glm", "cam_full"),
    # ("e", "officeqa_baseline", "officeqa", "glm", "baseline"),
    # ("e", "officeqa_cam_full", "officeqa", "glm", "cam_full"),
]


def cell_dir_name(target: str) -> str:
    """目录名里的 target：':' 在 Windows 目录名中非法，换成 '-'。

    与 run_cell_robust.py / run_experiment.py 的 cell_dir_name() 规则
    完全一致：gemma4:26b -> gemma4-26b（模型 id 本身不变）。
    """
    return target.replace(":", "-")


def log_name_part(target: str) -> str:
    """日志文件名里的 target：冒号同样非法，这里换 '_'（gemma4_26b）。"""
    return target.replace(":", "_")


def out_root_of(benchmark: str, target: str, method: str) -> Path:
    """单元输出目录，定位逻辑与 run_cell_robust.py 保持一致。"""
    return (_PROJECT_ROOT / "outputs" / "matrix" / benchmark
            / cell_dir_name(target) / f"{method}_seed{_SEED}")


def log_file_of(benchmark: str, target: str, method: str) -> Path:
    """单元日志文件：outputs/logs/cell_<method>_<target>[_<benchmark>].log。

    spreadsheetbench 是默认 benchmark，不带后缀，与既有日志命名一致
    （outputs/logs/cell_baseline_glm.log 等）。
    """
    parts = ["cell", method, log_name_part(target)]
    if benchmark != "spreadsheetbench":
        parts.append(benchmark)
    return _PROJECT_ROOT / "outputs" / "logs" / ("_".join(parts) + ".log")


def summary_is_complete(out_root: Path) -> tuple[bool, str]:
    """判断单元是否已有完整结果（幂等跳过依据）。

    **成功的 summary.json 没有 `status` 键**（`status`/`validity`/
    `eligible_for_paper` 只在中止/无效时才写）。所以"干净完成"的判据是：
    summary.json 存在 **且** `total_steps` 非空 **且** 若带 `status` 则不以
    `invalid` 开头。把"无 status"当成"未完成"会让队列重跑已完成的单元
    （2026-10-17 踩到：交接任务因此永不触发）。返回 (是否完整, 原因说明)。
    """
    summary = out_root / "summary.json"
    if not summary.exists():
        return False, "no summary.json yet"
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"unreadable summary.json ({exc})"
    status = str(data.get("status", "") or "")
    if status.startswith("invalid"):
        return False, f"status={status}"
    if not data.get("total_steps"):
        return False, f"total_steps={data.get('total_steps')!r}"
    return True, f"status={status or '(clean)'}, total_steps={data.get('total_steps')}"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def clock() -> str:
    return time.strftime("%H:%M:%S")


def rel(path: Path) -> str:
    """相对项目根的 posix 风格路径，写入状态文件/打印用。"""
    return path.resolve().relative_to(_PROJECT_ROOT).as_posix()


class QueueState:
    """线程安全的状态容器；每次单元状态变化都原子写一次状态文件。"""

    def __init__(self, lanes: int, cells: list[dict]) -> None:
        self._lock = threading.Lock()
        self._doc: dict = {
            "updated_at": now_iso(),
            "lanes": lanes,
            "cells": cells,
        }

    def save(self) -> None:
        """主动把当前状态落盘一次（启动建盘 / dry-run 收尾用）。"""
        with self._lock:
            self._write_locked()

    def update(self, cell_id: str, **fields) -> None:
        """更新一个单元的字段并落盘（原子写）。"""
        with self._lock:
            for cell in self._doc["cells"]:
                if cell["cell_id"] == cell_id:
                    cell.update(fields)
                    break
            else:
                raise KeyError(cell_id)
            self._doc["updated_at"] = now_iso()
            self._write_locked()

    def _write_locked(self) -> None:
        # 调用方必须已持有 _lock。先写同目录下的临时文件，再 os.replace
        # 原子替换，避免读方看到半截 JSON。无 BOM、\n 换行（PowerShell 5.1
        # 的 UTF8 BOM 坑，见执行计划 6.2 节）。
        self._doc["updated_at"] = now_iso()
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_name(_STATE_FILE.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(self._doc, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, _STATE_FILE)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(c) for c in self._doc["cells"]]


def build_plan(phases: list[str], start_from: str | None) -> list[tuple[str, str, str, str, str]]:
    """按阶段规范顺序构建单元队列，并应用 --start-from 截断。"""
    plan = [entry for entry in CELLS if entry[0] in phases]
    if start_from is not None:
        ids = [entry[1] for entry in plan]
        if start_from not in ids:
            known = ", ".join(ids)
            raise SystemExit(
                f"error: --start-from {start_from!r} is not among the selected "
                f"cells (available: {known})")
        plan = plan[ids.index(start_from):]
    return plan


def make_cell_record(entry: tuple[str, str, str, str, str]) -> dict:
    """单元在状态文件里的记录（真实 target 原样保留，目录名已替换）。"""
    _phase, cell_id, benchmark, target, method = entry
    return {
        "cell_id": cell_id,
        "benchmark": benchmark,
        "target": target,          # 真实模型 id，如 gemma4:26b
        "method": method,
        "out_root": rel(out_root_of(benchmark, target, method)),
        "status": "pending",
        "exit_code": None,
        "started_at": None,
        "finished_at": None,
        "log": rel(log_file_of(benchmark, target, method)),
    }


def print_plan(records, dry_run: bool) -> None:
    """打印执行计划；dry-run 时这是全部输出（不启动任何进程）。"""
    n_run = sum(1 for r in records if r["status"] == "pending")
    n_skip = len(records) - n_run
    print("=" * 78)
    print("  phase queue driver for run_cell_robust.py")
    print("=" * 78)
    if dry_run:
        print("  DRY RUN: no cell will be launched")
    print(f"  cells in plan : {len(records)}  (to run: {n_run}, already done: {n_skip})")
    print(f"  order        : as listed below")
    print("-" * 78)
    for idx, record in enumerate(records, start=1):
        out_root = Path(record["out_root"])
        summary = out_root / "summary.json"
        if record["status"] == "skipped_done":
            note = f"SKIP (done: {summary_is_complete(_PROJECT_ROOT / out_root)[1]})"
        else:
            note = "RUN"
        print(f"  {idx:>2}. {record['cell_id']:<28} "
              f"[{record['benchmark']}/{record['target']}/{record['method']}]")
        print(f"      out_root : {record['out_root']}")
        print(f"      log      : {record['log']}   -> {note}")
    print("-" * 78, flush=True)


def lane_worker(lane: int, work: "queue.Queue[dict]", state: QueueState,
                max_attempts: int, out_lock: threading.Lock) -> None:
    """一个 lane：循环取单元 -> 幂等检查 -> 启动 run_cell_robust.py -> 等待。"""
    while True:
        try:
            record = work.get_nowait()
        except queue.Empty:
            return  # 队列空，本 lane 结束
        cell_id = record["cell_id"]
        target, benchmark, method = record["target"], record["benchmark"], record["method"]
        out_root = out_root_of(benchmark, target, method)
        log_path = log_file_of(benchmark, target, method)

        # 幂等检查：已有完整结果则跳过，绝不覆盖。
        done, reason = summary_is_complete(out_root)
        if done:
            state.update(cell_id, status="skipped_done", finished_at=now_iso())
            with out_lock:
                print(f"[{clock()}] SKIP    {cell_id} ({reason})", flush=True)
            continue

        # 启动单元。stdout/stderr 直接落到日志文件（追加、二进制直通，
        # 不在内存里捕获，也不转发 API key 之类任何环境变量之外的东西）。
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-X", "utf8", str(_ROBUST_RUNNER),
               "--action", "train", "--method", method, "--target", target,
               "--benchmark", benchmark,
               "--max-attempts", str(max_attempts),
               "--only-if-incomplete"]
        state.update(cell_id, status="running", started_at=now_iso())
        with out_lock:
            print(f"[{clock()}] START   {cell_id} lane={lane} "
                  f"log={rel(log_path)}", flush=True)
        started = time.monotonic()
        with log_path.open("ab") as sink:
            proc = subprocess.Popen(cmd, cwd=str(_PROJECT_ROOT),
                                    stdout=sink, stderr=subprocess.STDOUT)
            rc = proc.wait()
        elapsed_min = (time.monotonic() - started) / 60.0

        status = "done" if rc == 0 else "failed"
        state.update(cell_id, status=status, exit_code=rc, finished_at=now_iso())
        with out_lock:
            tag = "DONE" if rc == 0 else "FAILED"
            print(f"[{clock()}] {tag} {cell_id} rc={rc} "
                  f"elapsed={elapsed_min:.1f}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unattended phase queue driver over scripts/run_cell_robust.py")
    parser.add_argument("--phases", default="b_rest,c,d,e",
                        help=f"comma-separated phases among {','.join(PHASE_ORDER)} "
                             f"(default: all, in canonical order)")
    parser.add_argument("--lanes", type=int, default=2,
                        help="concurrent cells, 1..3 (default 2)")
    parser.add_argument("--start-from", default=None, metavar="CELL_ID",
                        help="skip every cell before this cell_id")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan only; launch nothing")
    parser.add_argument("--max-attempts", type=int, default=15,
                        help="passed through to run_cell_robust.py (default 15)")
    args = parser.parse_args()

    phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    unknown = [p for p in phases if p not in PHASE_ORDER]
    if unknown:
        parser.error(f"--phases has unknown phase(s) {unknown}; "
                     f"valid: {','.join(PHASE_ORDER)}")
    phases = [p for p in PHASE_ORDER if p in phases]  # 规范化顺序
    if not 1 <= args.lanes <= 3:
        parser.error("--lanes must be between 1 and 3")
    if args.max_attempts < 1:
        parser.error("--max-attempts must be >= 1")

    plan = build_plan(phases, args.start_from)
    records = [make_cell_record(entry) for entry in plan]
    state = QueueState(args.lanes, records)

    if args.dry_run:
        # dry-run 也把跳过判定如实跑一遍（只读磁盘），但状态只写
        # pending / skipped_done，绝不写 running，也不启动任何进程。
        for record in records:
            done, _ = summary_is_complete(_PROJECT_ROOT / record["out_root"])
            if done:
                record["status"] = "skipped_done"
        state = QueueState(args.lanes, records)
        state.save()
        print_plan(records, dry_run=True)
        print(f"  state file   : {rel(_STATE_FILE)} (statuses: pending/skipped_done only)")
        print("DRY RUN finished; nothing was launched.", flush=True)
        return 0

    state.save()  # 启动前先建盘（全部 pending），便于中途查看进度
    print_plan(records, dry_run=False)
    work: "queue.Queue[dict]" = queue.Queue()
    for record in records:
        work.put(record)
    out_lock = threading.Lock()
    threads = [threading.Thread(target=lane_worker,
                                args=(lane + 1, work, state, args.max_attempts, out_lock),
                                name=f"lane-{lane + 1}")
               for lane in range(min(args.lanes, len(records)))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # ---- 汇总表 ----
    print("=" * 78)
    print("  queue summary")
    print("=" * 78)
    any_failed = False
    counts: dict[str, int] = {}
    for record in state.snapshot():
        counts[record["status"]] = counts.get(record["status"], 0) + 1
        if record["status"] == "failed":
            any_failed = True
        print(f"  {record['cell_id']:<28} {record['status']:<13} "
              f"rc={record['exit_code']!s:<4} log={record['log']}")
    print("-" * 78)
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())), flush=True)
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
