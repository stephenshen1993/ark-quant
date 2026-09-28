"""App entrypoint whose market quotes stay deterministic for browser smoke."""
from datasource import market
from datetime import datetime
from zoneinfo import ZoneInfo
from app import plan_service
from scripts.ui_smoke_quotes import (
    fetch_cb_quotes_tencent,
    fetch_tencent_snapshot,
)

market.fetch_tencent_snapshot = fetch_tencent_snapshot
market.fetch_cb_quotes_tencent = fetch_cb_quotes_tencent
plan_service.current_plan_date = lambda: datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()

from app.main import app  # noqa: E402

__all__ = ["app"]
