#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PYTHON = ROOT / ".venv" / "bin" / "python"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "ui-smoke"
VIEWPORTS = {
    "wide": {"width": 2048, "height": 1178},
    "desktop": {"width": 1440, "height": 1000},
    "narrow": {"width": 390, "height": 900},
}


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    session = f"ark-ui-smoke-{uuid.uuid4().hex[:8]}"
    expected_plan_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    server = None
    server_log = None
    temp_dir = None

    try:
        if args.base_url:
            base_url = args.base_url.rstrip("/")
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="ark-ui-smoke-")
            db_path = Path(temp_dir.name) / "ark_quant_ui_smoke.db"
            seed_database(db_path, expected_plan_date)
            port = find_free_port()
            base_url = f"http://127.0.0.1:{port}"
            server_log = (output_dir / "server.log").open("w", encoding="utf-8")
            server = start_server(db_path, port, server_log)
            wait_for_health(base_url, output_dir / "server.log")

        results = []
        for name in selected_viewports(args.viewport):
            results.append(run_viewport(session, name, base_url, output_dir, expected_plan_date))

        print(json.dumps({"base_url": base_url, "results": results}, ensure_ascii=False, indent=2))
        return 0
    except SmokeFailure as exc:
        print(f"UI smoke failed: {exc}", file=sys.stderr)
        return 1
    finally:
        close_browser(session)
        if server is not None:
            stop_server(server)
        if server_log is not None:
            server_log.close()
        if temp_dir is not None:
            temp_dir.cleanup()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real-browser smoke checks for ark-quant account, plan and changelog pages.",
    )
    parser.add_argument(
        "--viewport",
        choices=("all", *VIEWPORTS.keys()),
        default="all",
        help="Viewport to run. Default: all.",
    )
    parser.add_argument(
        "--base-url",
        help="Use an already running app instead of starting an isolated local server.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for screenshots and diagnostic artifacts.",
    )
    return parser.parse_args()


def selected_viewports(value: str) -> list[str]:
    if value == "all":
        return list(VIEWPORTS)
    return [value]


