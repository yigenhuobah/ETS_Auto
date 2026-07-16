#!/usr/bin/env python3
"""Read-only compatibility preflight for the ETS desktop client."""
import json
import os
import urllib.request
from urllib.parse import urlsplit

import websocket

from ets_common import (
    APP_VERSION,
    ETSBase,
    constrain_ets_data_root,
    is_loopback_ws_url,
)


SCHEMA_VERSION = 1
SUPPORTED_MODES = ('exam', 'pk')

_CHECK_ORDER = (
    'input.parameters',
    'cdp.endpoint',
    'cdp.target',
    'cdp.ws_loopback',
    'cdp.attach',
    'page.runtime',
    'page.vue',
    'page.pinia',
    'page.surface',
    'bridge.native',
    'data.root',
)

_SNAPSHOT_JS = r'''(function(){
var r = {
    href: String(window.location.href || ''),
    readyState: String(document.readyState || ''),
    userAgent: String(navigator.userAgent || '').slice(0, 160),
    app: false,
    vue3: false,
    pinia: false,
    appDataPath: '',
    doHomework: null,
    iframe: {present: false, accessible: false, readyState: '', error: ''},
    exam: {choiceCount: 0, fillCount: 0, nextIcon: false},
    bridge: {
        returnChoose: false,
        returnBlank: false,
        setPCChoose2: false,
        iframeNext: false,
        alreadyHooked: false
    },
    pk: {title: false, optionCount: 0}
};
try {
    var app = document.getElementById('app');
    r.app = !!app;
    r.vue3 = !!(app && app.__vue_app__);
    var pinia = null;
    if (r.vue3 && app.__vue_app__.config && app.__vue_app__.config.globalProperties) {
        pinia = app.__vue_app__.config.globalProperties.$pinia || null;
    }
    r.pinia = !!(pinia && pinia.state && pinia.state.value);
    if (r.pinia) {
        var state = pinia.state.value;
        var cfg = state.appConfig || {};
        var homework = state.homeworkStore || {};
        r.appDataPath = String(cfg.appDataPath || '');
        r.doHomework = !!homework.doHomework;
    }

    r.pk.title = !!document.querySelector('.question-title');
    r.pk.optionCount = document.querySelectorAll('.question-items-item').length;

    var iframe = document.querySelector('iframe');
    r.iframe.present = !!iframe;
    if (iframe) {
        try {
            var win = iframe.contentWindow;
            var doc = iframe.contentDocument || (win && win.document);
            r.iframe.accessible = !!doc;
            if (doc) {
                r.iframe.readyState = String(doc.readyState || '');
                r.exam.choiceCount = doc.querySelectorAll('.choose2').length;
                r.exam.fillCount = doc.querySelectorAll('input, textarea').length;
                r.exam.nextIcon = !!doc.querySelector('.next_icon');
            }
            if (win) {
                r.bridge.returnChoose = typeof win.kttb_ReturnChoose === 'function';
                r.bridge.returnBlank = typeof win.kttb_returnPcBlank === 'function';
                r.bridge.setPCChoose2 = typeof win.setPCChoose2 === 'function';
                r.bridge.iframeNext = typeof win.next === 'function';
                r.bridge.alreadyHooked = !!win.__ets_hooked;
            }
        } catch (frameError) {
            r.iframe.error = String(frameError && frameError.message || frameError);
        }
    }
    return JSON.stringify(r);
} catch (error) {
    return JSON.stringify({error: String(error && error.message || error)});
}
})()'''


def _new_report(port, mode):
    return {
        'schema_version': SCHEMA_VERSION,
        'app_version': APP_VERSION,
        'mode': mode,
        'port': port,
        'ok': False,
        'can_start': False,
        'summary': '',
        'checks': [],
        'observations': {},
    }


def _add_check(report, check_id, status, summary, *, blocking=False,
               detail='', remediation=''):
    report['checks'].append({
        'id': check_id,
        'status': status,
        'blocking': bool(blocking),
        'summary': summary,
        'detail': str(detail or ''),
        'remediation': str(remediation or ''),
    })


