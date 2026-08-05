import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class TestAppScripts(unittest.TestCase):
    def test_service_scripts_exist_and_are_executable(self):
        for name in ["start_app.sh", "stop_app.sh", "status_app.sh"]:
            path = SCRIPTS / name
            self.assertTrue(path.exists(), name)
            self.assertTrue(os.access(path, os.X_OK), name)

    def test_start_script_uses_launchctl_and_project_runtime(self):
        text = (SCRIPTS / "start_app.sh").read_text(encoding="utf-8")
        self.assertIn("ark_quant_server", text)
        self.assertIn("launchctl submit", text)
        self.assertIn('.venv/bin/python"', text)
        self.assertIn("run_app.py", text)
        self.assertIn('HOST="127.0.0.1"', text)
        self.assertIn('PORT="8000"', text)
        self.assertIn("logs/ark_quant_server.log", text)
        self.assertIn("logs/ark_quant_server.pid", text)

    def test_stop_and_status_scripts_use_the_same_label_and_port(self):
        for name in ["stop_app.sh", "status_app.sh"]:
            text = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIn("ark_quant_server", text)
            self.assertIn("codex.arkquant", text)
            self.assertIn("8000", text)


if __name__ == "__main__":
    unittest.main()
