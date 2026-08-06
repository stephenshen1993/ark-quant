import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
ARCHITECTURE = ROOT / "docs" / "工程" / "系统架构.md"
RUNBOOK = ROOT / "docs" / "工程" / "视觉回归闸门.md"


class TestUiQualityGateDocs(unittest.TestCase):
    def test_readme_documents_aihot_visual_regression_gate(self):
        text = README.read_text(encoding="utf-8")

        self.assertIn("AIHOT 视觉回归闸门", text)
        self.assertIn("scripts/ui_smoke.py", text)
        self.assertIn("唯一最高层真实浏览器视觉回归闸门", text)
        self.assertIn("1512 × 749", text)
        self.assertIn("1440 × 1000", text)
        self.assertIn("390 × 900", text)
        self.assertIn("Web 视觉回归闸门", text)
        self.assertIn("outputs/ui-smoke/", text)

    def test_architecture_records_visual_gate_scope(self):
        text = ARCHITECTURE.read_text(encoding="utf-8")

        self.assertIn("AIHOT 视觉回归闸门", text)
        self.assertIn("账户页、计划页、更新日志页", text)
        self.assertIn("宽屏、桌面和窄屏", text)
        self.assertIn("布局尺寸、横向溢出", text)

    def test_visual_gate_runbook_records_baseline_artifacts_and_update_rule(self):
        text = RUNBOOK.read_text(encoding="utf-8")

        self.assertIn("1512 × 749", text)
        self.assertIn("1440 × 1000", text)
        self.assertIn("390 × 900", text)
        self.assertIn("scripts/ui_smoke.py", text)
        self.assertIn("<视口>-account.png", text)
        self.assertIn("<视口>-plan.png", text)
        self.assertIn("<视口>-changelog.png", text)
        self.assertIn("<视口>-failure.json", text)
        self.assertIn("<视口>-failure.txt", text)
        self.assertIn("server.log", text)
        self.assertIn("基准更新规则", text)
        self.assertIn("不得仅为让测试通过而放宽", text)


if __name__ == "__main__":
    unittest.main()
