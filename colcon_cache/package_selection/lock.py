# Copyright 2026 colcon-cache contributors
# Licensed under the Apache License, Version 2.0

"""Refresh package locks before package selection."""

import os
import shutil
import subprocess

from colcon_core.logging import colcon_logger
from colcon_core.package_selection import PackageSelectionExtensionPoint
from colcon_core.plugin_system import satisfies_version


logger = colcon_logger.getChild(__name__)


class LockPackageSelection(PackageSelectionExtensionPoint):
    """Refresh package locks before selecting valid build artifacts."""

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            PackageSelectionExtensionPoint.EXTENSION_POINT_VERSION, '^1.0')

    def add_arguments(self, *, parser):  # noqa: D102
        parser.add_argument(
            '--cache-lock', action='store_true',
            help='Refresh package cache locks before building')

    def select_packages(self, args, decorators):  # noqa: D102
        if not args.cache_lock:
            return
        if args.verb_name != 'build':
            raise RuntimeError("'--cache-lock' currently supports build only")

        executable = shutil.which('colcon')
        if not executable:
            raise RuntimeError("could not find the 'colcon' executable")

        command = [
            executable,
            'cache',
            'lock',
            '--build-base',
            str(args.build_base),
        ]
        base_paths = getattr(args, 'base_paths', None)
        if base_paths:
            command.append('--base-paths')
            command.extend(str(path) for path in base_paths)

        result = subprocess.run(
            command,
            cwd=os.getcwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True)
        if result.returncode:
            raise RuntimeError(
                'failed to refresh package cache locks:\n{}'.format(
                    result.stdout.rstrip()))
        logger.info('Refreshed package cache locks')
