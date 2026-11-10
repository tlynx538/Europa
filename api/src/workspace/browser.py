"""Descriptor-relative, read-only browsing of the launch workspace."""
import errno
import hashlib
import heapq
import json
import os
import stat
import struct
from pathlib import Path
from urllib.parse import unquote


class WorkspaceError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def _segments(value: str) -> list[str]:
    if value == "":
        return []
    parts = value.split('/')
    for part in parts:
        if part in ('', '.', '..') or part.startswith('.') or '\\' in part or '\x00' in part:
            raise WorkspaceError(400, 'Invalid workspace path')
        # A second interpretation of escaped input must not become a path.
        decoded = unquote(part)
        if decoded != part and (decoded in ('.', '..') or '/' in decoded or '\\' in decoded or '\x00' in decoded):
            raise WorkspaceError(400, 'Invalid workspace path')
    return parts


def _error(exc: OSError) -> WorkspaceError:
    if exc.errno == errno.ENOENT:
        return WorkspaceError(404, 'Workspace entry not found')
    if exc.errno in (errno.EACCES, errno.EPERM):
        return WorkspaceError(403, 'Workspace entry inaccessible')
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        return WorkspaceError(400, 'Unsafe workspace entry')
    return WorkspaceError(503, 'Workspace storage unavailable')


class WorkspaceBrowser:
    def __init__(self, root: Path):
        # Open every ancestor without following symlinks; never resolve a
        # caller-supplied path or borrow the private NotebookStore's policy.
        path = Path(os.path.abspath(root))
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
        except OSError as exc:
            os.close(fd)
            raise _error(exc) from None
        self._root_fd = fd
        self.private_identities = set()
        self.root_path = path
        self.private_paths = set()

    def protect(self, path):
        relative = os.path.relpath(os.path.abspath(path), self.root_path)
        if relative != '..' and not relative.startswith('../'):
            self.private_paths.add('' if relative == '.' else relative)

    def check_path(self, path):
        if any(not private or path == private or path.startswith(private + '/') for private in self.private_paths):
            raise WorkspaceError(403, 'Private application storage is not browsable')

    def check_private(self, info):
        if (info.st_dev, info.st_ino) in self.private_identities:
            raise WorkspaceError(403, 'Private application storage is not browsable')

    def close(self) -> None:
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def project(self, path: str) -> dict:
        if path.endswith('/.europa-project.json'):
            path = path[:-len('/.europa-project.json')]
        self.check_path(path)
        parts = _segments(path)
        if not parts or not hasattr(self, 'project_store'):
            raise WorkspaceError(404, 'Project not registered')
        root = os.fstat(self._root_fd)
        row = self.project_store.project_binding((root.st_dev, root.st_ino), path)
        if row is None:
            raise WorkspaceError(404, 'Project not registered')
        parent = os.dup(self._root_fd)
        try:
            for part in parts[:-1]:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = fd
            directory = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(directory)
                if (info.st_dev, info.st_ino) != (row['dir_device'], row['dir_inode']):
                    raise WorkspaceError(404, 'Project directory changed')
                descriptor = os.open('.europa-project.json', os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    info = os.fstat(descriptor)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 256 or
                            (info.st_dev, info.st_ino) != (row['descriptor_device'], row['descriptor_inode'])):
                        raise WorkspaceError(404, 'Project descriptor changed')
                    expected = {'format': 'europa-project', 'version': 1, 'id': row['project_id']}
                    try:
                        data = json.loads(os.read(descriptor, 257))
                    except (ValueError, UnicodeError):
                        raise WorkspaceError(404, 'Invalid project descriptor') from None
                    if data != expected:
                        raise WorkspaceError(404, 'Invalid project descriptor')
                finally:
                    os.close(descriptor)
                # A rename between opening and inspection must not claim the new location.
                linked = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                if (linked.st_dev, linked.st_ino) != (row['dir_device'], row['dir_inode']):
                    raise WorkspaceError(404, 'Project directory moved')
                return {'id': row['project_id'], 'path': path, 'type': 'project'}
            finally:
                os.close(directory)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise WorkspaceError(404, 'Project not found') from None
            raise _error(exc) from None
        finally:
            os.close(parent)

    def read_text(self, path: str) -> dict:
        self.check_path(path)
        parts = _segments(path)
        if not parts:
            raise WorkspaceError(400, 'A file path is required')
        fd = os.dup(self._root_fd)
        try:
            self.check_private(os.fstat(fd))
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
                self.check_private(os.fstat(fd))
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                before = os.fstat(file_fd)
                self.check_private(before)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise WorkspaceError(400, 'Unsafe workspace entry')
                max_bytes = 1024 * 1024
                if before.st_size > max_bytes:
                    raise WorkspaceError(413, 'Text file exceeds 1 MiB')
                data = bytearray()
                while len(data) <= max_bytes:
                    chunk = os.read(file_fd, min(65536, max_bytes + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                if len(data) > max_bytes:
                    raise WorkspaceError(413, 'Text file exceeds 1 MiB')
                after = os.fstat(file_fd)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
                ):
                    raise WorkspaceError(409, 'Text file changed while reading')
                if b'\x00' in data:
                    raise WorkspaceError(415, 'Not a UTF-8 text file')
                try:
                    content = data.decode('utf-8')
                except UnicodeDecodeError:
                    raise WorkspaceError(415, 'Not a UTF-8 text file') from None
                revision = hashlib.sha256(
                    struct.pack('!QQQQQ', before.st_dev, before.st_ino, before.st_size,
                                before.st_mtime_ns, before.st_ctime_ns) + data
                ).hexdigest()
                return {'path': path, 'content': content, 'revision': revision}
            finally:
                os.close(file_fd)
        except OSError as exc:
            raise _error(exc) from None
        finally:
            os.close(fd)

    def list(self, path: str = '', cursor: str | None = None, limit: int = 100) -> dict:
        self.check_path(path)
        parts = _segments(path)
        if cursor is not None:
            if len(_segments(cursor)) != 1:
                raise WorkspaceError(400, 'Invalid cursor')
        fd = os.dup(self._root_fd)
        try:
            self.check_private(os.fstat(fd))
            self.check_private(os.fstat(fd))
            for part in parts:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
                self.check_private(os.fstat(fd))
            # Keep only the next page in memory, including one lookahead.
            with os.scandir(fd) as entries:
                names = heapq.nsmallest(limit + 1, (
                    entry.name for entry in entries
                    if not entry.name.startswith('.') and (cursor is None or entry.name > cursor)
                ))
            items = []
            # Skipped unsafe entries do not count towards the page.
            # Collect metadata for the selected page only.
            for name in names[:limit]:
                try:
                    self.check_path('/'.join([*parts, name]))
                except WorkspaceError:
                    continue
                try:
                    entry_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENXIO):
                        continue
                    raise
                try:
                    info = os.fstat(entry_fd)
                    if (info.st_dev, info.st_ino) in self.private_identities:
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        kind, size = 'directory', None
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        kind, size = 'file', info.st_size
                    else:
                        continue
                    item = {'name': name, 'type': kind, 'size': size, 'modified': info.st_mtime}
                    if kind == 'directory':
                        try:
                            item['project_id'] = self.project('/'.join([*parts, name]))['id']
                        except WorkspaceError as exc:
                            if exc.status_code != 404:
                                raise
                    items.append(item)
                finally:
                    os.close(entry_fd)
            return {'path': path, 'items': items, 'next_cursor': names[limit - 1] if len(names) > limit else None}
        except OSError as exc:
            raise _error(exc) from None
        finally:
            os.close(fd)
