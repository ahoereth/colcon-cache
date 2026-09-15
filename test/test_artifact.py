# Copyright 2026 colcon-cache contributors
# Licensed under the Apache License, Version 2.0

import fcntl
import os
from types import SimpleNamespace

import pytest

from colcon_cache.artifact import ArtifactCache
from colcon_cache.artifact import parse_duration
from colcon_cache.artifact import parse_size
from colcon_cache.cache import CacheChecksums
from colcon_cache.cache import CacheLockfile


def make_lockfile(checksum='0123456789abcdef'):
    return CacheLockfile(
        lock_type='dirhash', checksums=CacheChecksums(current=checksum))


def test_store_and_restore_package_artifact(tmp_path):
    build_base = tmp_path / 'workspace' / 'build'
    install_base = tmp_path / 'workspace' / 'install'
    package_build = build_base / 'example'
    package_install = install_base / 'example'
    package_build.mkdir(parents=True)
    package_install.mkdir(parents=True)
    (package_build / 'object.o').write_text('object')
    (package_install / 'library.so').write_text('library')
    (package_install / 'library-link.so').symlink_to('library.so')

    cache = ArtifactCache(
        tmp_path / 'artifacts', 'gpu-amd64-release',
        build_base, install_base)
    lockfile = make_lockfile()
    cache.store('example', package_build, package_install, lockfile)

    (package_build / 'object.o').write_text('changed')
    (package_install / 'library.so').unlink()

    assert cache.restore('example', lockfile)
    assert (package_build / 'object.o').read_text() == 'object'
    assert (package_install / 'library.so').read_text() == 'library'
    assert (package_install / 'library-link.so').is_symlink()


def test_different_context_is_a_cache_miss(tmp_path):
    build_base = tmp_path / 'workspace' / 'build'
    install_base = tmp_path / 'workspace' / 'install'
    package_build = build_base / 'example'
    package_install = install_base / 'example'
    package_build.mkdir(parents=True)
    package_install.mkdir(parents=True)

    lockfile = make_lockfile()
    ArtifactCache(
        tmp_path / 'artifacts', 'debug', build_base, install_base,
    ).store('example', package_build, package_install, lockfile)

    assert not ArtifactCache(
        tmp_path / 'artifacts', 'release', build_base, install_base,
    ).restore('example', lockfile)


def test_context_file(tmp_path):
    context_file = tmp_path / 'context'
    context_file.write_text('build-context\n')
    args = SimpleNamespace(
        cache_artifacts=str(tmp_path / 'artifacts'),
        cache_context=None,
        cache_context_file=str(context_file),
        verb_name='build',
        merge_install=False,
        build_base='build',
        install_base='install')

    cache = ArtifactCache.from_args(args)

    assert cache.context == 'build-context'


def test_restore_records_artifact_use(tmp_path):
    build_base = tmp_path / 'workspace' / 'build'
    install_base = tmp_path / 'workspace' / 'install'
    package_build = build_base / 'example'
    package_install = install_base / 'example'
    package_build.mkdir(parents=True)
    package_install.mkdir(parents=True)
    cache = ArtifactCache(
        tmp_path / 'artifacts', 'context', build_base, install_base)
    lockfile = make_lockfile()
    cache.store('example', package_build, package_install, lockfile)
    artifact = cache._artifact_path(cache._manifest('example', lockfile))
    access = artifact.parent / '.access' / artifact.name
    os.utime(str(access), (1, 1))

    assert cache.restore('example', lockfile)

    assert access.stat().st_mtime > 1


def test_prune_expired_artifact(tmp_path):
    build_base = tmp_path / 'workspace' / 'build'
    install_base = tmp_path / 'workspace' / 'install'
    package_build = build_base / 'example'
    package_install = install_base / 'example'
    package_build.mkdir(parents=True)
    package_install.mkdir(parents=True)
    cache = ArtifactCache(
        tmp_path / 'artifacts', 'context', build_base, install_base,
        max_age=60)
    old_lockfile = make_lockfile('old')
    current_lockfile = make_lockfile('current')
    cache.store('example', package_build, package_install, old_lockfile)
    cache.store('example', package_build, package_install, current_lockfile)
    old_artifact = cache._artifact_path(
        cache._manifest('example', old_lockfile))
    current_artifact = cache._artifact_path(
        cache._manifest('example', current_lockfile))
    old_access = old_artifact.parent / '.access' / old_artifact.name
    os.utime(str(old_access), (1, 1))

    removed_count, removed_size = cache.prune()

    assert removed_count == 1
    assert removed_size > 0
    assert not old_artifact.exists()
    assert current_artifact.exists()
    assert not old_access.exists()


def test_prune_least_recently_used_to_size(tmp_path):
    build_base = tmp_path / 'workspace' / 'build'
    install_base = tmp_path / 'workspace' / 'install'
    package_build = build_base / 'example'
    package_install = install_base / 'example'
    package_build.mkdir(parents=True)
    package_install.mkdir(parents=True)
    (package_build / 'output').write_bytes(b'x' * 4096)
    cache = ArtifactCache(
        tmp_path / 'artifacts', 'context', build_base, install_base)
    old_lockfile = make_lockfile('old')
    current_lockfile = make_lockfile('current')
    cache.store('example', package_build, package_install, old_lockfile)
    cache.store('example', package_build, package_install, current_lockfile)
    old_artifact = cache._artifact_path(
        cache._manifest('example', old_lockfile))
    current_artifact = cache._artifact_path(
        cache._manifest('example', current_lockfile))
    old_access = old_artifact.parent / '.access' / old_artifact.name
    os.utime(str(old_access), (1, 1))
    current_size_path = (
        current_artifact.parent / '.size' / current_artifact.name)
    cache.max_size = int(current_size_path.read_text())

    removed_count, _ = cache.prune()

    assert removed_count == 1
    assert not old_artifact.exists()
    assert current_artifact.exists()


def test_prune_skips_cache_in_use(tmp_path):
    root = tmp_path / 'artifacts'
    root.mkdir()
    cache = ArtifactCache(
        root, 'context', tmp_path / 'build', tmp_path / 'install',
        max_age=0)
    with (root / '.lock').open('a') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
        try:
            assert cache.prune() == (0, 0)
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def test_parse_prune_limits():
    assert parse_duration('30d') == 30 * 24 * 60 * 60
    assert parse_size('20GiB') == 20 * 1024 ** 3
    with pytest.raises(ValueError, match='invalid duration'):
        parse_duration('30')
    with pytest.raises(ValueError, match='invalid size'):
        parse_size('20GB')


def test_store_rejects_unexpected_output_paths(tmp_path):
    build_base = tmp_path / 'workspace' / 'build'
    install_base = tmp_path / 'workspace' / 'install'
    cache = ArtifactCache(
        tmp_path / 'artifacts', 'context', build_base, install_base)

    with pytest.raises(RuntimeError, match='unexpected output paths'):
        cache.store(
            'example', tmp_path / 'other-build',
            tmp_path / 'other-install', make_lockfile())
