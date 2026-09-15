# Copyright 2026 colcon-cache contributors
# Licensed under the Apache License, Version 2.0

"""Store, restore, and prune package build artifacts."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from colcon_cache.event_handler import get_previous_lockfile
from colcon_core.logging import colcon_logger


logger = colcon_logger.getChild(__name__)
ARTIFACT_SCHEMA = 1
MANIFEST_FILENAME = 'manifest.json'
_ACCESS_DIRECTORY = '.access'
_SIZE_DIRECTORY = '.size'
_LOCK_FILENAME = '.lock'
_KEY_PATTERN = re.compile(r'^[0-9a-f]{64}$')
_DURATION_UNITS = {
    's': 1,
    'm': 60,
    'h': 60 * 60,
    'd': 24 * 60 * 60,
    'w': 7 * 24 * 60 * 60,
}
_SIZE_UNITS = {
    'B': 1,
    'KiB': 1024,
    'MiB': 1024 ** 2,
    'GiB': 1024 ** 3,
    'TiB': 1024 ** 4,
}


def parse_duration(value):
    """Parse a non-negative duration such as ``30d`` into seconds."""
    match = re.match(r'^(\d+)([smhdw])$', value)
    if not match:
        raise ValueError("invalid duration '{}'; expected e.g. 30d".format(
            value))
    return int(match.group(1)) * _DURATION_UNITS[match.group(2)]


def parse_size(value):
    """Parse a non-negative binary size such as ``20GiB`` into bytes."""
    match = re.match(r'^(\d+)(B|KiB|MiB|GiB|TiB)$', value)
    if not match:
        raise ValueError("invalid size '{}'; expected e.g. 20GiB".format(
            value))
    return int(match.group(1)) * _SIZE_UNITS[match.group(2)]


class ArtifactCache:
    """Content-addressed cache for isolated package prefixes."""

    def __init__(self, root, context, build_base, install_base,
                 max_age=None, max_size=None):
        self.root = Path(root).absolute()
        self.context = context
        self.build_base = Path(build_base).absolute()
        self.install_base = Path(install_base).absolute()
        self.max_age = max_age
        self.max_size = max_size

    @classmethod
    def from_args(cls, args):
        """Create a cache from colcon arguments, if configured."""
        root = getattr(args, 'cache_artifacts', None)
        context = getattr(args, 'cache_context', None)
        context_file = getattr(args, 'cache_context_file', None)
        if context and context_file:
            raise RuntimeError(
                "'--cache-context' and '--cache-context-file' are mutually "
                'exclusive')
        if context_file:
            path = Path(context_file)
            try:
                context = path.read_text().strip()
            except OSError as error:
                raise RuntimeError(
                    "could not read cache context file '{}': {}".format(
                        path, error))
            if not context:
                raise RuntimeError(
                    "cache context file '{}' is empty".format(path))
        if not root and not context:
            return None
        if not root or not context:
            raise RuntimeError(
                "'--cache-artifacts' requires a cache context")
        if getattr(args, 'verb_name', None) != 'build':
            raise RuntimeError('artifact caching currently supports build only')
        if getattr(args, 'merge_install', False):
            raise RuntimeError('artifact caching requires an isolated install')
        return cls(
            root, context, args.build_base, args.install_base,
            max_age=getattr(args, 'cache_artifacts_max_age', None),
            max_size=getattr(args, 'cache_artifacts_max_size', None))

    def prune(self):
        """Best-effort prune expired and least-recently-used artifacts."""
        return prune_artifacts(self.root, self.max_age, self.max_size)

    def restore(self, package_name, lockfile):
        """Restore one exact package artifact, returning whether it existed."""
        manifest = self._manifest(package_name, lockfile)
        artifact = self._artifact_path(manifest)
        with _cache_lock(self.root, exclusive=False):
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
                _record_usage(artifact)
                logger.info(
                    "Restored artifact for package '%s'", package_name)
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
        with _cache_lock(self.root, exclusive=False):
            if artifact.exists():
                self._validate_manifest(artifact, manifest)
                _record_usage(artifact)
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
                artifact_size = _tree_size(temp)
                try:
                    os.rename(str(temp), str(artifact))
                except OSError:
                    if not artifact.exists():
                        raise
                    artifact_size = _tree_size(artifact)
                self._validate_manifest(artifact, manifest)
                _record_usage(artifact, size=artifact_size)
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


def prune_artifacts(root, max_age=None, max_size=None):
    """Prune one artifact store without waiting for active cache operations."""
    if max_age is None and max_size is None:
        return 0, 0
    if fcntl is None:  # pragma: no cover
        logger.warning('Artifact pruning requires POSIX file locking')
        return 0, 0

    root = Path(root).absolute()
    version_root = root / 'v{}'.format(ARTIFACT_SCHEMA)
    trash = None
    removed_count = 0
    removed_size = 0
    with _cache_lock(
            root, exclusive=True, blocking=False) as lock_acquired:
        if not lock_acquired:
            logger.info('Skipping artifact pruning; cache is in use')
            return 0, 0
        if not version_root.is_dir():
            return 0, 0

        now = time.time()
        entries = []
        for artifact in version_root.iterdir():
            if not artifact.is_dir() or not _KEY_PATTERN.match(artifact.name):
                continue
            access = _metadata_path(artifact, _ACCESS_DIRECTORY)
            last_used = (
                access.stat().st_mtime if access.exists()
                else artifact.stat().st_mtime)
            size = _artifact_size(artifact)
            entries.append((last_used, size, artifact))

        selected = set()
        if max_age is not None:
            selected.update(
                artifact for last_used, _, artifact in entries
                if now - last_used > max_age)

        retained_size = sum(
            size for _, size, artifact in entries if artifact not in selected)
        if max_size is not None and retained_size > max_size:
            for _, size, artifact in sorted(
                    entries, key=lambda entry: (entry[0], entry[2].name)):
                if artifact in selected:
                    continue
                selected.add(artifact)
                retained_size -= size
                if retained_size <= max_size:
                    break

        if selected:
            trash = Path(tempfile.mkdtemp(
                prefix='.trash-', dir=str(version_root)))
        sizes = {artifact: size for _, size, artifact in entries}
        for artifact in selected:
            try:
                os.rename(str(artifact), str(trash / artifact.name))
            except FileNotFoundError:  # pragma: no cover
                continue
            _remove_metadata(artifact)
            removed_count += 1
            removed_size += sizes[artifact]

    if trash is not None:
        shutil.rmtree(str(trash))
    if removed_count:
        logger.info(
            'Pruned %d package artifacts (%d bytes)',
            removed_count, removed_size)
    return removed_count, removed_size


@contextmanager
def _cache_lock(root, exclusive, blocking=True):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if fcntl is None:  # pragma: no cover
        yield True
        return

    with (root / _LOCK_FILENAME).open('a') as stream:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(stream.fileno(), operation)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _metadata_path(artifact, directory):
    return artifact.parent / directory / artifact.name


def _record_usage(artifact, size=None):
    access = _metadata_path(artifact, _ACCESS_DIRECTORY)
    access.parent.mkdir(parents=True, exist_ok=True)
    access.touch()
    size_path = _metadata_path(artifact, _SIZE_DIRECTORY)
    if size is None and not size_path.exists():
        size = _tree_size(artifact)
    if size is not None:
        _write_text_atomic(size_path, str(size))


def _artifact_size(artifact):
    path = _metadata_path(artifact, _SIZE_DIRECTORY)
    try:
        return int(path.read_text())
    except (OSError, ValueError):
        size = _tree_size(artifact)
        _write_text_atomic(path, str(size))
        return size


def _remove_metadata(artifact):
    for directory in (_ACCESS_DIRECTORY, _SIZE_DIRECTORY):
        path = _metadata_path(artifact, directory)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _write_text_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix='.{}.'.format(path.name), dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(value)
        os.replace(temporary, str(path))
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _tree_size(path):
    total = 0
    for root, directories, filenames in os.walk(str(path)):
        for name in directories + filenames:
            stat = os.lstat(os.path.join(root, name))
            total += getattr(stat, 'st_blocks', 0) * 512 or stat.st_size
    return total


def get_package_lockfile(package_build_base):
    """Return the source/dependency lock used as the artifact input."""
    return get_previous_lockfile(package_build_base, 'cache')