def seed_database(db_path: Path, plan_date: str) -> None:
    os.environ["ARK_QUANT_DB_PATH"] = str(db_path)

    import pandas as pd

    from datasource import db
    from datasource.youzhiyouxing import DATA_URL

    data_date = date.fromisoformat(plan_date)
    db.init_db()
    with db._conn() as conn:
        conn.execute(
            """INSERT INTO market_temperatures
               (temperature,label,source_updated_at,source,fetched_at)
               VALUES (45.0,'正常',?,?,?)""",
            (f"{plan_date}T15:00", DATA_URL, f"{plan_date}T15:30:00"),
        )
    db.insert_account_context(
        plan_date,
        45.0,
        check_type="quarterly",
        b_purchase_status="unavailable",
    )
    db.insert_account_value_snapshot("stock", plan_date, 316450.39, 12000, 500)
    db.insert_account_value_snapshot("cb", plan_date, 182325.35, 8000, 0)
    db.insert_account_value_snapshot("cash", plan_date, 32078.24)
    db.insert_account_value_snapshot("overseas", plan_date, 93468.85)
    db.insert_account_value_snapshot("changqian", plan_date, 115632.73)
    db.append_position_snapshot("stock", plan_date, [])
    db.append_position_snapshot("cb", plan_date, [])

    cb_run_id = db.insert_strategy_run("cb", data_date)
    db.insert_cb_rankings(
        cb_run_id,
        pd.DataFrame([
            {
                "bond_code": "113062",
                "bond_name": "常银转债",
                "cb_price": 126.80,
                "premium_rate": 10.0,
                "double_low": 136.8,
                "score": 0.9,
            }
        ]),
    )
    stock_run_id = db.insert_strategy_run("stock", data_date)
    db.insert_stock_rankings(
        stock_run_id,
        pd.DataFrame([
            {
                "rank": 1,
                "stock_code": "600051",
                "stock_name": "宁波联合",
                "total_mv_yuan": 1_000_000_000,
                "pe_ttm": 10.0,
                "roe_pct": 12.0,
            }
        ]),
    )


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(db_path: Path, port: int, log_file) -> subprocess.Popen:
    env = {**os.environ, "ARK_QUANT_DB_PATH": str(db_path)}
    return subprocess.Popen(
        [
            str(PYTHON if PYTHON.exists() else sys.executable),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=log_file,
        stderr=log_file,
        text=True,
    )


def wait_for_health(base_url: str, log_path: Path) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise SmokeFailure(f"app did not become healthy at {base_url}/health; log={log_path}")


def run_viewport(
    session: str,
    viewport_name: str,
    base_url: str,
    output_dir: Path,
    expected_plan_date: str,
) -> dict:
    if shutil.which("playwright-cli") is None:
        raise SmokeFailure("playwright-cli not found; install it before running browser smoke checks")

    viewport = VIEWPORTS[viewport_name]
    account_screenshot_path = output_dir / f"{viewport_name}-account.png"
    plan_screenshot_path = output_dir / f"{viewport_name}-plan.png"
    screenshot_path = output_dir / f"{viewport_name}-changelog.png"
    failure_screenshot_path = output_dir / f"{viewport_name}-failure.png"
    failure_text_path = output_dir / f"{viewport_name}-failure.txt"
    code = browser_check_code(
        base_url,
        viewport_name,
        viewport,
        account_screenshot_path,
        plan_screenshot_path,
        screenshot_path,
        failure_screenshot_path,
        failure_text_path,
        expected_plan_date,
    )
    run_cli(session, ["open", "about:blank"])
    raw = run_cli(session, ["--raw", "run-code", code])
    result = parse_json_result(raw)
    if not result.get("ok"):
        failure_text = Path(result.get("failureText", failure_text_path))
        failure_text.write_text(result.get("bodyText", ""), encoding="utf-8")
        raise SmokeFailure(
            f"{viewport_name} failed: {result.get('error')}; "
            f"screenshot={result.get('failureScreenshot')}; text={result.get('failureText')}"
        )
    if result.get("consoleErrors") or result.get("pageErrors"):
        raise SmokeFailure(
            f"{viewport_name} browser errors: "
            f"{result.get('consoleErrors') or result.get('pageErrors')}"
        )
    return result


def browser_check_code(
    base_url: str,
    viewport_name: str,
    viewport: dict,
    account_screenshot_path: Path,
    plan_screenshot_path: Path,
    screenshot_path: Path,
    failure_screenshot_path: Path,
    failure_text_path: Path,
    expected_plan_date: str,
) -> str:
    account_screenshot = str(account_screenshot_path)
    plan_screenshot = str(plan_screenshot_path)
    screenshot = str(screenshot_path)
    failure_screenshot = str(failure_screenshot_path)
    failure_text = str(failure_text_path)
    return textwrap.dedent(
        f"""
        async page => {{
          const consoleErrors = [];
          const pageErrors = [];
          page.on('console', msg => {{
            if (msg.type() === 'error') consoleErrors.push(msg.text());
          }});
          page.on('pageerror', error => pageErrors.push(String(error)));
          const assertVisibleText = async text => {{
            const ok = await page.evaluate(text => {{
              const visible = element => {{
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && Number(style.opacity || 1) !== 0
                  && rect.width > 0
                  && rect.height > 0;
              }};
              const nodes = Array.from(document.body.querySelectorAll('*'))
                .filter(element => visible(element) && (element.innerText || '').includes(text));
              const leaf = nodes.find(element =>
                !Array.from(element.children).some(child => visible(child) && (child.innerText || '').includes(text))
              ) || nodes[0];
              if (!leaf) return false;
              leaf.scrollIntoView({{ block: 'center', inline: 'nearest' }});
              const rect = leaf.getBoundingClientRect();
              return rect.width > 0
                && rect.height > 0
                && rect.bottom >= 0
                && rect.top <= window.innerHeight
                && rect.right >= 0
                && rect.left <= window.innerWidth;
            }}, text);
            if (!ok) throw new Error('不可见: ' + text);
          }};
          try {{
            await page.route('**/favicon.ico', route => route.fulfill({{ status: 204, body: '' }}));
            await page.setViewportSize({json.dumps(viewport)});
            await page.goto({json.dumps(base_url)}, {{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await page.waitForFunction(() => window.Alpine, {{ timeout: 10000 }});
            await page.waitForFunction(() => document.body.innerText.includes('总资产'), {{ timeout: 10000 }});
            const navItems = await page.evaluate(() => Array.from(document.querySelectorAll('.app-nav button')).map(button => ({{
              label: button.innerText.trim(),
              ariaLabel: button.getAttribute('aria-label'),
              disabled: button.disabled,
              ariaDisabled: button.getAttribute('aria-disabled'),
            }})));
            const navLabels = navItems.map(item => item.label || item.ariaLabel).join('>');
            ['账户', '计划', '关于', '更新日志', '反馈'].forEach(label => {{
              if (!navLabels.includes(label)) throw new Error('侧栏缺少入口: ' + label + '; actual=' + navLabels);
            }});
            if (navLabels.indexOf('账户') > navLabels.indexOf('计划')
                || navLabels.indexOf('计划') > navLabels.indexOf('关于')
                || navLabels.indexOf('关于') > navLabels.indexOf('更新日志')
                || navLabels.indexOf('更新日志') > navLabels.indexOf('反馈')) {{
              throw new Error('侧栏入口顺序不符: ' + navLabels);
            }}
            const aboutItem = navItems.find(item => (item.label || item.ariaLabel).includes('关于'));
            const feedbackItem = navItems.find(item => (item.label || item.ariaLabel).includes('反馈'));
            if (!aboutItem?.disabled || aboutItem.ariaDisabled !== 'true') throw new Error('关于入口不是明确禁用态');
            if (!feedbackItem?.disabled || feedbackItem.ariaDisabled !== 'true') throw new Error('反馈入口不是明确禁用态');
            if (!aboutItem.ariaLabel.includes('暂未开放')) throw new Error('关于入口缺少禁用说明');
            if (!feedbackItem.ariaLabel.includes('暂未开放')) throw new Error('反馈入口缺少禁用说明');

            const accountRequired = [
              '账户事实日',
              '事实日 {expected_plan_date}',
              '广发账户',
              '华泰账户',
              '浦发现金账户',
              'A 组合小市值股票策略承载账户',
              'A 组合多因子可转债策略承载账户',
              'A 组合现金池承载账户',
            ];
            for (const text of accountRequired) await assertVisibleText(text);
            await page.screenshot({{ path: {json.dumps(account_screenshot)}, fullPage: true }});
            await page.goto({json.dumps(base_url + '#changelog')}, {{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await page.waitForFunction(
              () => window.Alpine
                && window.Alpine.store('page') === 'changelog'
                && document.body.innerText.includes('更新日志'),
              {{ timeout: 10000 }}
            );
            await page.getByRole('button', {{ name: /账户/ }}).click();
            await page.waitForFunction(() => window.Alpine.store('page') === 'account', {{ timeout: 10000 }});
            await page.goBack();
            await page.waitForFunction(() => window.Alpine.store('page') === 'changelog', {{ timeout: 10000 }});
            await page.getByRole('button', {{ name: /账户/ }}).click();
            await page.waitForFunction(() => window.Alpine.store('page') === 'account', {{ timeout: 10000 }});

            await page.getByRole('button', {{ name: /计划/ }}).click();
            await page.waitForFunction(
              () => document.body.innerText.includes('账户间资金调拨')
                && document.body.innerText.includes('展开查看计算依据'),
              {{ timeout: 10000 }}
            );
            await page.getByText('展开查看计算依据').click();
            await page.waitForFunction(
              () => document.body.innerText.includes('主动组合可展开查看其下属账户。')
                && document.body.innerText.includes('含 3 个账户'),
              {{ timeout: 10000 }}
            );
            const planRequired = [
              '组合再平衡检查',
              '主动组合',
              '海外长钱',
              '国内长钱',
              '广发账户',
              '华泰账户',
              '资金账户',
              '展开查看计算依据',
              '主动组合可展开查看其下属账户。',
              '含 3 个账户',
            ];
            for (const text of planRequired) await assertVisibleText(text);
            const hierarchy = await page.evaluate(() => {{
              const host = document.querySelector('[x-data="tradingPage()"]');
              const data = window.Alpine.$data(host);
              return data.fundingBasisRows().map(item => ({{
                kind: item.kind,
                itemKey: item.key,
                rowKey: item.row.key,
                label: item.row.label,
              }}));
            }});
            const hierarchyJson = JSON.stringify(hierarchy);
            const expectedHierarchy = [
              {{ kind: 'top', itemKey: 'top-A', rowKey: 'A', label: '主动组合' }},
              {{ kind: 'child', itemKey: 'top-A-stock', rowKey: 'stock', label: '广发账户' }},
              {{ kind: 'child', itemKey: 'top-A-bond', rowKey: 'bond', label: '华泰账户' }},
              {{ kind: 'child', itemKey: 'top-A-cash_pool', rowKey: 'cash_pool', label: '浦发现金账户' }},
              {{ kind: 'top', itemKey: 'top-B', rowKey: 'B', label: '海外长钱' }},
              {{ kind: 'top', itemKey: 'top-C', rowKey: 'C', label: '国内长钱' }},
            ];
            if (hierarchy.length !== expectedHierarchy.length) {{
              throw new Error('计算依据层级行数不符: expected=' + expectedHierarchy.length + '; actual=' + hierarchyJson);
            }}
            expectedHierarchy.forEach((expected, index) => {{
              const actual = hierarchy[index];
              const matched = actual
                && actual.kind === expected.kind
                && actual.itemKey === expected.itemKey
                && actual.rowKey === expected.rowKey
                && actual.label.includes(expected.label);
              if (!matched) {{
                throw new Error('计算依据层级顺序不符: index=' + index + '; expected=' + JSON.stringify(expected) + '; actual=' + hierarchyJson);
              }}
            }});

            const basis = page.locator('details:has-text("展开查看计算依据")');
            const domRows = await basis.locator('tbody tr').evaluateAll(rows =>
              rows.map(row => row.innerText.replace(/\\s+/g, ' ').trim())
            );
            const expectedDomLabels = ['主动组合', '广发账户', '华泰账户', '浦发现金账户', '海外长钱', '国内长钱'];
            expectedDomLabels.forEach((label, index) => {{
              if (!(domRows[index] || '').includes(label)) {{
                throw new Error(
                  '计算依据 DOM 行顺序不符: index=' + index
                    + '; expectedLabel=' + label
                    + '; actual=' + JSON.stringify(domRows)
                );
              }}
            }});
            const childRowsAreIndented = await basis.locator('tbody tr').evaluateAll(rows =>
              rows.slice(1, 4).every(row =>
                row.className.includes('bg-gray-50')
                  && row.querySelector('td div')?.className.includes('ml-7')
              )
            );
            if (!childRowsAreIndented) throw new Error('主动组合下属账户没有以子行缩进展示');

            await basis.scrollIntoViewIfNeeded();
            const box = await basis.boundingBox();
            if (!box || box.width <= 0 || box.height <= 0) {{
              throw new Error('计算依据区域不可见');
            }}
            await page.evaluate(() => document.querySelector('[x-data="tradingPage()"]')?.scrollIntoView({{ block: 'start' }}));
            await page.waitForTimeout(100);
            await page.screenshot({{ path: {json.dumps(plan_screenshot)}, fullPage: true }});

            await page.getByRole('button', {{ name: /更新日志/ }}).click();
            await page.waitForFunction(
              () => document.body.innerText.includes('最近发生了什么')
                && document.body.innerText.includes('账户事实展示口径统一')
                && document.body.innerText.includes('Web 看板实施计划形成'),
              {{ timeout: 10000 }}
            );
            const changelogRequired = [
              '更新日志',
              '最近发生了什么',
              'CHANGELOG',
              '2026 年 8 月 5 日',
              '周三',
              '15:59',
              '优化',
              '账户事实展示口径统一',
              '2026 年 6 月 29 日',
              'Web 看板实施计划形成',
            ];
            for (const text of changelogRequired) await assertVisibleText(text);
            const changelogText = await page.locator('[x-data="changelogPage()"]').innerText();
            const forbiddenText = ['issue', 'pull request', 'commit', 'hash', '影响范围', '内部模块', '验证命令', '维护者备注'];
            forbiddenText.forEach(text => {{
              if (changelogText.toLowerCase().includes(text.toLowerCase())) {{
                throw new Error('更新日志泄露了工程追溯信息: ' + text);
              }}
            }});
            if (/#\\d+/.test(changelogText) || /\\b[0-9a-f]{{7,40}}\\b/i.test(changelogText)) {{
              throw new Error('更新日志泄露了编号或哈希');
            }}

            await page.evaluate(() => {{
              document.querySelector('.app-main')?.scrollTo({{ top: 0, left: 0 }});
              window.scrollTo({{ top: 0, left: 0 }});
            }});
            await page.waitForTimeout(100);
            await page.locator('[x-data="changelogPage()"]').scrollIntoViewIfNeeded();
            const changelogBox = await page.locator('[x-data="changelogPage()"]').boundingBox();
            if (!changelogBox || changelogBox.width <= 0 || changelogBox.height <= 0) {{
              throw new Error('更新日志区域不可见');
            }}
            const readingMetrics = await page.evaluate(() => {{
              const nav = document.querySelector('.app-nav')?.getBoundingClientRect();
              const shell = document.querySelector('[x-data="changelogPage()"]')?.getBoundingClientRect();
              const title = document.querySelector('.changelog-title')?.getBoundingClientRect();
              return {{
                navWidth: nav?.width || 0,
                shellLeft: shell?.left || 0,
                shellTop: shell?.top || 0,
                shellWidth: shell?.width || 0,
                titleTop: title?.top || 0,
              }};
            }});
            if ({json.dumps(viewport_name)} === 'wide') {{
              if (Math.abs(readingMetrics.navWidth - 240) > 2) {{
                throw new Error('AIHOT 侧栏宽度偏离: ' + JSON.stringify(readingMetrics));
              }}
              if (readingMetrics.shellWidth < 1100 || readingMetrics.shellWidth > 1140) {{
                throw new Error('更新日志阅读容器宽度偏离: ' + JSON.stringify(readingMetrics));
              }}
              if (readingMetrics.shellLeft < 560 || readingMetrics.shellLeft > 600) {{
                throw new Error('更新日志阅读容器起点偏离 AIHOT 坐标: ' + JSON.stringify(readingMetrics));
              }}
              if (readingMetrics.titleTop < 140 || readingMetrics.titleTop > 170) {{
                throw new Error('更新日志标题首屏位置偏离: ' + JSON.stringify(readingMetrics));
              }}
            }}
            await page.screenshot({{ path: {json.dumps(screenshot)}, fullPage: true }});
            return {{
              ok: true,
              viewport: {json.dumps(viewport_name)},
              size: {json.dumps(viewport)},
              accountScreenshot: {json.dumps(account_screenshot)},
              planScreenshot: {json.dumps(plan_screenshot)},
              screenshot: {json.dumps(screenshot)},
              accountChecks: accountRequired.length,
              planChecks: planRequired.length,
              hierarchyChecks: expectedHierarchy.length + expectedDomLabels.length + 1,
              changelogChecks: changelogRequired.length,
              consoleErrors,
              pageErrors,
            }};
          }} catch (error) {{
            const bodyText = await page.locator('body').innerText().catch(() => '');
            await page.screenshot({{ path: {json.dumps(failure_screenshot)}, fullPage: true }}).catch(() => null);
            return {{
              ok: false,
              viewport: {json.dumps(viewport_name)},
              error: String(error),
              failureScreenshot: {json.dumps(failure_screenshot)},
              failureText: {json.dumps(failure_text)},
              bodyText,
              consoleErrors,
              pageErrors,
            }};
          }}
        }}
        """
    ).strip()


def run_cli(session: str, args: list[str]) -> str:
    result = subprocess.run(
        ["playwright-cli", f"-s={session}", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SmokeFailure((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def parse_json_result(raw: str) -> dict:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise SmokeFailure(f"playwright-cli did not return JSON: {raw}")
    return json.loads(raw[start:end + 1])


def close_browser(session: str) -> None:
    if shutil.which("playwright-cli") is None:
        return
    subprocess.run(
        ["playwright-cli", f"-s={session}", "close"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def stop_server(server: subprocess.Popen) -> None:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)


class SmokeFailure(Exception):
    pass


if __name__ == "__main__":
    raise SystemExit(main())
