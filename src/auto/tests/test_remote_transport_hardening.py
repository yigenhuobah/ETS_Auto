#!/usr/bin/env python3
"""Remote transport, cache timestamp and schema-boundary regressions."""
import json
import math
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch


AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(os.path.dirname(AUTO_DIR))
if AUTO_DIR not in sys.path:
    sys.path.insert(0, AUTO_DIR)

import ets_remote
from ets_pk_store import PKExtraBackupError


INFO_URL = (
    'https://raw.githubusercontent.com/'
    'yigenhuobah/ETS_Auto/master/info.json'
)
PK_URL = (
    'https://raw.githubusercontent.com/'
    'yigenhuobah/ETS_Auto/master/pk_extra.json'
)


class Response:
    status = 200

    def __init__(self, body, final_url=INFO_URL):
        self.body = body
        self.final_url = final_url
        self.read_args = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.final_url

    def read(self, *args):
        self.read_args.append(args)
        return self.body


class NoArgResponse:
    status = 200

    def __init__(self, body):
        self.body = body
        self.read_called = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        self.read_called = True
        return self.body


class TestRedirectGuard(unittest.TestCase):
    def setUp(self):
        self.request = urllib.request.Request(INFO_URL)
        self.handler = ets_remote._AllowlistedRedirectHandler()

    def test_disallowed_redirect_is_rejected_before_parent_handler(self):
        with patch.object(
                urllib.request.HTTPRedirectHandler,
                'redirect_request',
        ) as parent_redirect:
            with self.assertRaisesRegex(
                    urllib.error.URLError, 'not allowlisted'):
                self.handler.redirect_request(
                    self.request, None, 302, 'Found', {},
                    'https://evil.example/info.json',
                )

        parent_redirect.assert_not_called()

    def test_allowlisted_absolute_and_relative_redirects_continue(self):
        cases = (
            (
                'https://gitee.com/o/r/raw/master/info.json',
                'https://gitee.com/o/r/raw/master/info.json',
            ),
            (
                '../next.json',
                (
                    'https://raw.githubusercontent.com/yigenhuobah/'
                    'ETS_Auto/next.json'
                ),
            ),
        )
        for location, expected in cases:
            with self.subTest(location=location):
                marker = object()
                with patch.object(
                        urllib.request.HTTPRedirectHandler,
                        'redirect_request',
                        return_value=marker,
                ) as parent_redirect:
                    result = self.handler.redirect_request(
                        self.request, None, 302, 'Found', {}, location,
                    )

                self.assertIs(result, marker)
                self.assertEqual(parent_redirect.call_args.args[-1], expected)

    def test_open_helper_installs_redirect_guard_without_network(self):
        response = object()

        class Opener:
            def __init__(self):
                self.calls = []

            def open(self, request, timeout=None):
                self.calls.append((request, timeout))
                return response

        opener = Opener()
        with patch.object(
                urllib.request, 'build_opener', return_value=opener
        ) as build_opener:
            actual = ets_remote._open_remote_url(self.request, timeout=3)

        self.assertIs(actual, response)
        self.assertIsInstance(
            build_opener.call_args.args[0],
            ets_remote._AllowlistedRedirectHandler,
        )
        self.assertEqual(opener.calls, [(self.request, 3)])


