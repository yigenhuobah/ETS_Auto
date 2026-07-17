#!/usr/bin/env python3
"""Validate release metadata and the Git tag before building artifacts."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import tomllib


SEMVER_RE = re.compile(
    r'^[0-9]+\.[0-9]+\.[0-9]+'
    r'(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')


class ReleaseGuardError(ValueError):
    """Release metadata is missing, malformed, or inconsistent."""


def _read_app_version(root: str) -> str:
    path = os.path.join(root, 'src', 'auto', 'ets_common.py')
    try:
        with open(path, encoding='utf-8') as handle:
            tree = ast.parse(handle.read(), filename=path)
    except (OSError, SyntaxError) as exc:
        raise ReleaseGuardError('cannot read APP_VERSION: %s' % exc) from exc
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == 'APP_VERSION'
                   for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value.strip()
        raise ReleaseGuardError('APP_VERSION must be a string literal')
    raise ReleaseGuardError('APP_VERSION not found in src/auto/ets_common.py')


def _read_info_version(root: str) -> str:
    path = os.path.join(root, 'info.json')
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseGuardError('cannot read info.json: %s' % exc) from exc
    if not isinstance(data, dict) or not isinstance(data.get('version'), str):
        raise ReleaseGuardError('info.json.version must be a string')
    return data['version'].strip()


def _read_project_version(root: str) -> str | None:
    path = os.path.join(root, 'pyproject.toml')
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'rb') as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseGuardError('cannot read pyproject.toml: %s' % exc) from exc
    version = data.get('project', {}).get('version')
    if not isinstance(version, str):
        raise ReleaseGuardError('pyproject.toml project.version must be a string')
    return version.strip()


def _environment_tag(environ=None) -> str | None:
    env = os.environ if environ is None else environ
    if env.get('GITHUB_REF_TYPE') == 'tag':
        return (env.get('GITHUB_REF_NAME') or '').strip() or None
    ref = (env.get('GITHUB_REF') or '').strip()
    prefix = 'refs/tags/'
    return ref[len(prefix):] if ref.startswith(prefix) else None


def validate_release(root: str, tag: str | None = None) -> tuple[str, dict[str, str]]:
    root = os.path.abspath(root)
    dictionary_path = os.path.join(root, 'ecdict_pk.json')
    if not os.path.isfile(dictionary_path) or os.path.getsize(dictionary_path) == 0:
        raise ReleaseGuardError(
            'ecdict_pk.json must exist and be non-empty for release validation')

    app_version = _read_app_version(root)
    versions = {
        'APP_VERSION': app_version,
        'info.json': _read_info_version(root),
    }
    project_version = _read_project_version(root)
    if project_version is not None:
        versions['pyproject.toml'] = project_version

    if not SEMVER_RE.fullmatch(app_version):
        raise ReleaseGuardError('APP_VERSION is not semantic versioning: %r' % app_version)
    mismatched = {
        source: version for source, version in versions.items()
        if version != app_version
    }
    if mismatched:
        details = ', '.join('%s=%r' % item for item in mismatched.items())
        raise ReleaseGuardError(
            'release versions disagree with APP_VERSION=%r: %s' % (
                app_version, details))

    if tag is not None:
        expected = 'v' + app_version
        if tag != expected:
            raise ReleaseGuardError(
                'release tag must be exactly %s, got %s' % (expected, tag))
    return app_version, versions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Validate ETS release metadata')
    parser.add_argument(
        '--root', default=os.path.dirname(os.path.abspath(__file__)),
        help='Repository root (default: directory containing this script)')
    parser.add_argument(
        '--tag', default=None,
        help='Tag to require; GitHub tag environment is used when omitted')
    args = parser.parse_args(argv)
    tag = args.tag if args.tag is not None else _environment_tag()
    try:
        version, sources = validate_release(args.root, tag=tag)
    except ReleaseGuardError as exc:
        print('RELEASE GUARD FAIL: %s' % exc, file=sys.stderr)
        return 1

    detail = ', '.join('%s=%s' % item for item in sources.items())
    if tag is not None:
        detail += ', tag=%s' % tag
    print('RELEASE GUARD OK: %s (%s)' % (version, detail))
    return 0


if __name__ == '__main__':
    sys.exit(main())
