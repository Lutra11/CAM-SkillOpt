"""Checkpointable drift protection, counting each training step at most once."""
from __future__ import annotations

import math


class DriftGuard:
    def __init__(self, patience=2, tolerance=0.0, state=None):
        if isinstance(patience, bool) or not isinstance(patience, int) or patience < 0:
            raise ValueError("CAM drift patience must be a nonnegative integer (0 disables it)")
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError("CAM drift tolerance must be finite and nonnegative")
        self.patience, self.tolerance = patience, tolerance
        self.state = {"streak": 0, "rollbacks": 0, "last_step": -1, "step_base_streak": 0}
        if state:
            for key in self.state:
                value = state.get(key, self.state[key])
                if isinstance(value, bool) or not isinstance(value, int) or value < (-1 if key == "last_step" else 0):
                    raise ValueError(f"Invalid drift checkpoint field: {key}")
                self.state[key] = value

    def observe(self, current_score, best_score, step):
        if step < self.state["last_step"]:
            raise ValueError("Drift observations cannot move backwards")
        if self.state["last_step"] != step:
            self.state["step_base_streak"] = self.state["streak"]
            self.state["last_step"] = step
        trailing = current_score < best_score - self.tolerance
        self.state["streak"] = self.state["step_base_streak"] + 1 if trailing else 0
        rollback = bool(self.patience and self.state["streak"] >= self.patience)
        if rollback:
            self.state["rollbacks"] += 1
            self.state["streak"] = self.state["step_base_streak"] = 0
        return {**self.state, "patience": self.patience, "tolerance": self.tolerance,
                "trailing": trailing, "rolled_back": rollback}
