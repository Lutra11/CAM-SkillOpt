# Runtime response contract repair

- Focused runtime suite: **15 passed**.
- CAM v3 diagnostics suite: **4 passed**.
- Module and test file compilation: passed.
- Invocations used `E:\MiniConda\envs\skillopt\python.exe -X utf8 -m unittest discover` and `py_compile`.
- Tests use mocked clients and an SDK `MockTransport`; no external provider call or network request was made.
- Coverage includes the real SDK `APITimeoutError` and `APIConnectionError` classes, an HTTP 503 then 200 response with exactly two mock transport requests, fail-fast mapping error payloads, response attempt/shape metadata, positive retry validation, SDK retry disabling, tool-call contract validation, and truncated/incomplete response handling.
- This focused runtime check did not execute the diagnostic CLI; its separate offline execution is recorded in `mechanism_diagnostics.log`.