def _finish(report):
    by_id = {check['id']: check for check in report['checks']}
    ordered = []
    for check_id in _CHECK_ORDER:
        check = by_id.pop(check_id, None)
        if check is None:
            check = {
                'id': check_id,
                'status': 'skip',
                'blocking': False,
                'summary': 'Not reached',
                'detail': '',
                'remediation': '',
            }
        ordered.append(check)
    ordered.extend(by_id.values())
    report['checks'] = ordered
    can_start = not any(
        check['status'] == 'fail' and check['blocking']
        for check in ordered
    )
    report['ok'] = can_start
    report['can_start'] = can_start
    warn_count = sum(check['status'] == 'warn' for check in ordered)
    fail_count = sum(check['status'] == 'fail' for check in ordered)
    report['summary'] = '%s; %d warning(s), %d failure(s)' % (
        'ready' if can_start else 'blocked', warn_count, fail_count)
    return report


def _sanitized_route(raw_url):
    try:
        parsed = urlsplit(str(raw_url or ''))
        if not parsed.scheme or not parsed.hostname:
            return ''
        host = parsed.hostname.lower()
        port = (':%d' % parsed.port) if parsed.port else ''
        return '%s://%s%s%s' % (parsed.scheme, host, port, parsed.path or '/')
    except (TypeError, ValueError):
        return ''


def _sanitized_ws_endpoint(raw_url):
    try:
        parsed = urlsplit(str(raw_url or ''))
        host = parsed.hostname or ''
        if ':' in host and not host.startswith('['):
            host = '[%s]' % host
        port = (':%d' % parsed.port) if parsed.port else ''
        return '%s://%s%s' % (parsed.scheme, host, port)
    except (TypeError, ValueError):
        return ''


def _normalize_ets_tab(tab):
    """Return a safe ETS target shape for ETSBase._pick_ets_tab, or None."""
    if not isinstance(tab, dict):
        return None
    url = tab.get('url')
    if not isinstance(url, str):
        return None
    try:
        host = (urlsplit(url).hostname or '').lower()
    except ValueError:
        return None
    if host != 'ets100.com' and not host.endswith('.ets100.com'):
        return None

    normalized = dict(tab)
    normalized['url'] = url
    for field in ('title', 'type', 'webSocketDebuggerUrl'):
        value = tab.get(field)
        normalized[field] = value if isinstance(value, str) else ''
    return normalized


def _read_response(opener, url, timeout):
    response = opener(url, timeout=timeout)
    try:
        return response.read()
    finally:
        close = getattr(response, 'close', None)
        if close:
            close()


def _probe_data_root(report, snapshot, appdata):
    raw_pinia = str(snapshot.get('appDataPath') or '')
    appdata_root = appdata if appdata is not None else os.environ.get('APPDATA', '')
    root = constrain_ets_data_root(raw_pinia, appdata=appdata_root or None)
    source = 'pinia' if root else 'default'
    if root is None and appdata_root:
        root = constrain_ets_data_root(appdata_root, appdata=appdata_root)

    observations = report['observations']
    observations['data_root'] = root or ''
    observations['data_root_source'] = source if root else 'unavailable'
    if not root:
        _add_check(
            report, 'data.root', 'warn', 'ETS data root is unavailable',
            detail='Pinia did not expose a safe path and APPDATA fallback is unavailable',
            remediation='Open an ETS exercise once, then run the check again.')
        return

    exists = os.path.isdir(root)
    readable = False
    if exists:
        try:
            with os.scandir(root) as entries:
                next(entries, None)
            readable = True
        except OSError:
            readable = False
    observations['data_root_exists'] = exists
    observations['data_root_readable'] = readable
    if exists and readable:
        _add_check(
            report, 'data.root', 'pass', 'ETS data root is readable',
            detail='%s (%s)' % (root, source))
    else:
        _add_check(
            report, 'data.root', 'warn', 'ETS data root is not ready',
            detail='%s (%s; exists=%s, readable=%s)' % (
                root, source, exists, readable),
            remediation='Open an ETS exercise so its local cache is initialized.')


