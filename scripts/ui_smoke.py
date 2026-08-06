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
VISUAL_GATE_NAME = "AIHOT 视觉回归闸门"
WORKBENCH_WIDTH_RANGE = (1000, 1100)
AIHOT_CHANGELOG_REFERENCE_METRICS = {
    "nav_width": 179,
    "shell_left": 429,
    "content_top": 80,
    "shell_width": 833,
    "header_stream_gap": 61,
    "date_group_gap": 72,
    "date_header_height": 44,
    "date_divider_y": 295.41,
    "meta_width": 86,
    "entry_column_gap": 22,
    "entry_divider_x": 537,
    "entry_copy_x": 562,
}
AIHOT_CHANGELOG_STYLE_METRICS = {
    "canvas": "rgb(244, 245, 246)",
    "surface": "rgb(255, 255, 255)",
    "navBorder": "rgb(227, 228, 231)",
    "navBorderWidth": 1,
    "ink": "rgb(27, 39, 51)",
    "secondary": "rgb(92, 102, 114)",
    "tertiary": "rgb(133, 140, 150)",
    "line": "rgb(227, 228, 231)",
    "navActive": "rgb(233, 238, 240)",
    "accent": "rgb(18, 94, 108)",
    "update": "rgb(46, 126, 93)",
    "neutral": "rgb(102, 113, 126)",
    "kicker": {"fontSize": 11, "lineHeight": 16, "fontWeight": 650},
    "title": {"fontSize": 32, "lineHeight": 39.04, "fontWeight": 680},
    "subtitle": {"fontSize": 17, "lineHeight": 27.2, "fontWeight": 400},
    "date": {"fontSize": 24, "lineHeight": 31.2, "fontWeight": 650},
    "entryTitle": {"fontSize": 18, "lineHeight": 25.2, "fontWeight": 650},
    "body": {"fontSize": 16, "lineHeight": 28, "fontWeight": 400},
    "time": {"fontSize": 16, "lineHeight": 20, "fontWeight": 500},
    "label": {"fontSize": 14, "lineHeight": 18.9, "fontWeight": 560},
    "dotSize": 8,
}
AIHOT_CHANGELOG_RESPONSIVE_METRICS = {
    "desktop": {
        "navWidth": 179,
        "shellLeft": 393,
        "contentTop": 80,
        "shellWidth": 833,
    },
    "narrow": {
        "navHeight": 64,
        "shellPaddingLeft": 22,
        "shellPaddingRight": 22,
        "headerStreamGap": 50,
        "dateGroupGap": 58,
        "entryMainPaddingLeft": 16,
        "titleFontSize": 32,
        "dateFontSize": 24,
        "entryTitleFontSize": 19,
        "bodyFontSize": 15,
    },
}
VIEWPORTS = {
    "wide": {"width": 1512, "height": 749},
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

        print(
            json.dumps(
                {"visualGate": VISUAL_GATE_NAME, "base_url": base_url, "results": results},
                ensure_ascii=False,
                indent=2,
            )
        )
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
        description=(
            "Run the AIHOT 视觉回归闸门 for ark-quant account, plan and changelog pages."
        ),
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
    fetched_at = (
        datetime.now(ZoneInfo("Asia/Shanghai"))
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )
    db.init_db()
    with db._conn() as conn:
        conn.execute(
            """INSERT INTO market_temperatures
               (temperature,label,source_updated_at,source,fetched_at)
               VALUES (45.0,'正常',?,?,?)""",
            (f"{plan_date}T15:00", DATA_URL, fetched_at),
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
    failure_report_path = output_dir / f"{viewport_name}-failure.json"
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
    try:
        run_cli(session, ["open", "about:blank"])
        raw = run_cli(session, ["--raw", "run-code", code])
        result = parse_json_result(raw)
    except (SmokeFailure, json.JSONDecodeError) as exc:
        result = collect_cli_failure(
            session,
            viewport_name=viewport_name,
            error=exc,
            failure_screenshot_path=failure_screenshot_path,
        )
    if result.get("ok") and (result.get("consoleErrors") or result.get("pageErrors")):
        result = {
            **result,
            "ok": False,
            "error": "browser console or page errors",
            "differences": result.get("differences", []),
        }
    if not result.get("ok"):
        result = write_failure_artifacts(
            result,
            failure_text_path=failure_text_path,
            failure_report_path=failure_report_path,
        )
        raise SmokeFailure(
            f"{viewport_name} failed: {result.get('error')}; "
            f"screenshot={result.get('failureScreenshot')}; "
            f"text={result.get('failureText')}; report={result.get('failureReport')}"
        )
    return result


def collect_cli_failure(
    session: str,
    *,
    viewport_name: str,
    error: Exception,
    failure_screenshot_path: Path,
) -> dict:
    diagnostic = {"bodyText": "", "consoleErrors": [], "pageErrors": []}
    diagnostic_code = textwrap.dedent(
        f"""
        async page => {{
          const bodyText = await page.locator('body').innerText().catch(() => '');
          const pageErrors = [];
          await page.screenshot({{
            path: {json.dumps(str(failure_screenshot_path))},
            fullPage: true,
          }}).catch(screenshotError => pageErrors.push(String(screenshotError)));
          return {{ bodyText, consoleErrors: [], pageErrors }};
        }}
        """
    ).strip()
    try:
        diagnostic = parse_json_result(
            run_cli(session, ["--raw", "run-code", diagnostic_code])
        )
    except (SmokeFailure, json.JSONDecodeError) as diagnostic_error:
        diagnostic["pageErrors"] = [str(diagnostic_error)]

    return {
        "ok": False,
        "visualGate": VISUAL_GATE_NAME,
        "viewport": viewport_name,
        "check": "browser.cli",
        "error": str(error),
        "differences": [
            {
                "metric": "browserCommand",
                "expected": "exit code 0",
                "actual": str(error),
                "tolerance": 0,
            }
        ],
        "failureScreenshot": str(failure_screenshot_path),
        "bodyText": diagnostic.get("bodyText", ""),
        "consoleErrors": diagnostic.get("consoleErrors", []),
        "pageErrors": diagnostic.get("pageErrors", []),
    }


def write_failure_artifacts(
    result: dict,
    *,
    failure_text_path: Path,
    failure_report_path: Path,
) -> dict:
    report = {
        **result,
        "failureText": str(failure_text_path),
        "failureReport": str(failure_report_path),
    }
    failure_text_path.write_text(report.get("bodyText", ""), encoding="utf-8")
    failure_report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


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
          const fail = (check, message, differences = []) => {{
            const error = new Error(message);
            error.check = check;
            error.differences = differences;
            throw error;
          }};
          const difference = (metric, expected, actual, tolerance = 0) => ({{
            metric,
            expected,
            actual,
            tolerance,
          }});
          const assertNoDifferences = (check, message, differences) => {{
            if (differences.length) fail(check, message, differences);
          }};
          const assertPageIntegrity = async (scopeLabel, root) => {{
            const diagnostics = await root.evaluate((rootElement, tolerance) => {{
              const isVisible = element => {{
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && Number(style.opacity || 1) !== 0
                  && rect.width > 0
                  && rect.height > 0;
              }};
              const rootRect = rootElement.getBoundingClientRect();
              const horizontalScroller = element => {{
                for (let current = element.parentElement; current && current !== rootElement; current = current.parentElement) {{
                  const style = window.getComputedStyle(current);
                  if (['auto', 'scroll'].includes(style.overflowX)
                      && current.scrollWidth > current.clientWidth + tolerance) return current;
                }}
                return null;
              }};
              const clippedTables = Array.from(rootElement.querySelectorAll('table'))
                .filter(table => isVisible(table))
                .map(table => {{
                  const rect = table.getBoundingClientRect();
                  const scroller = horizontalScroller(table);
                  const scrollerRect = scroller?.getBoundingClientRect();
                  const tableBeyondRoot = rect.left < rootRect.left - tolerance
                    || rect.right > rootRect.right + tolerance;
                  const scrollerBeyondRoot = scrollerRect
                    ? scrollerRect.left < rootRect.left - tolerance
                      || scrollerRect.right > rootRect.right + tolerance
                    : false;
                  return {{
                    role: table.getAttribute('aria-label') || table.querySelector('caption')?.innerText || 'table',
                    tableBeyondRoot,
                    hasScroller: Boolean(scroller),
                    scrollerBeyondRoot,
                  }};
                }})
                .filter(item => item.tableBeyondRoot && (!item.hasScroller || item.scrollerBeyondRoot));
              const clippedControls = Array.from(rootElement.querySelectorAll('button, input, select, summary, a[href]'))
                .filter(element => isVisible(element))
                .map(element => {{
                  const rect = element.getBoundingClientRect();
                  return {{
                    label: element.getAttribute('aria-label') || element.innerText || element.tagName,
                    left: rect.left,
                    right: rect.right,
                  }};
                }})
                .filter(item => item.left < rootRect.left - tolerance || item.right > rootRect.right + tolerance);
              const clippedText = Array.from(rootElement.querySelectorAll('h1, h2, h3, p, label, button, summary, td, th'))
                .filter(element => isVisible(element) && (element.innerText || '').trim())
                .filter(element => !horizontalScroller(element))
                .map(element => {{
                  const style = window.getComputedStyle(element);
                  const rect = element.getBoundingClientRect();
                  return {{
                    text: element.innerText.trim().slice(0, 80),
                    outsideRoot: rect.left < rootRect.left - tolerance
                      || rect.right > rootRect.right + tolerance,
                    horizontal: element.scrollWidth > element.clientWidth + tolerance
                      && ['hidden', 'clip'].includes(style.overflowX),
                    vertical: element.scrollHeight > element.clientHeight + tolerance
                      && ['hidden', 'clip'].includes(style.overflowY),
                  }};
                }})
                .filter(item => item.outsideRoot || item.horizontal || item.vertical);
              return {{
                clientWidth: rootElement.clientWidth,
                scrollWidth: rootElement.scrollWidth,
                documentClientWidth: document.documentElement.clientWidth,
                documentScrollWidth: document.documentElement.scrollWidth,
                clippedTables,
                clippedControls,
                clippedText,
              }};
            }}, 2);
            const differences = [];
            if (diagnostics.scrollWidth > diagnostics.clientWidth + 2) {{
              differences.push(difference('rootScrollWidth', diagnostics.clientWidth, diagnostics.scrollWidth, 2));
            }}
            if (diagnostics.documentScrollWidth > diagnostics.documentClientWidth + 2) {{
              differences.push(difference(
                'documentScrollWidth',
                diagnostics.documentClientWidth,
                diagnostics.documentScrollWidth,
                2,
              ));
            }}
            if (diagnostics.clippedTables.length) {{
              differences.push(difference('clippedTables', [], diagnostics.clippedTables));
            }}
            if (diagnostics.clippedControls.length) {{
              differences.push(difference('clippedControls', [], diagnostics.clippedControls));
            }}
            if (diagnostics.clippedText.length) {{
              differences.push(difference('clippedText', [], diagnostics.clippedText));
            }}
            assertNoDifferences(scopeLabel + '.integrity', scopeLabel + ' 页面出现裁切或横向溢出', differences);
            return diagnostics;
          }};
          try {{
            await page.route('**/favicon.ico', route => route.fulfill({{ status: 204, body: '' }}));
            await page.setViewportSize({json.dumps(viewport)});
            await page.goto({json.dumps(base_url)}, {{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await page.waitForFunction(() => window.Alpine, {{ timeout: 10000 }});
            const navigation = page.getByRole('navigation', {{ name: '主导航' }});
            const main = page.getByRole('main', {{ name: '工作台内容' }});
            const accountNav = navigation.getByRole('button', {{ name: '账户', exact: true }});
            const planNav = navigation.getByRole('button', {{ name: '计划', exact: true }});
            const changelogNav = navigation.getByRole('button', {{ name: '更新日志', exact: true }});
            const assertActivePage = async (check, button, expectedHash) => {{
              await button.waitFor({{ state: 'visible', timeout: 10000 }});
              const label = await button.getAttribute('aria-label');
              await page.waitForFunction(
                ({{ label, expectedHash }}) => window.location.hash === expectedHash
                  && document.querySelector(`button[aria-label="${{label}}"]`)?.getAttribute('aria-current') === 'page',
                {{ label, expectedHash }},
                {{ timeout: 10000 }},
              );
              const actual = {{
                hash: await page.evaluate(() => window.location.hash),
                ariaCurrent: await button.getAttribute('aria-current'),
              }};
              const differences = [];
              if (actual.hash !== expectedHash) {{
                differences.push(difference(check + '.hash', expectedHash, actual.hash));
              }}
              if (actual.ariaCurrent !== 'page') {{
                differences.push(difference(check + '.ariaCurrent', 'page', actual.ariaCurrent));
              }}
              assertNoDifferences(check, check + ' 导航状态异常', differences);
            }};
            await assertActivePage('account.initial', accountNav, '');
            const navItems = await navigation.getByRole('button').evaluateAll(buttons => buttons.map(button => ({{
              label: button.innerText.trim(),
              ariaLabel: button.getAttribute('aria-label'),
              disabled: button.disabled,
              ariaDisabled: button.getAttribute('aria-disabled'),
            }})));
            const expectedNavLabels = ['账户', '计划', '更新日志'];
            const actualNavLabels = navItems.map(item => (item.ariaLabel || item.label).split('，')[0]);
            const navigationDifferences = [];
            if (JSON.stringify(actualNavLabels) !== JSON.stringify(expectedNavLabels)) {{
              navigationDifferences.push(difference('navigation.order', expectedNavLabels, actualNavLabels));
            }}
            const disabledItems = navItems.filter(item => item.disabled);
            if (disabledItems.length) {{
              navigationDifferences.push(difference(
                'navigation.disabledItems',
                [],
                disabledItems,
              ));
            }}
            assertNoDifferences('navigation', '全站导航顺序或可用状态异常', navigationDifferences);

            const accountPage = main.getByRole('region', {{ name: '账户工作台' }});
            const accountHeading = accountPage.getByRole('heading', {{ name: '账户', level: 1 }});
            await accountHeading.waitFor({{ state: 'visible', timeout: 10000 }});
            const accountDate = accountPage.getByLabel('账户事实日', {{ exact: true }});
            await accountDate.fill({json.dumps(expected_plan_date)});
            const accountLedger = accountPage.getByRole('table', {{ name: '账户明细' }});
            await page.waitForFunction(
              () => document.querySelector('[aria-label="账户明细"]')?.rows.length > 0,
              {{ timeout: 10000 }},
            );
            const accountTotal = accountPage.getByRole('region', {{ name: '账户资产总览' }});
            const accountShell = accountPage.getByRole('region', {{ name: '账户内容区域' }});
            const [accountShellBox, accountTotalBox, accountLedgerBox] = await Promise.all([
              accountShell.boundingBox(),
              accountTotal.boundingBox(),
              accountLedger.boundingBox(),
            ]);
            const accountMetrics = {{
              shellWidth: accountShellBox?.width || 0,
              totalWidth: accountTotalBox?.width || 0,
              ledgerWidth: accountLedgerBox?.width || 0,
              rowCount: await accountLedger.getByRole('row').count(),
              editableDate: await accountDate.inputValue(),
            }};
            const accountDifferences = [];
            if ({json.dumps(viewport_name)} !== 'narrow') {{
              if (accountMetrics.shellWidth < {WORKBENCH_WIDTH_RANGE[0]} || accountMetrics.shellWidth > {WORKBENCH_WIDTH_RANGE[1]}) {{
                accountDifferences.push(difference(
                  'account.shellWidth',
                  {{ min: {WORKBENCH_WIDTH_RANGE[0]}, max: {WORKBENCH_WIDTH_RANGE[1]} }},
                  accountMetrics.shellWidth,
                ));
              }}
              if (Math.abs(accountMetrics.totalWidth - accountMetrics.ledgerWidth) > 2) {{
                accountDifferences.push(difference(
                  'account.ledgerWidth',
                  accountMetrics.totalWidth,
                  accountMetrics.ledgerWidth,
                  2,
                ));
              }}
            }}
            if (accountMetrics.editableDate !== {json.dumps(expected_plan_date)}) {{
              accountDifferences.push(difference(
                'account.editableDate',
                {json.dumps(expected_plan_date)},
                accountMetrics.editableDate,
              ));
            }}
            if (accountMetrics.rowCount < 1) {{
              accountDifferences.push(difference('account.rowCount', {{ min: 1 }}, accountMetrics.rowCount));
            }}
            assertNoDifferences('account.behavior', '账户页关键可见行为异常', accountDifferences);
            const accountIntegrity = await assertPageIntegrity('account', main);
            await page.screenshot({{ path: {json.dumps(account_screenshot)}, fullPage: true }});
            await page.reload({{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await assertActivePage('account.reload', accountNav, '');
            await planNav.click();
            await assertActivePage('plan.fromAccount', planNav, '#trading');
            await page.goBack();
            await assertActivePage('account.back', accountNav, '');

            await page.goto({json.dumps(base_url + '#changelog')}, {{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await assertActivePage('changelog.direct', changelogNav, '#changelog');
            await main.getByRole('heading', {{ name: '更新日志', level: 1 }}).waitFor({{ state: 'visible', timeout: 10000 }});
            await page.reload({{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await assertActivePage('changelog.reload', changelogNav, '#changelog');
            await accountNav.click();
            await assertActivePage('account.fromChangelog', accountNav, '');
            await page.goBack();
            await assertActivePage('changelog.back', changelogNav, '#changelog');

            await page.goto({json.dumps(base_url + '#trading')}, {{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await assertActivePage('plan.direct', planNav, '#trading');
            await page.reload({{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await assertActivePage('plan.reload', planNav, '#trading');
            await accountNav.click();
            await assertActivePage('account.fromPlan', accountNav, '');
            await page.goBack();
            await assertActivePage('plan.back', planNav, '#trading');
            const planPage = main.getByRole('region', {{ name: '计划工作台' }});
            await planPage.waitFor({{ state: 'visible', timeout: 10000 }});
            await page.waitForFunction(
              label => Array.from(document.querySelectorAll(`[role="region"][aria-label="${{label}}"]`))
                .find(element => element.checkVisibility())
                ?.getAttribute('aria-busy') === 'false',
              '计划工作台',
              {{ timeout: 10000 }},
            );
            const planHeading = planPage.getByRole('heading', {{ name: '计划', level: 1 }});
            await planHeading.waitFor({{ state: 'visible', timeout: 10000 }});
            const planDetails = planPage.locator('details[aria-label="计划条件"]:visible');
            const planDetailsCount = await planDetails.count();
            if (planDetailsCount !== 1) {{
              fail(
                'plan.detailsControl',
                '计划条件必须有且只有一个可见详情控件',
                [difference('plan.visibleDetails', 1, planDetailsCount)],
              );
            }}
            const planSummary = planDetails.locator(':scope > summary:visible');
            const planSummaryCount = await planSummary.count();
            if (planSummaryCount !== 1) {{
              fail(
                'plan.detailsControl',
                '计划条件必须有且只有一个可见切换控件',
                [difference('plan.visibleSummaries', 1, planSummaryCount)],
              );
            }}
            await planSummary.waitFor({{ state: 'visible', timeout: 10000 }});
            const wasOpen = await planDetails.evaluate(element => element.open);
            await planSummary.focus();
            const planFocus = await planSummary.evaluate(element => {{
              return {{
                activeTag: document.activeElement?.tagName || '',
                isFocused: document.activeElement === element,
              }};
            }});
            const clickAndReadDetailsState = async () => {{
              const toggleResult = planDetails.evaluate(element => new Promise(resolve => {{
                const summary = element.querySelector(':scope > summary');
                let clickObserved = false;
                const cleanup = () => {{
                  summary?.removeEventListener('click', observeClick);
                  element.removeEventListener('toggle', observeToggle);
                }};
                const observeClick = () => {{ clickObserved = true; }};
                const observeToggle = () => {{
                  if (!clickObserved) return;
                  window.clearTimeout(timeout);
                  cleanup();
                  resolve({{ clickObserved, eventObserved: true, open: element.open }});
                }};
                const timeout = window.setTimeout(
                  () => {{
                    cleanup();
                    resolve({{ clickObserved, eventObserved: false, open: element.open }});
                  }},
                  2000,
                );
                summary?.addEventListener('click', observeClick, {{ once: true }});
                element.addEventListener('toggle', observeToggle);
              }}));
              await planSummary.click();
              return toggleResult;
            }};
            const toggledResult = await clickAndReadDetailsState();
            const restoredResult = await clickAndReadDetailsState();
            const toggledOpen = toggledResult.open;
            const restoredOpen = restoredResult.open;
            const planShellBox = await planPage.boundingBox();
            const planMetrics = {{
              shellWidth: planShellBox?.width || 0,
              visibleDetails: planDetailsCount,
              wasOpen,
              toggledOpen,
              restoredOpen,
              toggleEvents: {{
                toggled: toggledResult.eventObserved,
                restored: restoredResult.eventObserved,
              }},
              clicks: {{
                toggled: toggledResult.clickObserved,
                restored: restoredResult.clickObserved,
              }},
              focus: planFocus,
            }};
            const planDifferences = [];
            if ({json.dumps(viewport_name)} !== 'narrow') {{
              if (planMetrics.shellWidth < {WORKBENCH_WIDTH_RANGE[0]} || planMetrics.shellWidth > {WORKBENCH_WIDTH_RANGE[1]}) {{
                planDifferences.push(difference(
                  'plan.shellWidth',
                  {{ min: {WORKBENCH_WIDTH_RANGE[0]}, max: {WORKBENCH_WIDTH_RANGE[1]} }},
                  planMetrics.shellWidth,
                ));
              }}
            }}
            if (
              !planMetrics.toggleEvents.toggled
              || !planMetrics.toggleEvents.restored
              || !planMetrics.clicks.toggled
              || !planMetrics.clicks.restored
              || planMetrics.toggledOpen === planMetrics.wasOpen
              || planMetrics.restoredOpen !== planMetrics.wasOpen
            ) {{
              planDifferences.push(difference(
                'plan.detailsToggle',
                {{
                  toggled: !planMetrics.wasOpen,
                  restored: planMetrics.wasOpen,
                  events: {{ toggled: true, restored: true }},
                  clicks: {{ toggled: true, restored: true }},
                }},
                {{
                  toggled: planMetrics.toggledOpen,
                  restored: planMetrics.restoredOpen,
                  events: planMetrics.toggleEvents,
                  clicks: planMetrics.clicks,
                }},
              ));
            }}
            if (planMetrics.visibleDetails < 1) {{
              planDifferences.push(difference('plan.visibleDetails', {{ min: 1 }}, planMetrics.visibleDetails));
            }}
            if (!planMetrics.focus.isFocused || planMetrics.focus.activeTag !== 'SUMMARY') {{
              planDifferences.push(difference(
                'plan.keyboardFocus',
                {{ activeTag: 'SUMMARY', isFocused: true }},
                planMetrics.focus,
              ));
            }}
            assertNoDifferences('plan.behavior', '计划页关键可见行为异常', planDifferences);
            const planIntegrity = await assertPageIntegrity('plan', main);
            await planHeading.scrollIntoViewIfNeeded();
            await page.waitForTimeout(100);
            await page.screenshot({{ path: {json.dumps(plan_screenshot)}, fullPage: true }});

            await changelogNav.click();
            await assertActivePage('changelog.fromPlan', changelogNav, '#changelog');
            const changelogShell = main.getByRole('region', {{ name: '更新日志内容' }});
            const changelogHeading = changelogShell.getByRole('heading', {{ name: '更新日志', level: 1 }});
            const dateHeadings = changelogShell.getByRole('heading', {{ level: 2 }});
            const entryHeadings = changelogShell.getByRole('heading', {{ level: 3 }});
            const articles = changelogShell.getByRole('article');
            await articles.first().waitFor({{ state: 'visible', timeout: 10000 }});
            const [firstArticle] = await articles.all();
            const firstArticleContract = await firstArticle.evaluate(article => ({{
              hasDatedTime: Boolean(article.querySelector('time[datetime]')),
              hasHeading: Boolean(article.querySelector('h3')?.innerText.trim()),
              hasBody: Boolean(article.querySelector('p')?.innerText.trim()),
            }}));
            const changelogBehavior = {{
              h1Count: await changelogHeading.count(),
              dateHeadingCount: await dateHeadings.count(),
              entryHeadingCount: await entryHeadings.count(),
              articleCount: await articles.count(),
              firstArticleContract,
            }};
            const changelogBehaviorDifferences = [];
            if (changelogBehavior.h1Count !== 1) {{
              changelogBehaviorDifferences.push(difference('changelog.h1Count', 1, changelogBehavior.h1Count));
            }}
            ['dateHeadingCount', 'entryHeadingCount', 'articleCount'].forEach(metric => {{
              if (changelogBehavior[metric] < 1) {{
                changelogBehaviorDifferences.push(difference(
                  'changelog.' + metric,
                  {{ min: 1 }},
                  changelogBehavior[metric],
                ));
              }}
            }});
            if (Object.values(firstArticleContract).some(value => !value)) {{
              changelogBehaviorDifferences.push(difference(
                'changelog.firstArticle',
                {{ hasDatedTime: true, hasHeading: true, hasBody: true }},
                firstArticleContract,
              ));
            }}
            assertNoDifferences(
              'changelog.behavior',
              '更新日志关键可见行为异常',
              changelogBehaviorDifferences,
            );
            await main.evaluate(element => element.scrollTo({{ top: 0, left: 0 }}));
            await page.evaluate(() => window.scrollTo({{ top: 0, left: 0 }}));
            await page.waitForTimeout(100);
            const changelogBox = await changelogShell.boundingBox();
            if (!changelogBox || changelogBox.width <= 0 || changelogBox.height <= 0) {{
              fail(
                'changelog.visibility',
                '更新日志区域不可见',
                [difference('changelog.boundingBox', {{ width: '>0', height: '>0' }}, changelogBox)],
              );
            }}
            const changelogHeader = changelogShell.getByRole('group', {{ name: '更新日志页头', exact: true }});
            const kickerLocator = changelogShell.locator('[data-visual-metric="kicker"]');
            const subtitleLocator = changelogShell.locator('[data-visual-metric="subtitle"]');
            const [firstDateHeading] = await dateHeadings.all();
            const [dateHeaderLocator] = await changelogShell.locator('[data-visual-metric="date-header"]').all();
            const groupsLocator = changelogShell.locator('[data-visual-metric="groups"]');
            const timeLocator = firstArticle.locator('[data-visual-metric="time"]');
            const metaLocator = firstArticle.locator('[data-visual-metric="entry-meta"]');
            const typeLocator = firstArticle.locator('[data-visual-metric="type"]');
            const entryTitleLocator = firstArticle.getByRole('heading', {{ level: 3 }});
            const entryMainLocator = firstArticle.locator('[data-visual-metric="entry-main"]');
            const [firstWeekday] = await changelogShell.locator('[data-visual-metric="weekday"]').all();
            const visualElements = {{
              body: await page.locator('body').elementHandle(),
              nav: await navigation.elementHandle(),
              activeNav: await changelogNav.elementHandle(),
              main: await main.elementHandle(),
              shell: await changelogShell.elementHandle(),
              kicker: await kickerLocator.elementHandle(),
              title: await changelogHeading.elementHandle(),
              subtitle: await subtitleLocator.elementHandle(),
              header: await changelogHeader.elementHandle(),
              groups: await groupsLocator.elementHandle(),
              dateHeader: await dateHeaderLocator.elementHandle(),
              date: await firstDateHeading.elementHandle(),
              weekday: await firstWeekday.elementHandle(),
              entryMeta: await metaLocator.elementHandle(),
              entryMain: await entryMainLocator.elementHandle(),
              entryTitle: await entryTitleLocator.elementHandle(),
              entryBody: await firstArticle.locator('[data-visual-metric="entry-body"]').elementHandle(),
              time: await timeLocator.elementHandle(),
              label: await typeLocator.elementHandle(),
              dot: await firstArticle.locator('[data-visual-metric="type-dot"]').elementHandle(),
            }};
            const readingMetrics = await page.evaluate(elements => {{
              const bodyStyle = window.getComputedStyle(elements.body);
              const navElement = elements.nav;
              const nav = navElement?.getBoundingClientRect();
              const navStyle = window.getComputedStyle(navElement);
              const main = elements.main?.getBoundingClientRect();
              const shellElement = elements.shell;
              const shell = shellElement?.getBoundingClientRect();
              const shellStyle = window.getComputedStyle(shellElement);
              const kicker = elements.kicker?.getBoundingClientRect();
              const title = elements.title?.getBoundingClientRect();
              const header = elements.header?.getBoundingClientRect();
              const groups = elements.groups;
              const dateHeader = elements.dateHeader?.getBoundingClientRect();
              const entryMetaElement = elements.entryMeta;
              const entryMeta = entryMetaElement?.getBoundingClientRect();
              const entryMetaStyle = window.getComputedStyle(entryMetaElement);
              const entryMainElement = elements.entryMain;
              const entryMain = entryMainElement?.getBoundingClientRect();
              const entryMainStyle = window.getComputedStyle(entryMainElement);
              const entryTitle = elements.entryTitle?.getBoundingClientRect();
              const time = elements.time?.getBoundingClientRect();
              const type = elements.label?.getBoundingClientRect();
              return {{
                navWidth: nav?.width || 0,
                navHeight: nav?.height || 0,
                navTop: nav?.top || 0,
                navLeft: nav?.left || 0,
                navBottom: nav?.bottom || 0,
                navFlexDirection: navStyle.flexDirection,
                navBorderRightWidth: Number.parseFloat(navStyle.borderRightWidth),
                navBorderBottomWidth: Number.parseFloat(navStyle.borderBottomWidth),
                navClientWidth: navElement?.clientWidth || 0,
                navScrollWidth: navElement?.scrollWidth || 0,
                mainLeft: main?.left || 0,
                mainTop: main?.top || 0,
                mainWidth: main?.width || 0,
                shellLeft: shell?.left || 0,
                shellRight: shell?.right || 0,
                contentTop: kicker?.top || 0,
                shellWidth: shell?.width || 0,
                shellPaddingLeft: Number.parseFloat(shellStyle.paddingLeft),
                shellPaddingRight: Number.parseFloat(shellStyle.paddingRight),
                titleTop: title?.top || 0,
                headerStreamGap: (dateHeader?.top || 0) - (header?.bottom || 0),
                dateGroupGap: Number.parseFloat(window.getComputedStyle(groups).gap),
                dateHeaderHeight: dateHeader?.height || 0,
                dateDividerY: dateHeader?.bottom || 0,
                metaWidth: entryMeta?.width || 0,
                entryColumnGap: (entryMain?.left || 0) - (entryMeta?.right || 0),
                entryDividerX: entryMain?.left || 0,
                entryCopyX: entryTitle?.left || 0,
                entryMetaDisplay: entryMetaStyle.display,
                entryMetaFlexDirection: entryMetaStyle.flexDirection,
                metaTimeTop: time?.top || 0,
                metaTypeTop: type?.top || 0,
                entryMainPaddingLeft: Number.parseFloat(entryMainStyle.paddingLeft),
                entryMainBorderLeftWidth: Number.parseFloat(entryMainStyle.borderLeftWidth),
                bodyFlexDirection: bodyStyle.flexDirection,
              }};
            }}, visualElements);
            const typeColors = await articles.evaluateAll(articleElements => {{
              const colors = {{}};
              articleElements.forEach(article => {{
                const label = article.querySelector('[data-visual-metric="type"]');
                const name = label?.innerText.trim();
                if (name && !colors[name]) colors[name] = window.getComputedStyle(label).color;
              }});
              return colors;
            }});
            const styleMetrics = await page.evaluate(({{ elements, typeColors }}) => {{
              const styleOf = element => {{
                const style = window.getComputedStyle(element);
                return {{
                  color: style.color,
                  backgroundColor: style.backgroundColor,
                  borderRightColor: style.borderRightColor,
                  borderBottomColor: style.borderBottomColor,
                  fontFamily: style.fontFamily,
                  fontSize: Number.parseFloat(style.fontSize),
                  lineHeight: Number.parseFloat(style.lineHeight),
                  fontWeight: Number.parseInt(style.fontWeight, 10),
                  width: Number.parseFloat(style.width),
                  height: Number.parseFloat(style.height),
                }};
              }};
              const {{
                nav,
                activeNav,
                dateHeader,
                kicker,
                title,
                subtitle,
                date,
                weekday,
                entryTitle,
                entryBody,
                time,
                label,
                dot,
                main: appMain,
              }} = elements;
              return {{
                canvas: styleOf(appMain).backgroundColor,
                surface: styleOf(nav).backgroundColor,
                navBorder: {json.dumps(viewport_name)} === 'narrow'
                  ? styleOf(nav).borderBottomColor
                  : styleOf(nav).borderRightColor,
                navBorderWidth: Number.parseFloat(
                  {json.dumps(viewport_name)} === 'narrow'
                    ? window.getComputedStyle(nav).borderBottomWidth
                    : window.getComputedStyle(nav).borderRightWidth
                ),
                ink: styleOf(title).color,
                secondary: styleOf(subtitle).color,
                tertiary: styleOf(weekday).color,
                line: styleOf(dateHeader).borderBottomColor,
                navActive: styleOf(activeNav).backgroundColor,
                accent: typeColors['优化'],
                update: typeColors['更新'],
                neutral: typeColors['公告'],
                kicker: styleOf(kicker),
                title: styleOf(title),
                subtitle: styleOf(subtitle),
                date: styleOf(date),
                entryTitle: styleOf(entryTitle),
                body: styleOf(entryBody),
                time: styleOf(time),
                label: styleOf(label),
                dotSize: styleOf(dot).width,
                timeUsesMonospace: styleOf(time).fontFamily.toLowerCase().includes('monospace'),
                appClientWidth: appMain?.clientWidth || 0,
                appScrollWidth: appMain?.scrollWidth || 0,
              }};
            }}, {{ elements: visualElements, typeColors }});
            await accountNav.focus();
            await page.keyboard.press('Tab');
            const focusMetrics = await page.evaluate(() => {{
              const focusStyle = window.getComputedStyle(document.activeElement);
              return {{
                activeTag: document.activeElement?.tagName || '',
                activeLabel: document.activeElement?.getAttribute('aria-label') || '',
                focusOutlineStyle: focusStyle.outlineStyle,
                focusOutlineWidth: Number.parseFloat(focusStyle.outlineWidth),
              }};
            }});
            const accessibilityMetrics = {{
              ...focusMetrics,
              activeCurrent: await changelogNav.getAttribute('aria-current') || '',
              h1Count: changelogBehavior.h1Count,
              h2Count: changelogBehavior.dateHeadingCount,
              h3Count: changelogBehavior.entryHeadingCount,
            }};
            const collectNumericDrift = (actual, expected, tolerance = 2) => Object.entries(expected)
              .filter(([key, value]) => Math.abs(actual[key] - value) > tolerance)
              .map(([key, value]) => difference(key, value, actual[key], tolerance));
            const expectedStyleMetrics = {json.dumps(AIHOT_CHANGELOG_STYLE_METRICS)};
            if ({json.dumps(viewport_name)} === 'narrow') {{
              expectedStyleMetrics.subtitle = {{ fontSize: 15, lineHeight: 27.2, fontWeight: 400 }};
              expectedStyleMetrics.entryTitle = {{ fontSize: 19, lineHeight: 25.2, fontWeight: 650 }};
              expectedStyleMetrics.body = {{ fontSize: 15, lineHeight: 25.5, fontWeight: 400 }};
            }}
            const styleDrift = [];
            Object.entries(expectedStyleMetrics).forEach(([role, expected]) => {{
              const actual = styleMetrics[role];
              if (expected && typeof expected === 'object') {{
                Object.entries(expected).forEach(([property, expectedValue]) => {{
                  if (Math.abs(actual[property] - expectedValue) > 0.2) {{
                    styleDrift.push(difference(
                      role + '.' + property,
                      expectedValue,
                      actual[property],
                      0.2,
                    ));
                  }}
                }});
              }} else if (typeof expected === 'number') {{
                if (Math.abs(actual - expected) > 0.2) {{
                  styleDrift.push(difference(role, expected, actual, 0.2));
                }}
              }} else if (actual !== expected) {{
                styleDrift.push(difference(role, expected, actual));
              }}
            }});
            if (!styleMetrics.timeUsesMonospace) {{
              styleDrift.push(difference('time.fontFamily', 'monospace', styleMetrics.time.fontFamily));
            }}
            assertNoDifferences(
              'changelog.' + {json.dumps(viewport_name)} + '.computedStyle',
              '更新日志计算样式偏离',
              styleDrift,
            );
            if ({json.dumps(viewport_name)} === 'wide') {{
              const expectedStructure = {{
                navWidth: {AIHOT_CHANGELOG_REFERENCE_METRICS["nav_width"]},
                shellLeft: {AIHOT_CHANGELOG_REFERENCE_METRICS["shell_left"]},
                contentTop: {AIHOT_CHANGELOG_REFERENCE_METRICS["content_top"]},
                shellWidth: {AIHOT_CHANGELOG_REFERENCE_METRICS["shell_width"]},
                headerStreamGap: {AIHOT_CHANGELOG_REFERENCE_METRICS["header_stream_gap"]},
                dateGroupGap: {AIHOT_CHANGELOG_REFERENCE_METRICS["date_group_gap"]},
                dateHeaderHeight: {AIHOT_CHANGELOG_REFERENCE_METRICS["date_header_height"]},
                dateDividerY: {AIHOT_CHANGELOG_REFERENCE_METRICS["date_divider_y"]},
                metaWidth: {AIHOT_CHANGELOG_REFERENCE_METRICS["meta_width"]},
                entryColumnGap: {AIHOT_CHANGELOG_REFERENCE_METRICS["entry_column_gap"]},
                entryDividerX: {AIHOT_CHANGELOG_REFERENCE_METRICS["entry_divider_x"]},
                entryCopyX: {AIHOT_CHANGELOG_REFERENCE_METRICS["entry_copy_x"]},
              }};
              const structureDrift = collectNumericDrift(readingMetrics, expectedStructure);
              assertNoDifferences(
                'changelog.wide.geometry',
                '更新日志桌面结构锚点偏离',
                structureDrift,
              );
            }}
            if ({json.dumps(viewport_name)} === 'desktop') {{
              const expected = {json.dumps(AIHOT_CHANGELOG_RESPONSIVE_METRICS["desktop"])};
              const drift = collectNumericDrift(readingMetrics, expected);
              assertNoDifferences(
                'changelog.desktop.geometry',
                '更新日志 1440px 阅读比例偏离',
                drift,
              );
            }}
            if ({json.dumps(viewport_name)} === 'narrow') {{
              const expected = {json.dumps(AIHOT_CHANGELOG_RESPONSIVE_METRICS["narrow"])};
              const actual = {{
                ...readingMetrics,
                titleFontSize: styleMetrics.title.fontSize,
                dateFontSize: styleMetrics.date.fontSize,
                entryTitleFontSize: styleMetrics.entryTitle.fontSize,
                bodyFontSize: styleMetrics.body.fontSize,
              }};
              const drift = collectNumericDrift(actual, expected);
              assertNoDifferences(
                'changelog.narrow.tokens',
                '更新日志 390px 响应式令牌偏离',
                drift,
              );
              const narrowLayoutExpected = {{
                navFlexDirection: 'row',
                bodyFlexDirection: 'column',
                navBorderRightWidth: 0,
                navBorderBottomWidth: 1,
                mainLeft: 0,
                entryMetaDisplay: 'flex',
                entryMetaFlexDirection: 'row',
                entryMainBorderLeftWidth: 1,
              }};
              const narrowLayoutDrift = Object.entries(narrowLayoutExpected)
                .filter(([key, value]) => readingMetrics[key] !== value)
                .map(([key, value]) => difference(key, value, readingMetrics[key]));
              if (readingMetrics.mainTop < readingMetrics.navBottom - 2) {{
                narrowLayoutDrift.push(difference(
                  'mainTop',
                  {{ min: readingMetrics.navBottom - 2 }},
                  readingMetrics.mainTop,
                  2,
                ));
              }}
              if (Math.abs(readingMetrics.metaTimeTop - readingMetrics.metaTypeTop) > 2) {{
                narrowLayoutDrift.push(difference(
                  'metaTypeTop',
                  readingMetrics.metaTimeTop,
                  readingMetrics.metaTypeTop,
                  2,
                ));
              }}
              if (readingMetrics.navScrollWidth > readingMetrics.navClientWidth + 2) {{
                narrowLayoutDrift.push(difference(
                  'navScrollWidth',
                  readingMetrics.navClientWidth,
                  readingMetrics.navScrollWidth,
                  2,
                ));
              }}
              if (readingMetrics.shellLeft < -2 || readingMetrics.shellRight > {VIEWPORTS["narrow"]["width"] + 2}) {{
                narrowLayoutDrift.push(difference(
                  'shellBounds',
                  {{ min: -2, max: {VIEWPORTS["narrow"]["width"] + 2} }},
                  {{ left: readingMetrics.shellLeft, right: readingMetrics.shellRight }},
                  2,
                ));
              }}
              assertNoDifferences(
                'changelog.narrow.layout',
                '更新日志移动顶栏、元信息或正文布局偏离',
                narrowLayoutDrift,
              );
              const accessibilityExpected = {{
                activeTag: 'BUTTON',
                activeLabel: '计划',
                activeCurrent: 'page',
              }};
              const accessibilityDrift = Object.entries(accessibilityExpected)
                .filter(([key, value]) => accessibilityMetrics[key] !== value)
                .map(([key, value]) => difference(key, value, accessibilityMetrics[key]));
              if (accessibilityMetrics.focusOutlineStyle === 'none') {{
                accessibilityDrift.push(difference('focusOutlineStyle', 'visible', 'none'));
              }}
              if (accessibilityMetrics.focusOutlineWidth < 2) {{
                accessibilityDrift.push(difference(
                  'focusOutlineWidth',
                  {{ min: 2 }},
                  accessibilityMetrics.focusOutlineWidth,
                ));
              }}
              assertNoDifferences(
                'changelog.narrow.accessibility',
                '更新日志移动导航焦点或标题语义异常',
                accessibilityDrift,
              );
            }}
            await page.evaluate(() => document.activeElement?.blur());
            const changelogIntegrity = await assertPageIntegrity('changelog', main);
            if (consoleErrors.length || pageErrors.length) {{
              fail(
                'browser.errors',
                '浏览器控制台或页面错误',
                [
                  difference('consoleErrors', [], consoleErrors),
                  difference('pageErrors', [], pageErrors),
                ],
              );
            }}
            await page.screenshot({{ path: {json.dumps(screenshot)}, fullPage: true }});
            return {{
              ok: true,
              visualGate: {json.dumps(VISUAL_GATE_NAME)},
              viewport: {json.dumps(viewport_name)},
              size: {json.dumps(viewport)},
              accountScreenshot: {json.dumps(account_screenshot)},
              planScreenshot: {json.dumps(plan_screenshot)},
              screenshot: {json.dumps(screenshot)},
              pageChecks: {{
                navigation: {{ items: navItems.length }},
                account: {{ metrics: accountMetrics, integrity: accountIntegrity }},
                plan: {{ metrics: planMetrics, integrity: planIntegrity }},
                changelog: {{ behavior: changelogBehavior, integrity: changelogIntegrity }},
              }},
              readingMetrics,
              styleMetrics,
              accessibilityMetrics,
              consoleErrors,
              pageErrors,
            }};
          }} catch (error) {{
            const bodyText = await page.locator('body').innerText().catch(() => '');
            await page.screenshot({{ path: {json.dumps(failure_screenshot)}, fullPage: true }}).catch(() => null);
            const caughtDifferences = error.differences?.length
              ? error.differences
              : [difference(
                  error.check || 'browser.execution',
                  'check completed without exception',
                  String(error),
                )];
            return {{
              ok: false,
              visualGate: {json.dumps(VISUAL_GATE_NAME)},
              viewport: {json.dumps(viewport_name)},
              check: error.check || 'browser.execution',
              error: String(error),
              differences: caughtDifferences,
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
