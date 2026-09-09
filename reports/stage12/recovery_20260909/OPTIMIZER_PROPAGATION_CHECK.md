# Optimizer infrastructure-error propagation check

Date: 2026-09-09. Offline test; no credentials or external model calls.

Optimizer model-call exception handlers previously allowed merge/ranking fallbacks, empty rewrites or absent meta/slow updates after a model infrastructure error. The affected handlers now re-raise typed InfraError and classify legacy RuntimeError / ConnectionError / TimeoutError before using any ordinary-content fallback.

Changed runtime files under SkillOpt/skillopt:

- gradient/aggregate.py (hierarchical batch merge and final merge)
- optimizer/clip.py (ranking)
- optimizer/lr_autonomous.py (budget choice)
- optimizer/meta_skill.py (optimizer memory update)
- optimizer/rewrite.py (full skill rewrite)
- optimizer/skill_aware.py (appendix consolidation)
- optimizer/slow_update.py (epoch slow update)

optimizer/skill.py was inspected and left unchanged: its catch surrounds local edit application, not a model request. The JSON-file-only trajectory parser catch in slow_update.py was also preserved.

Validation:

- Python compileall completed successfully for all eight inspected modules and the new test file.
- `python -m unittest tests.test_optimizer_infra_propagation -v`: 3 test methods passed.
- The tests exercise eight call paths: each propagates synthetic InfraError, legacy 401 RuntimeError, ConnectionError and TimeoutError after exactly one mocked optimizer invocation (32 fault cases).
- The same eight paths accept ordinary answer text mentioning 401 without raising infrastructure errors (8 cases).
- Parser ValueError mentioning character 401 preserves the pre-existing content fallback rather than creating a false authentication diagnosis (8 cases).

The 48 mocked cases establish local exception propagation behavior. They do not establish live authentication, network availability or end-to-end experiment success. The parent task integrates these modules with its model wrapper, trainer and live preflight.
