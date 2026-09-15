# Copyright 2021 Ruffin White
# Licensed under the Apache License, Version 2.0

# from colcon_core.package_augmentation import logger
import os

from colcon_core.package_augmentation import PackageAugmentationExtensionPoint
from colcon_core.package_augmentation import update_descriptor
from colcon_core.plugin_system import satisfies_version
from git import InvalidGitRepositoryError, Repo

VCS_TYPE = 'git'


class GitPackageAugmentation(PackageAugmentationExtensionPoint):
    """Augment packages using git version control system."""

    # the priority needs to be lower than the default extensions
    # augmenting packages using no version control system
    PRIORITY = 90

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            PackageAugmentationExtensionPoint.EXTENSION_POINT_VERSION,
            '^1.0')

    def augment_package(  # noqa: D102
        self, desc, *, additional_argument_names=None
    ):
        lock_type = os.environ.get('COLCON_CACHE_LOCK_TYPE')
        if lock_type not in (None, 'dirhash', 'git'):
            raise RuntimeError(
                "unsupported COLCON_CACHE_LOCK_TYPE '{}'".format(lock_type))
        if lock_type == 'dirhash':
            return

        # deliberately ignore the package type
        # since this extension can contribute meta information to any package
        try:
            repo = Repo(desc.path, search_parent_directories=True)
        except InvalidGitRepositoryError:
            repo = None
        if repo:
            data = {'vcs_type': VCS_TYPE}
            update_descriptor(desc, data, additional_argument_names=['*'])