def _add_snapshot_checks(report, snapshot, mode, appdata):
    observations = report['observations']
    observations.update({
        'page_route': _sanitized_route(snapshot.get('href')),
        'ready_state': str(snapshot.get('readyState') or ''),
        'user_agent': str(snapshot.get('userAgent') or ''),
        'vue3': bool(snapshot.get('vue3')),
        'pinia': bool(snapshot.get('pinia')),
        'homework_mode': snapshot.get('doHomework'),
        'iframe': dict(snapshot.get('iframe') or {}),
        'exam': dict(snapshot.get('exam') or {}),
        'bridge': dict(snapshot.get('bridge') or {}),
        'pk': dict(snapshot.get('pk') or {}),
    })

    if snapshot.get('app') and snapshot.get('vue3'):
        _add_check(report, 'page.vue', 'pass', 'Vue application detected')
    else:
        _add_check(
            report, 'page.vue', 'warn', 'Vue application is not ready',
            remediation='Navigate to an ETS exercise and wait for the page to finish loading.')

    if snapshot.get('pinia'):
        _add_check(report, 'page.pinia', 'pass', 'Pinia store detected')
    else:
        _add_check(
            report, 'page.pinia', 'warn', 'Pinia store is not available',
            remediation='This can be temporary on the portal or while the page is loading.')

    if mode == 'exam':
        iframe = snapshot.get('iframe') or {}
        exam = snapshot.get('exam') or {}
        choice_count = int(exam.get('choiceCount') or 0)
        fill_count = int(exam.get('fillCount') or 0)
        has_next = bool(exam.get('nextIcon'))
        has_exam_marker = choice_count > 0 or fill_count > 0 or has_next
        if iframe.get('accessible') and has_exam_marker:
            _add_check(
                report, 'page.surface', 'pass', 'Exam surface detected',
                detail='choices=%d, fills=%d, next=%s' % (
                    choice_count, fill_count, has_next))
        elif iframe.get('accessible'):
            _add_check(
                report, 'page.surface', 'warn', 'Exam iframe has no known markers',
                detail='choices=0, fills=0, next=False',
                remediation='Enter a supported exam question and run the check again.')
        else:
            detail = iframe.get('error') or 'No accessible exercise iframe detected'
            _add_check(
                report, 'page.surface', 'warn', 'Exam surface is not ready',
                detail=detail,
                remediation='Enter a question page and run the check again.')

        bridge = snapshot.get('bridge') or {}
        available = [
            name for name in (
                'returnChoose', 'returnBlank', 'setPCChoose2', 'iframeNext')
            if bridge.get(name)
        ]
        if available:
            _add_check(
                report, 'bridge.native', 'pass', 'Exercise bridge functions detected',
                detail=', '.join(available))
        else:
            _add_check(
                report, 'bridge.native', 'warn', 'Exercise bridge is not ready',
                remediation='Native functions may appear only after a homework question loads.')
    else:
        pk = snapshot.get('pk') or {}
        option_count = int(pk.get('optionCount') or 0)
        if pk.get('title') and option_count >= 2:
            _add_check(
                report, 'page.surface', 'pass', 'Word PK surface detected',
                detail='options=%d' % option_count)
        else:
            _add_check(
                report, 'page.surface', 'warn', 'Word PK surface is not ready',
                detail='title=%s, options=%d' % (bool(pk.get('title')), option_count),
                remediation='Enter an active Word PK question and run the check again.')
        _add_check(
            report, 'bridge.native', 'skip', 'Exercise bridge is not used in PK mode')

    _probe_data_root(report, snapshot, appdata)