class TestInfoTransport(unittest.TestCase):
    def test_deeply_nested_info_is_rejected(self):
        response = Response(('[' * 1500 + '0' + ']' * 1500).encode('ascii'))
        remote = ets_remote.ETSRemote(current_version='0.7.0')
        with patch.object(
                ets_remote, '_open_remote_url', return_value=response):
            self.assertIsNone(remote._fetch_json(INFO_URL))

    def test_info_read_is_bounded(self):
        body = json.dumps({'version': '0.7.0'}).encode('utf-8')
        response = Response(body)
        remote = ets_remote.ETSRemote(current_version='0.7.0')

        with patch.object(
                ets_remote, '_open_remote_url',
                return_value=response):
            data = remote._fetch_json(INFO_URL)

        self.assertEqual(data, {'version': '0.7.0'})
        self.assertEqual(
            response.read_args,
            [(ets_remote._INFO_MAX_BYTES + 1,)],
        )

    def test_no_arg_read_double_remains_supported(self):
        response = NoArgResponse(
            json.dumps({'version': '0.7.0'}).encode('utf-8'))
        remote = ets_remote.ETSRemote(current_version='0.7.0')

        with patch.object(
                ets_remote, '_open_remote_url',
                return_value=response):
            data = remote._fetch_json(INFO_URL)

        self.assertEqual(data, {'version': '0.7.0'})
        self.assertTrue(response.read_called)

    def test_oversized_info_body_is_rejected(self):
        response = Response(b'x' * (ets_remote._INFO_MAX_BYTES + 1))
        remote = ets_remote.ETSRemote(current_version='0.7.0')

        with patch.object(
                ets_remote, '_open_remote_url',
                return_value=response):
            self.assertIsNone(remote._fetch_json(INFO_URL))

    def test_info_redirect_to_disallowed_host_is_rejected_before_read(self):
        response = Response(
            json.dumps({'version': '9.9.9'}).encode('utf-8'),
            final_url='https://evil.example/info.json',
        )
        remote = ets_remote.ETSRemote(current_version='0.7.0')

        with patch.object(
                ets_remote, '_open_remote_url',
                return_value=response):
            self.assertIsNone(remote._fetch_json(INFO_URL))

        self.assertEqual(response.read_args, [])

    def test_info_redirect_between_allowed_hosts_is_accepted(self):
        response = Response(
            json.dumps({'version': '0.7.0'}).encode('utf-8'),
            final_url='https://gitee.com/yigenhuobah/ETS_Auto/raw/master/info.json',
        )
        remote = ets_remote.ETSRemote(current_version='0.7.0')

        with patch.object(
                ets_remote, '_open_remote_url',
                return_value=response):
            self.assertEqual(
                remote._fetch_json(INFO_URL),
                {'version': '0.7.0'},
            )


