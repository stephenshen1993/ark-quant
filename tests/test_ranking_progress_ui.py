import shutil
import subprocess
import unittest
from pathlib import Path


class TestRankingProgressUi(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for browser state tests')
    def test_polling_recovers_after_reload_and_loads_completed_ranking(self):
        html = Path('app/static/index.html').read_text()
        start = html.index('  function rankingsWorkspace()')
        end = html.index('\n  function ', start + 10)
        script = html[start:end] + r'''
const assert = require('node:assert/strict');
(async () => {
  const page = rankingsWorkspace();
  let state = {status: 'running', stage: '补齐正股历史行情', completed: 2, total: 5, elapsed_seconds: 8};
  global.fetch = async () => ({ok: true, json: async () => state});
  let loaded = 0;
  page.loadDates = async () => {};
  page.loadRanking = async () => {loaded++};
  await page.refreshProgress();
  assert.equal(page.running.cb, true);
  assert.match(page.progressText('cb'), /2\/5.*8 秒/);
  state = {status: 'complete', data_date: '2026-10-12'};
  await page.refreshProgress();
  assert.equal(page.running.cb, false);
  assert.equal(page.selectedDateByStrategy.cb, '2026-10-12');
  assert.equal(loaded, 2);
  await page.refreshProgress();
  assert.equal(loaded, 2); // completed polling does not continually reload rows
  state = {status: 'failed', error: '行情请求超时'};
  await page.refreshProgress();
  assert.equal(page.componentErrors.cb, '行情请求超时');
  global.fetch = async () => {throw new Error('offline')};
  await page.refreshProgress();
  assert.ok(page.progressError);
  assert.equal(page.polling, false);
  page.destroy();
  global.fetch = async () => {throw new Error('must not poll after destroy')};
  await page.refreshProgress();
})().catch(e => {console.error(e); process.exit(1)});
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
