from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from datasource import market, store


class SymbolTests(unittest.TestCase):
    def test_normalize_stock_code_extracts_last_six_digits(self) -> None:
        self.assertEqual(market.normalize_stock_code("sh600519"), "600519")
        self.assertEqual(market.normalize_stock_code("000001"), "000001")

    def test_stock_symbol_with_exchange_picks_prefix(self) -> None:
        self.assertEqual(market.stock_symbol_with_exchange("600519"), "sh600519")
        self.assertEqual(market.stock_symbol_with_exchange("000001"), "sz000001")
        self.assertEqual(market.stock_symbol_with_exchange("513050"), "sh513050")

    def test_bond_symbol_with_exchange_picks_prefix(self) -> None:
        self.assertEqual(market.bond_symbol_with_exchange("113052"), "sh113052")
        self.assertEqual(market.bond_symbol_with_exchange("127102"), "sz127102")


class ParseTencentCapsTests(unittest.TestCase):
    def test_extracts_total_mv(self) -> None:
        fields = ["1"] + ["x"] * 87
        fields[1] = "山东路桥"
        fields[2] = "000498"
        fields[45] = "80.11"  # 总市值, 亿元
        text = 'v_sz000498="' + "~".join(fields) + '";'

        rows = market._parse_tencent_quote_caps(text, "2026-06-05")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_code"], "000498")
        self.assertEqual(rows[0]["market_cap"], 80.11 * 100_000_000)
        self.assertEqual(rows[0]["market_cap_source"], "tencent_qt_total_mv")
        self.assertEqual(rows[0]["market_cap_as_of_date"], "2026-06-05")

    def test_skips_malformed(self) -> None:
        self.assertEqual(market._parse_tencent_quote_caps('v_sz000001="1~平安~000001~11.0";', "2026-06-05"), [])
        self.assertEqual(market._parse_tencent_quote_caps("", "2026-06-05"), [])


class StockPriceAdjustmentTests(unittest.TestCase):
    def test_chuhuan_technology_ex_rights_price_adjusts_stale_pre_action_close(self) -> None:
        row = {"stock_code": "001336", "price": 20.25}

        adjusted = market.apply_stock_price_adjustments(row)

        self.assertEqual(adjusted["price"], 15.48)
        self.assertEqual(round(adjusted["price"] * 910, 2), 14086.8)
        self.assertEqual(adjusted["price_adjustment"], "2025_profit_distribution")

    def test_chuhuan_technology_keeps_already_adjusted_price(self) -> None:
        row = {"stock_code": "001336", "price": 15.49}

        adjusted = market.apply_stock_price_adjustments(row)

        self.assertEqual(adjusted["price"], 15.49)
        self.assertNotIn("price_adjustment", adjusted)


class StoreTests(unittest.TestCase):
    def test_accounts_state_round_trip(self) -> None:
        state = {"temperature": 57.0, "stock": {"total": 1.5, "cash": 2.0}, "label": "测试中文"}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "accounts_state.json"
            store.save_accounts_state(state, path)
            self.assertEqual(store.load_accounts_state(path), state)

    def test_load_holdings_zero_pads_codes_and_reads_shares(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pos.csv"
            path.write_text(
                "stock_code,stock_name,shares\n2486,嘉麟杰,4700\n3008,开普检测,600\n",
                encoding="utf-8",
            )
            self.assertEqual(
                store.load_holdings(path, "stock_code"),
                {"002486": 4700, "003008": 600},
            )

    def test_load_holdings_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(store.load_holdings(Path(d) / "nope.csv", "bond_code"), {})


if __name__ == "__main__":
    unittest.main()
