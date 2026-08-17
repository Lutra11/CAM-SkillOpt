"""Cross-plugin parity tests — ensure all plugins document the same features.

Run: python3 -m pytest tests/test_plugin_sync.py -v
"""
import os
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

PLUGIN_SKILL_MDS = {
    "claude-code": os.path.join(REPO, "plugins/claude-code/skills/skillopt-sleep/SKILL.md"),
    "codex": os.path.join(REPO, "plugins/codex/skills/skillopt-sleep/SKILL.md"),
    "openclaw": os.path.join(REPO, "plugins/openclaw/SKILL.md"),
}

MCP_SERVER = os.path.join(REPO, "plugins/copilot/mcp_server.py")
COPILOT_INSTRUCTIONS = os.path.join(REPO, "plugins/copilot/copilot-instructions.snippet.md")

CANONICAL_BACKENDS = {"mock", "claude", "codex", "copilot"}


def _read(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestPluginParity(unittest.TestCase):
    def test_all_skill_mds_mention_all_backends(self):
        for name, path in PLUGIN_SKILL_MDS.items():
            text = _read(path)
            if not text:
                self.skipTest(f"{name} SKILL.md not found")
            for backend in CANONICAL_BACKENDS:
                self.assertIn(backend, text,
                              f"{name}/SKILL.md missing backend '{backend}'")

    def test_all_skill_mds_mention_schedule(self):
        for name, path in PLUGIN_SKILL_MDS.items():
            text = _read(path)
            if not text:
                continue
            self.assertIn("schedule", text.lower(),
                          f"{name}/SKILL.md missing 'schedule'")
            self.assertIn("unschedule", text.lower(),
                          f"{name}/SKILL.md missing 'unschedule'")

    def test_copilot_instructions_mention_schedule(self):
        text = _read(COPILOT_INSTRUCTIONS)
        self.assertIn("sleep_schedule", text)
        self.assertIn("sleep_unschedule", text)

    def test_copilot_instructions_mention_all_backends(self):
        text = _read(COPILOT_INSTRUCTIONS)
        for backend in CANONICAL_BACKENDS:
            self.assertIn(backend, text,
                          f"copilot-instructions missing backend '{backend}'")

    def test_mcp_server_has_schedule_tools(self):
        text = _read(MCP_SERVER)
        self.assertIn("sleep_schedule", text)
        self.assertIn("sleep_unschedule", text)

    def test_mcp_schema_has_key_params(self):
        text = _read(MCP_SERVER)
        for param in ["source", "tasks_file", "target_skill_path",
                       "max_sessions", "max_tasks", "auto_adopt", "json"]:
            self.assertIn(f'"{param}"', text,
                          f"MCP schema missing param '{param}'")

    def test_all_skill_mds_mention_memory_consolidation(self):
        for name, path in PLUGIN_SKILL_MDS.items():
            text = _read(path).lower()
            if not text:
                continue
            has_mention = (
                "memory consolidation" in text
                or "evolve_memory" in text
                or ("consolidate" in text and "memory" in text)
            )
            self.assertTrue(has_mention,
                            f"{name}/SKILL.md missing memory consolidation docs")


if __name__ == "__main__":
    unittest.main()
