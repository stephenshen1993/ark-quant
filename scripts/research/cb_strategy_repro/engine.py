from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


VARIANTS = ("B0_BASE", "C_BAL", "C_OFF")


@dataclass(frozen=True)
class EngineConfig:
    target: int = 20
    fee: float = 0.001
    include_coupons: bool = True


def signal_dates(trading_dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(trading_dates).unique()))
    frame = pd.DataFrame({"date": dates})
    frame["month"] = frame["date"].dt.to_period("M")
    output = []
    for month, group in frame.groupby("month", sort=True):
        month_dates = pd.DatetimeIndex(group["date"])
        for day in (10, 20, 30):
            target = month.start_time.normalize() + pd.Timedelta(days=day - 1)
            if target.month != month.month:
                target = month.end_time.normalize()
            choices = month_dates[month_dates >= target]
            if len(choices):
                output.append(choices.min())
    return sorted(set(output))


def eligible(snapshot: pd.DataFrame, date: pd.Timestamp, variant: str) -> pd.DataFrame:
    required = ["close_em", "conv_premium", "double_low", "contract_maturity"]
    if variant in ("C_BAL", "C_OFF"):
        required.append("double_low_z252")
    if variant == "C_OFF":
        required.append("balance_bil")
    frame = snapshot.dropna(subset=required).copy()
    frame = frame[frame["contract_maturity"] - date >= pd.Timedelta(days=365)]
    frame = frame[frame["call_notice_date"].isna() | frame["call_notice_date"].gt(date)]
    frame = frame[frame["tail_risk_date"].isna() | frame["tail_risk_date"].gt(date)]
    frame = frame[~frame["st_risk_active"].fillna(False)]
    return frame


def score(frame: pd.DataFrame, variant: str) -> pd.Series:
    if variant == "B0_BASE":
        return frame["double_low"]
    if variant == "C_BAL":
        return frame["double_low_z252"]
    if variant == "C_OFF":
        return (
            frame["double_low"].rank(pct=True)
            + frame["double_low_z252"].rank(pct=True)
            + frame["balance_bil"].rank(pct=True)
        )
    raise KeyError(variant)


def build_rankings(panel: pd.DataFrame, variant: str, minimum: int) -> tuple[dict, dict]:
    trading_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    signals = [date for date in signal_dates(trading_dates) if date < trading_dates.max()]
    next_trade = {}
    for date in signals:
        later = trading_dates[trading_dates > date]
        if len(later):
            next_trade[date] = later.min()
    snapshots = {date: group for date, group in panel.groupby("date", sort=False) if date in next_trade}
    ranked_at_execution, universes = {}, {}
    for signal, execution in next_trade.items():
        universe = eligible(snapshots[signal], signal, variant)
        if len(universe) < minimum:
            continue
        ranked = universe.assign(_score=score(universe, variant)).sort_values(
            ["_score", "bond_code"], ascending=[True, True]
        )
        ranked_at_execution[execution] = {
            "signal_date": signal, "codes": ranked["bond_code"].tolist(),
        }
        universes[execution] = ranked["bond_code"].tolist()
    return ranked_at_execution, universes


def _event_maps(frame: pd.DataFrame, date_col: str, value_col: str):
    output = defaultdict(list)
    if frame.empty:
        return output
    for row in frame.dropna(subset=[date_col, value_col]).itertuples(index=False):
        output[pd.Timestamp(getattr(row, date_col)).normalize()].append(row)
    return output


