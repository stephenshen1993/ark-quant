import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ui_smoke.py"
GATE_DOC = ROOT / "docs" / "工程" / "视觉回归闸门.md"


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
        self.assertIn("方舟计划", result.stdout)
        self.assertIn("{all,wide,desktop,narrow}", result.stdout)

    def test_gate_locks_the_selected_shell_geometry_and_three_pages(self):
        source = SCRIPT.read_text(encoding="utf-8")
        documentation = GATE_DOC.read_text(encoding="utf-8")

        for literal in [
            "DESKTOP_NAV_WIDTH = 220",
            "WORKBENCH_WIDTH = 1140",
            "READING_WIDTH = 880",
            "NARROW_NAV_HEIGHT = 64",
            "NARROW_GUTTER = 18",
            '"wide": {"width": 1512, "height": 749}',
            '"desktop": {"width": 1440, "height": 1000}',
            '"narrow": {"width": 390, "height": 900}',
            "今日计划",
            "账户事实就绪度",
            "更新日志内容",
            "planScenarioChecks",
            "待补充账户事实",
            "正在生成完整计划",
            "今日无需操作",
            "今日需要操作",
            "计划已失效",
            "计划生成失败",
            "scenario-no-action",
            "scenario-future-funding",
            "scenario-running",
            "scenario-stale",
            "scenario-failed",
            "readiness-error",
            "无法确认账户事实",
            "generateDisabled",
            "retryReadiness",
            "readinessRetry",
            "focusAccountId",
            "accountFocus",
            "executionBadgeVisible",
            "orderEntryNarrowChecks",
            "plan.orderEntries.narrow",
            "noActionConclusion",
            "完整计划已检查",
            "scenario.readiness.plan_date",
            "事实窗口",
            "scenario.readiness.input_window.start",
            "scenario.readiness.input_window.end",
            "['动作', '证券代码与名称', '数量', '参考价', '估算金额']",
            "accountFactScenarioChecks",
            "summary-http-error",
            "positions-http-error",
            "quotes-http-error",
            "quotes-network-error",
            "empty-account",
            "重新读取账户事实",
            "maintenanceVisibleDuringFailure",
            "unexpectedConsoleErrors",
            "accountFactDateIdentityChecks",
            "date-switch-dirty-gate",
            "date-switch-loading-gate",
            "date-switch-error-gate",
            "save-in-progress.duplicateRequestCount",
            "save-error-preserves-input",
            "save-success-refresh.readiness",
            "old fact rows",
            "savedPayloads",
        ]:
            self.assertIn(literal, source)

        for literal in ["220px", "1140px", "880px", "64px", "18px", "2px"]:
            self.assertIn(literal, documentation)

    def test_readiness_error_consumes_only_its_settled_expected_console_error(self):
        source = SCRIPT.read_text(encoding="utf-8")
        scenario_start = source.index("const planScenarioChecks")
        retry = source.index("if (scenario.retryReadiness)", scenario_start)
        consume = source.index("const controlledReadinessConsoleErrors", scenario_start)

        self.assertLess(retry, consume)
        self.assertIn("await page.waitForTimeout(100)", source[retry:consume])
        self.assertIn("consoleErrors.splice(consoleErrorStart)", source[consume:consume + 700])
        self.assertIn("expectedNetworkMessage", source[consume:consume + 700])

    def test_gate_checks_the_execution_plan_hierarchy_and_expanded_trade_rows(self):
        source = SCRIPT.read_text(encoding="utf-8")

        for literal in [
            ".plan-execution-dossier",
            ".plan-funding-plan",
            ".plan-account-card",
            ".plan-cash-equation",
            ".plan-trade-row",
            "fundingBeforeAccounts",
            "accountsCollapsedByDefault",
            "expandedAccountPlanChecks",
            "计划后预计资金余额",
            "['动作', '证券代码与名称', '数量', '参考价', '估算金额']",
            "noHorizontalOverflow",
        ]:
            self.assertIn(literal, source)

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
