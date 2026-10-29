# CAM v3r1 offline freeze acceptance

Freeze SHA-256: `c1d435957f1132f6e897515a6a2c9b249463b3651d7ca84838b673740dc35319`

The r1 freeze covers 1445 source/data files, including all 21 recursive prompt markdown files. Preparation succeeded twice with the same SHA-256; `--verify` passed. No runtime queue or auth preflight was created.

Offline regression passed 140 unique unittest IDs after skipping 20 duplicate IDs. The test_types.py assertion methods also passed separately (24); its requested interpreter lacks pytest, so the unused import was removed only from an in-memory AST for this compatibility run.

Preservation checks matched all 1378 old source snapshot hashes against the old freeze and all 225 registered prechange backup hashes. The 21 prechange prompts match the old prompt observation byte-for-byte; current live prompt changes are limited to slow_update.md and meta_skill.md. cam-20 remains PAUSED.

The separate mechanism diagnosis read 8 training steps, made zero API calls, and did not read held-out predictions.

Detailed logs and machine-readable checks are listed in `freeze_acceptance.json`.

Checks: all passed.
