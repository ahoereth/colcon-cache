# Copyright 2026 colcon-cache contributors
# Licensed under the Apache License, Version 2.0

from pathlib import Path
from types import SimpleNamespace

import pytest

from colcon_cache.package_selection.lock import LockPackageSelection


def test_lock_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        'colcon_cache.package_selection.lock.subprocess.run',
        lambda *args, **kwargs: pytest.fail('subprocess should not run'))

    LockPackageSelection().select_packages(
        SimpleNamespace(cache_lock=False), [])


def test_lock_refreshes_workspace(monkeypatch):
    invocation = {}

    monkeypatch.setattr(
        'colcon_cache.package_selection.lock.shutil.which',
        lambda name: '/usr/bin/colcon')

    def run(command, **kwargs):
        invocation['command'] = command
        invocation['kwargs'] = kwargs
        return SimpleNamespace(returncode=0, stdout='')

    monkeypatch.setattr(
        'colcon_cache.package_selection.lock.subprocess.run', run)

    args = SimpleNamespace(
        cache_lock=True,
        verb_name='build',
        build_base=Path('/workspace/build'),
        base_paths=[Path('/workspace/src')])
    LockPackageSelection().select_packages(args, [])

    assert invocation['command'] == [
        '/usr/bin/colcon',
        'cache',
        'lock',
        '--build-base',
        '/workspace/build',
        '--base-paths',
        '/workspace/src',
    ]
    assert invocation['kwargs']['stdout'] is not None


def test_lock_reports_failure(monkeypatch):
    monkeypatch.setattr(
        'colcon_cache.package_selection.lock.shutil.which',
        lambda name: '/usr/bin/colcon')
    monkeypatch.setattr(
        'colcon_cache.package_selection.lock.subprocess.run',
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout='lock failed'))

    args = SimpleNamespace(
        cache_lock=True,
        verb_name='build',
        build_base='build',
        base_paths=None)
    with pytest.raises(SystemExit, match='lock failed'):
        LockPackageSelection().select_packages(args, [])
