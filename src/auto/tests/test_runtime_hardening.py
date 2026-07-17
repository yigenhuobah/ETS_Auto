#!/usr/bin/env python3
"""Regression tests for CDP/runtime boundary hardening (no live network)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


AUTO_DIR = Path(__file__).resolve().parents[1]
if str(AUTO_DIR) not in sys.path:
    sys.path.insert(0, str(AUTO_DIR))

import websocket

import ets_common


class FakeResponse:
    def __init__(self, data, on_close=None):
        self.data = data
        self.on_close = on_close
        self.read_sizes = []
        self.closed = False

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.data if size is None or size < 0 else self.data[:size]

    def close(self):
        self.closed = True
        if self.on_close:
            self.on_close()


class FakeSocket:
    def __init__(self, *, send_error=None, recv_items=None,
                 handshake_status=101, handshake_headers=None):
        self.send_error = send_error
        self.recv_items = list(recv_items or [])
        self.closed = False
        self.handshake_response = SimpleNamespace(
            status=handshake_status,
            headers=dict(handshake_headers or {}),
        )
        self.timeouts = []
        self.sent = []

    def close(self):
        self.closed = True

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def send(self, payload):
        self.sent.append(payload)
        if self.send_error:
            raise self.send_error

    def recv(self):
        if not self.recv_items:
            raise AssertionError("unexpected recv")
        item = self.recv_items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _tab(url='https://statics.ets100.com/app/mockExam'):
    return {
        'url': url,
        'title': 'Exam',
        'type': 'page',
        'webSocketDebuggerUrl': 'ws://127.0.0.1:10086/devtools/page/1',
    }


def _response(tabs):
    return FakeResponse(json.dumps(tabs).encode('utf-8'))


class TestEtsPageUrl(unittest.TestCase):
    def test_accepts_apex_and_subdomain(self):
        self.assertTrue(ets_common.is_ets_page_url('https://ets100.com/'))
        self.assertTrue(ets_common.is_ets_page_url(
            'https://statics.ets100.com/app/mockExam?set_id=1'))

    def test_rejects_query_bait_and_suffix_domain(self):
        self.assertFalse(ets_common.is_ets_page_url(
            'https://evil.test/?next=https://ets100.com/exam'))
        self.assertFalse(ets_common.is_ets_page_url(
            'https://ets100.com.evil.test/exam'))

    def test_rejects_non_http_and_credentials(self):
        self.assertFalse(ets_common.is_ets_page_url(
            'file://ets100.com/exam'))
        self.assertFalse(ets_common.is_ets_page_url(
            'https://user:pass@ets100.com/exam'))
        self.assertFalse(ets_common.is_ets_page_url(None))


class TestCDPDiscoveryTransport(unittest.TestCase):
    def test_redirect_is_rejected_before_parent_handler(self):
        request = Mock(full_url='http://127.0.0.1:10086/json')
        handler = ets_common._RejectCDPRedirectHandler()
        with patch.object(
                ets_common.urllib.request.HTTPRedirectHandler,
                'redirect_request',
        ) as parent_redirect:
            with self.assertRaisesRegex(
                    ets_common.urllib.error.URLError,
                    'redirects are not allowed',
            ):
                handler.redirect_request(
                    request, None, 302, 'Found', {}, 'https://evil.example/json')

        parent_redirect.assert_not_called()

    def test_open_helper_disables_proxy_and_installs_redirect_guard(self):
        response = object()
        opener = Mock()
        opener.open.return_value = response
        with patch.object(
                ets_common.urllib.request,
                'build_opener',
                return_value=opener,
        ) as build_opener:
            actual = ets_common._open_local_cdp_url(
                'http://127.0.0.1:10086/json', timeout=3)

        self.assertIs(actual, response)
        opener.open.assert_called_once_with(
            'http://127.0.0.1:10086/json', timeout=3)
        handlers = build_opener.call_args.args
        self.assertIsInstance(handlers[0], ets_common.urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], ets_common._RejectCDPRedirectHandler)

    def test_open_helper_rejects_non_loopback_before_building_opener(self):
        with patch.object(ets_common.urllib.request, 'build_opener') as build_opener:
            with self.assertRaisesRegex(ValueError, 'loopback'):
                ets_common._open_local_cdp_url('http://example.com:10086/json', 3)
        build_opener.assert_not_called()


class TestCDPWebSocketTransport(unittest.TestCase):
    def test_proxy_environment_is_bypassed_with_preconnected_socket(self):
        raw_socket = FakeSocket()
        ws = FakeSocket()
        socket_factory = Mock(return_value=raw_socket)
        ws_factory = Mock(return_value=ws)
        proxy_env = {
            'HTTP_PROXY': 'http://192.0.2.1:3128',
            'HTTPS_PROXY': 'http://192.0.2.1:3128',
            'NO_PROXY': '',
        }

        with patch.dict(os.environ, proxy_env, clear=False):
            actual = ets_common._connect_local_cdp_websocket(
                'ws://localhost:10086/devtools/page/1',
                timeout=4,
                ws_factory=ws_factory,
                socket_factory=socket_factory,
            )

        self.assertIs(actual, ws)
        socket_factory.assert_called_once_with(
            ('127.0.0.1', 10086), timeout=4)
        ws_factory.assert_called_once_with(
            'ws://localhost:10086/devtools/page/1',
            timeout=4,
            socket=raw_socket,
            redirect_limit=0,
        )
        self.assertFalse(raw_socket.closed)

    def test_redirect_handshake_is_rejected_and_transport_is_closed(self):
        raw_socket = FakeSocket()
        ws = FakeSocket(
            handshake_status=302,
            handshake_headers={
                'location': 'ws://192.0.2.10:10086/devtools/page/evil',
            },
        )
        socket_factory = Mock(return_value=raw_socket)
        ws_factory = Mock(return_value=ws)

        with self.assertRaisesRegex(
                websocket.WebSocketException, 'redirects are not allowed'):
            ets_common._connect_local_cdp_websocket(
                'ws://127.0.0.1:10086/devtools/page/1',
                timeout=4,
                ws_factory=ws_factory,
                socket_factory=socket_factory,
            )

        socket_factory.assert_called_once()
        ws_factory.assert_called_once()
        self.assertTrue(ws.closed)
        self.assertTrue(raw_socket.closed)

    def test_websocket_factory_failure_closes_preconnected_socket(self):
        raw_socket = FakeSocket()

        with self.assertRaisesRegex(OSError, 'handshake failed'):
            ets_common._connect_local_cdp_websocket(
                'ws://127.0.0.1:10086/devtools/page/1',
                timeout=4,
                ws_factory=Mock(side_effect=OSError('handshake failed')),
                socket_factory=Mock(return_value=raw_socket),
            )

        self.assertTrue(raw_socket.closed)

    def test_missing_preconnected_socket_never_calls_websocket_factory(self):
        ws_factory = Mock()

        with self.assertRaisesRegex(
                ConnectionError, 'factory returned no socket'):
            ets_common._connect_local_cdp_websocket(
                'ws://127.0.0.1:10086/devtools/page/1',
                timeout=4,
                ws_factory=ws_factory,
                socket_factory=Mock(return_value=None),
            )

        ws_factory.assert_not_called()

    def test_wss_is_rejected_before_opening_socket(self):
        socket_factory = Mock()
        with self.assertRaisesRegex(ValueError, 'loopback ws://'):
            ets_common._connect_local_cdp_websocket(
                'wss://127.0.0.1:10086/devtools/page/1',
                timeout=4,
                socket_factory=socket_factory,
            )
        socket_factory.assert_not_called()


class TestConnectionHardening(unittest.TestCase):
    def test_connect_bounds_discovery_and_handshake(self):
        base = ets_common.ETSBase()
        response = _response([_tab()])
        ws = FakeSocket()
        with patch.object(ets_common, '_open_local_cdp_url',
                          return_value=response) as open_mock, \
                patch.object(ets_common, '_connect_local_cdp_websocket',
                             return_value=ws) as ws_mock:
            base.connect()

        self.assertEqual(
            response.read_sizes, [base._CDP_JSON_MAX_BYTES + 1])
        self.assertTrue(response.closed)
        self.assertEqual(
            open_mock.call_args.kwargs['timeout'], base._CDP_DISCOVERY_TIMEOUT)
        self.assertEqual(
            ws_mock.call_args.kwargs['timeout'], base._WS_CONNECT_TIMEOUT)
        self.assertIs(base.ws, ws)
        self.assertEqual(base.mid, 0)

    def test_oversized_discovery_is_rejected_before_websocket(self):
        base = ets_common.ETSBase()
        response = FakeResponse(b'x' * (base._CDP_JSON_MAX_BYTES + 1))
        ws_mock = Mock()
        with patch.object(ets_common, '_open_local_cdp_url',
                          return_value=response), \
                patch.object(
                    ets_common, '_connect_local_cdp_websocket', ws_mock):
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                base.connect()

        ws_mock.assert_not_called()
        self.assertTrue(response.closed)
        self.assertIsNone(base.ws)
        self.assertIsNone(base.tab)

    def test_non_list_discovery_is_rejected(self):
        base = ets_common.ETSBase()
        response = FakeResponse(json.dumps({'url': 'x'}).encode('utf-8'))
        with patch.object(ets_common, '_open_local_cdp_url',
                          return_value=response), \
                patch.object(ets_common, '_connect_local_cdp_websocket') as ws_mock:
            with self.assertRaisesRegex(ValueError, 'not a list'):
                base.connect()
        ws_mock.assert_not_called()
        self.assertIsNone(base.ws)
        self.assertIsNone(base.tab)

    def test_query_bait_target_never_opens_websocket(self):
        base = ets_common.ETSBase()
        response = _response([_tab(
            'https://evil.test/?next=https://ets100.com/mockExam')])
        with patch.object(ets_common, '_open_local_cdp_url',
                          return_value=response), \
                patch.object(ets_common, '_connect_local_cdp_websocket') as ws_mock:
            with self.assertRaisesRegex(Exception, 'No ETS tab'):
                base.connect()
        ws_mock.assert_not_called()
        self.assertIsNone(base.ws)
        self.assertIsNone(base.tab)

    def test_repeated_connect_closes_old_socket_before_discovery(self):
        base = ets_common.ETSBase()
        old_ws = FakeSocket()
        new_ws = FakeSocket()
        base.ws = old_ws
        base.tab = {'url': 'stale'}
        response = _response([_tab()])

        def open_after_close(*_args, **_kwargs):
            self.assertTrue(old_ws.closed)
            self.assertIsNone(base.ws)
            self.assertIsNone(base.tab)
            return response

        with patch.object(ets_common, '_open_local_cdp_url',
                          side_effect=open_after_close), \
                patch.object(ets_common, '_connect_local_cdp_websocket',
                             return_value=new_ws):
            base.connect()
        self.assertIs(base.ws, new_ws)

    def test_connect_failure_clears_selected_tab(self):
        base = ets_common.ETSBase()
        response = _response([_tab()])
        error = websocket.WebSocketTimeoutException('handshake timeout')
        with patch.object(ets_common, '_open_local_cdp_url',
                          return_value=response), \
                patch.object(ets_common, '_connect_local_cdp_websocket',
                             side_effect=error):
            with self.assertRaises(websocket.WebSocketTimeoutException):
                base.connect()
        self.assertIsNone(base.ws)
        self.assertIsNone(base.tab)

    def test_stopped_connect_closes_old_socket_without_network(self):
        stopped = threading.Event()
        stopped.set()
        base = ets_common.ETSBase(stop_event=stopped)
        old_ws = FakeSocket()
        base.ws = old_ws
        base.tab = {'url': 'stale'}
        with patch.object(ets_common, '_open_local_cdp_url') as open_mock, \
                patch.object(ets_common, '_connect_local_cdp_websocket') as ws_mock:
            with self.assertRaises(InterruptedError):
                base.connect()
        self.assertTrue(old_ws.closed)
        open_mock.assert_not_called()
        ws_mock.assert_not_called()
        self.assertIsNone(base.ws)
        self.assertIsNone(base.tab)

    def test_stopped_reconnect_does_not_network(self):
        stopped = threading.Event()
        stopped.set()
        base = ets_common.ETSBase(stop_event=stopped)
        with patch.object(ets_common, '_open_local_cdp_url') as open_mock, \
                patch.object(ets_common, '_connect_local_cdp_websocket') as ws_mock:
            with self.assertRaises(InterruptedError):
                base.reconnect()
        open_mock.assert_not_called()
        ws_mock.assert_not_called()
        self.assertIsNone(base.ws)
        self.assertIsNone(base.tab)

    def test_stop_after_discovery_skips_websocket(self):
        stopped = threading.Event()
        base = ets_common.ETSBase(stop_event=stopped)
        response = FakeResponse(
            json.dumps([_tab()]).encode('utf-8'), on_close=stopped.set)
        with patch.object(ets_common, '_open_local_cdp_url',
                          return_value=response), \
                patch.object(ets_common, '_connect_local_cdp_websocket') as ws_mock:
            with self.assertRaises(InterruptedError):
                base.connect()
        ws_mock.assert_not_called()
        self.assertIsNone(base.ws)
        self.assertIsNone(base.tab)

    def test_reconnect_uses_finite_handshake_timeout(self):
        base = ets_common.ETSBase()
        base._RECONNECT_MAX_RETRIES = 1
        response = _response([_tab()])
        ws = FakeSocket()
        with patch.object(ets_common, '_open_local_cdp_url',
                          return_value=response), \
                patch.object(ets_common, '_connect_local_cdp_websocket',
                             return_value=ws) as ws_mock:
            self.assertTrue(base.reconnect())
        self.assertEqual(
            ws_mock.call_args.kwargs['timeout'], base._WS_CONNECT_TIMEOUT)
        self.assertIs(base.ws, ws)


class TestEvalJsHardening(unittest.TestCase):
    def _base_with_socket(self, ws):
        base = ets_common.ETSBase()
        base.ws = ws
        return base

    def test_send_timeout_becomes_builtin_timeout_and_invalidates(self):
        ws = FakeSocket(send_error=websocket.WebSocketTimeoutException('slow'))
        base = self._base_with_socket(ws)
        with self.assertRaises(TimeoutError):
            base.eval_js('1 + 1')
        self.assertEqual(ws.timeouts, [base._EVAL_JS_TIMEOUT])
        self.assertTrue(ws.closed)
        self.assertIsNone(base.ws)

    def test_send_protocol_error_becomes_connection_error(self):
        ws = FakeSocket(send_error=websocket.WebSocketProtocolException('bad'))
        base = self._base_with_socket(ws)
        with self.assertRaises(ConnectionError):
            base.eval_js('1 + 1')
        self.assertTrue(ws.closed)
        self.assertIsNone(base.ws)

    def test_generic_websocket_send_error_becomes_connection_error(self):
        ws = FakeSocket(send_error=websocket.WebSocketException('bad'))
        base = self._base_with_socket(ws)
        with self.assertRaises(ConnectionError):
            base.eval_js('1 + 1')
        self.assertTrue(ws.closed)
        self.assertIsNone(base.ws)

    def test_recv_protocol_error_becomes_connection_error(self):
        ws = FakeSocket(recv_items=[
            websocket.WebSocketProtocolException('bad frame')])
        base = self._base_with_socket(ws)
        with self.assertRaises(ConnectionError):
            base.eval_js('1 + 1')
        self.assertTrue(ws.closed)
        self.assertIsNone(base.ws)

    def test_non_object_and_non_json_frames_are_skipped(self):
        valid = json.dumps({'id': 1, 'result': {'result': {'value': 42}}})
        ws = FakeSocket(recv_items=[None, json.dumps([]), valid])
        base = self._base_with_socket(ws)
        self.assertEqual(base.eval_js('6 * 7'), 42)
        self.assertFalse(ws.closed)

    def test_matching_response_with_non_object_result_invalidates_socket(self):
        malformed = json.dumps({'id': 1, 'result': []})
        ws = FakeSocket(recv_items=[malformed])
        base = self._base_with_socket(ws)
        with self.assertRaisesRegex(ConnectionError, 'result is not an object'):
            base.eval_js('1 + 1')
        self.assertTrue(ws.closed)
        self.assertIsNone(base.ws)

    def test_matching_response_with_malformed_exception_invalidates_socket(self):
        malformed = json.dumps({
            'id': 1,
            'result': {'result': {}, 'exceptionDetails': []},
        })
        ws = FakeSocket(recv_items=[malformed])
        base = self._base_with_socket(ws)
        with self.assertRaisesRegex(ConnectionError, 'exception is not an object'):
            base.eval_js('1 + 1')
        self.assertTrue(ws.closed)
        self.assertIsNone(base.ws)
    def test_eval_deadline_uses_monotonic(self):
        ws = FakeSocket(recv_items=[
            websocket.WebSocketTimeoutException('slice')])
        base = self._base_with_socket(ws)
        base._EVAL_JS_TIMEOUT = 1
        with patch.object(ets_common.time, 'time',
                          side_effect=AssertionError('wall clock used')), \
                patch.object(ets_common.time, 'monotonic',
                             side_effect=[0.0, 0.2, 1.1]):
            with self.assertRaises(TimeoutError):
                base.eval_js('1 + 1')
        self.assertTrue(ws.closed)
        self.assertIsNone(base.ws)

    def test_success_sets_send_timeout_and_uses_monotonic(self):
        result = json.dumps({
            'id': 1,
            'result': {'result': {'value': 42}},
        })
        ws = FakeSocket(recv_items=[result])
        base = self._base_with_socket(ws)
        with patch.object(ets_common.time, 'time',
                          side_effect=AssertionError('wall clock used')), \
                patch.object(ets_common.time, 'monotonic',
                             side_effect=[100.0, 100.1]):
            self.assertEqual(base.eval_js('6 * 7'), 42)
        self.assertEqual(ws.timeouts[0], base._EVAL_JS_TIMEOUT)
        self.assertEqual(len(ws.sent), 1)
        self.assertFalse(ws.closed)

    def test_interruptible_sleep_uses_monotonic(self):
        event = Mock()
        event.is_set.return_value = False
        base = ets_common.ETSBase(stop_event=event)
        with patch.object(ets_common.time, 'time',
                          side_effect=AssertionError('wall clock used')), \
                patch.object(ets_common.time, 'monotonic',
                             side_effect=[10.0, 10.5, 10.6, 11.0]):
            base.interruptible_sleep(1.0)
        event.wait.assert_called_once_with(timeout=0.2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
