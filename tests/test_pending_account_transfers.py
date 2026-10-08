import sqlite3
import unittest
from fastapi.testclient import TestClient
from app import account_current_state as state
from datasource import db


class PendingAccountTransferTests(unittest.TestCase):
    def setUp(self):
        db._TEST_CONN = sqlite3.connect(':memory:', check_same_thread=False)
        db._TEST_CONN.row_factory = sqlite3.Row
        db.init_db()
        from app.main import app
        self.client = TestClient(app)

    def tearDown(self):
        db._TEST_CONN.close()
        db._TEST_CONN = None

    def test_debited_inflight_is_preserved_counted_once_and_settled(self):
        response = self.client.put('/api/accounts/cash', json=dict(amount=9000, expected_version=None,
            pending_transfers=[dict(target='stock', amount=1000, debited=True)]))
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved['valuation']['total'], 10000)
        summary = state.build_current_account_summary()
        self.assertEqual(summary['cash_pool'], 10000)
        self.assertEqual(summary['pending_transfers'][0]['amount'], 1000)
        # 未提供新字段的旧客户端不能清除已有事实。
        saved = state.update_current_account('cash', amount=9000, expected_version=saved['version'])
        self.assertEqual(saved['valuation']['total'], 10000)
        # 到账后删除在途并更新接收账户；资金守恒。
        source = state.update_current_account('cash', amount=9000, pending_transfers=[], expected_version=saved['version'])
        target = state.update_current_account('stock', available_cash=1000, frozen_cash=0,
                                              positions=[], expected_version=None)
        self.assertEqual(source['valuation']['total'] + target['valuation']['total'], 10000)

    def test_undebited_reservation_does_not_add_asset_value(self):
        saved = state.update_current_account('cash', amount=10000, expected_version=None,
            pending_transfers=[dict(target='cb', amount=4000, debited=False)], unavailable_amount=2000)
        self.assertEqual(saved['valuation']['total'], 10000)
        summary = state.build_current_account_summary()
        self.assertEqual(summary['cash_unavailable'], 2000)
        with self.assertRaisesRegex(ValueError, '超过'):
            state.update_current_account('cash', amount=10000, expected_version=saved['version'],
                pending_transfers=[dict(target='cb', amount=9000, debited=False)])

    def test_securities_debited_transit_is_in_total_but_not_cash(self):
        saved = state.update_current_account('stock', available_cash=2000, frozen_cash=0, positions=[],
            expected_version=None, pending_transfers=[dict(target='cash', amount=5000, debited=True)])
        self.assertEqual(saved['valuation']['total'], 7000)
        summary = state.build_current_account_summary()
        self.assertEqual(summary['stock_total'], 7000)
        self.assertEqual(summary['stock_available_cash'], 2000)

    def test_invalid_route_duplicate_and_adviser_double_record_are_rejected(self):
        for source, rows in [
            ('stock', [dict(target='cb', amount=100, debited=False)]),
            ('cash', [dict(target=['stock'], amount=100, debited=False)]),
            ('cash', [dict(target='cb', amount=100, debited=False)]*2),
            ('changqian', [dict(target='cash', amount=100, debited=True)]),
        ]:
            body = dict(amount=1000) if source != 'stock' else dict(available_cash=1000, frozen_cash=0, positions=[])
            with self.subTest(source=source):
                response = self.client.put('/api/accounts/'+source, json={**body,'expected_version':None,'pending_transfers':rows})
                self.assertEqual(response.status_code, 400)
