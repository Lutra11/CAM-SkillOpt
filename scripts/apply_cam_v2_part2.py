"""CAM-v2 patch set #2: drift guard + inconclusive memory.

Applied to the main checkout after v1 was discarded (2026-10-18, user decision).
Soft-accept already lets an inconclusive candidate become `current`; these two
changes stop that from becoming a liability:

1. **Drift guard** — if `current` stays below `best` for `patience` consecutive
   steps, revert `current` to `best`. Soft acceptance must not walk the run into
   a worse state that `best` no longer describes.
2. **Inconclusive memory** — v1 only wrote memory on `reject`; measured on v1,
   the glm arm had 0 rejects so the memory mechanism was never exercised. v2
   also records soft-accepted (`inconclusive`) candidates with a `kind` tag and
   ranks them slightly below true rejects.

Every anchor is asserted; nothing is written unless all edits match.
"""
from __future__ import annotations

import io
import sys

ROOT = r"E:\Programs\CAM\CAM-SkillOpt"
TRAINER = ROOT + r"\skillopt\engine\trainer.py"
MEMORY = ROOT + r"\skillopt\cam\rejected_memory.py"

TRAINER_EDITS = [
    # --- drift guard state: declared next to the other nonlocal counters ------
    (
        "        def _cam_decide(candidate, directory, *, origin, reference_skill=None):",
        "        # v2 drift guard: how many consecutive steps `current` has trailed `best`.\n"
        "        drift_streak = 0\n"
        "        drift_rollbacks = 0\n"
        "\n"
        "        def _cam_decide(candidate, directory, *, origin, reference_skill=None):",
        "drift counters",
    ),
    # --- apply drift guard right after the gate result is written back --------
    (
        "                if gate.action == \"accept_new_best\" or (\n"
        "                    gate.action == \"force_accept\" and best_step == global_step\n"
        "                ):\n"
        "                    best_origin = current_origin\n",
        "                if gate.action == \"accept_new_best\" or (\n"
        "                    gate.action == \"force_accept\" and best_step == global_step\n"
        "                ):\n"
        "                    best_origin = current_origin\n"
        "\n"
        "                # v2 drift guard ------------------------------------------------\n"
        "                drift_tol = float(cfg.get(\"cam_drift_tolerance\", 0.0) or 0.0)\n"
        "                drift_patience = int(cfg.get(\"cam_drift_patience\", 2) or 2)\n"
        "                if best_step > 0 and current_score < best_score - drift_tol:\n"
        "                    drift_streak += 1\n"
        "                else:\n"
        "                    drift_streak = 0\n"
        "                if drift_patience > 0 and best_step > 0 and drift_streak >= drift_patience:\n"
        "                    print(\n"
        "                        f\"    [drift guard] current={current_score:.4f} trailed best={best_score:.4f} \"\n"
        "                        f\"for {drift_streak} steps -> reverting current to best\"\n"
        "                    )\n"
        "                    current_skill, current_score = best_skill, best_score\n"
        "                    current_origin = best_origin or current_origin\n"
        "                    drift_streak = 0\n"
        "                    drift_rollbacks += 1\n"
        "                step_rec[\"drift_guard\"] = {\"streak\": drift_streak,\n"
        "                                           \"rollbacks\": drift_rollbacks,\n"
        "                                           \"patience\": drift_patience}\n",
        "drift guard application",
    ),
    # --- memory: also record soft-accepted (inconclusive) candidates ---------
    (
        "                if \"reject\" in action and ranked_patch:\n",
        "                if (\"reject\" in action or action == \"soft_accept\") and ranked_patch:\n",
        "memory gate condition",
    ),
    (
        "                            epoch=epoch,\n"
        "                            step=global_step,\n"
        "                        )\n",
        "                            epoch=epoch,\n"
        "                            step=global_step,\n"
        "                            kind=\"rejected\" if \"reject\" in action else \"inconclusive\",\n"
        "                        )\n",
        "memory kind argument",
    ),
    (
        '                            json.dump({"stage": "rejected_update", "epoch": epoch,\n',
        '                            json.dump({"stage": "rejected_update", "epoch": epoch,\n'
        '                                       "kind": "rejected" if "reject" in action else "inconclusive",\n',
        "memory side-car kind",
    ),
]

MEMORY_EDITS = [
    (
        "    epoch: int\n    step: int\n",
        "    epoch: int\n    step: int\n    kind: str = \"rejected\"\n",
        "item kind field",
    ),
    (
        "        task_type: str,\n        epoch: int,\n        step: int,\n    ) -> list[RejectedEditMemoryItem]:\n"
        "        \"\"\"Record each rejected edit against the observed failure evidence.\"\"\"\n",
        "        task_type: str,\n        epoch: int,\n        step: int,\n        kind: str = \"rejected\",\n"
        "    ) -> list[RejectedEditMemoryItem]:\n"
        "        \"\"\"Record an edit against the observed failure evidence.\n\n"
        "        `kind` is `rejected` for a scalar rejection or `inconclusive` for a\n"
        "        paired-gate soft acceptance (v2): the edit was neither proven better\n"
        "        nor proven worse, which is still useful negative evidence.\n"
        "        \"\"\"\n",
        "add_rejected_step kind parameter",
    ),
    (
        "                epoch=int(epoch),\n                step=int(step),\n            )\n",
        "                epoch=int(epoch),\n                step=int(step),\n                kind=str(kind),\n            )\n",
        "item construction kind",
    ),
    (
        "        return sorted(\n"
        "            retrieved,\n"
        "            key=lambda result: (result.retrieval_score, result.similarity, result.item.memory_id),\n"
        "            reverse=True,\n"
        "        )[:top_k]\n",
        "        # v2: soft-accepted (inconclusive) edits are weaker negative evidence than\n"
        "        # true rejects, so they rank slightly lower at equal similarity.\n"
        "        return sorted(\n"
        "            retrieved,\n"
        "            key=lambda result: (\n"
        "                result.retrieval_score\n"
        "                * (1.0 if getattr(result.item, \"kind\", \"rejected\") == \"rejected\" else 0.9),\n"
        "                result.similarity,\n"
        "                result.item.memory_id,\n"
        "            ),\n"
        "            reverse=True,\n"
        "        )[:top_k]\n",
        "retrieval penalty",
    ),
]


def apply(path: str, edits) -> None:
    text = io.open(path, encoding="utf-8").read()
    out = text
    for old, new, label in edits:
        if label == "__noop__":
            continue
        if old not in out:
            raise SystemExit(f"anchor missing in {path}: {label}")
        out = out.replace(old, new, 1)
        print(f"ok  {label}")
    io.open(path, "w", encoding="utf-8", newline="\n").write(out)


def main() -> int:
    apply(TRAINER, TRAINER_EDITS)
    apply(MEMORY, MEMORY_EDITS)
    print("patched trainer.py + rejected_memory.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
