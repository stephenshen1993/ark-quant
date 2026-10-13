import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import requests

from datasource import request_control as control


class TestMarketRequestBounds(unittest.TestCase):
    def send(self, timeout=None):
        return requests.Session().send(requests.Request('GET', 'https://example.test').prepare(), timeout=timeout)

    def test_internal_akshare_requests_are_bounded_but_other_threads_are_not(self):
        def echo(_session, _request, **kwargs):
            return kwargs['timeout']
        with patch.object(control, '_original_send', echo):
            self.assertIsNone(self.send())
            with control.market_requests():
                self.assertEqual(self.send(), (5.0, 20.0))
                self.assertEqual(self.send(60), (5.0, 20.0))
                self.assertEqual(self.send((2, 3)), (2, 3))
                with ThreadPoolExecutor(1) as pool:
                    self.assertIsNone(pool.submit(self.send).result())
            self.assertIsNone(self.send())

    def test_timeout_propagates_and_scope_is_restored(self):
        @control.bounded_market_requests
        def fetch():
            return self.send()
        with patch.object(control, '_original_send', side_effect=requests.ReadTimeout('slow source')):
            with self.assertRaises(requests.ReadTimeout):
                fetch()
        self.assertFalse(control._active.get())