class TestPKTransport(unittest.TestCase):
    def setUp(self):
        # These tests exercise the transport itself; re-arm the network switch
        # that production keeps off (REMOTE_NETWORK_ENABLED=False).
        patcher = patch.object(ets_remote, 'REMOTE_NETWORK_ENABLED', True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_deeply_nested_pk_payload_uses_failure_contract(self):
        response = Response(
            ('[' * 1500 + '0' + ']' * 1500).encode('ascii'),
            final_url=PK_URL,
        )
        remote = ets_remote.ETSRemote(current_version='0.7.0')
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                ets_remote, '_open_remote_url', return_value=response):
            ok, message = remote.download_pk_extra(
                url=PK_URL,
                target_path=os.path.join(tmp, 'pk_extra.json'),
            )

        self.assertFalse(ok)
        self.assertIsInstance(message, str)

    def test_pk_redirect_to_disallowed_host_is_rejected(self):
        response = Response(
            json.dumps({'remote': 'value'}).encode('utf-8'),
            final_url='https://evil.example/pk_extra.json',
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            remote = ets_remote.ETSRemote(current_version='0.7.0')
            with patch.dict(
                    os.environ,
                    {'ETS_REMOTE_HMAC': '', 'ETS_REMOTE_PUBKEY': ''}), \
                    patch.object(
                        ets_remote, '_open_remote_url',
                        return_value=response):
                ok, _message = remote.download_pk_extra(
                    url=PK_URL, target_path=target)

            self.assertFalse(ok)
            self.assertFalse(os.path.exists(target))
            self.assertEqual(response.read_args, [])

    def test_pk_no_arg_read_double_remains_supported(self):
        response = NoArgResponse(
            json.dumps({'remote': 'value'}).encode('utf-8'))
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            remote = ets_remote.ETSRemote(current_version='0.7.0')
            with patch.dict(
                    os.environ,
                    {'ETS_REMOTE_HMAC': '', 'ETS_REMOTE_PUBKEY': ''}), \
                    patch.object(
                        ets_remote, '_open_remote_url',
                        return_value=response):
                ok, message = remote.download_pk_extra(
                    url=PK_URL, target_path=target)

            self.assertTrue(ok, message)
            self.assertEqual(_read_json(target), {'remote': 'value'})
            self.assertTrue(response.read_called)

    def test_local_commit_failure_returns_false_without_mirror_retry(self):
        response = Response(
            json.dumps({'remote': 'value'}).encode('utf-8'),
            final_url=PK_URL,
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            remote = ets_remote.ETSRemote(current_version='0.7.0')
            with patch.dict(
                    os.environ,
                    {'ETS_REMOTE_HMAC': '', 'ETS_REMOTE_PUBKEY': ''}), \
                    patch.object(
                        ets_remote, '_open_remote_url',
                        return_value=response) as open_url, \
                    patch.object(
                        ets_remote, 'merge_write_pk_extra',
                        side_effect=PKExtraBackupError('backup failed')):
                ok, message = remote.download_pk_extra(
                    url=PK_URL, target_path=target)

            self.assertFalse(ok)
            self.assertIn('保存失败', message)
            self.assertEqual(open_url.call_count, 1)

    def test_gitee_fallback_supports_main_and_master(self):
        for branch in ('main', 'master'):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as tmp:
                requested = []

                def fail(request, timeout=None, requested=requested):
                    requested.append(request.full_url)
                    raise urllib.error.URLError('offline')

                remote = ets_remote.ETSRemote(current_version='0.7.0')
                url = (
                    'https://raw.githubusercontent.com/o/r/%s/pk_extra.json'
                    % branch
                )
                with patch.object(
                        ets_remote, '_open_remote_url', side_effect=fail):
                    ok, _message = remote.download_pk_extra(
                        url=url,
                        target_path=os.path.join(tmp, 'pk_extra.json'),
                    )

                self.assertFalse(ok)
                self.assertIn(
                    'https://gitee.com/o/r/raw/%s/pk_extra.json' % branch,
                    requested,
                )


def _read_json(path):
    with open(path, 'r', encoding='utf-8') as stream:
        return json.load(stream)


class TestCacheTimestamp(unittest.TestCase):
    def test_deeply_nested_cache_load_and_save_are_best_effort(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = self._make_remote(tmp)
            with open(remote._cache_path, 'w', encoding='utf-8') as stream:
                stream.write('[' * 1500 + '0' + ']' * 1500)
            self.assertIsNone(remote._load_cache())

            os.unlink(remote._cache_path)
            deep = {}
            cursor = deep
            for _ in range(1500):
                child = {}
                cursor['nested'] = child
                cursor = child
            remote._save_cache(deep)
            self.assertFalse(os.path.exists(remote._cache_path))

    def _make_remote(self, tmp):
        remote = ets_remote.ETSRemote(current_version='0.7.0')
        remote._cache_path = os.path.join(tmp, 'cache.json')
        return remote

    def _write_cache(self, remote, fetched_at, wrapper=True):
        if wrapper:
            data = {
                'data': {'version': '0.7.0'},
                '_fetched_at': fetched_at,
                '_source': 'cache',
            }
        else:
            data = {
                'version': '0.7.0',
                '_fetched_at': fetched_at,
                '_source': 'cache',
            }
        with open(remote._cache_path, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)

    def test_invalid_cache_timestamps_are_rejected_without_exception(self):
        now = time.time()
        invalid_values = [
            '123', None, True, float('nan'), float('inf'),
            -float('inf'), now - ets_remote._CACHE_MAX_AGE - 1,
            now + ets_remote._CACHE_FUTURE_SKEW + 60,
            10 ** 400,
        ]
        with tempfile.TemporaryDirectory() as tmp:
            remote = self._make_remote(tmp)
            for value in invalid_values:
                with self.subTest(value=value):
                    self._write_cache(remote, value)
                    self.assertIsNone(remote._load_cache())

    def test_legacy_string_timestamp_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = self._make_remote(tmp)
            self._write_cache(remote, 'not-a-number', wrapper=False)
            self.assertIsNone(remote._load_cache())

    def test_small_future_skew_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = self._make_remote(tmp)
            future = time.time() + 30
            self._write_cache(remote, future)
            cached = remote._load_cache()
            self.assertIsNotNone(cached)
            self.assertTrue(math.isfinite(cached['_fetched_at']))


class TestRemoteFieldTypes(unittest.TestCase):
    def test_compare_versions_treats_non_strings_as_zero(self):
        self.assertEqual(ets_remote.compare_versions(None, []), 0)
        self.assertEqual(ets_remote.compare_versions('1.0.0', 100), 1)
        self.assertEqual(ets_remote.compare_versions({}, '1.0.0'), -1)

    def test_overlong_numeric_version_does_not_raise(self):
        overlong = '9' * 5000
        self.assertEqual(ets_remote.compare_versions(overlong, '0.0.0'), 0)

    def test_semver_prerelease_order_and_build_metadata(self):
        self.assertLess(ets_remote.compare_versions('1.0.0-alpha', '1.0.0-beta'), 0)
        self.assertLess(ets_remote.compare_versions('1.0.0-beta.2', '1.0.0-beta.11'), 0)
        self.assertLess(ets_remote.compare_versions('1.0.0-alpha.1', '1.0.0-alpha.beta'), 0)
        self.assertLess(ets_remote.compare_versions('1.0.0-beta', '1.0.0-beta.1'), 0)
        self.assertEqual(ets_remote.compare_versions('1.0.0+one', '1.0+two'), 0)

    def test_remote_text_fields_have_explicit_bounds(self):
        remote = ets_remote.ETSRemote(current_version='0.7.0')
        oversized_version = '9' * (ets_remote._VERSION_MAX_CHARS + 1)
        info = remote._parse_remote_info({
            'version': oversized_version,
            'minVer': '1.0.0-',
            'announcement': 'A' * (ets_remote._ANNOUNCEMENT_MAX_CHARS + 10),
            'pkExtraUrl': 'https://github.com/' + 'x' * ets_remote._URL_MAX_CHARS,
            'downloadUrl': 'https://github.com/' + 'y' * ets_remote._URL_MAX_CHARS,
        }, source='s' * (ets_remote._SOURCE_MAX_CHARS + 1), fetched_at=1000)

        self.assertEqual(info.latest_version, '0.0.0')
        self.assertEqual(info.min_version, '0.0.0')
        self.assertEqual(len(info.announcement), ets_remote._ANNOUNCEMENT_MAX_CHARS)
        self.assertEqual(info.announcement, 'A' * ets_remote._ANNOUNCEMENT_MAX_CHARS)
        self.assertEqual(info.pk_extra_url, '')
        self.assertEqual(info.download_url, '')
        self.assertEqual(info.source, 'unknown')


    def test_non_string_remote_fields_fall_back_safely(self):
        remote = ets_remote.ETSRemote(current_version=100)
        info = remote._parse_remote_info({
            'version': {'bad': 'type'},
            'minVer': ['bad'],
            'allowStart': 'false',
            'announcement': {'bad': 'type'},
            'pkExtraUrl': ['bad'],
            'downloadUrl': 123,
        }, source=42, fetched_at=1000)

        self.assertEqual(info.latest_version, '0.0.0')
        self.assertEqual(info.min_version, '0.0.0')
        self.assertTrue(info.allow_start)
        self.assertEqual(info.announcement, '')
        self.assertEqual(info.pk_extra_url, '')
        self.assertEqual(info.download_url, '')
        self.assertEqual(info.source, 'unknown')

    def test_config_and_info_mirrors_use_master_without_pk_url(self):
        remote = ets_remote.ETSRemote(current_version='0.7.0')
        urls = [url for _name, url in remote._build_urls()]
        self.assertTrue(all('/master/info.json' in url for url in urls))
        self.assertFalse(any('/main/info.json' in url for url in urls))

        with open(
                os.path.join(PROJECT_ROOT, 'info.json'),
                'r', encoding='utf-8') as stream:
            info_json = json.load(stream)
        self.assertEqual(info_json.get('pkExtraUrl'), '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