def collect_compatibility_report(port=10086, mode='exam', timeout=5,
                                 opener=None, ws_factory=None, appdata=None):
    """Inspect ETS/CDP compatibility without changing page or cache state.

    Expected environment failures are represented as blocking checks instead
    of exceptions. DOM and bridge readiness are warnings because ETS may still
    be on its portal or loading an exercise.
    """
    report = _new_report(port, mode)
    if mode not in SUPPORTED_MODES or not isinstance(port, int) or not 1 <= port <= 65535:
        _add_check(
            report, 'input.parameters', 'fail', 'Invalid check parameters',
            blocking=True, detail='mode=%r, port=%r' % (mode, port),
            remediation='Use mode exam|pk and a port from 1 to 65535.')
        return _finish(report)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = 0
    if timeout <= 0:
        _add_check(
            report, 'input.parameters', 'fail', 'Invalid check timeout',
            blocking=True, detail=repr(timeout),
            remediation='Use a positive timeout in seconds.')
        return _finish(report)
    _add_check(report, 'input.parameters', 'pass', 'Parameters accepted')

    opener = opener or urllib.request.urlopen
    ws_factory = ws_factory or websocket.create_connection
    endpoint = 'http://127.0.0.1:%d/json' % port
    try:
        tabs = json.loads(_read_response(opener, endpoint, timeout))
        if not isinstance(tabs, list):
            raise ValueError('CDP /json response is not a list')
        _add_check(
            report, 'cdp.endpoint', 'pass', 'CDP endpoint responded',
            detail='%d target(s)' % len(tabs))
    except Exception as error:
        _add_check(
            report, 'cdp.endpoint', 'fail', 'CDP endpoint is unavailable',
            blocking=True, detail=str(error),
            remediation='Start the ETS desktop client and confirm CDP port %d is open.' % port)
        return _finish(report)

    ets_tabs = []
    for candidate in tabs:
        normalized = _normalize_ets_tab(candidate)
        if normalized is not None:
            ets_tabs.append(normalized)
    base = ETSBase(port=port)
    try:
        tab = base._pick_ets_tab(ets_tabs)
    except Exception as error:
        _add_check(
            report, 'cdp.target', 'fail', 'ETS target metadata is invalid',
            blocking=True, detail=type(error).__name__,
            remediation='Restart ETS so its CDP target list is rebuilt.')
        return _finish(report)
    if tab is None:
        _add_check(
            report, 'cdp.target', 'fail', 'No ETS browser target found',
            blocking=True,
            remediation='Open an ETS page in the desktop client, then retry.')
        return _finish(report)
    report['observations']['tab_route'] = _sanitized_route(tab.get('url'))
    _add_check(
        report, 'cdp.target', 'pass', 'ETS browser target selected',
        detail=report['observations']['tab_route'])

    ws_url = str(tab.get('webSocketDebuggerUrl') or '').strip()
    report['observations']['ws_endpoint'] = _sanitized_ws_endpoint(ws_url)
    if not ws_url:
        _add_check(
            report, 'cdp.ws_loopback', 'fail', 'ETS target is not attachable',
            blocking=True, detail='webSocketDebuggerUrl is missing',
            remediation='Restart ETS and make sure the selected target is a page.')
        return _finish(report)
    if not is_loopback_ws_url(ws_url):
        _add_check(
            report, 'cdp.ws_loopback', 'fail', 'Remote CDP WebSocket refused',
            blocking=True, detail=report['observations']['ws_endpoint'],
            remediation='Only loopback CDP endpoints are allowed.')
        return _finish(report)
    _add_check(
        report, 'cdp.ws_loopback', 'pass', 'CDP WebSocket is loopback-only',
        detail=report['observations']['ws_endpoint'])

    try:
        base.ws = ws_factory(ws_url, timeout=timeout)
        base.tab = tab
        base._EVAL_JS_TIMEOUT = timeout
        if base.ws is None:
            raise ConnectionError('WebSocket factory returned no connection')
        _add_check(report, 'cdp.attach', 'pass', 'CDP WebSocket attached')
        try:
            raw = base.eval_js(_SNAPSHOT_JS)
            if not raw:
                raise ValueError('Runtime.evaluate returned no value')
            snapshot = json.loads(raw)
            if not isinstance(snapshot, dict):
                raise ValueError('Runtime.evaluate result is not an object')
            if snapshot.get('error'):
                raise ValueError(str(snapshot['error']))
            _add_check(report, 'page.runtime', 'pass', 'Read-only page snapshot collected')
            _add_snapshot_checks(report, snapshot, mode, appdata)
        except Exception as error:
            _add_check(
                report, 'page.runtime', 'fail', 'Page runtime inspection failed',
                blocking=True, detail=str(error),
                remediation='Reload the ETS page or restart the desktop client.')
    except Exception as error:
        _add_check(
            report, 'cdp.attach', 'fail', 'CDP WebSocket attach failed',
            blocking=True, detail='%s at %s' % (
                type(error).__name__, report['observations']['ws_endpoint']),
            remediation='Restart ETS and check whether another tool owns the debugger socket.')
    finally:
        base._drop_connection()

    return _finish(report)


def format_compatibility_report(report):
    """Render a stable, terminal-friendly compatibility report."""
    state = 'PASS' if report.get('ok') else 'BLOCKED'
    lines = [
        'ETS compatibility preflight: %s (mode=%s, port=%s)' % (
            state, report.get('mode'), report.get('port')),
    ]
    labels = {'pass': 'PASS', 'warn': 'WARN', 'fail': 'FAIL', 'skip': 'SKIP'}
    for check in report.get('checks') or []:
        label = labels.get(check.get('status'), 'INFO')
        line = '[%s] %s: %s' % (
            label, check.get('id', '?'), check.get('summary', ''))
        if check.get('detail'):
            line += ' - ' + str(check['detail'])
        lines.append(line)
        if check.get('remediation') and check.get('status') in ('warn', 'fail'):
            lines.append('       Next: ' + str(check['remediation']))
    lines.append('Summary: ' + str(report.get('summary') or ''))
    return '\n'.join(lines)
