# Copyright 2026 colcon-cache contributors
# Licensed under the Apache License, Version 2.0

import pytest

from colcon_cache.artifact import ArtifactCache
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


def test_store_rejects_unexpected_output_paths(tmp_path):
    build_base = tmp_path / 'workspace' / 'build'
    install_base = tmp_path / 'workspace' / 'install'
    cache = ArtifactCache(
        tmp_path / 'artifacts', 'context', build_base, install_base)

    with pytest.raises(RuntimeError, match='unexpected output paths'):
        cache.store(
            'example', tmp_path / 'other-build',
            tmp_path / 'other-install', make_lockfile())
