#!/usr/bin/env python3
"""Boundary tests for the read-only compatibility preflight."""
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import Mock, patch


AUTO_DIR = Path(__file__).resolve().parents[1]
if str(AUTO_DIR) not in sys.path:
    sys.path.insert(0, str(AUTO_DIR))

import ets_compat


class Response:
    def __init__(self, body):
        self.body = body
        self.read_sizes = []
        self.closed = False

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.body if size < 0 else self.body[:size]

    def close(self):
        self.closed = True


class TestCompatibilityTargetBoundary(unittest.TestCase):
    def test_accepts_only_http_ets_pages(self):
        accepted = ets_compat._normalize_ets_tab({
            'url': 'https://statics.ets100.com/mockExam',
            'title': 'Exam',
            'webSocketDebuggerUrl': 'ws://127.0.0.1:10086/page/1',
        })
        self.assertIsNotNone(accepted)

        rejected = (
            'ftp://ets100.com/mockExam',
            'https://user:pass@ets100.com/mockExam',
            'https://ets100.com.evil.example/mockExam',
            'https://evil.example/?next=https://ets100.com/mockExam',
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertIsNone(ets_compat._normalize_ets_tab({'url': url}))

    def test_malformed_target_shapes_are_rejected(self):
        for value in (None, [], 'tab', {'url': None}):
            with self.subTest(value=value):
                self.assertIsNone(ets_compat._normalize_ets_tab(value))


    def test_default_discovery_uses_protected_loopback_opener(self):
        response = Response(b'[]')
        with patch.object(
                ets_compat, '_open_local_cdp_url', return_value=response) as opener:
            report = ets_compat.collect_compatibility_report(ws_factory=Mock())

        self.assertFalse(report['ok'])
        opener.assert_called_once_with('http://127.0.0.1:10086/json', timeout=5.0)
        self.assertTrue(response.closed)

    def test_default_attach_uses_direct_loopback_helper(self):
        response = Response(json.dumps([{
            'url': 'https://statics.ets100.com/mockExam',
            'title': 'Exam',
            'type': 'page',
            'webSocketDebuggerUrl': 'ws://127.0.0.1:10086/page/1',
        }]).encode('utf-8'))
        ws = Mock()
        ws.close = Mock()
        connector = Mock(return_value=ws)
        snapshot = json.dumps({
            'href': 'https://statics.ets100.com/mockExam',
            'iframe': {},
            'exam': {},
            'bridge': {},
            'pk': {},
        })

        with patch.object(
                ets_compat, '_connect_local_cdp_websocket', connector), \
                patch.object(ets_compat.ETSBase, 'eval_js',
                             return_value=snapshot):
            ets_compat.collect_compatibility_report(
                opener=Mock(return_value=response))

        connector.assert_called_once_with(
            'ws://127.0.0.1:10086/page/1', timeout=5.0)
        ws.close.assert_called_once()

class TestCompatibilityDiscoveryBoundary(unittest.TestCase):
    def test_read_response_is_bounded_and_closed(self):
        body = json.dumps([]).encode('utf-8')
        response = Response(body)
        opener = Mock(return_value=response)

        self.assertEqual(
            ets_compat._read_response(opener, 'http://127.0.0.1:10086/json', 2),
            body,
        )
        self.assertEqual(
            response.read_sizes,
            [ets_compat.ETSBase._CDP_JSON_MAX_BYTES + 1],
        )
        self.assertTrue(response.closed)

    def test_oversized_discovery_blocks_before_websocket(self):
        response = Response(
            b'x' * (ets_compat.ETSBase._CDP_JSON_MAX_BYTES + 1))
        opener = Mock(return_value=response)
        ws_factory = Mock()

        report = ets_compat.collect_compatibility_report(
            opener=opener, ws_factory=ws_factory)

        self.assertFalse(report['ok'])
        endpoint = next(
            item for item in report['checks']
            if item['id'] == 'cdp.endpoint')
        self.assertEqual(endpoint['status'], 'fail')
        self.assertTrue(endpoint['blocking'])
        self.assertIn('exceeds', endpoint['detail'])
        self.assertTrue(response.closed)
        ws_factory.assert_not_called()

    def test_non_bytes_discovery_blocks_cleanly(self):
        response = Response('not bytes')
        report = ets_compat.collect_compatibility_report(
            opener=Mock(return_value=response), ws_factory=Mock())
        self.assertFalse(report['ok'])
        endpoint = next(
            item for item in report['checks']
            if item['id'] == 'cdp.endpoint')
        self.assertIn('bytes', endpoint['detail'])


class TestCompatibilityInputBoundary(unittest.TestCase):
    def test_bool_port_is_not_treated_as_integer_port(self):
        opener = Mock()
        report = ets_compat.collect_compatibility_report(
            port=True, opener=opener)
        self.assertFalse(report['ok'])
        self.assertEqual(report['checks'][0]['id'], 'input.parameters')
        opener.assert_not_called()

    def test_nonfinite_and_huge_timeouts_fail_before_network(self):
        for timeout in (float('nan'), float('inf'), -float('inf'), 10 ** 400):
            opener = Mock()
            with self.subTest(timeout=timeout):
                report = ets_compat.collect_compatibility_report(
                    timeout=timeout, opener=opener)
                self.assertFalse(report['ok'])
                parameters = next(
                    item for item in report['checks']
                    if item['id'] == 'input.parameters')
                self.assertEqual(parameters['status'], 'fail')
                opener.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
