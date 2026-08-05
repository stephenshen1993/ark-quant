import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ui_smoke.py"


class TestUiSmokeScript(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.exists())
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_help_documents_browser_smoke_entrypoint(self):
        result = subprocess.run(
            [str(SCRIPT), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("--viewport", result.stdout)
        self.assertIn("--base-url", result.stdout)
        self.assertIn("--output-dir", result.stdout)

    def test_script_contract_keeps_smoke_isolated_and_diagnostic(self):
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("ARK_QUANT_DB_PATH", text)
        self.assertIn("playwright-cli", text)
        self.assertIn("outputs", text)
        self.assertIn("ui-smoke", text)
        self.assertIn("wide", text)
        self.assertIn("desktop", text)
        self.assertIn("narrow", text)
        self.assertIn("consoleErrors", text)
        self.assertIn("page.screenshot", text)
        self.assertIn("accountScreenshot", text)
        self.assertIn("planScreenshot", text)
        self.assertIn("failureScreenshot", text)
        self.assertIn("failureText", text)
        self.assertIn("server.log", text)
        self.assertIn("page.route('**/favicon.ico'", text)
        self.assertIn("assertVisibleText", text)
        self.assertIn("fundingBasisRows()", text)
        self.assertIn("hierarchyChecks", text)
        self.assertIn("top-A-stock", text)
        self.assertIn("expectedDomLabels", text)
        self.assertIn("主动组合下属账户没有以子行缩进展示", text)
        self.assertIn("账户事实日", text)
        self.assertIn("展开查看计算依据", text)
        self.assertIn("更新日志", text)
        self.assertIn("最近发生了什么", text)
        self.assertIn("changelogChecks", text)


if __name__ == "__main__":
    unittest.main()
