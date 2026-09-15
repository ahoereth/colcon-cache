# Copyright 2026 colcon-cache contributors
# Licensed under the Apache License, Version 2.0

import os

from colcon_cache.artifact import parse_duration
from colcon_cache.artifact import parse_size
from colcon_cache.artifact import prune_artifacts
from colcon_cache.subverb import CacheSubverbExtensionPoint
from colcon_core.plugin_system import satisfies_version


class PruneCacheSubverb(CacheSubverbExtensionPoint):
    """Prune package artifacts by age and total size."""

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            CacheSubverbExtensionPoint.EXTENSION_POINT_VERSION, '^1.0')

    def add_arguments(self, *, parser):  # noqa: D102
        parser.add_argument(
            '--cache-artifacts',
            default=os.environ.get('COLCON_CACHE_ARTIFACTS'),
            help='Package artifact directory')
        parser.add_argument(
            '--max-age', type=parse_duration,
            default=os.environ.get('COLCON_CACHE_ARTIFACT_MAX_AGE'),
            help='Prune artifacts unused for this duration (e.g. 30d)')
        parser.add_argument(
            '--max-size', type=parse_size,
            default=os.environ.get('COLCON_CACHE_ARTIFACT_MAX_SIZE'),
            help='Prune least-recently-used artifacts above this size '
                 '(e.g. 20GiB)')

    def main(self, *, context):  # noqa: D102
        args = context.args
        if not args.cache_artifacts:
            raise RuntimeError("'--cache-artifacts' is required")
        if args.max_age is None and args.max_size is None:
            raise RuntimeError("'--max-age' or '--max-size' is required")
        prune_artifacts(
            args.cache_artifacts, max_age=args.max_age,
            max_size=args.max_size)