def simulate_ranked_portfolio(
    panel: pd.DataFrame,
    rankings: dict,
    risk_events: pd.DataFrame,
    exits: pd.DataFrame,
    coupons: pd.DataFrame,
    config: EngineConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    histories = {
        code: group.set_index("date").sort_index()[["open_sina", "close_em"]]
        for code, group in panel.groupby("bond_code", sort=False)
    }
    trading_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    risk_by_code = defaultdict(list)
    for row in risk_events.dropna(subset=["risk_date"]).itertuples(index=False):
        risk_by_code[str(row.bond_code).zfill(6)].append({
            "start": pd.Timestamp(row.risk_date).normalize(),
            "end": pd.Timestamp(row.risk_end).normalize() if pd.notna(row.risk_end) else pd.NaT,
            "type": str(row.risk_type),
        })
    exit_events = _event_maps(exits, "holder_cash_date", "execute_price")
    coupon_events = _event_maps(coupons, "payment_date", "coupon_per_100_pretax")

    start_date = min(rankings)
    dates = trading_dates[trading_dates >= start_date]
    holdings: dict[str, float] = {}
    last_mark: dict[str, float] = {}
    cash = 1.0
    daily, trades = [], []
    stats = defaultdict(float)
    stats.update({"rebalances": 0, "missed_target_buys": 0, "risk_exits": 0,
                  "standard_cash_exits": 0, "coupon_payments": 0, "max_holdings": 0})

    def quote(code: str, date: pd.Timestamp, field: str) -> float:
        history = histories.get(code)
        if history is None or date not in history.index:
            return np.nan
        value = history.at[date, field]
        if isinstance(value, pd.Series):
            value = value.iloc[-1]
        return float(value) if pd.notna(value) else np.nan

    def blocked_at_open(code: str, date: pd.Timestamp) -> bool:
        for event in risk_by_code.get(code, []):
            if event["start"] >= date:
                continue
            if event["type"] != "st":
                return True
            if pd.isna(event["end"]) or date < event["end"]:
                return True
        return False

    def sell(code: str, date: pd.Timestamp, price: float, reason: str, charge_fee: bool) -> None:
        nonlocal cash
        qty = holdings.pop(code, 0.0)
        notional = qty * price
        fee = notional * config.fee if charge_fee else 0.0
        cash += notional - fee
        stats["turnover_notional"] += notional
        stats["fees_paid"] += fee
        trades.append({"date": date, "bond_code": code, "side": "sell", "reason": reason,
                       "price": price, "quantity": qty, "notional": notional, "fee": fee})

    for date in dates:
        date = pd.Timestamp(date).normalize()

        for row in exit_events.get(date, []):
            code = str(row.bond_code).zfill(6)
            if code in holdings:
                sell(code, date, float(row.execute_price), "standard_cash_exit", False)
                stats["standard_cash_exits"] += 1

        if config.include_coupons:
            for row in coupon_events.get(date, []):
                code = str(row.bond_code).zfill(6)
                if code in holdings:
                    amount = holdings[code] * float(row.coupon_per_100_pretax)
                    cash += amount
                    stats["coupon_cash"] += amount
                    stats["coupon_payments"] += 1

        for code in list(holdings):
            if blocked_at_open(code, date):
                price = quote(code, date, "open_sina")
                if np.isfinite(price) and price > 0:
                    sell(code, date, price, "risk_exit_next_open", True)
                    stats["risk_exits"] += 1

        if date in rankings:
            stats["rebalances"] += 1
            ranked = rankings[date]["codes"]
            desired = []
            for code in ranked:
                if blocked_at_open(code, date):
                    continue
                desired.append(code)
                if len(desired) == config.target:
                    break
            desired_set = set(desired)

            locked = [code for code in holdings if not np.isfinite(quote(code, date, "open_sina"))
                      or quote(code, date, "open_sina") <= 0]
            if locked:
                stats["locked_rebalance_events"] += 1
                stats["locked_position_instances"] += len(locked)

            for code in list(holdings):
                if code in desired_set or code in locked:
                    continue
                price = quote(code, date, "open_sina")
                if np.isfinite(price) and price > 0:
                    sell(code, date, price, "rebalance", True)

            marked_open = {}
            for code, qty in holdings.items():
                price = quote(code, date, "open_sina")
                if not np.isfinite(price) or price <= 0:
                    price = last_mark.get(code, np.nan)
                if np.isfinite(price) and price > 0:
                    marked_open[code] = qty * price
            nav_open = cash + sum(marked_open.values())
            target_value = nav_open / config.target
            tradable = [code for code in desired if np.isfinite(quote(code, date, "open_sina"))
                        and quote(code, date, "open_sina") > 0]
            missing = [code for code in desired if code not in tradable and code not in holdings]
            stats["missed_target_buys"] += len(missing)

            for code in tradable:
                if code not in holdings:
                    continue
                price = quote(code, date, "open_sina")
                current = holdings[code] * price
                if current > target_value:
                    qty = (current - target_value) / price
                    notional = qty * price
                    fee = notional * config.fee
                    holdings[code] -= qty
                    cash += notional - fee
                    stats["turnover_notional"] += notional
                    stats["fees_paid"] += fee
                    trades.append({"date": date, "bond_code": code, "side": "sell",
                                   "reason": "rebalance_resize", "price": price,
                                   "quantity": qty, "notional": notional, "fee": fee})

            needs = {}
            for code in tradable:
                price = quote(code, date, "open_sina")
                current = holdings.get(code, 0.0) * price
                needs[code] = max(0.0, target_value - current)
            required_cash = sum(value * (1 + config.fee) for value in needs.values())
            scale = min(1.0, cash / required_cash) if required_cash > 0 else 0.0
            for code, need in needs.items():
                spend = need * scale
                if spend <= 0:
                    continue
                price = quote(code, date, "open_sina")
                qty = spend / price
                fee = spend * config.fee
                holdings[code] = holdings.get(code, 0.0) + qty
                cash -= spend + fee
                stats["turnover_notional"] += spend
                stats["fees_paid"] += fee
                trades.append({"date": date, "bond_code": code, "side": "buy",
                               "reason": "rebalance", "price": price, "quantity": qty,
                               "notional": spend, "fee": fee})

        marked = 0.0
        locked_today = 0
        for code, qty in holdings.items():
            price = quote(code, date, "close_em")
            if np.isfinite(price) and price > 0:
                last_mark[code] = price
            else:
                price = last_mark.get(code, np.nan)
                locked_today += 1
            if np.isfinite(price) and price > 0:
                marked += qty * price
        nav = cash + marked
        daily.append({"date": date, "nav": nav, "cash": cash,
                      "cash_weight": cash / nav if nav > 0 else np.nan,
                      "holdings": len(holdings), "locked_today": locked_today})
        stats["max_holdings"] = max(stats["max_holdings"], len(holdings))

    daily_frame = pd.DataFrame(daily)
    daily_frame["daily_return"] = daily_frame["nav"].pct_change()
    if len(daily_frame):
        daily_frame.loc[daily_frame.index[0], "daily_return"] = daily_frame.iloc[0]["nav"] - 1.0
    nav_with_base = pd.concat([pd.Series([1.0]), daily_frame["nav"]], ignore_index=True)
    years = (daily_frame["date"].iloc[-1] - daily_frame["date"].iloc[0]).days / 365.25
    summary = {
        "start": str(daily_frame["date"].iloc[0].date()),
        "end": str(daily_frame["date"].iloc[-1].date()),
        "observations": int(len(daily_frame)),
        "ending_nav": float(daily_frame["nav"].iloc[-1]),
        "cagr": float(daily_frame["nav"].iloc[-1] ** (1 / years) - 1),
        "max_drawdown": float((nav_with_base / nav_with_base.cummax() - 1).min()),
        "mean_cash_weight": float(daily_frame["cash_weight"].mean()),
        "p95_cash_weight": float(daily_frame["cash_weight"].quantile(0.95)),
        **{key: (int(value) if key not in ("fees_paid", "turnover_notional", "coupon_cash") else float(value))
           for key, value in stats.items()},
    }
    return daily_frame, pd.DataFrame(trades), summary


def opportunity_set_events(
    panel: pd.DataFrame,
    universes: dict[pd.Timestamp, list[str]],
    risk_events: pd.DataFrame,
    exits: pd.DataFrame,
    coupons: pd.DataFrame,
    include_coupons: bool,
) -> pd.DataFrame:
    histories = {
        code: group.set_index("date").sort_index()[["open_sina", "close_em"]]
        for code, group in panel.groupby("bond_code", sort=False)
    }
    risk_by_code = defaultdict(list)
    for row in risk_events.dropna(subset=["risk_date"]).itertuples(index=False):
        risk_by_code[str(row.bond_code).zfill(6)].append({
            "start": pd.Timestamp(row.risk_date).normalize(),
            "end": pd.Timestamp(row.risk_end).normalize() if pd.notna(row.risk_end) else pd.NaT,
            "type": str(row.risk_type),
        })
    exits_by_code = defaultdict(list)
    for row in exits.dropna(subset=["holder_cash_date", "execute_price"]).itertuples(index=False):
        exits_by_code[str(row.bond_code).zfill(6)].append(row)
    coupons_by_code = defaultdict(list)
    for row in coupons.dropna(subset=["payment_date", "coupon_per_100_pretax"]).itertuples(index=False):
        coupons_by_code[str(row.bond_code).zfill(6)].append(row)

    def quote(code: str, date: pd.Timestamp, field: str) -> float:
        history = histories.get(code)
        if history is None or date not in history.index:
            return np.nan
        value = history.at[date, field]
        if isinstance(value, pd.Series):
            value = value.iloc[-1]
        return float(value) if pd.notna(value) else np.nan

    def holding_return(code: str, start: pd.Timestamp, end: pd.Timestamp) -> float:
        history = histories.get(code)
        entry = quote(code, start, "open_sina")
        if history is None or not np.isfinite(entry) or entry <= 0:
            return 0.0
        cash = 0.0
        if include_coupons:
            cash += sum(
                float(row.coupon_per_100_pretax)
                for row in coupons_by_code.get(code, [])
                if start <= pd.Timestamp(row.payment_date) < end
            )
        exit_candidates = []
        for event in risk_by_code.get(code, []):
            event_start = event["start"]
            if not (start <= event_start < end):
                continue
            later = history[(history.index > event_start) & (history.index <= end)]["open_sina"].dropna()
            later = later[later > 0]
            if len(later):
                exit_candidates.append((later.index[0], float(later.iloc[0])))
        for row in exits_by_code.get(code, []):
            cash_date = pd.Timestamp(row.holder_cash_date).normalize()
            if start <= cash_date <= end:
                exit_candidates.append((cash_date, float(row.execute_price)))
        if exit_candidates:
            _, price = min(exit_candidates, key=lambda item: item[0])
            return (price + cash) / entry - 1
        exit_price = quote(code, end, "open_sina")
        if not np.isfinite(exit_price) or exit_price <= 0:
            stale = history[(history.index >= start) & (history.index < end)]["close_em"].dropna()
            exit_price = float(stale.iloc[-1]) if len(stale) else entry
        return (exit_price + cash) / entry - 1

    executions = sorted(universes)
    rows = []
    for index, start in enumerate(executions[:-1]):
        end = executions[index + 1]
        values = [holding_return(code, start, end) for code in universes[start]]
        rows.append({
            "start_date": start, "end_date": end,
            "return": float(np.mean(values)) if values else np.nan,
            "universe_size": len(values),
        })
    return pd.DataFrame(rows)


def event_returns(daily: pd.DataFrame, execution_dates: list[pd.Timestamp]) -> pd.DataFrame:
    rows = []
    dates = sorted(date for date in execution_dates if date in set(daily["date"]))
    for index, start in enumerate(dates):
        end = dates[index + 1] if index + 1 < len(dates) else daily["date"].max() + pd.Timedelta(days=1)
        part = daily[(daily["date"] >= start) & (daily["date"] < end)]
        if len(part):
            rows.append({"start_date": start, "end_date": part["date"].iloc[-1],
                         "return": float(np.prod(1 + part["daily_return"]) - 1)})
    return pd.DataFrame(rows)


def jsl_replica(panel: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    for code, group in panel.groupby("bond_code", sort=False):
        frame = group[["date", "close_em"]].dropna().drop_duplicates("date").sort_values("date").copy()
        frame["bond_code"] = code
        frame["return"] = frame["close_em"].pct_change()
        frame = frame.iloc[1:]
        pieces.append(frame[["date", "bond_code", "return"]])
    returns = pd.concat(pieces, ignore_index=True).dropna(subset=["return"])
    daily = returns.groupby("date", as_index=False)["return"].mean()
    daily["index"] = 1000.0 * (1 + daily["return"]).cumprod()
    return daily


def load_inputs(root: Path):
    panel = pd.read_pickle(root / "frozen_panel.pkl.gz", compression="gzip")
    for column in ["date", "contract_maturity", "call_notice_date", "tail_risk_date"]:
        panel[column] = pd.to_datetime(panel[column], errors="coerce").dt.normalize()
    risks = pd.read_csv(root / "risk_events.csv", dtype={"bond_code": str}, parse_dates=["risk_date", "risk_end"])
    exits = pd.read_csv(root / "standard_exits.csv", dtype={"bond_code": str}, parse_dates=["holder_cash_date"])
    coupons = pd.read_csv(root / "coupon_events.csv", dtype={"bond_code": str}, parse_dates=["payment_date", "nominal_date"])
    return panel, risks, exits, coupons
