import json
import re
import unittest
from datetime import datetime
from pathlib import Path


CHANGELOG_JSON = Path(__file__).resolve().parents[1] / "app" / "static" / "changelog.json"
CHANGELOG_DOC = Path(__file__).resolve().parents[1] / "docs" / "agents" / "changelog.md"
INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


class TestChangelogData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = json.loads(CHANGELOG_JSON.read_text(encoding="utf-8"))
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_changelog_is_a_static_visible_field_list(self):
        self.assertIsInstance(self.entries, list)
        self.assertGreaterEqual(len(self.entries), 12)
        required_keys = {"date", "time", "type", "title", "body"}
        allowed_keys = {*required_keys, "scopes"}
        allowed_types = {"更新", "优化", "公告", "下线"}
        allowed_scopes = {"今日状态", "账户", "账户事实", "计划生成", "视觉系统"}

        for entry in self.entries:
            self.assertTrue(required_keys.issubset(entry))
            self.assertLessEqual(set(entry), allowed_keys)
            self.assertIn(entry["type"], allowed_types)
            self.assertIsInstance(entry["title"], str)
            self.assertIsInstance(entry["body"], str)
            self.assertGreater(len(entry["title"].strip()), 0)
            self.assertGreater(len(entry["body"].strip()), 0)
            if "scopes" in entry:
                self.assertIsInstance(entry["scopes"], list)
                self.assertGreater(len(entry["scopes"]), 0)
                self.assertLessEqual(set(entry["scopes"]), allowed_scopes)

    def test_changelog_dates_and_times_are_valid_and_descending(self):
        moments = []
        for entry in self.entries:
            moment = datetime.strptime(f"{entry['date']} {entry['time']}", "%Y-%m-%d %H:%M")
            moments.append(moment)

        self.assertEqual(moments, sorted(moments, reverse=True))
        self.assertEqual(self.entries[0]["date"], "2026-08-08")
        self.assertLessEqual(self.entries[-1]["date"], "2026-06-29")

    def test_changelog_does_not_leak_engineering_tracker_metadata(self):
        text = json.dumps(self.entries, ensure_ascii=False)
        forbidden_literals = [
            "issue",
            "Issue",
            "PR",
            "pull request",
            "commit",
            "hash",
            "影响范围",
            "内部模块",
            "验证命令",
            "维护者备注",
            "ruff",
            "unittest",
            "pytest",
        ]
        for literal in forbidden_literals:
            self.assertNotIn(literal, text)

        self.assertIsNone(re.search(r"#\d+", text))
        self.assertIsNone(re.search(r"\b[0-9a-f]{7,40}\b", text))

    def test_changelog_maintenance_rule_is_documented(self):
        text = CHANGELOG_DOC.read_text(encoding="utf-8")

        self.assertIn("更新日志维护规则", text)
        self.assertIn("日期分组、时分、类型、标题、正文和影响范围标签", text)
        self.assertIn("更新”“优化”“公告”“下线", text)
        self.assertIn("静态 JSON", text)
        self.assertIn("git log", text)
        self.assertIn("不进入 SQLite", text)
        self.assertIn("不得出现追溯编号", text)

    def test_changelog_keeps_editorial_semantics_and_inline_retry(self):
        self.assertEqual(self.html.count('class="changelog-title"'), 1)
        self.assertIn('class="changelog-date-title"', self.html)
        self.assertIn('class="changelog-entry-grid"', self.html)
        self.assertIn('class="changelog-entry-title"', self.html)
        self.assertIn("SYSTEM / CHANGELOG", self.html)
        self.assertIn('@click="load()"', self.html)
        self.assertIn("this.loadError = ''", self.html)
        self.assertNotIn("暂无更新", self.html)

    def test_scope_tags_only_use_explicit_changelog_metadata(self):
        self.assertIn("scopeTags(entry)", self.html)
        start = self.html.index("scopeTags(entry) {")
        end = self.html.index("}", start)
        body = self.html[start:end]
        self.assertIn("Array.isArray(entry.scopes) ? entry.scopes : []", body)
        self.assertNotIn("/计划|调拨|订单|榜单/", body)
        self.assertNotIn("/账户|事实|持仓|余额/", body)


if __name__ == "__main__":
    unittest.main()
