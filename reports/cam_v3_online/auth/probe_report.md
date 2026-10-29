# CAM v3 credential probe

Config: `configs/experiments/cam_v3_mechanisms.yaml`
Config SHA-256: `bf840f1f56ffa967b4a03e8f15a24c0d98a7141cd570267773b4bd4c0c24f3d2`
Observed at (UTC): `2026-10-18T12:16:39Z` (`file_mtime`)
Timestamp reflects the original report file mtime; no new request was made for this metadata update.
Requests used a fixed minimal prompt, at most two candidates per role, zero SDK retries, and a 30-second timeout.
Credential values, prefixes, credential hashes, response bodies, and raw error text are never recorded.

## optimizer

- Model: `glm`
- Endpoint: `https://hub.zgci.org/v1`
- Auth mode: `openai_compatible`
- Result: `ok`
- Candidate `project_dotenv:OPTIMIZER_AZURE_OPENAI_API_KEY`: HTTP `200`, request ID `None`, error type `None`

## target

- Model: `qwen`
- Endpoint: `https://hub.zgci.org/v1`
- Auth mode: `openai_compatible`
- Result: `ok`
- Candidate `project_dotenv:TARGET_AZURE_OPENAI_API_KEY`: HTTP `200`, request ID `None`, error type `None`
