"""Read-only completion checks shared by runners and final analysis."""
import json
import math
from pathlib import Path


def load_rows(path):
    rows = {}
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            item_id = str(row["id"])
            if item_id in rows:
                raise ValueError(f"Duplicate ID {item_id} in {path}:{number}")
            score = row.get("hard")
            if isinstance(score, bool) or score not in (0, 1):
                raise ValueError(f"Invalid hard score in {path}:{number}")
            if row.get("status") in {"infra_error", "failed", "error", "cancelled", "timeout"}:
                raise ValueError(f"Invalid infrastructure row in {path}:{number}")
            rows[item_id] = row
    return rows


def phase_rows(cell, phase):
    cell = Path(cell)
    path = cell / phase / "results.jsonl"
    if phase == "test_eval_final" and not path.exists():
        state = json.loads((cell / "runtime_state.json").read_text(encoding="utf-8"))
        current = Path(state["current_skill_path"]).read_bytes()
        best = Path(state["best_skill_path"]).read_bytes()
        if current != best:
            raise ValueError("Final test rows missing for distinct final and best skills")
        path = cell / "test_eval" / "results.jsonl"
    return load_rows(path)


def validate_cell(cell):
    cell = Path(cell)
    try:
        cfg = json.loads((cell / "config.json").read_text(encoding="utf-8"))
        summary = json.loads((cell / "summary.json").read_text(encoding="utf-8"))
        history = json.loads((cell / "history.json").read_text(encoding="utf-8"))
        if str(summary.get("status", "")).startswith("invalid") or summary.get("status") == "infra_error":
            raise ValueError(f"Invalid summary: {summary.get('status')}")
        expected = cfg["num_epochs"] * math.ceil(cfg["train_size"] / cfg["batch_size"])
        if len(history) != expected or summary.get("total_steps") != expected:
            raise ValueError(f"Expected {expected} complete training steps")
        if [h.get("step") for h in history] != list(range(1, expected + 1)):
            raise ValueError("Training history is not contiguous")
        counts = {name: sum(h.get("action") in actions for h in history) for name, actions in {
            "total_accepts": {"accept", "accept_new_best", "force_accept"},
            "total_soft_accepts": {"soft_accept"}, "total_rejects": {"reject"}}.items()}
        if not sum(counts.values()):
            raise ValueError("No evaluated candidate updates")
        for name, count in counts.items():
            if summary.get(name, 0) != count:
                raise ValueError(f"Summary/history action mismatch: {name}")
        strict = cfg.get("cam_enabled") and cfg.get("cam_bootstrap_gate", True)
        gates = cell / "cam_gate_events.json"
        if strict:
            events = json.loads(gates.read_text(encoding="utf-8"))
            if not isinstance(events, list) or not events:
                raise ValueError("Missing CAM gate evidence")
        elif any((cell / name).exists() for name in ("cam_gate_events.json", "cam_gate_state.json", "final_cam_gate.json")):
            raise ValueError("Non-bootstrap arm has bootstrap artifacts")
        if not (cfg.get("cam_enabled") and cfg.get("cam_memory_enabled")) and (cell / "cam_rejected_memory.json").exists():
            raise ValueError("Disabled persistent memory produced records")
        if cfg.get("eval_test"):
            test_ids = None
            if cfg.get("split_dir"):
                items = json.loads((Path(cfg["split_dir"]) / "test" / "items.json").read_text(encoding="utf-8"))
                test_ids = {str(item["id"]) for item in items}
            phase_ids = None
            for phase, key in (("test_eval_baseline", "baseline_test_hard"),
                               ("test_eval", "test_hard"), ("test_eval_final", "final_test_hard")):
                rows = phase_rows(cell, phase)
                actual_ids = set(rows)
                bad_ids = test_ids is not None and (
                    not actual_ids.issubset(test_ids)
                    or (cfg["test_env_num"] >= len(test_ids) and actual_ids != test_ids))
                if len(rows) != cfg["test_env_num"] or bad_ids or (phase_ids is not None and actual_ids != phase_ids):
                    raise ValueError(f"Incomplete or mismatched test IDs: {phase}")
                phase_ids = actual_ids
                score = sum(r["hard"] for r in rows.values()) / len(rows)
                reported = summary.get(key)
                if reported is None or not math.isclose(score, reported, abs_tol=1e-12):
                    raise ValueError(f"Test aggregate mismatch: {key}")
        return True, "complete; steps, actions, test IDs and scores verified"
    except (OSError, ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
        return False, str(exc)
