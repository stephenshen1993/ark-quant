import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ui_smoke.py"


class TestUiSmokeScript(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.exists())
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_help_documents_the_single_browser_gate_entrypoint(self):
        result = subprocess.run(
            [str(SCRIPT), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("--viewport", result.stdout)
        self.assertIn("--base-url", result.stdout)
        self.assertIn("--output-dir", result.stdout)
        self.assertIn("AIHOT", result.stdout)
        self.assertIn("{all,wide,desktop,narrow}", result.stdout)

    def test_failed_browser_gate_writes_complete_diagnostic_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_cli = temp_path / "playwright-cli"
            fake_cli.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import sys

                    if "run-code" in sys.argv:
                        print(json.dumps({
                            "ok": False,
                            "viewport": "wide",
                            "error": "visual drift",
                            "differences": [
                                {"metric": "shellLeft", "expected": 429, "actual": 421}
                            ],
                            "bodyText": "visible page text",
                            "consoleErrors": ["console exploded"],
                            "pageErrors": ["page exploded"],
                            "failureScreenshot": "wide-failure.png"
                        }))
                    """
                ),
                encoding="utf-8",
            )
            fake_cli.chmod(0o755)
            output_dir = temp_path / "artifacts"
            env = {**os.environ, "PATH": f"{temp_path}:{os.environ.get('PATH', '')}"}

            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--base-url",
                    "http://browser-boundary.invalid",
                    "--viewport",
                    "wide",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 1)
            report = json.loads((output_dir / "wide-failure.json").read_text(encoding="utf-8"))
            self.assertEqual(report["error"], "visual drift")
            self.assertEqual(
                report["differences"],
                [{"metric": "shellLeft", "expected": 429, "actual": 421}],
            )
            self.assertEqual(report["consoleErrors"], ["console exploded"])
            self.assertEqual(report["pageErrors"], ["page exploded"])
            self.assertEqual(report["bodyText"], "visible page text")
            self.assertEqual(report["failureScreenshot"], "wide-failure.png")
            self.assertEqual(
                (output_dir / "wide-failure.txt").read_text(encoding="utf-8"),
                "visible page text",
            )

    def test_browser_cli_failure_still_writes_structured_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_cli = temp_path / "playwright-cli"
            fake_cli.write_text(
                "#!/bin/sh\necho 'browser command failed' >&2\nexit 3\n",
                encoding="utf-8",
            )
            fake_cli.chmod(0o755)
            output_dir = temp_path / "artifacts"
            env = {**os.environ, "PATH": f"{temp_path}:{os.environ.get('PATH', '')}"}

            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--base-url",
                    "http://browser-boundary.invalid",
                    "--viewport",
                    "wide",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 1)
            report = json.loads((output_dir / "wide-failure.json").read_text(encoding="utf-8"))
            self.assertEqual(report["check"], "browser.cli")
            self.assertIn("browser command failed", report["error"])
            self.assertGreaterEqual(len(report["differences"]), 1)
            self.assertEqual(report["differences"][0]["expected"], "exit code 0")
            self.assertTrue((output_dir / "wide-failure.txt").exists())


if __name__ == "__main__":
    unittest.main()
