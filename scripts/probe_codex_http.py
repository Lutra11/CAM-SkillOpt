"""Bounded transport diagnostic. Sends only a constant marker to OpenAI."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ["CODEX_HOME"] = str(ROOT.parent / "codex-home")
    from skillopt.model.infra_errors import run_cli_failfast, InfraError, sanitize_details
    binary = ROOT.parent / "codex-cli/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
    overrides = {
        "model_provider": '"cam_openai_http"',
        "model_providers.cam_openai_http.name": '"OpenAI HTTP"',
        "model_providers.cam_openai_http.base_url": '"https://chatgpt.com/backend-api/codex"',
        "model_providers.cam_openai_http.requires_openai_auth": "true",
        "model_providers.cam_openai_http.wire_api": '"responses"',
        "model_providers.cam_openai_http.supports_websockets": "false",
        "model_providers.cam_openai_http.request_max_retries": "0",
        "model_providers.cam_openai_http.stream_max_retries": "0",
        "model_reasoning_effort": '"none"',
        "approval_policy": '"never"',
    }
    cmd = [str(binary)]
    for k, v in overrides.items():
        cmd.extend(["-c", f"{k}={v}"])
    cmd.extend(["exec", "--ephemeral", "--skip-git-repo-check", "--color", "never",
                "--sandbox", "read-only", "-C", str(out), "-m", "gpt-5.6-terra",
                "--output-last-message", str(out / "last_message.txt"), "-"])
    prompt = "Do not use tools or read files. Reply with exactly CAM_AUTH_OK."
    (out / "request.json").write_text(json.dumps({"command": cmd, "prompt": prompt}, indent=2), encoding="utf-8")
    try:
        result = run_cli_failfast(cmd, prompt=prompt, timeout=90, stage="http_diagnostic",
                                  model="gpt-5.6-terra", cwd=str(out), evidence_dir=out)
        raw = sanitize_details(result.stdout + "\n[stderr]\n" + result.stderr)
        (out / "raw_trace.txt").write_text(raw, encoding="utf-8")
        message = (out / "last_message.txt").read_text(encoding="utf-8").strip()
        summary = {"status": "passed" if result.returncode == 0 and message == "CAM_AUTH_OK" else "failed",
                   "exit_code": result.returncode, "message": message,
                   "websocket_diagnostic": "responses_websocket" in raw}
    except InfraError as exc:
        summary = exc.to_dict()
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
