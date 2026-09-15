# Copyright 2021 Ruffin White
# Licensed under the Apache License, Version 2.0

from argparse import ArgumentParser

from colcon_cache.package_selection.valid import ValidPackageSelection
# from colcon_core.package_selection import logger


class Object(object):
    pass


def test_valid():
    valid_package_selection = ValidPackageSelection()

    decorator = Object()
    decorator.selected = False
    decorators = [decorator]

    # TODO: check event log for warning by mocking logger
    args = Object()
    args.packages_select_cache_invalid = True
    args.packages_skip_cache_valid = False
    valid_package_selection.select_packages(args, decorators)

    args = Object()
    args.packages_select_cache_invalid = False
    args.packages_skip_cache_valid = True
    valid_package_selection.select_packages(args, decorators)

    args.build_base = 'foo'
    args.packages_select_cache_key = 'cache'
    valid_package_selection.select_packages(args, decorators)

    args.packages_select_cache_key = 'foo'
    valid_package_selection.select_packages(args, decorators)


def test_artifact_defaults_from_environment(monkeypatch):
    monkeypatch.delenv('COLCON_CACHE_CONTEXT_FILE', raising=False)
    monkeypatch.setenv('COLCON_CACHE_ARTIFACTS', '/tmp/artifacts')
    monkeypatch.setenv('COLCON_CACHE_CONTEXT', 'target-v1')
    monkeypatch.setenv('COLCON_CACHE_ARTIFACT_MAX_AGE', '30d')
    monkeypatch.setenv('COLCON_CACHE_ARTIFACT_MAX_SIZE', '20GiB')
    parser = ArgumentParser()
    ValidPackageSelection().add_arguments(parser=parser)

    args = parser.parse_args([])

    assert args.cache_artifacts == '/tmp/artifacts'
    assert args.cache_context == 'target-v1'
    assert args.cache_context_file is None
    assert args.cache_artifacts_max_age == 30 * 24 * 60 * 60
    assert args.cache_artifacts_max_size == 20 * 1024 ** 3
