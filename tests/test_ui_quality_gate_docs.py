import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
ARCHITECTURE = ROOT / "docs" / "工程" / "系统架构.md"


class TestUiQualityGateDocs(unittest.TestCase):
    def test_readme_documents_aihot_visual_regression_gate(self):
        text = README.read_text(encoding="utf-8")

        self.assertIn("AIHOT 视觉回归闸门", text)
        self.assertIn("scripts/ui_smoke.py", text)
        self.assertIn("账户、计划、更新日志", text)
        self.assertIn("wide、desktop、narrow", text)
        self.assertIn("outputs/ui-smoke/", text)

    def test_architecture_records_visual_gate_scope(self):
        text = ARCHITECTURE.read_text(encoding="utf-8")

        self.assertIn("AIHOT 视觉回归闸门", text)
        self.assertIn("账户页、计划页、更新日志页", text)
        self.assertIn("宽屏、桌面和窄屏", text)
        self.assertIn("布局尺寸、横向溢出", text)


if __name__ == "__main__":
    unittest.main()
