"""补齐基金预算草案的公开参考数据，不代替执行日限价和溢价判断。"""
from __future__ import annotations

from datetime import datetime
import math
import requests

REFERENCE_CODES = {"nasdaq": "161130", "technology": "501312"}


def fetch_reference_quotes(codes: list[str], plan_date: str, execution_date: str) -> dict:
    query = ','.join(('sh' if code.startswith(('5', '6')) else 'sz') + code for code in codes)
    try:
        response = requests.get(f'https://qt.gtimg.cn/q={query}', timeout=10)
        response.raise_for_status()
        response.encoding = 'gbk'
    except requests.RequestException:
        return {}
    result = {}
    for line in response.text.split(';'):
        if '="' not in line:
            continue
        fields = line.split('"', 1)[1].rstrip('"').split('~')
        if len(fields) <= 30 or fields[2] not in codes:
            continue
        try:
            stamp = datetime.strptime(fields[30], '%Y%m%d%H%M%S')
            day = stamp.date().isoformat()
            if day == execution_date and execution_date != plan_date:
                price = float(fields[4])
                basis = '腾讯行情昨收字段'
            elif day == plan_date and stamp.hour >= 15:
                price = float(fields[3])
                basis = '腾讯行情计划日收盘记录'
            else:
                continue
            if not math.isfinite(price) or price <= 0:
                continue
        except (ValueError, IndexError):
            continue
        result[fields[2]] = dict(price=price, price_date=plan_date, quote_timestamp=fields[30],
                                price_source=basis, source_url='https://qt.gtimg.cn/q='+query)
    return result


def with_reference_terms(account: dict, plan_date: str, execution_date: str) -> dict:
    explicit = {item['direction']: dict(item) for item in account.get('fund_terms', [])}
    codes = {d: explicit.get(d, {}).get('code') or code for d, code in REFERENCE_CODES.items()}
    needed = [codes[d] for d in codes if not explicit.get(d, {}).get('limit_price')
              or explicit[d].get('price_date') != plan_date]
    quotes = fetch_reference_quotes(needed, plan_date, execution_date) if needed else {}
    terms = []
    for direction, code in codes.items():
        item = explicit.get(direction, {}).copy()
        item.update(direction=direction, code=code)
        if code in REFERENCE_CODES.values():
            item.setdefault('lot_size', 100)
            if item['lot_size'] is None:
                item['lot_size'] = 100
        if code in quotes:
            quote = quotes[code]
            item.update(quote, limit_price=quote['price'], reference_only=True, cost_reviewed=False)
        terms.append(item)
    return {**account, 'fund_terms': terms}
