"""Print a compact status table for every unit under outputs/matrix/**.

Read-only. Used to decide which units are complete / partial / missing.

Usage:
    python -X utf8 scripts/matrix_status.py [--json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "outputs" / "matrix"


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def count_jsonl(p: Path) -> tuple[int, int, int]:
    """Return (total, completed, infra_error) rows for a results.jsonl."""
    total = completed = infra = 0
    if not p.exists():
        return 0, 0, 0
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
            except Exception:
                continue
            status = row.get("status")
            if status == "completed":
                completed += 1
            elif status in {"infra_error", "error"}:
                infra += 1
    return total, completed, infra


def unit_status(unit_dir: Path) -> dict:
    info: dict = {"unit": str(unit_dir.relative_to(ROOT)).replace("\\", "/")}
    cfg = read_json(unit_dir / "config.json")
    if cfg:
        info["batch_size"] = cfg.get("batch_size")
        info["num_epochs"] = cfg.get("num_epochs")
        info["cam_enabled"] = cfg.get("cam_enabled")
        info["sel_env_num"] = cfg.get("sel_env_num")
    summary = read_json(unit_dir / "summary.json")
    if summary:
        info["status"] = summary.get("status")
        info["test_hard"] = summary.get("test_hard")
        info["total_steps"] = summary.get("total_steps")
        info["A/R/S"] = "%s/%s/%s" % (
            summary.get("total_accepts"),
            summary.get("total_rejects"),
            summary.get("total_skips"),
        )
    stages = []
    for st in sorted(unit_dir.glob("*/stage_stats.json")):
        ss = read_json(st)
        if not ss:
            continue
        n_rec, n_exp = ss.get("n_recorded"), ss.get("n_expected")
        stages.append(
            "%s:%s %s/%s hard=%s" % (ss.get("stage"), ss.get("status"), n_rec, n_exp, ss.get("hard"))
        )
    if stages:
        info["stages"] = stages
    # top-level results.jsonl (static / eval units)
    tot, comp, infra = count_jsonl(unit_dir / "results.jsonl")
    if tot:
        info["results"] = "completed=%d/%d infra=%d" % (comp, tot, infra)
    return info


def main() -> int:
    as_json = "--json" in sys.argv
    units = []
    for bench in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        for target in sorted(p for p in bench.iterdir() if p.is_dir()):
            for unit in sorted(p for p in target.iterdir() if p.is_dir()):
                units.append(unit_status(unit))
    if as_json:
        print(json.dumps(units, indent=2, ensure_ascii=False))
        return 0
    for u in units:
        head = u["unit"]
        bits = []
        for k in ("batch_size", "num_epochs", "cam_enabled", "sel_env_num", "status", "test_hard", "total_steps", "A/R/S", "results"):
            if k in u:
                bits.append("%s=%s" % (k, u[k]))
        print(head)
        if bits:
            print("    " + "  ".join(bits))
        for s in u.get("stages", []):
            print("    stage " + s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
