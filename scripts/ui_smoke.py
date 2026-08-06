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
VISUAL_GATE_NAME = "方舟计划视觉回归闸门"
DESKTOP_NAV_WIDTH = 220
WORKBENCH_WIDTH = 1140
READING_WIDTH = 880
NARROW_NAV_HEIGHT = 64
NARROW_GUTTER = 18
GEOMETRY_TOLERANCE = 2
ARK_CHANGELOG_REFERENCE_METRICS = {
    "nav_width": DESKTOP_NAV_WIDTH,
    "shell_left": 426,
    "content_top": 54,
    "shell_width": READING_WIDTH,
    "header_stream_gap": 48,
    "date_group_gap": 46,
    "date_header_height": 44,
    "date_divider_y": 256.24,
    "meta_width": 96,
    "entry_column_gap": 24,
    "entry_divider_x": 546,
    "entry_copy_x": 546,
}
ARK_CHANGELOG_STYLE_METRICS = {
    "canvas": "rgb(243, 245, 246)",
    "surface": "rgb(255, 255, 255)",
    "navBorder": "rgb(216, 223, 227)",
    "navBorderWidth": 1,
    "ink": "rgb(23, 33, 43)",
    "secondary": "rgb(101, 113, 124)",
    "tertiary": "rgb(142, 153, 163)",
    "line": "rgb(216, 223, 227)",
    "navActive": "rgb(220, 236, 234)",
    "accent": "rgb(13, 113, 111)",
    "update": "rgb(40, 114, 79)",
    "neutral": "rgb(101, 113, 124)",
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
ARK_CHANGELOG_RESPONSIVE_METRICS = {
    "desktop": {
        "navWidth": DESKTOP_NAV_WIDTH,
        "shellLeft": 390,
        "contentTop": 54,
        "shellWidth": READING_WIDTH,
    },
    "narrow": {
        "navHeight": NARROW_NAV_HEIGHT,
        "shellPaddingLeft": NARROW_GUTTER,
        "shellPaddingRight": NARROW_GUTTER,
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
            "运行方舟计划视觉回归闸门，检查账户、今日计划和更新日志。"
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

    from app import plan_lifecycle
    from app.plan_service import build_current_plan

    saved_plan = build_current_plan()
    plan_id = plan_lifecycle.new_plan_id(plan_date)
    saved_plan["generation"] = {
        "plan_id": plan_id,
        "plan_date": plan_date,
        "status": plan_lifecycle.DRAFT,
        "stages": plan_lifecycle.generation_stages(),
    }
    plan_lifecycle.start(plan_id, plan_date, saved_plan)
    plan_lifecycle.complete(plan_id, saved_plan)


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
    plan_expanded_screenshot_path = output_dir / f"{viewport_name}-plan-expanded.png"
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
        plan_expanded_screenshot_path,
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
    plan_expanded_screenshot_path: Path,
    screenshot_path: Path,
    failure_screenshot_path: Path,
    failure_text_path: Path,
    expected_plan_date: str,
) -> str:
    account_screenshot = str(account_screenshot_path)
    plan_screenshot = str(plan_screenshot_path)
    plan_expanded_screenshot = str(plan_expanded_screenshot_path)
    plan_future_screenshot = str(
        plan_screenshot_path.with_name(f"{viewport_name}-plan-future-funding.png")
    )
    plan_no_action_screenshot = str(
        plan_screenshot_path.with_name(f"{viewport_name}-plan-no-action.png")
    )
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
            const accountNav = navigation.getByRole('link', {{ name: '账户', exact: true }});
            const planNav = navigation.getByRole('link', {{ name: '今日计划', exact: true }});
            const changelogNav = navigation.getByRole('link', {{ name: '更新日志', exact: true }});
            const assertActivePage = async (check, link, expectedHash) => {{
              await link.waitFor({{ state: 'visible', timeout: 10000 }});
              const label = await link.getAttribute('aria-label');
              await page.waitForFunction(
                ({{ label, expectedHash }}) => window.location.hash === expectedHash
                  && document.querySelector(`a[aria-label="${{label}}"]`)?.getAttribute('aria-current') === 'page',
                {{ label, expectedHash }},
                {{ timeout: 10000 }},
              );
              const actual = {{
                hash: await page.evaluate(() => window.location.hash),
                ariaCurrent: await link.getAttribute('aria-current'),
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
            await assertActivePage('plan.initial', planNav, '');
            const navItems = await navigation.getByRole('link').evaluateAll(links => links.map(link => ({{
              label: link.innerText.trim(),
              ariaLabel: link.getAttribute('aria-label'),
              disabled: link.hasAttribute('disabled'),
              ariaDisabled: link.getAttribute('aria-disabled'),
            }})));
            const expectedNavLabels = ['今日计划', '账户', '更新日志'];
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

            await accountNav.click();
            await assertActivePage('account.fromPlan', accountNav, '#account');
            const accountPage = main.getByRole('region', {{ name: '账户工作台' }});
            const accountHeading = accountPage.getByRole('heading', {{ name: '账户', level: 1 }});
            await accountHeading.waitFor({{ state: 'visible', timeout: 10000 }});
            const accountDate = accountPage.getByLabel('账户事实日', {{ exact: true }});
            await accountDate.fill({json.dumps(expected_plan_date)});
            const initialAccountDate = await accountDate.inputValue();
            const accountReadiness = accountPage.getByRole('region', {{ name: '账户事实就绪度' }});
            const accountStructure = accountPage.getByRole('region', {{ name: '资产结构' }});
            await accountReadiness.waitFor({{ state: 'visible', timeout: 10000 }});
            await accountStructure.waitFor({{ state: 'visible', timeout: 10000 }});
            const maintenance = accountPage.locator('#account-facts');
            const maintenanceSummary = maintenance.locator(':scope > summary');
            await maintenanceSummary.focus();
            await maintenanceSummary.click();
            const accountLedger = accountPage.getByRole('table', {{ name: '账户明细' }});
            await accountLedger.waitFor({{ state: 'visible', timeout: 10000 }});
            const accountShell = accountPage.getByRole('region', {{ name: '账户内容区域' }});
            const [accountShellBox, accountReadinessBox, accountStructureBox, accountLedgerBox] = await Promise.all([
              accountShell.boundingBox(),
              accountReadiness.boundingBox(),
              accountStructure.boundingBox(),
              accountLedger.boundingBox(),
            ]);
            const accountShellStyle = await accountShell.evaluate(element => {{
              const style = window.getComputedStyle(element);
              return {{
                paddingLeft: Number.parseFloat(style.paddingLeft),
                paddingRight: Number.parseFloat(style.paddingRight),
              }};
            }});
            const accountMetrics = {{
              shellWidth: accountShellBox?.width || 0,
              readinessWidth: accountReadinessBox?.width || 0,
              structureWidth: accountStructureBox?.width || 0,
              ledgerWidth: accountLedgerBox?.width || 0,
              rowCount: await accountLedger.getByRole('row').count(),
              portfolioCount: await accountStructure.locator('.account-portfolio-card').count(),
              readinessTitle: await accountReadiness.getByRole('heading', {{ level: 2 }}).innerText(),
              issueEntryCount: await accountPage.locator('button', {{ hasText: '查看并更新这些账户' }}).count(),
              maintenanceOpen: await maintenance.evaluate(element => element.open),
              editableDate: await accountDate.inputValue(),
              shellStyle: accountShellStyle,
            }};
            const accountDifferences = [];
            if ({json.dumps(viewport_name)} !== 'narrow') {{
              if (Math.abs(accountMetrics.shellWidth - {WORKBENCH_WIDTH}) > {GEOMETRY_TOLERANCE}) {{
                accountDifferences.push(difference(
                  'account.shellWidth',
                  {WORKBENCH_WIDTH},
                  accountMetrics.shellWidth,
                  {GEOMETRY_TOLERANCE},
                ));
              }}
            }} else if (
              Math.abs(accountMetrics.shellStyle.paddingLeft - {NARROW_GUTTER}) > {GEOMETRY_TOLERANCE}
              || Math.abs(accountMetrics.shellStyle.paddingRight - {NARROW_GUTTER}) > {GEOMETRY_TOLERANCE}
            ) {{
              accountDifferences.push(difference(
                'account.narrowGutter',
                {NARROW_GUTTER},
                accountMetrics.shellStyle,
                {GEOMETRY_TOLERANCE},
              ));
            }}
            for (const [metric, actual] of [
              ['readinessWidth', accountMetrics.readinessWidth],
              ['structureWidth', accountMetrics.structureWidth],
              ['ledgerWidth', accountMetrics.ledgerWidth],
            ]) {{
              if (Math.abs(accountMetrics.structureWidth - actual) > {GEOMETRY_TOLERANCE}) {{
                accountDifferences.push(difference(
                  'account.' + metric,
                  accountMetrics.structureWidth,
                  actual,
                  {GEOMETRY_TOLERANCE},
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
            if (accountMetrics.portfolioCount !== 3) {{
              accountDifferences.push(difference('account.portfolioCount', 3, accountMetrics.portfolioCount));
            }}
            if (accountMetrics.readinessTitle !== '事实已就绪') {{
              accountDifferences.push(difference('account.readinessTitle', '事实已就绪', accountMetrics.readinessTitle));
            }}
            if (accountMetrics.issueEntryCount !== 1 || !accountMetrics.maintenanceOpen) {{
              accountDifferences.push(difference(
                'account.maintenanceEntry',
                {{ issueEntryCount: 1, maintenanceOpen: true }},
                {{ issueEntryCount: accountMetrics.issueEntryCount, maintenanceOpen: accountMetrics.maintenanceOpen }},
              ));
            }}
            assertNoDifferences('account.behavior', '账户页关键可见行为异常', accountDifferences);
            const accountMaintenanceNarrowChecks = [];
            if ({json.dumps(viewport_name)} === 'narrow') {{
              const narrowAccountPage = await page.context().newPage();
              await narrowAccountPage.setViewportSize({json.dumps(VIEWPORTS["narrow"])});
              await narrowAccountPage.goto(
                {json.dumps(base_url + '#account')},
                {{ waitUntil: 'domcontentloaded', timeout: 15000 }},
              );
              await narrowAccountPage.waitForFunction(() => window.Alpine, {{ timeout: 10000 }});
              const narrowAccount = narrowAccountPage.getByRole('region', {{ name: '账户工作台' }});
              const narrowMaintenance = narrowAccount.locator('#account-facts');
              await narrowMaintenance.locator(':scope > summary').click();
              const narrowLedger = narrowAccount.getByRole('table', {{ name: '账户明细' }});
              await narrowLedger.waitFor({{ state: 'visible', timeout: 10000 }});
              const narrowStockLedgerRow = narrowLedger.locator('tr').filter({{ hasText: '广发账户' }}).first();
              await narrowStockLedgerRow.focus();
              await narrowStockLedgerRow.press('Space');
              const stockMaintenance = narrowAccount.locator('tr.account-position-row:visible');
              await stockMaintenance.waitFor({{ state: 'visible', timeout: 10000 }});
              const addRow = stockMaintenance.locator('button', {{ hasText: '添加行' }});
              await addRow.focus();
              await addRow.press('Enter');
              const codeInput = stockMaintenance.getByLabel('股票持仓代码 新行', {{ exact: true }});
              const quantityInput = stockMaintenance.getByLabel('股票持仓数量 新行', {{ exact: true }});
              const availableCash = stockMaintenance.getByLabel('股票可用现金', {{ exact: true }});
              const deleteRow = stockMaintenance.getByRole('button', {{ name: '删除股票持仓 新行', exact: true }});
              const save = stockMaintenance.getByRole('button', {{ name: '保存', exact: true }});
              await Promise.all([
                codeInput.waitFor({{ state: 'visible', timeout: 10000 }}),
                quantityInput.waitFor({{ state: 'visible', timeout: 10000 }}),
                deleteRow.waitFor({{ state: 'visible', timeout: 10000 }}),
              ]);
              await codeInput.focus();
              const [scrollMetrics, focusMetrics, targetMetrics, entryDisplay] = await Promise.all([
                narrowAccount.evaluate(element => ({{
                  clientWidth: element.clientWidth,
                  scrollWidth: element.scrollWidth,
                  documentClientWidth: document.documentElement.clientWidth,
                  documentScrollWidth: document.documentElement.scrollWidth,
                }})),
                codeInput.evaluate(element => {{
                  const style = window.getComputedStyle(element);
                  return {{
                    focused: document.activeElement === element,
                    outlineStyle: style.outlineStyle,
                    outlineWidth: Number.parseFloat(style.outlineWidth),
                  }};
                }}),
                Promise.all([availableCash, codeInput, quantityInput, deleteRow, save].map(async control => {{
                  const box = await control.boundingBox();
                  return {{ label: await control.getAttribute('aria-label') || await control.innerText(), height: box?.height || 0 }};
                }})),
                stockMaintenance.locator('tr.account-position-entry:visible').first().evaluate(
                  element => window.getComputedStyle(element).display,
                ),
              ]);
              const narrowDifferences = [];
              if (scrollMetrics.scrollWidth > scrollMetrics.clientWidth + 2
                  || scrollMetrics.documentScrollWidth > scrollMetrics.documentClientWidth + 2) {{
                narrowDifferences.push(difference(
                  'account.narrow.maintenance.scrollWidth',
                  {{ account: 'no overflow', document: 'no overflow' }},
                  scrollMetrics,
                ));
              }}
              if (!focusMetrics.focused || focusMetrics.outlineStyle === 'none' || focusMetrics.outlineWidth < 2) {{
                narrowDifferences.push(difference(
                  'account.narrow.maintenance.focusVisible',
                  {{ focused: true, outline: {{ minWidth: 2 }} }},
                  focusMetrics,
                ));
              }}
              if (entryDisplay !== 'grid') {{
                narrowDifferences.push(difference(
                  'account.narrow.maintenance.verticalEntry', 'grid', entryDisplay,
                ));
              }}
              targetMetrics.forEach(target => {{
                const minimum = target.label.startsWith('删除') ? 36 : 42;
                if (target.height < minimum) {{
                  narrowDifferences.push(difference(
                    'account.narrow.maintenance.touchTarget',
                    {{ control: target.label, minHeight: minimum }},
                    target,
                  ));
                }}
              }});
              await codeInput.press('Tab');
              const focusedAfterTab = await narrowAccountPage.evaluate(
                () => document.activeElement?.getAttribute('aria-label') || '',
              );
              if (focusedAfterTab !== '股票持仓数量 新行') {{
                narrowDifferences.push(difference(
                  'account.narrow.maintenance.keyboardTab',
                  '股票持仓数量 新行',
                  focusedAfterTab,
                ));
              }}
              await deleteRow.focus();
              await deleteRow.press('Enter');
              if (await stockMaintenance.getByLabel('股票持仓代码 新行', {{ exact: true }}).count()) {{
                narrowDifferences.push(difference(
                  'account.narrow.maintenance.keyboardDelete', 0,
                  await stockMaintenance.getByLabel('股票持仓代码 新行', {{ exact: true }}).count(),
                ));
              }}
              await save.focus();
              const keyboardSaveHandler = route => route.fulfill({{
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({{ accounts: [] }}),
              }});
              await narrowAccountPage.route('**/api/account/stock/state', keyboardSaveHandler);
              const keyboardSaveRequest = narrowAccountPage.waitForRequest(
                request => request.url().includes('/api/account/stock/state') && request.method() === 'POST',
                {{ timeout: 10000 }},
              );
              await save.press('Enter');
              await keyboardSaveRequest;
              await narrowAccountPage.unroute('**/api/account/stock/state', keyboardSaveHandler);
              assertNoDifferences(
                'account.narrow.maintenance',
                '390px 账户维护存在横向裁切、焦点或触达尺寸问题',
                narrowDifferences,
              );
              accountMaintenanceNarrowChecks.push({{
                name: 'expanded-account-maintenance',
                scrollMetrics,
                focusMetrics,
                targetMetrics,
                entryDisplay,
                focusedAfterTab,
                savedByKeyboard: true,
              }});
              await narrowAccountPage.close();
            }}
            const accountFactDateIdentityChecks = [];
            const dateIdentityConsoleErrorStart = consoleErrors.length;
            await accountDate.fill('');
            await accountDate.dispatchEvent('change');
            await page.waitForFunction(
              expected => document.querySelector('input[aria-label="账户事实日"]')?.value === expected,
              initialAccountDate,
            );
            const restoredEmptyDate = await accountDate.inputValue();
            if (restoredEmptyDate !== initialAccountDate) {{
              fail('account.date-identity', '清空事实日后未恢复当前数据身份', [
                difference('date-clear-restores-current-fact', initialAccountDate, restoredEmptyDate),
              ]);
            }}
            const alternateFactDate = '2026-01-02';
            const datedRequests = [];
            const readinessRequests = [];
            const recordDatedRequest = request => {{
              if (request.url().includes(`date=${{alternateFactDate}}`)) datedRequests.push(request.url());
              if (request.url().includes('/api/plan/readiness')) readinessRequests.push(request.url());
            }};
            page.on('request', recordDatedRequest);
            const savedPayloads = [];
            let releaseStateSave;
            let markStateSaveReceived;
            const stateSavePending = new Promise(resolve => {{ releaseStateSave = resolve; }});
            const stateSaveReceived = new Promise(resolve => {{ markStateSaveReceived = resolve; }});
            const stateSaveHandler = async route => {{
              savedPayloads.push(JSON.parse(route.request().postData() || '{{}}'));
              markStateSaveReceived();
              await stateSavePending;
              await route.fulfill({{
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({{ accounts: [] }}),
              }});
            }};
            const quoteHandler = route => route.fulfill({{
              status: 200,
              contentType: 'application/json',
              body: JSON.stringify({{ code: '000001', name: '旧日期持仓', price: 10 }}),
            }});
            await page.route('**/api/account/stock/state', stateSaveHandler);
            await page.route('**/api/positions/stock/quote?code=000001', quoteHandler);
            await accountLedger.locator('tr').filter({{ hasText: '广发账户' }}).first().click();
            const addOldFactRow = accountPage.locator('button', {{ hasText: '添加行' }}).first();
            await addOldFactRow.click();
            const oldFactCode = accountPage.locator('input[placeholder="代码"]').first();
            await oldFactCode.fill('000001');
            await oldFactCode.dispatchEvent('change');
            await accountDate.fill(alternateFactDate);
            const dirtyGate = accountPage.getByRole('alert').filter({{ hasText: '当前事实日有未保存编辑' }});
            await dirtyGate.waitFor({{ state: 'visible', timeout: 10000 }});
            const blockedSave = accountPage.locator('button', {{ hasText: '保存' }}).first();
            const dateIdentityDifferences = [];
            if (!await blockedSave.isDisabled()) {{
              dateIdentityDifferences.push(difference('date-switch-dirty-gate.saveDisabled', true, false));
            }}
            if (savedPayloads.length) {{
              dateIdentityDifferences.push(difference('date-switch-dirty-gate.requestsBeforeConfirm', 0, savedPayloads.length));
            }}
            await dirtyGate.getByRole('button', {{ name: '保留当前日期', exact: true }}).click();
            const retainedFactDate = await accountDate.inputValue();
            if (retainedFactDate !== initialAccountDate) {{
              dateIdentityDifferences.push(difference('date-switch-dirty-gate.retainedDate', initialAccountDate, retainedFactDate));
            }}
            await accountDate.fill(alternateFactDate);
            await dirtyGate.waitFor({{ state: 'visible', timeout: 10000 }});
            let releaseAlternateSummary;
            let markAlternateSummaryStarted;
            const alternateSummaryPending = new Promise(resolve => {{ releaseAlternateSummary = resolve; }});
            const alternateSummaryStarted = new Promise(resolve => {{ markAlternateSummaryStarted = resolve; }});
            const alternateSummaryHandler = async route => {{
              markAlternateSummaryStarted();
              await alternateSummaryPending;
              await route.continue();
            }};
            await page.route(`**/api/account/summary?date=${{alternateFactDate}}`, alternateSummaryHandler);
            await dirtyGate.getByRole('button', {{ name: '放弃编辑并读取新日期', exact: true }}).click();
            await alternateSummaryStarted;
            if (!await blockedSave.isDisabled()) {{
              dateIdentityDifferences.push(difference('date-switch-loading-gate.saveDisabled', true, false));
            }}
            if (savedPayloads.length) {{
              dateIdentityDifferences.push(difference('date-switch-loading-gate.requestsBeforeLoad', 0, savedPayloads.length));
            }}
            releaseAlternateSummary();
            await page.waitForFunction(
              expected => document.querySelector('input[aria-label="账户事实日"]')?.value === expected,
              alternateFactDate,
            );
            await accountReadiness.getByRole('heading', {{ name: '尚未录入账户事实', level: 2 }})
              .waitFor({{ state: 'visible', timeout: 10000 }});
            await page.unroute(`**/api/account/summary?date=${{alternateFactDate}}`, alternateSummaryHandler);
            if (!await maintenance.evaluate(element => element.open)) {{
              await maintenanceSummary.click();
            }}
            await accountLedger.waitFor({{ state: 'visible', timeout: 10000 }});
            await accountLedger.locator('tr').filter({{ hasText: '广发账户' }}).first().click();
            const stockSave = accountPage.locator('button', {{ hasText: '保存' }}).first();
            const readinessBeforeSuccessfulSave = readinessRequests.length;
            await stockSave.click();
            await stateSaveReceived;
            if (savedPayloads.length !== 1) {{
              dateIdentityDifferences.push(difference('save-in-progress.firstRequestCount', 1, savedPayloads.length));
            }}
            if (!await stockSave.isDisabled()) {{
              dateIdentityDifferences.push(difference('save-in-progress.saveDisabled', true, false));
            }}
            await stockSave.evaluate(button => button.click());
            if (savedPayloads.length !== 1) {{
              dateIdentityDifferences.push(difference('save-in-progress.duplicateRequestCount', 1, savedPayloads.length));
            }}
            releaseStateSave();
            await page.waitForTimeout(350);
            const requiredDateRequests = [
              `/api/account/summary?date=${{alternateFactDate}}`,
              `/api/positions/stock?date=${{alternateFactDate}}`,
              `/api/positions/cb?date=${{alternateFactDate}}`,
              `/api/positions/stock/quotes?date=${{alternateFactDate}}`,
              `/api/positions/cb/quotes?date=${{alternateFactDate}}`,
            ];
            requiredDateRequests.forEach(path => {{
              if (!datedRequests.some(url => url.includes(path))) {{
                dateIdentityDifferences.push(difference(`date-switch-loads.${{path}}`, true, false));
              }}
            }});
            const savedPayload = savedPayloads[0];
            if (!savedPayload || savedPayload.snapshot_date !== alternateFactDate) {{
              dateIdentityDifferences.push(difference(
                'date-switch-save-date', alternateFactDate, savedPayload?.snapshot_date || null,
              ));
            }}
            if ((savedPayload?.positions || []).some(row => row.code === '000001')) {{
              dateIdentityDifferences.push(difference('old fact rows', false, true));
            }}
            requiredDateRequests.forEach(path => {{
              const requestCount = datedRequests.filter(url => url.includes(path)).length;
              if (requestCount < 2) {{
                dateIdentityDifferences.push(difference(`save-success-refresh.${{path}}`, {{ min: 2 }}, requestCount));
              }}
            }});
            if (readinessRequests.length < readinessBeforeSuccessfulSave + 2) {{
              dateIdentityDifferences.push(difference(
                'save-success-refresh.readiness',
                {{ min: readinessBeforeSuccessfulSave + 2 }},
                readinessRequests.length,
              ));
            }}
            await page.unroute('**/api/account/stock/state', stateSaveHandler);

            const failedFactDate = '2026-01-03';
            const failedSummaryHandler = route => route.fulfill({{
              status: 503,
              contentType: 'application/json',
              body: JSON.stringify({{ detail: '模拟事实日读取失败' }}),
            }});
            await page.route(`**/api/account/summary?date=${{failedFactDate}}`, failedSummaryHandler);
            await accountDate.fill(failedFactDate);
            await accountReadiness.getByRole('heading', {{ name: '无法确认账户事实', level: 2 }})
              .waitFor({{ state: 'visible', timeout: 10000 }});
            if (!await stockSave.isDisabled()) {{
              dateIdentityDifferences.push(difference('date-switch-error-gate.saveDisabled', true, false));
            }}
            if (savedPayloads.length !== 1) {{
              dateIdentityDifferences.push(difference('date-switch-error-gate.requestsBeforeRecovery', 1, savedPayloads.length));
            }}
            await page.unroute(`**/api/account/summary?date=${{failedFactDate}}`, failedSummaryHandler);
            await accountDate.fill(alternateFactDate);
            await page.waitForFunction(
              expected => document.querySelector('input[aria-label="账户事实日"]')?.value === expected,
              alternateFactDate,
            );
            if (!await maintenance.evaluate(element => element.open)) {{
              await maintenanceSummary.click();
            }}
            await accountLedger.waitFor({{ state: 'visible', timeout: 10000 }});
            await accountLedger.locator('tr').filter({{ hasText: '广发账户' }}).first().click();
            const availableCash = accountPage.locator('tr.account-position-row input[placeholder="0"]').first();
            await availableCash.fill('4321');
            const failedSavePayloads = [];
            const failedStateSaveHandler = route => {{
              failedSavePayloads.push(JSON.parse(route.request().postData() || '{{}}'));
              return route.fulfill({{
                status: 503,
                contentType: 'application/json',
                body: JSON.stringify({{ detail: '模拟保存失败' }}),
              }});
            }};
            await page.route('**/api/account/stock/state', failedStateSaveHandler);
            await stockSave.click();
            await accountPage.getByRole('alert').filter({{ hasText: '模拟保存失败' }})
              .waitFor({{ state: 'visible', timeout: 10000 }});
            if (await availableCash.inputValue() !== '4321') {{
              dateIdentityDifferences.push(difference('save-error-preserves-input', '4321', await availableCash.inputValue()));
            }}
            const stockSaveError = accountPage.locator('#stock-save-error');
            const describedBy = await availableCash.getAttribute('aria-describedby');
            const focusedErrorInput = await page.evaluate(
              () => document.activeElement?.getAttribute('aria-label') || '',
            );
            if (!await stockSaveError.isVisible() || describedBy !== 'stock-save-error'
                || focusedErrorInput !== '股票可用现金') {{
              dateIdentityDifferences.push(difference(
                'save-error-locates-input',
                {{ errorVisible: true, describedBy: 'stock-save-error', focused: '股票可用现金' }},
                {{ errorVisible: await stockSaveError.isVisible(), describedBy, focused: focusedErrorInput }},
              ));
            }}
            if (failedSavePayloads.length !== 1 || failedSavePayloads[0]?.snapshot_date !== alternateFactDate) {{
              dateIdentityDifferences.push(difference(
                'save-error-payload',
                {{ count: 1, snapshot_date: alternateFactDate }},
                {{ count: failedSavePayloads.length, snapshot_date: failedSavePayloads[0]?.snapshot_date || null }},
              ));
            }}
            await page.unroute('**/api/account/stock/state', failedStateSaveHandler);
            const dateIdentityConsoleErrors = consoleErrors.splice(dateIdentityConsoleErrorStart);
            const unexpectedDateIdentityConsoleErrors = dateIdentityConsoleErrors.filter(
              message => !message.includes('status of 503') && !message.includes('模拟保存失败'),
            );
            if (unexpectedDateIdentityConsoleErrors.length) {{
              dateIdentityDifferences.push(difference(
                'date-switch.unexpectedConsoleErrors', [], unexpectedDateIdentityConsoleErrors,
              ));
            }}
            assertNoDifferences(
              'account.date-identity',
              '事实日切换没有保持加载与保存的同一数据身份',
              dateIdentityDifferences,
            );
            accountFactDateIdentityChecks.push({{
              name: 'date-switch-dirty-gate',
              savedPayloads,
              datedRequests,
              consoleErrors: dateIdentityConsoleErrors,
            }});
            await page.unroute('**/api/positions/stock/quote?code=000001', quoteHandler);
            const accountIntegrity = await assertPageIntegrity('account', main);
            await main.evaluate(element => element.scrollTo({{ top: 0, left: 0 }}));
            await page.evaluate(() => window.scrollTo({{ top: 0, left: 0 }}));
            await accountHeading.scrollIntoViewIfNeeded();
            await page.waitForTimeout(100);
            await page.screenshot({{ path: {json.dumps(account_screenshot)}, fullPage: true }});
            await page.reload({{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await assertActivePage('account.reload', accountNav, '#account');
            await planNav.click();
            await assertActivePage('plan.fromAccount', planNav, '#trading');
            await page.goBack();
            await assertActivePage('account.back', accountNav, '#account');

            await page.goto({json.dumps(base_url + '#changelog')}, {{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await assertActivePage('changelog.direct', changelogNav, '#changelog');
            await main.getByRole('heading', {{ name: '更新日志', level: 1 }}).waitFor({{ state: 'visible', timeout: 10000 }});
            await page.reload({{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await assertActivePage('changelog.reload', changelogNav, '#changelog');
            await accountNav.click();
            await assertActivePage('account.fromChangelog', accountNav, '#account');
            await page.goBack();
            await assertActivePage('changelog.back', changelogNav, '#changelog');

            await page.goto({json.dumps(base_url + '#trading')}, {{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await assertActivePage('plan.direct', planNav, '#trading');
            await page.reload({{ waitUntil: 'domcontentloaded', timeout: 15000 }});
            await assertActivePage('plan.reload', planNav, '#trading');
            await accountNav.click();
            await assertActivePage('account.fromPlan', accountNav, '#account');
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
            const planHeading = planPage.getByRole('heading', {{ name: '今日计划', level: 1 }});
            await planHeading.waitFor({{ state: 'visible', timeout: 10000 }});
            const decisionHero = planPage.locator('.plan-decision-hero');
            const executionDossier = planPage.locator('.plan-execution-dossier');
            const readinessPanel = planPage.locator('.plan-readiness-panel');
            await decisionHero.waitFor({{ state: 'visible', timeout: 10000 }});
            await executionDossier.waitFor({{ state: 'visible', timeout: 10000 }});
            await readinessPanel.waitFor({{ state: 'visible', timeout: 10000 }});
            const fundingPlanPanel = executionDossier.locator('.plan-funding-plan');
            const accountPlanCards = executionDossier.locator('.plan-account-card');
            await fundingPlanPanel.waitFor({{ state: 'visible', timeout: 10000 }});
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
            const [planShellBox, decisionBox, dossierBox, readinessBox] = await Promise.all([
              planPage.boundingBox(),
              decisionHero.boundingBox(),
              executionDossier.boundingBox(),
              readinessPanel.boundingBox(),
            ]);
            const planStateTitle = await decisionHero.getByRole('heading', {{ level: 2 }}).innerText();
            const planSectionOrder = await planPage.evaluate(element => {{
              const selectors = ['.plan-decision-hero', '.plan-execution-dossier', '.plan-readiness-panel'];
              return selectors.map(selector => Array.from(element.children).findIndex(child => child.matches(selector)));
            }});
            const fundingBeforeAccounts = await executionDossier.evaluate(element => {{
              const funding = element.querySelector('.plan-funding-plan');
              const accounts = element.querySelector('[aria-label="账户交易计划"]');
              if (!accounts) return true;
              return Boolean(funding)
                && Boolean(funding.compareDocumentPosition(accounts) & Node.DOCUMENT_POSITION_FOLLOWING);
            }});
            const accountsCollapsedByDefault = await accountPlanCards.evaluateAll(cards =>
              cards.every(card => !card.open)
            );
            const planMetrics = {{
              shellWidth: planShellBox?.width || 0,
              decisionWidth: decisionBox?.width || 0,
              dossierWidth: dossierBox?.width || 0,
              readinessWidth: readinessBox?.width || 0,
              stateTitle: planStateTitle,
              sectionOrder: planSectionOrder,
              fundingBeforeAccounts,
              accountsCollapsedByDefault,
              fundingActionCount: await fundingPlanPanel.locator('.plan-funding-action').count(),
              accountPlanCount: await accountPlanCards.count(),
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
              if (Math.abs(planMetrics.shellWidth - {WORKBENCH_WIDTH}) > {GEOMETRY_TOLERANCE}) {{
                planDifferences.push(difference(
                  'plan.shellWidth',
                  {WORKBENCH_WIDTH},
                  planMetrics.shellWidth,
                  {GEOMETRY_TOLERANCE},
                ));
              }}
            }}
            for (const [metric, actual] of [
              ['dossierWidth', planMetrics.dossierWidth],
              ['readinessWidth', planMetrics.readinessWidth],
            ]) {{
              if (Math.abs(planMetrics.decisionWidth - actual) > {GEOMETRY_TOLERANCE}) {{
                planDifferences.push(difference(
                  'plan.' + metric,
                  planMetrics.decisionWidth,
                  actual,
                  {GEOMETRY_TOLERANCE},
                ));
              }}
            }}
            const validPlanStates = [
              '正在生成完整计划',
              '待补充账户事实',
              '计划已失效',
              '计划生成失败',
              '今日无需操作',
            ];
            if (!validPlanStates.includes(planMetrics.stateTitle)
                && !planMetrics.stateTitle.startsWith('今日需要操作')) {{
              planDifferences.push(difference('plan.stateTitle', validPlanStates, planMetrics.stateTitle));
            }}
            const sectionOrderIsValid = planMetrics.sectionOrder.every(
              (value, index, values) => value >= 0 && (index === 0 || value > values[index - 1]),
            );
            if (!sectionOrderIsValid) {{
              planDifferences.push(difference(
                'plan.sectionOrder',
                'decision < actions < readiness',
                planMetrics.sectionOrder,
              ));
            }}
            if (!planMetrics.fundingBeforeAccounts || !planMetrics.accountsCollapsedByDefault) {{
              planDifferences.push(difference(
                'plan.executionHierarchy',
                {{ fundingBeforeAccounts: true, accountsCollapsedByDefault: true }},
                {{
                  fundingBeforeAccounts: planMetrics.fundingBeforeAccounts,
                  accountsCollapsedByDefault: planMetrics.accountsCollapsedByDefault,
                }},
              ));
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
            await main.evaluate(element => element.scrollTo({{ top: 0, left: 0 }}));
            await page.evaluate(() => window.scrollTo({{ top: 0, left: 0 }}));
            await planHeading.scrollIntoViewIfNeeded();
            await page.waitForTimeout(100);
            await page.screenshot({{ path: {json.dumps(plan_screenshot)}, fullPage: true }});
            let expandedAccountPlanChecks = null;

            const scenarioFixtures = await page.evaluate(async () => {{
              const readiness = await fetch('/api/plan/readiness').then(response => response.json());
              const generation = await fetch('/api/plan/generated')
                .then(response => response.json())
                .then(body => body.generation);
              const savedRecord = await fetch(`/api/plan/generated/${{generation.plan_id}}`)
                .then(response => response.json());
              const plan = savedRecord.plan || savedRecord;
              return {{ readiness, generation, plan }};
            }});
            const scenarioPlan = (planId, mode) => {{
              const plan = JSON.parse(JSON.stringify(scenarioFixtures.plan));
              const withActions = mode !== 'no-action';
              const futureFunding = mode === 'future-funding';
              plan.generation = {{
                ...plan.generation,
                plan_id: planId,
                plan_date: scenarioFixtures.readiness.plan_date,
                status: 'complete',
              }};
              plan.execution_sequence = withActions
                ? [
                    {{
                      phase: 'sell',
                      orders: [{{
                        strategy: 'stock',
                        action: 'SELL',
                        stock_code: '600051',
                        stock_name: '宁波联合',
                        delta_shares: -100,
                        price: 5.68,
                        amount: 568,
                      }}],
                    }},
                    {{ phase: 'transfer', orders: [] }},
                    {{ phase: 'buy', orders: [] }},
                  ]
                : [
                    {{ phase: 'sell', orders: [] }},
                    {{ phase: 'transfer', orders: [] }},
                    {{ phase: 'buy', orders: [] }},
                  ];
              plan.cb = {{
                ...plan.cb,
                orders: withActions ? [{{
                  action: 'BUY',
                  bond_code: '123456',
                  bond_name: '测试转债',
                  delta_shares: 10,
                  price: 101.25,
                  amount: 1012.5,
                }}] : [],
              }};
              plan.stock = {{
                ...plan.stock,
                orders: withActions ? [{{
                  action: 'SELL',
                  stock_code: '600051',
                  stock_name: '宁波联合',
                  delta_shares: -100,
                  price: 5.68,
                  amount: 568,
                }}] : [],
              }};
              if (plan.fund_transfer?.top_level) {{
                plan.fund_transfer.top_level.executed_actions = [];
                plan.fund_transfer.top_level.outflows = [];
              }}
              if (plan.fund_transfer?.a_internal) plan.fund_transfer.a_internal.actions = [];
              const fundingAvailability = futureFunding ? 'deferred' : 'same_day';
              const fundingLabel = futureFunding ? '等待未来资金可用' : '当日可用';
              const fundingState = futureFunding ? 'waits_for_funds' : 'needs_same_day_transfer';
              plan.execution_read_model = {{
                plan_date: scenarioFixtures.readiness.plan_date,
                funding_plan: withActions ? {{
                  groups: [{{
                    availability: fundingAvailability,
                    label: fundingLabel,
                    actions: [{{
                      source_account_id: 'cash',
                      source_account_name: '资金账户',
                      target_account_id: 'stock',
                      target_account_name: '广发账户',
                      amount: 6000,
                      reason: 'a_internal_rebalance',
                      reason_label: '完成主动组合内部资金调整',
                      availability: fundingAvailability,
                      cash_effect: futureFunding ? 'deferred_cash_in' : 'immediate_cash_in',
                    }}],
                  }}],
                }} : null,
                account_trading_plans: withActions ? [
                  {{
                    account_id: 'stock',
                    account_name: '广发账户',
                    portfolio_name: '主动组合',
                    strategy_name: '小市值股票策略',
                    trade_date: scenarioFixtures.readiness.plan_date,
                    funding: {{
                      state: fundingState,
                      available_on: fundingAvailability,
                      transfer_in: 6000,
                      transfer_out: 0,
                      blocked_reason: null,
                    }},
                    trade_summary: {{
                      sell_count: 1,
                      sell_estimated_amount: 568,
                      buy_count: 1,
                      buy_estimated_amount: 1012.5,
                    }},
                    cash: {{
                      starting_available: 12000,
                      transfer_in: 6000,
                      transfer_out: 0,
                      expected_sell: 568,
                      expected_buy: 1012.5,
                      expected_ending: 17555.5,
                    }},
                    phases: [
                      {{
                        phase: 'sell',
                        orders: [{{
                          action: 'SELL',
                          code: '600051',
                          name: '宁波联合',
                          quantity: 100,
                          unit: '股',
                          reference_price: 5.68,
                          estimated_amount: 568,
                        }}],
                      }},
                      {{
                        phase: 'buy',
                        orders: [{{
                          action: 'BUY',
                          code: '600052',
                          name: '东望时代',
                          quantity: 100,
                          unit: '股',
                          reference_price: 10.125,
                          estimated_amount: 1012.5,
                        }}],
                      }},
                    ],
                  }},
                  {{
                    account_id: 'cb',
                    account_name: '华泰账户',
                    portfolio_name: '主动组合',
                    strategy_name: '多因子可转债策略',
                    trade_date: scenarioFixtures.readiness.plan_date,
                    funding: {{
                      state: 'ready',
                      available_on: null,
                      transfer_in: 0,
                      transfer_out: 0,
                      blocked_reason: null,
                    }},
                    trade_summary: {{
                      sell_count: 0,
                      sell_estimated_amount: 0,
                      buy_count: 1,
                      buy_estimated_amount: 1012.5,
                    }},
                    cash: {{
                      starting_available: 8000,
                      transfer_in: 0,
                      transfer_out: 0,
                      expected_sell: 0,
                      expected_buy: 1012.5,
                      expected_ending: 6987.5,
                    }},
                    phases: [{{
                      phase: 'buy',
                      orders: [{{
                        action: 'ADD',
                        code: '123456',
                        name: '测试转债',
                        quantity: 10,
                        unit: '张',
                        reference_price: 101.25,
                        estimated_amount: 1012.5,
                      }}],
                    }}],
                  }},
                ] : [],
              }};
              return plan;
            }};
            const actionPlan = scenarioPlan('scenario-action', 'action');
            const futurePlan = scenarioPlan('scenario-future-funding', 'future-funding');
            const noActionPlan = scenarioPlan('scenario-no-action', 'no-action');
            const completeGeneration = planId => ({{
              ...scenarioFixtures.generation,
              plan_id: planId,
              plan_date: scenarioFixtures.readiness.plan_date,
              status: 'complete',
              error: null,
            }});
            const needsFactsReadiness = JSON.parse(JSON.stringify(scenarioFixtures.readiness));
            needsFactsReadiness.status = 'needs_facts';
            needsFactsReadiness.accounts[0] = {{
              ...needsFactsReadiness.accounts[0],
              status: 'missing',
              snapshot_date: null,
            }};
            needsFactsReadiness.errors = [{{
              account_id: needsFactsReadiness.accounts[0].account_id,
              message: '缺少账户事实',
            }}];
            const planScenarios = [
              {{
                name: 'needs-facts',
                readiness: needsFactsReadiness,
                generation: completeGeneration('scenario-action'),
                plan: actionPlan,
                expectedTitle: '待补充账户事实',
                executionDossierVisible: false,
                fundingPlanVisible: false,
                expectedAccountPlanCount: 0,
                generateDisabled: true,
                focusAccountId: needsFactsReadiness.accounts[0].account_id,
              }},
              {{
                name: 'running',
                readiness: scenarioFixtures.readiness,
                generation: {{
                  ...scenarioFixtures.generation,
                  plan_id: 'scenario-running',
                  status: 'running',
                }},
                plan: null,
                expectedTitle: '正在生成完整计划',
                executionDossierVisible: false,
                fundingPlanVisible: false,
                expectedAccountPlanCount: 0,
                generateDisabled: true,
              }},
              {{
                name: 'no-action',
                readiness: scenarioFixtures.readiness,
                generation: completeGeneration('scenario-no-action'),
                plan: noActionPlan,
                expectedTitle: '今日无需操作',
                executionDossierVisible: false,
                fundingPlanVisible: false,
                expectedAccountPlanCount: 0,
                generateDisabled: false,
              }},
              {{
                name: 'action',
                readiness: scenarioFixtures.readiness,
                generation: completeGeneration('scenario-action'),
                plan: actionPlan,
                expectedTitle: '今日需要操作',
                executionDossierVisible: true,
                fundingPlanVisible: true,
                expectedAccountPlanCount: 2,
                generateDisabled: false,
              }},
              {{
                name: 'future-funding',
                readiness: scenarioFixtures.readiness,
                generation: completeGeneration('scenario-future-funding'),
                plan: futurePlan,
                expectedTitle: '今日需要操作',
                executionDossierVisible: true,
                fundingPlanVisible: true,
                expectedAccountPlanCount: 2,
                generateDisabled: false,
              }},
              {{
                name: 'readiness-error',
                readiness: null,
                readinessStatus: 503,
                readinessBody: {{
                  detail: {{
                    code: 'PLAN_READINESS_UNAVAILABLE',
                    message: '无法读取计划输入就绪度，请稍后重试。',
                  }},
                }},
                generation: completeGeneration('scenario-action'),
                plan: actionPlan,
                expectedTitle: '无法确认账户事实',
                executionDossierVisible: false,
                fundingPlanVisible: false,
                expectedAccountPlanCount: 0,
                generateDisabled: true,
                retryReadiness: true,
              }},
              {{
                name: 'stale',
                readiness: scenarioFixtures.readiness,
                generation: {{
                  ...scenarioFixtures.generation,
                  plan_id: 'scenario-stale',
                  status: 'stale',
                  error: {{ code: 'PLAN_INPUTS_CHANGED', message: '计划输入已变化' }},
                }},
                plan: null,
                expectedTitle: '计划已失效',
                executionDossierVisible: false,
                fundingPlanVisible: false,
                expectedAccountPlanCount: 0,
                generateDisabled: false,
              }},
              {{
                name: 'failed',
                readiness: scenarioFixtures.readiness,
                generation: {{
                  ...scenarioFixtures.generation,
                  plan_id: 'scenario-failed',
                  status: 'failed',
                  error: {{ code: 'PLAN_GENERATION_FAILED', message: '计划生成失败' }},
                }},
                plan: null,
                expectedTitle: '计划生成失败',
                executionDossierVisible: false,
                fundingPlanVisible: false,
                expectedAccountPlanCount: 0,
                generateDisabled: false,
              }},
            ];
            const planScenarioChecks = [];
            for (const scenario of planScenarios) {{
              const consoleErrorStart = consoleErrors.length;
              const scenarioRequests = [];
              const readinessHandler = route => {{
                scenarioRequests.push('/' + route.request().url().split('/').slice(3).join('/').split('?')[0]);
                return route.fulfill({{
                  status: scenario.readinessStatus || 200,
                  contentType: 'application/json',
                  body: JSON.stringify(scenario.readinessBody || scenario.readiness),
                }});
              }};
              const generationHandler = route => {{
                const path = '/' + route.request().url().split('/').slice(3).join('/').split('?')[0];
                scenarioRequests.push(path);
                if (path === '/api/plan/generated') {{
                  return route.fulfill({{
                    status: 200,
                    contentType: 'application/json',
                    body: JSON.stringify({{ generation: scenario.generation }}),
                  }});
                }}
                if (path.startsWith('/api/plan/generated/') && scenario.plan) {{
                  return route.fulfill({{
                    status: 200,
                    contentType: 'application/json',
                    body: JSON.stringify(scenario.plan),
                  }});
                }}
                return route.fulfill({{ status: 404, body: 'not found' }});
              }};
              await page.route('**/api/plan/readiness', readinessHandler);
              await page.route('**/api/plan/generated**', generationHandler);
              await page.goto(
                {json.dumps(base_url)} + `?ui_scenario=${{scenario.name}}#trading`,
                {{ waitUntil: 'domcontentloaded', timeout: 15000 }},
              );
              await page.waitForFunction(
                () => Array.from(document.querySelectorAll('[role="region"][aria-label="计划工作台"]'))
                  .find(element => element.checkVisibility())
                  ?.getAttribute('aria-busy') === 'false',
                {{ timeout: 10000 }},
              );
              const scenarioPage = main.getByRole('region', {{ name: '计划工作台' }});
              const scenarioHero = scenarioPage.locator('.plan-decision-hero');
              const scenarioDossier = scenarioPage.locator('.plan-execution-dossier');
              const scenarioFundingPlan = scenarioPage.locator('.plan-funding-plan');
              const scenarioAccountPlans = scenarioPage.locator('.plan-account-card');
              const scenarioExecutionBadge = scenarioPage.locator('.plan-execution-badge');
              const generateButton = scenarioPage.getByRole('button', {{ name: '生成完整计划' }});
              const actualTitle = await scenarioHero.getByRole('heading', {{ level: 2 }}).innerText();
              const executionDossierVisible = await scenarioDossier.isVisible();
              const executionDossierCount = await scenarioDossier.count();
              const fundingPlanVisible = await scenarioFundingPlan.isVisible();
              const accountPlanCount = await scenarioAccountPlans.count();
              const accountsCollapsedByDefault = accountPlanCount > 0
                ? await scenarioAccountPlans.evaluateAll(cards => cards.every(card => !card.open))
                : true;
              const noActionConclusion = scenario.name === 'no-action'
                ? {{
                    decisionCopy: await scenarioHero.locator('.plan-decision-copy').innerText(),
                    decisionMark: await scenarioHero.locator('.plan-decision-mark').innerText(),
                  }}
                : null;
              const executionBadgeVisible = await scenarioExecutionBadge.isVisible();
              const generateDisabled = await generateButton.isDisabled();
              const legacyPanelsVisible = await scenarioPage
                .locator('.legacy-plan-transfer-panel:visible, .legacy-plan-order-panel:visible')
                .count();
              const scenarioDifferences = [];
              if (!scenarioRequests.includes('/api/plan/readiness')) {{
                scenarioDifferences.push(difference(
                  `plan.scenarios.${{scenario.name}}.requests`,
                  ['/api/plan/readiness'],
                  scenarioRequests,
                ));
              }}
              if (actualTitle !== scenario.expectedTitle) {{
                scenarioDifferences.push(difference(
                  `plan.scenarios.${{scenario.name}}.title`,
                  scenario.expectedTitle,
                  actualTitle,
                ));
              }}
              if (executionDossierVisible !== scenario.executionDossierVisible) {{
                scenarioDifferences.push(difference(
                  `plan.scenarios.${{scenario.name}}.executionDossierVisible`,
                  scenario.executionDossierVisible,
                  executionDossierVisible,
                ));
              }}
              if (scenario.name === 'no-action' && executionDossierCount !== 0) {{
                scenarioDifferences.push(difference(
                  `plan.scenarios.${{scenario.name}}.executionDossierRendered`,
                  0,
                  executionDossierCount,
                ));
              }}
              if (scenario.name === 'no-action' && (
                !noActionConclusion.decisionCopy.includes('完整计划已检查')
                || !noActionConclusion.decisionMark.includes(scenario.readiness.plan_date)
                || !noActionConclusion.decisionMark.includes('事实窗口')
                || !noActionConclusion.decisionMark.includes(scenario.readiness.input_window.start)
                || !noActionConclusion.decisionMark.includes(scenario.readiness.input_window.end)
              )) {{
                scenarioDifferences.push(difference(
                  `plan.scenarios.${{scenario.name}}.noActionConclusion`,
                  {{
                    decisionCopyIncludes: '完整计划已检查',
                    decisionMarkIncludes: scenario.readiness.plan_date,
                    factWindowIncludes: [
                      '事实窗口',
                      scenario.readiness.input_window.start,
                      scenario.readiness.input_window.end,
                    ],
                  }},
                  noActionConclusion,
                ));
              }}
              if (fundingPlanVisible !== scenario.fundingPlanVisible) {{
                scenarioDifferences.push(difference(
                  `plan.scenarios.${{scenario.name}}.fundingPlanVisible`,
                  scenario.fundingPlanVisible,
                  fundingPlanVisible,
                ));
              }}
              if (accountPlanCount !== scenario.expectedAccountPlanCount) {{
                scenarioDifferences.push(difference(
                  `plan.scenarios.${{scenario.name}}.accountPlanCount`,
                  scenario.expectedAccountPlanCount,
                  accountPlanCount,
                ));
              }}
              if (!accountsCollapsedByDefault || legacyPanelsVisible !== 0) {{
                scenarioDifferences.push(difference(
                  `plan.scenarios.${{scenario.name}}.planHierarchy`,
                  {{ accountsCollapsedByDefault: true, legacyPanelsVisible: 0 }},
                  {{ accountsCollapsedByDefault, legacyPanelsVisible }},
                ));
              }}
              const expectedExecutionBadge = ['action', 'future-funding', 'no-action'].includes(scenario.name);
              if (executionBadgeVisible !== expectedExecutionBadge) {{
                scenarioDifferences.push(difference(
                  `plan.scenarios.${{scenario.name}}.executionBadgeVisible`,
                  expectedExecutionBadge,
                  executionBadgeVisible,
                ));
              }}
              if (generateDisabled !== scenario.generateDisabled) {{
                scenarioDifferences.push(difference(
                  `plan.scenarios.${{scenario.name}}.generateDisabled`,
                  scenario.generateDisabled,
                  generateDisabled,
                ));
              }}
              if (scenario.name === 'action' && accountPlanCount > 0) {{
                const firstAccountPlan = scenarioAccountPlans.first();
                await firstAccountPlan.locator(':scope > summary').click();
                expandedAccountPlanChecks = await firstAccountPlan.evaluate(card => {{
                  const rows = Array.from(card.querySelectorAll('.plan-trade-row'));
                  const cashLabels = Array.from(card.querySelectorAll('.plan-cash-label'))
                    .map(label => label.textContent.trim());
                  const requiredLabels = ['动作', '证券代码与名称', '数量', '参考价', '估算金额'];
                  const completeRows = rows.every(row => {{
                    const labels = Array.from(row.querySelectorAll('[data-label]'))
                      .map(cell => cell.dataset.label);
                    return requiredLabels.every(label => labels.includes(label));
                  }});
                  const scrollContainers = Array.from(card.querySelectorAll('.plan-trade-table-scroll'));
                  const noHorizontalOverflow = scrollContainers.every(container =>
                    container.scrollWidth <= container.clientWidth + 2
                  );
                  return {{
                    open: card.open,
                    phaseCount: card.querySelectorAll('.plan-trade-phase').length,
                    rowCount: rows.length,
                    completeRows,
                    noHorizontalOverflow,
                    hasCashEquation: Boolean(card.querySelector('.plan-cash-equation')),
                    hasEndingBalance: cashLabels.includes('计划后预计资金余额'),
                  }};
                }});
                if (
                  !expandedAccountPlanChecks.open
                  || expandedAccountPlanChecks.phaseCount < 1
                  || expandedAccountPlanChecks.rowCount < 1
                  || !expandedAccountPlanChecks.completeRows
                  || !expandedAccountPlanChecks.noHorizontalOverflow
                  || !expandedAccountPlanChecks.hasCashEquation
                  || !expandedAccountPlanChecks.hasEndingBalance
                ) {{
                  scenarioDifferences.push(difference(
                    'plan.expandedAccountPlanChecks',
                    {{
                      open: true,
                      minPhases: 1,
                      minRows: 1,
                      completeRows: true,
                      noHorizontalOverflow: true,
                      hasCashEquation: true,
                      hasEndingBalance: true,
                    }},
                    expandedAccountPlanChecks,
                  ));
                }}
                await firstAccountPlan.screenshot({{ path: {json.dumps(plan_expanded_screenshot)} }});
              }}
              let orderEntryNarrowChecks = null;
              if ({json.dumps(viewport_name)} === 'narrow' && scenario.name === 'action') {{
                const orderRows = scenarioPage.locator('.plan-trade-row:visible');
                const requiredOrderLabels = ['动作', '证券代码与名称', '数量', '参考价', '估算金额'];
                const orderEntryMetrics = await scenarioPage.evaluate(() => {{
                  const rows = Array.from(document.querySelectorAll('.plan-trade-row'))
                    .filter(row => row.checkVisibility());
                  return {{
                    rowCount: rows.length,
                    completeRows: rows.map(row => ({{
                      labels: Array.from(row.querySelectorAll('[data-label]')).map(cell => cell.dataset.label),
                      text: row.innerText,
                      gridTemplateAreas: window.getComputedStyle(row).gridTemplateAreas,
                    }})),
                    horizontalOverflow: Array.from(document.querySelectorAll('.plan-trade-table-scroll'))
                      .filter(container => container.checkVisibility())
                      .map(container => ({{
                        clientWidth: container.clientWidth,
                        scrollWidth: container.scrollWidth,
                      }})),
                  }};
                }});
                const completeRows = orderEntryMetrics.completeRows.every(row =>
                  requiredOrderLabels
                    .every(label => row.labels.includes(label))
                    && (
                      row.text.includes('宁波联合')
                      || row.text.includes('东望时代')
                      || row.text.includes('测试转债')
                    )
                );
                const noHorizontalOverflow = orderEntryMetrics.horizontalOverflow
                  .every(size => size.scrollWidth <= size.clientWidth + 2);
                const twoLineLayout = orderEntryMetrics.completeRows.every(row =>
                  row.gridTemplateAreas.includes('security security action')
                  && row.gridTemplateAreas.includes('quantity price amount')
                );
                if (await orderRows.count() < 2 || !completeRows || !noHorizontalOverflow || !twoLineLayout) {{
                  scenarioDifferences.push(difference(
                    'plan.orderEntries.narrow',
                    {{ minRows: 2, completeRows: true, noHorizontalOverflow: true, twoLineLayout: true }},
                    {{ ...orderEntryMetrics, completeRows, noHorizontalOverflow, twoLineLayout }},
                  ));
                }}
                orderEntryNarrowChecks = {{
                  ...orderEntryMetrics,
                  completeRows,
                  noHorizontalOverflow,
                  twoLineLayout,
                }};
              }}
              if (scenario.retryReadiness) {{
                const requestsBeforeRetry = scenarioRequests.filter(path => path === '/api/plan/readiness').length;
                await scenarioHero.getByRole('button', {{ name: '重新读取计划输入就绪度' }}).click();
                await page.waitForTimeout(100);
                const requestsAfterRetry = scenarioRequests.filter(path => path === '/api/plan/readiness').length;
                if (requestsAfterRetry <= requestsBeforeRetry) {{
                  scenarioDifferences.push(difference(
                    `plan.scenarios.${{scenario.name}}.readinessRetry`,
                    `>${{requestsBeforeRetry}}`,
                    requestsAfterRetry,
                  ));
                }}
              }}
              if (scenario.focusAccountId) {{
                await scenarioHero.getByRole('button', {{ name: '前往账户页补充事实' }}).click();
                const accountId = scenario.focusAccountId;
                await page.waitForFunction(
                  expected => {{
                    const target = document.getElementById(`account-fact-${{expected}}`);
                    const inputLabel = expected === 'stock' ? '股票可用现金' : '转债可用现金';
                    const input = document.querySelector(
                      `#account-facts input[aria-label="${{inputLabel}}"]`,
                    );
                    return window.location.hash === '#account'
                      && Boolean(target?.checkVisibility())
                      && document.activeElement === input;
                  }},
                  accountId,
                  {{ timeout: 10000 }},
                );
                const focusResult = await page.evaluate(expected => {{
                  const target = document.getElementById(`account-fact-${{expected}}`);
                  const inputLabel = expected === 'stock' ? '股票可用现金' : '转债可用现金';
                  const input = document.querySelector(
                    `#account-facts input[aria-label="${{inputLabel}}"]`,
                  );
                  return {{
                    visible: Boolean(target?.checkVisibility()),
                    focused: document.activeElement === input,
                  }};
                }}, accountId);
                if (!focusResult.visible || !focusResult.focused) {{
                  scenarioDifferences.push(difference(
                    `plan.scenarios.${{scenario.name}}.accountFocus`,
                    {{ visible: true, focused: true }},
                    focusResult,
                  ));
                }}
              }}
              if (scenario.readinessStatus >= 400) {{
                await page.waitForTimeout(100);
                const expectedNetworkMessage = `status of ${{scenario.readinessStatus}}`;
                const controlledReadinessConsoleErrors = consoleErrors.splice(consoleErrorStart);
                const unexpectedConsoleErrors = controlledReadinessConsoleErrors
                  .filter(message => !message.includes(expectedNetworkMessage));
                if (unexpectedConsoleErrors.length) {{
                  scenarioDifferences.push(difference(
                    `plan.scenarios.${{scenario.name}}.consoleErrors`,
                    [],
                    unexpectedConsoleErrors,
                  ));
                }}
              }}
              assertNoDifferences(
                `plan.scenarios.${{scenario.name}}`,
                `计划状态 ${{scenario.name}} 行为异常`,
                scenarioDifferences,
              );
              if (scenario.name === 'future-funding') {{
                await page.screenshot({{ path: {json.dumps(plan_future_screenshot)}, fullPage: true }});
              }}
              if (scenario.name === 'no-action') {{
                await page.screenshot({{ path: {json.dumps(plan_no_action_screenshot)}, fullPage: true }});
              }}
              planScenarioChecks.push({{
                name: scenario.name,
                title: actualTitle,
                executionDossierVisible,
                executionDossierCount,
                fundingPlanVisible,
                accountPlanCount,
                accountsCollapsedByDefault,
                noActionConclusion,
                legacyPanelsVisible,
                executionBadgeVisible,
                generateDisabled,
                requests: scenarioRequests,
                orderEntryNarrowChecks,
              }});
              await page.unroute('**/api/plan/readiness', readinessHandler);
              await page.unroute('**/api/plan/generated**', generationHandler);
            }}

            const accountFactScenarios = [
              {{ name: 'summary-http-error', path: '**/api/account/summary?date=*', mode: 'http' }},
              {{ name: 'positions-http-error', path: '**/api/positions/stock?**', mode: 'http' }},
              {{ name: 'quotes-http-error', path: '**/api/positions/cb/quotes?**', mode: 'http' }},
              {{ name: 'summary-network-error', path: '**/api/account/summary?date=*', mode: 'network' }},
              {{ name: 'positions-network-error', path: '**/api/positions/stock?**', mode: 'network' }},
              {{ name: 'quotes-network-error', path: '**/api/positions/cb/quotes?**', mode: 'network' }},
            ];
            const accountFactScenarioChecks = [];
            for (const scenario of accountFactScenarios) {{
              let shouldFail = true;
              const failureHandler = route => {{
                if (!shouldFail) return route.continue();
                shouldFail = false;
                if (scenario.mode === 'network') return route.abort('failed');
                return route.fulfill({{
                  status: 503,
                  contentType: 'application/json',
                  body: JSON.stringify({{ detail: '账户事实服务暂不可用' }}),
                }});
              }};
              await page.route(scenario.path, failureHandler);
              const consoleErrorStart = consoleErrors.length;
              await page.goto(
                {json.dumps(base_url)} + `?account_fact_scenario=${{scenario.name}}#account`,
                {{ waitUntil: 'domcontentloaded', timeout: 15000 }},
              );
              const scenarioAccountPage = main.getByRole('region', {{ name: '账户工作台' }});
              const scenarioReadiness = scenarioAccountPage.getByRole('region', {{ name: '账户事实就绪度' }});
              await scenarioReadiness.getByRole('heading', {{ name: '无法确认账户事实', level: 2 }})
                .waitFor({{ state: 'visible', timeout: 10000 }});
              const retry = scenarioAccountPage.getByRole('button', {{ name: '重新读取账户事实', exact: true }});
              const failurePanel = scenarioAccountPage.getByRole('alert');
              const scenarioStructure = scenarioAccountPage.getByRole('region', {{ name: '资产结构' }});
              const scenarioMaintenance = scenarioAccountPage.locator('#account-facts');
              const scenarioDifferences = [];
              if (!await retry.isVisible()) {{
                scenarioDifferences.push(difference(`account.scenarios.${{scenario.name}}.retry`, true, false));
              }}
              if (!await failurePanel.isVisible()) {{
                scenarioDifferences.push(difference(`account.scenarios.${{scenario.name}}.failure`, true, false));
              }}
              if (await scenarioStructure.isVisible()) {{
                scenarioDifferences.push(difference(`account.scenarios.${{scenario.name}}.assetValues`, false, true));
              }}
              if (await scenarioMaintenance.isVisible()) {{
                scenarioDifferences.push(difference(
                  `account.scenarios.${{scenario.name}}.maintenanceVisibleDuringFailure`, false, true,
                ));
              }}
              await retry.click();
              await scenarioReadiness.getByRole('heading', {{ name: '事实已就绪', level: 2 }})
                .waitFor({{ state: 'visible', timeout: 10000 }});
              if (!await scenarioStructure.isVisible()) {{
                scenarioDifferences.push(difference(`account.scenarios.${{scenario.name}}.retryRecovery`, true, false));
              }}
              const scenarioConsoleErrors = consoleErrors.splice(consoleErrorStart);
              const expectedConsoleMessage = scenario.mode === 'network'
                ? 'net::ERR_FAILED'
                : 'status of 503';
              const unexpectedConsoleErrors = scenarioConsoleErrors.filter(
                message => !message.includes(expectedConsoleMessage),
              );
              if (unexpectedConsoleErrors.length) {{
                scenarioDifferences.push(difference(
                  `account.scenarios.${{scenario.name}}.unexpectedConsoleErrors`, [], unexpectedConsoleErrors,
                ));
              }}
              if (pageErrors.length) {{
                scenarioDifferences.push(difference(
                  `account.scenarios.${{scenario.name}}.pageErrors`, [], pageErrors,
                ));
              }}
              consoleErrors.splice(consoleErrorStart, scenarioConsoleErrors.length);
              assertNoDifferences(
                `account.scenarios.${{scenario.name}}`,
                `账户事实失败场景 ${{scenario.name}} 行为异常`,
                scenarioDifferences,
              );
              accountFactScenarioChecks.push({{
                name: scenario.name,
                retryVisible: true,
                assetValuesVisibleDuringFailure: false,
                recovered: true,
                consoleErrors: scenarioConsoleErrors,
              }});
              await page.unroute(scenario.path, failureHandler);
            }}

            const emptyAccountHandler = route => route.fulfill({{
              status: 200,
              contentType: 'application/json',
              body: 'null',
            }});
            await page.route('**/api/account/summary?date=*', emptyAccountHandler);
            await page.goto(
              {json.dumps(base_url)} + '?account_fact_scenario=empty-account#account',
              {{ waitUntil: 'domcontentloaded', timeout: 15000 }},
            );
            const emptyAccountPage = main.getByRole('region', {{ name: '账户工作台' }});
            const emptyReadiness = emptyAccountPage.getByRole('region', {{ name: '账户事实就绪度' }});
            await emptyReadiness.getByRole('heading', {{ name: '尚未录入账户事实', level: 2 }})
              .waitFor({{ state: 'visible', timeout: 10000 }});
            const emptyDifferences = [];
            if (!await emptyAccountPage.getByLabel('空账户状态').isVisible()) {{
              emptyDifferences.push(difference('account.scenarios.empty-account.message', true, false));
            }}
            if (await emptyAccountPage.getByRole('alert').isVisible()) {{
              emptyDifferences.push(difference('account.scenarios.empty-account.failure', false, true));
            }}
            if (await emptyAccountPage.getByRole('button', {{ name: '重新读取账户事实', exact: true }}).isVisible()) {{
              emptyDifferences.push(difference('account.scenarios.empty-account.retry', false, true));
            }}
            if (await emptyAccountPage.getByRole('region', {{ name: '资产结构' }}).isVisible()) {{
              emptyDifferences.push(difference('account.scenarios.empty-account.assetValues', false, true));
            }}
            assertNoDifferences(
              'account.scenarios.empty-account',
              '真实空账户场景与读取失败混淆',
              emptyDifferences,
            );
            accountFactScenarioChecks.push({{
              name: 'empty-account',
              retryVisible: false,
              assetValuesVisible: false,
            }});
            await page.unroute('**/api/account/summary?date=*', emptyAccountHandler);

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
            const expectedStyleMetrics = {json.dumps(ARK_CHANGELOG_STYLE_METRICS)};
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
                navWidth: {ARK_CHANGELOG_REFERENCE_METRICS["nav_width"]},
                shellLeft: {ARK_CHANGELOG_REFERENCE_METRICS["shell_left"]},
                contentTop: {ARK_CHANGELOG_REFERENCE_METRICS["content_top"]},
                shellWidth: {ARK_CHANGELOG_REFERENCE_METRICS["shell_width"]},
                headerStreamGap: {ARK_CHANGELOG_REFERENCE_METRICS["header_stream_gap"]},
                dateGroupGap: {ARK_CHANGELOG_REFERENCE_METRICS["date_group_gap"]},
                dateHeaderHeight: {ARK_CHANGELOG_REFERENCE_METRICS["date_header_height"]},
                dateDividerY: {ARK_CHANGELOG_REFERENCE_METRICS["date_divider_y"]},
                metaWidth: {ARK_CHANGELOG_REFERENCE_METRICS["meta_width"]},
                entryColumnGap: {ARK_CHANGELOG_REFERENCE_METRICS["entry_column_gap"]},
                entryDividerX: {ARK_CHANGELOG_REFERENCE_METRICS["entry_divider_x"]},
                entryCopyX: {ARK_CHANGELOG_REFERENCE_METRICS["entry_copy_x"]},
              }};
              const structureDrift = collectNumericDrift(readingMetrics, expectedStructure);
              assertNoDifferences(
                'changelog.wide.geometry',
                '更新日志桌面结构锚点偏离',
                structureDrift,
              );
            }}
            if ({json.dumps(viewport_name)} === 'desktop') {{
              const expected = {json.dumps(ARK_CHANGELOG_RESPONSIVE_METRICS["desktop"])};
              const drift = collectNumericDrift(readingMetrics, expected);
              assertNoDifferences(
                'changelog.desktop.geometry',
                '更新日志 1440px 阅读比例偏离',
                drift,
              );
            }}
            if ({json.dumps(viewport_name)} === 'narrow') {{
              const expected = {json.dumps(ARK_CHANGELOG_RESPONSIVE_METRICS["narrow"])};
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
                activeTag: 'A',
                activeLabel: '更新日志',
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
              planExpandedScreenshot: {json.dumps(plan_expanded_screenshot)},
              planFutureFundingScreenshot: {json.dumps(plan_future_screenshot)},
              planNoActionScreenshot: {json.dumps(plan_no_action_screenshot)},
              screenshot: {json.dumps(screenshot)},
              pageChecks: {{
                navigation: {{ items: navItems.length }},
                account: {{
                  metrics: accountMetrics,
                  integrity: accountIntegrity,
                  scenarios: accountFactScenarioChecks,
                  dateIdentity: accountFactDateIdentityChecks,
                  narrowMaintenance: accountMaintenanceNarrowChecks,
                }},
                plan: {{
                  metrics: planMetrics,
                  integrity: planIntegrity,
                  expandedAccountPlanChecks,
                  scenarios: planScenarioChecks,
                }},
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
