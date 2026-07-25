from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from strategies.stock_smallcap import run

CONFIG = {
    "universe": {"board_prefixes": ["600", "601", "603", "605", "000", "001", "002", "003"]},
    "filters": {
        "min_amount_yuan": 10_000_000,
        "exclude_st": True,
        "exclude_limit_up_down": True,
        "require_positive_pe": True,
        "min_roe_pct": -1.0,
    },
    "selection": {"hold_n": 3, "sell_rank": 4, "candidate_pool": 10, "max_roe_fetch": 50},
}


class StockSmallCapTests(unittest.TestCase):
    def test_persist_rankings_propagates_database_failure(self) -> None:
        with patch("datasource.db.init_db"), patch(
            "datasource.db.create_complete_strategy_run",
            side_effect=RuntimeError("forced persistence failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced persistence failure"):
                run.persist_rankings(date(2026, 6, 4), pd.DataFrame([{"stock_code": "600000"}]))

    def test_board_filter_keeps_main_and_sme_excludes_others(self) -> None:
        df = pd.DataFrame(
            {
                "stock_code": ["600000", "000001", "002001", "300001", "688001", "830001"],
                "stock_name": ["浦发银行", "平安银行", "新和成", "特锐德", "华兴源创", "某北交所"],
            }
        )
        kept = run.filter_board_and_st(df, CONFIG)
        self.assertEqual(set(kept["stock_code"]), {"600000", "000001", "002001"})

    def test_board_filter_excludes_st_and_delisting(self) -> None:
        df = pd.DataFrame(
            {"stock_code": ["600000", "600001", "600002"], "stock_name": ["正常股", "ST坏股", "某退"]}
        )
        kept = run.filter_board_and_st(df, CONFIG)
        self.assertEqual(set(kept["stock_code"]), {"600000"})

    def test_apply_filters_excludes_limitup_lowamount_nonpositive_pe_suspended(self) -> None:
        df = pd.DataFrame(
            [
                {"stock_code": "600000", "price": 10.0, "volume_hand": 100, "amount_yuan": 2e7, "pe_ttm": 15, "limit_up": 11.0, "limit_down": 9.0},  # 通过
                {"stock_code": "600001", "price": 11.0, "volume_hand": 100, "amount_yuan": 2e7, "pe_ttm": 15, "limit_up": 11.0, "limit_down": 9.0},  # 涨停
                {"stock_code": "600002", "price": 10.0, "volume_hand": 100, "amount_yuan": 5e6, "pe_ttm": 15, "limit_up": 11.0, "limit_down": 9.0},  # 成交额不足
                {"stock_code": "600003", "price": 10.0, "volume_hand": 100, "amount_yuan": 2e7, "pe_ttm": -3, "limit_up": 11.0, "limit_down": 9.0},  # 亏损 PE<0
                {"stock_code": "600004", "price": 0.0, "volume_hand": 0, "amount_yuan": 0, "pe_ttm": 15, "limit_up": 0.0, "limit_down": 0.0},  # 停牌
            ]
        )
        kept = run.apply_filters(df, CONFIG)
        self.assertEqual(set(kept["stock_code"]), {"600000"})

    def test_rebalance_sells_when_rank_drops_past_sell_rank(self) -> None:
        ranked = pd.DataFrame(
            {
                "stock_code": ["000001", "000002", "000003", "000004", "000005"],
                "stock_name_q": ["甲", "乙", "丙", "丁", "戊"],
                "rank": [1, 2, 3, 4, 5],
            }
        )
        # 持有 丁(rank4>=sell_rank4 应卖) 和 甲(rank1 应留)
        current = pd.DataFrame([{"stock_code": "000004", "stock_name": "丁", "shares": 100}, {"stock_code": "000001", "stock_name": "甲", "shares": 100}])
        target, reb = run.build_target_and_rebalance(current, ranked, CONFIG)

        actions = dict(zip(reb["stock_code"], reb["action"]))
        self.assertEqual(actions["000004"], "SELL")  # 跌出第4名
        self.assertEqual(actions["000001"], "HOLD")  # 仍在前列
        self.assertEqual(len(target), 3)  # hold_n=3, 甲+补足2只
        self.assertIn("000002", set(target["stock_code"]))  # 用最小市值补足

    def test_rebalance_no_force_sell_when_over_hold_n_but_all_pass_gate(self) -> None:
        # 持有 5 只但 hold_n=3, sell_rank=4 —— 全部 rank<4, 不应强制卖任何一只
        ranked = pd.DataFrame(
            {
                "stock_code": ["000001", "000002", "000003", "000004", "000005"],
                "stock_name_q": ["甲", "乙", "丙", "丁", "戊"],
                "rank": [1, 2, 3, 4, 5],
            }
        )
        current = pd.DataFrame(
            [{"stock_code": c, "stock_name": n, "shares": 100}
             for c, n in [("000001","甲"),("000002","乙"),("000003","丙"),("000004","丁"),("000005","戊")]]
        )
        # hold_n=3, sell_rank=4: 000004/000005 rank>=sell_rank → should sell
        _, reb = run.build_target_and_rebalance(current, ranked, CONFIG)
        actions = dict(zip(reb["stock_code"], reb["action"]))
        # rank 1/2/3 all < sell_rank(4): keep
        self.assertEqual(actions["000001"], "HOLD")
        self.assertEqual(actions["000002"], "HOLD")
        self.assertEqual(actions["000003"], "HOLD")
        # rank 4 == sell_rank → sell
        self.assertEqual(actions["000004"], "SELL")
        # rank 5 > sell_rank → sell
        self.assertEqual(actions["000005"], "SELL")

    def test_persisted_rankings_follow_rebalance_target_not_mechanical_head(self) -> None:
        ranked = pd.DataFrame(
            {
                "stock_code": ["000001", "000002", "000003", "000004", "000005"],
                "stock_name_q": ["甲", "乙", "丙", "丁", "戊"],
                "rank": [1, 2, 3, 4, 5],
            }
        )
        current = pd.DataFrame([
            {"stock_code": "000004", "stock_name": "丁", "shares": 100},
        ])
        config = {**CONFIG, "selection": {**CONFIG["selection"], "hold_n": 3, "sell_rank": 5}}
        target, _ = run.build_target_and_rebalance(current, ranked, config)

        persisted = run.rankings_for_order_sizing(ranked, target, config)

        self.assertEqual(list(persisted["stock_code"]), ["000001", "000002", "000004"])
        self.assertNotIn("000003", set(persisted["stock_code"]))


if __name__ == "__main__":
    unittest.main()
