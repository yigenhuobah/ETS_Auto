#!/usr/bin/env python3
"""Execute frozen ETS binaries with deterministic offline smoke arguments."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile


SMOKE_CASES = (
    ('ets_gui.exe', '--verify-version={version}', None),
    ('ets_gui.exe', '--version', None),
    ('ets_gui.exe', '--help', None),
    ('ets_gui.exe', '--self-test', None),
    ('ets_auto.exe', '--version', '{version}'),
    ('ets_auto.exe', '--help', '--self-test'),
    ('ets_auto.exe', '--self-test', 'SELF-TEST OK'),
    ('ets_pk.exe', '--version', '{version}'),
    ('ets_pk.exe', '--help', '--self-test'),
    ('ets_pk.exe', '--self-test', 'SELF-TEST OK'),
)


def _read_expected_version(project_root):
    info_path = os.path.join(project_root, 'info.json')
    with open(info_path, encoding='utf-8') as handle:
        info = json.load(handle)
    version = info.get('version')
    if not isinstance(version, str) or not version.strip():
        raise ValueError('info.json has no valid version')
    return version.strip()


def _terminate_process_tree(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        try:
            result = subprocess.run(
                ['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
            if result.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.kill()


def _wait_after_termination(process):
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired as exc:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired as final_exc:
            raise RuntimeError(
                'process did not terminate after forced cleanup') from final_exc
        raise RuntimeError(
            'process tree cleanup required a direct kill fallback') from exc


def _read_output(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            return handle.read()
    except OSError as exc:
        return '[unable to read captured output: %s]' % exc


def _format_output(stdout, stderr):
    parts = []
    if stdout and stdout.strip():
        parts.append('stdout:\n' + stdout.strip())
    if stderr and stderr.strip():
        parts.append('stderr:\n' + stderr.strip())
    return '\n'.join(parts) or '(no console output)'


def _run_case(executable, argument, expected_text, timeout):
    creationflags = 0
    if os.name == 'nt':
        creationflags = subprocess.CREATE_NO_WINDOW

    with tempfile.TemporaryDirectory(prefix='ets-packaged-smoke-') as temp_dir:
        stdout_path = os.path.join(temp_dir, 'stdout.txt')
        stderr_path = os.path.join(temp_dir, 'stderr.txt')
        timed_out = False
        with open(stdout_path, 'wb') as stdout_file, \
                open(stderr_path, 'wb') as stderr_file:
            process = subprocess.Popen(
                [executable, argument],
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=creationflags,
            )
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_tree(process)
                _wait_after_termination(process)

        stdout = _read_output(stdout_path)
        stderr = _read_output(stderr_path)
        output = _format_output(stdout, stderr)
        if timed_out:
            raise RuntimeError(
                '%s %s timed out after %ss\n%s' % (
                    os.path.basename(executable), argument, timeout, output))
        if process.returncode != 0:
            raise RuntimeError(
                '%s %s exited %s\n%s' % (
                    os.path.basename(executable), argument,
                    process.returncode, output))
        combined = (stdout or '') + '\n' + (stderr or '')
        if expected_text and expected_text not in combined:
            raise RuntimeError(
                '%s %s did not emit %r\n%s' % (
                    os.path.basename(executable), argument,
                    expected_text, output))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Smoke-test packaged ETS executables without connecting to ETS')
    parser.add_argument('--dist', default='dist', help='Directory containing built EXEs')
    parser.add_argument('--timeout', type=float, default=60.0,
                        help='Per-process timeout in seconds')
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error('--timeout must be greater than zero')

    project_root = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.abspath(args.dist)
    version = _read_expected_version(project_root)
    print('Expected version: %s' % version)

    for executable_name, argument_template, expected_template in SMOKE_CASES:
        executable = os.path.join(dist_dir, executable_name)
        if not os.path.isfile(executable):
            raise FileNotFoundError('built executable not found: %s' % executable)
        argument = argument_template.format(version=version)
        expected_text = None
        if expected_template:
            expected_text = expected_template.format(version=version)
        print('[RUN] %s %s' % (executable_name, argument))
        _run_case(executable, argument, expected_text, args.timeout)
        print('[PASS] %s %s' % (executable_name, argument))

    print('PACKAGED SMOKE OK: %d command(s)' % len(SMOKE_CASES))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('PACKAGED SMOKE FAIL: %s' % exc, file=sys.stderr)
        sys.exit(1)
