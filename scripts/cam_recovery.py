#!/usr/bin/env python3
"""Explicit, sequential recovery gates; never launches the formal matrix.

Run with the experiment Python. Each invocation creates a fresh output directory.
Auth probes send only a constant marker. Single/P0 use the existing verified data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def emit(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False), flush=True)


def source_hash() -> str:
    digest = hashlib.sha256()
    paths = list((ROOT / "skillopt").rglob("*.py")) + [
        ROOT / "scripts" / name for name in ("train.py", "eval_only.py", "cam_recovery.py")]
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def configure_env(args, out: Path) -> None:
    proxy = getattr(args, "proxy_url", "")
    http_keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    all_keys = ("ALL_PROXY", "all_proxy")
    if proxy:
        parsed = urlparse(proxy)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not parsed.port or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("Use an existing loopback HTTP proxy without embedded credentials")
    elif any(os.environ.get(key) for key in http_keys + all_keys):
        raise ValueError("Inherited proxy route is not recorded; explicitly select an existing loopback HTTP proxy")
    # Both real wrappers and the login probes must use this exact identity/cache.
    os.environ["CODEX_HOME"] = str(Path(args.auth_home).resolve())
    os.environ["CODEX_CLI_BIN"] = str(Path(args.codex_bin).resolve())
    os.environ["CODEX_EXEC_PATH"] = os.environ["CODEX_CLI_BIN"]
    os.environ["CODEX_PROFILE"] = ""
    os.environ["CODEX_EXEC_PROFILE"] = ""
    os.environ["CODEX_WORKING_DIRECTORY"] = str(ROOT)
    os.environ["SKILLOPT_INFRA_ARTIFACT_DIR"] = str(out / "model_calls")
    os.environ["EXEC_EMPTY_RESPONSE_RETRIES"] = "0"
    os.environ["SKILLOPT_CODEX_TIMEOUT_SECONDS"] = "420"
    os.environ["SKILLOPT_CODEX_TRANSPORT"] = args.transport
    os.environ["PYTHONUTF8"] = "1"
    if proxy:
        for key in all_keys + ("NO_PROXY", "no_proxy"):
            os.environ.pop(key, None)
        for key in http_keys:
            os.environ[key] = proxy


def auth_check(args, out: Path) -> dict:
    from skillopt.model.infra_errors import InfraError, run_cli_failfast, sanitize_details, codex_transport_args
    records = []
    prompt = "Do not use tools or read files. Reply with exactly CAM_AUTH_OK."
    for role, model, effort in (("target", "gpt-5.6-terra", getattr(args, "target_reasoning", "none")),
                                ("optimizer", "gpt-5.6-sol", "medium")):
        for attempt in (1, 2):
            stage = f"{role}_{attempt}"
            work = out / stage
            work.mkdir()
            last = work / "last_message.txt"
            cmd = [args.codex_bin, "exec", "--ephemeral", "--skip-git-repo-check",
                   "--color", "never", "--sandbox", "read-only", "-C", str(work),
                   "-c", 'approval_policy="never"', "-c", f'model_reasoning_effort="{effort}"',
                   "--model", model, "--output-last-message", str(last), "-"]
            cmd[2:2] = codex_transport_args()
            save(work / "request.json", {"role": role, "model_requested": model,
                 "reasoning": effort, "auth_home": args.auth_home, "prompt": prompt})
            save(work / "conversation.json", [{"role": "user", "content": prompt}])
            started = time.monotonic()
            print(f"Starting minimal auth request {stage}: {model} / {effort}", flush=True)
            try:
                proc = run_cli_failfast(cmd, prompt=prompt, timeout=90, stage=stage,
                                        model=model, cwd=str(work), evidence_dir=work)
            except InfraError as exc:
                save(work / "conversation_meta.json", {"status": "infra_error",
                     "assistant_response_received": False, "error": exc.to_dict()})
                record = {"stage": stage, "status": "infra_error", "error": exc.to_dict(),
                          "model_requested": model, "hard": None, "soft": None}
                emit(out / "results.jsonl", record)
                save(out / "stage_stats.json", {"status": "aborted", "attempted": len(records)+1,
                     "passed": len(records), "infra_errors": 1})
                raise
            raw = sanitize_details((proc.stdout or "") + "\n[stderr]\n" + (proc.stderr or ""))
            (work / "raw_trace.txt").write_text(raw, encoding="utf-8")
            reply = last.read_text(encoding="utf-8").strip() if last.exists() else ""
            observed = re.search(r"(?m)^model:\s*(\S+)", raw)
            selected = observed.group(1) if observed else None
            provider_match = re.search(r"(?m)^provider:\s*(\S+)", raw)
            provider = provider_match.group(1) if provider_match else None
            expected_provider = "cam_openai_http" if getattr(args, "transport", "default") == "http" else "openai"
            ok = (proc.returncode == 0 and reply == "CAM_AUTH_OK" and selected == model
                  and provider == expected_provider)
            record = {"stage": stage, "status": "passed" if ok else "failed",
                      "exit_code": proc.returncode, "model_requested": model,
                      "model_cli_header": selected, "model_verification": "CLI selected-model header",
                      "provider_cli_header": provider, "expected_provider": expected_provider,
                      "reasoning": effort, "marker_ok": reply == "CAM_AUTH_OK",
                      "wall_seconds": round(time.monotonic() - started, 3),
                      "hard": None, "soft": None}
            save(work / "conversation.json", [{"role": "user", "content": prompt}]
                 + ([{"role": "assistant", "content": reply}] if reply else []))
            emit(out / "results.jsonl", record)
            records.append(record)
            save(out / "stage_stats.json", {"status": "running", "attempted": len(records),
                 "passed": sum(r["status"] == "passed" for r in records), "infra_errors": 0})
            if not ok:
                raise InfraError("provider_error", "Minimal request did not pass marker/model/exit checks",
                                 stage=stage, model=model)
    save(out / "stage_stats.json", {"status": "passed", "attempted": 4, "passed": 4, "infra_errors": 0})
    return {"status": "passed", "stage": "auth-check", "consecutive_passes_per_role": 2,
            "request_count": len(records), "results": records}


def load_probe_config(args, out: Path) -> dict:
    cfg = json.loads(Path(args.base_config).read_text(encoding="utf-8"))
    if (cfg["target_model"], cfg["optimizer_model"], cfg["reasoning_effort"]) != (
            "gpt-5.6-terra", "gpt-5.6-sol", "medium"):
        raise ValueError("Recovery model/reasoning must match the accepted authentication probes")
    if any(cfg[key] != 4 for key in ("train_size", "batch_size", "sel_env_num", "test_env_num")):
        raise ValueError("This recovery entrypoint is restricted to the four-sample P0 configuration")
    cfg.update(out_root=str(out), workers=1, analyst_workers=1, exec_timeout=420,
               codex_exec_path=args.codex_bin, codex_exec_profile="",
               codex_exec_use_sdk="cli", codex_exec_full_auto=False,
               codex_exec_reasoning_effort=args.target_reasoning)
    for key in ("data_root", "split_dir", "skill_init"):
        value = Path(cfg[key])
        cfg[key] = str(value if value.is_absolute() else ROOT / value)
    return cfg


def require_gate(path: str | None, stage: str, args) -> dict:
    if not path:
        raise ValueError(f"A passed {stage} summary is required")
    gate = json.loads(Path(path).read_text(encoding="utf-8"))
    if gate.get("status") != "passed" or gate.get("stage") != stage:
        raise ValueError(f"Previous {stage} gate has not passed")
    manifest = gate.get("manifest", {})
    if stage != "auth-check" and manifest.get("source_sha256") != source_hash():
        raise ValueError("Source changed after gate; rerun the gate for this code version")
    if manifest.get("auth_home") != str(Path(args.auth_home).resolve()):
        raise ValueError("Auth home changed after gate")
    if manifest.get("codex_bin") != args.codex_bin:
        raise ValueError("CLI binary path changed after gate")
    if manifest.get("http_only", False):
        raise ValueError("Legacy HTTP override probe is not an accepted recovery gate")
    if manifest.get("transport", "default") != getattr(args, "transport", "default"):
        raise ValueError("Transport changed after the previous gate")
    if manifest.get("proxy_url", "") != getattr(args, "proxy_url", ""):
        raise ValueError("Experiment proxy route changed after the previous gate")
    if stage != "auth-check":
        if manifest.get("target_reasoning") != args.target_reasoning:
            raise ValueError("Target reasoning changed after the single-sample gate")
        config_hash = hashlib.sha256(Path(args.base_config).read_bytes()).hexdigest()
        if manifest.get("base_config_sha256") != config_hash:
            raise ValueError("Base experiment configuration changed after the single-sample gate")
    else:
        effort = getattr(args, "target_reasoning", "none")
        expected = [("target_1", "gpt-5.6-terra", effort), ("target_2", "gpt-5.6-terra", effort),
                    ("optimizer_1", "gpt-5.6-sol", "medium"), ("optimizer_2", "gpt-5.6-sol", "medium")]
        observed = [(r.get("stage"), r.get("model_cli_header"), r.get("reasoning"))
                    for r in gate.get("results", []) if r.get("status") == "passed" and r.get("exit_code") == 0]
        if observed != expected:
            raise ValueError("Authentication gate lacks four verified model/exit results")
        expected_provider = "cam_openai_http" if getattr(args, "transport", "default") == "http" else "openai"
        if any(r.get("provider_cli_header") != expected_provider for r in gate["results"]):
            raise ValueError("Authentication gate did not verify the effective provider/transport")
    return gate


def single(args, out: Path) -> dict:
    require_gate(args.auth_summary, "auth-check", args)
    cfg = load_probe_config(args, out)
    from scripts.train import get_adapter
    from skillopt.model import (set_optimizer_backend, set_target_backend,
         set_optimizer_deployment, set_target_deployment, set_reasoning_effort,
         configure_codex_exec)
    from skillopt.model.common import tracker
    set_optimizer_backend(cfg["optimizer_backend"])
    set_target_backend(cfg["target_backend"])
    set_optimizer_deployment(cfg["optimizer_model"])
    set_target_deployment(cfg["target_model"])
    set_reasoning_effort(cfg["reasoning_effort"])
    configure_codex_exec(path=args.codex_bin, profile="", use_sdk="cli", full_auto=False,
                         reasoning_effort=args.target_reasoning, sandbox="workspace-write")
    os.environ["REFLACT_CODEX_TRACE_TO_OPTIMIZER"] = "1"
    adapter = get_adapter(cfg)
    adapter.setup(cfg)
    plan = adapter.dataloader.plan_train_epoch(epoch=1, steps_per_epoch=1,
                 accumulation=1, batch_size=4, seed=cfg["seed"])
    items = adapter.build_env_from_batch(plan[0])[:1]
    save(out / "sample_manifest.json", {"ids": [str(x["id"]) for x in items],
         "split": "train", "seed": cfg["seed"], "batch_seed": plan[0].seed})
    save(out / "config.json", cfg)
    skill = Path(cfg["skill_init"]).read_text(encoding="utf-8")
    tracker.reset()
    results = adapter.rollout(items, skill, str(out / "rollout"))
    patches = adapter.reflect(results, skill, str(out / "reflection"),
              prediction_dir=str(out / "rollout" / "predictions"), random_seed=cfg["seed"])
    save(out / "patches.json", patches)
    usage = tracker.summary()
    analyst_calls = sum(v.get("calls", 0) for k, v in usage.items() if "analyst" in k)
    result = results[0]
    conv = out / "rollout" / "predictions" / str(result["id"]) / "conversation.json"
    patch_count = sum(bool(p and p.get("patch", {}).get("edits")) for p in patches)
    checks = {k: bool(result.get(k)) for k in ("llm_ok", "code_ok", "exec_ok")}
    checks.update(conversation_exists=conv.exists(), optimizer_called=analyst_calls > 0,
                  patch_generated=patch_count > 0)
    record = {"stage": "single", "status": "passed" if all(checks.values()) else "failed",
              "checks": checks, "id": str(result["id"]), "hard": result["hard"],
              "soft": result["soft"], "patch_count": patch_count,
              "analyst_calls": analyst_calls, "usage": usage}
    emit(out / "results.jsonl", record)
    return record


def probe(args, out: Path) -> dict:
    require_gate(args.auth_summary, "auth-check", args)
    require_gate(args.single_summary, "single", args)
    from scripts.train import get_adapter
    from skillopt.engine.trainer import ReflACTTrainer
    cfg = load_probe_config(args, out)
    summary = ReflACTTrainer(cfg, get_adapter(cfg)).train()
    patch_files = list((out / "steps").glob("**/patches/*.json"))
    patches = [json.loads(p.read_text(encoding="utf-8")) for p in patch_files]
    patch_count = sum(bool(p.get("patch", p).get("edits")) for p in patches if isinstance(p, dict))
    # Full training summary remains untouched; recovery gate is saved separately.
    return {"status": "completed_pending_audit", "stage": "p0", "summary": summary,
            "patch_artifact_count": patch_count,
            "note": "Verify analyst calls, selection/test completeness and mechanism records before formal runs"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=("auth-check", "single", "p0"))
    p.add_argument("--out", required=True)
    p.add_argument("--auth-home", default=str(ROOT.parent / "codex-home"))
    p.add_argument("--codex-bin", default=str(ROOT.parent / "codex-cli/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"))
    p.add_argument("--base-config", default=str(ROOT / "configs/spreadsheetbench/cam_recovery_p0.json"))
    p.add_argument("--target-reasoning", choices=("none", "low"), default="none")
    p.add_argument("--auth-summary")
    p.add_argument("--transport", choices=("default", "http"), default="default")
    p.add_argument("--proxy-url", default="", help="Existing loopback HTTP proxy; applies only to this process tree")
    p.add_argument("--single-summary")
    args = p.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        p.error("Output already exists; choose a fresh path to avoid stale/cache evidence")
    out.mkdir(parents=True)
    os.chdir(ROOT)
    configure_env(args, out)
    manifest = {"stage": args.stage, "source_sha256": source_hash(), "python": sys.executable,
                "auth_home": str(Path(args.auth_home).resolve()), "codex_bin": args.codex_bin,
                "target_reasoning": args.target_reasoning, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    manifest["http_only"] = False
    manifest["transport"] = args.transport
    manifest["proxy_url"] = args.proxy_url
    if Path(args.base_config).exists():
        manifest["base_config_sha256"] = hashlib.sha256(Path(args.base_config).read_bytes()).hexdigest()
    save(out / "manifest.json", manifest)
    started = time.monotonic()
    try:
        summary = {"auth-check": auth_check, "single": single, "p0": probe}[args.stage](args, out)
    except Exception as exc:
        from skillopt.model.infra_errors import InfraError, sanitize_details
        detail = exc.to_dict() if isinstance(exc, InfraError) else {
            "failure_type": type(exc).__name__, "details": sanitize_details(str(exc))}
        summary = {"status": "infra_error" if isinstance(exc, InfraError) else "failed",
                   "stage": args.stage, "error": detail, "hard": None, "soft": None}
        save(out / "error_summary.json", summary)
        emit(out / "results.jsonl", summary)
    summary.update(manifest=manifest, wall_seconds=round(time.monotonic() - started, 3))
    save(out / "recovery_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["status"] in {"passed", "completed_pending_audit"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
