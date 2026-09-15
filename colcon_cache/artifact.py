# Copyright 2026 colcon-cache contributors
# Licensed under the Apache License, Version 2.0

"""Store and restore package build artifacts."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from colcon_cache.event_handler import get_previous_lockfile
from colcon_core.logging import colcon_logger


logger = colcon_logger.getChild(__name__)

ARTIFACT_SCHEMA = 1
MANIFEST_FILENAME = 'manifest.json'


class ArtifactCache:
    """Content-addressed cache for isolated package prefixes."""

    def __init__(self, root, context, build_base, install_base):
        self.root = Path(root).absolute()
        self.context = context
        self.build_base = Path(build_base).absolute()
        self.install_base = Path(install_base).absolute()

    @classmethod
    def from_args(cls, args):
        """Create a cache from colcon arguments, if configured."""
        root = getattr(args, 'cache_artifacts', None)
        context = getattr(args, 'cache_context', None)
        if not root and not context:
            return None
        if not root or not context:
            raise RuntimeError(
                "'--cache-artifacts' and '--cache-context' must be used "
                'together')
        if getattr(args, 'verb_name', None) != 'build':
            raise RuntimeError('artifact caching currently supports build only')
        if getattr(args, 'merge_install', False):
            raise RuntimeError('artifact caching requires an isolated install')
        return cls(root, context, args.build_base, args.install_base)

    def restore(self, package_name, lockfile):
        """Restore one exact package artifact, returning whether it existed."""
        manifest = self._manifest(package_name, lockfile)
        artifact = self._artifact_path(manifest)
        if not artifact.exists():
            return False
        self._validate_manifest(artifact, manifest)

        destinations = self._destinations(package_name)
        temporary = []
        try:
            for name, destination in destinations.items():
                source = artifact / name
                if not source.is_dir():
                    raise RuntimeError(
                        "artifact '{}' has no {} directory".format(
                            artifact, name))
                destination.parent.mkdir(parents=True, exist_ok=True)
                temp = Path(tempfile.mkdtemp(
                    prefix='.{}.artifact-'.format(package_name),
                    dir=str(destination.parent)))
                shutil.rmtree(str(temp))
                shutil.copytree(str(source), str(temp), symlinks=True)
                temporary.append((temp, destination))

            for temp, destination in temporary:
                self._remove(destination)
                os.rename(str(temp), str(destination))
            logger.info("Restored artifact for package '%s'", package_name)
            return True
        finally:
            for temp, _ in temporary:
                self._remove(temp)

    def store(self, package_name, package_build_base, package_install_base,
              lockfile):
        """Atomically store one successfully built package."""
        expected = self._destinations(package_name)
        actual = {
            'build': Path(package_build_base).absolute(),
            'install': Path(package_install_base).absolute(),
        }
        if actual != expected:
            raise RuntimeError(
                "package '{}' used unexpected output paths: {}".format(
                    package_name, actual))
        for name, source in actual.items():
            if not source.is_dir():
                raise RuntimeError(
                    "package '{}' has no {} output at '{}'".format(
                        package_name, name, source))

        manifest = self._manifest(package_name, lockfile)
        artifact = self._artifact_path(manifest)
        if artifact.exists():
            self._validate_manifest(artifact, manifest)
            return

        artifact.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(
            prefix='.{}.'.format(artifact.name), dir=str(artifact.parent)))
        try:
            for name, source in actual.items():
                shutil.copytree(
                    str(source), str(temp / name), symlinks=True)
            with (temp / MANIFEST_FILENAME).open('w') as stream:
                json.dump(manifest, stream, indent=2, sort_keys=True)
                stream.write('\n')
            try:
                os.rename(str(temp), str(artifact))
            except OSError:
                if not artifact.exists():
                    raise
            self._validate_manifest(artifact, manifest)
            logger.info("Stored artifact for package '%s'", package_name)
        finally:
            self._remove(temp)

    def _destinations(self, package_name):
        return {
            'build': self.build_base / package_name,
            'install': self.install_base / package_name,
        }

    def _manifest(self, package_name, lockfile):
        checksum = lockfile.checksums.current
        if not checksum:
            raise RuntimeError(
                "package '{}' has no current cache checksum".format(
                    package_name))
        return {
            'schema': ARTIFACT_SCHEMA,
            'package': package_name,
            'checksum': checksum,
            'context': self.context,
            'build_base': str(self.build_base),
            'install_base': str(self.install_base),
        }

    def _artifact_path(self, manifest):
        serialized = json.dumps(
            manifest, sort_keys=True, separators=(',', ':')).encode()
        key = hashlib.sha256(serialized).hexdigest()
        return self.root / 'v{}'.format(ARTIFACT_SCHEMA) / key

    @staticmethod
    def _validate_manifest(artifact, expected):
        path = artifact / MANIFEST_FILENAME
        try:
            with path.open() as stream:
                actual = json.load(stream)
        except (OSError, ValueError) as error:
            raise RuntimeError(
                "invalid artifact manifest '{}': {}".format(path, error))
        if actual != expected:
            raise RuntimeError(
                "artifact manifest '{}' does not match its key".format(path))

    @staticmethod
    def _remove(path):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(str(path))


def get_package_lockfile(package_build_base):
    """Return the source/dependency lock used as the artifact input."""
    return get_previous_lockfile(package_build_base, 'cache')
