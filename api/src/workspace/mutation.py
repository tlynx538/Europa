"""Single-owner workspace mutations with durable intents and conservative recovery."""
import hashlib
import json
import os
import stat
import threading
import uuid

from workspace.browser import WorkspaceError, _error, _segments


class WorkspaceMutation:
    def __init__(self, browser, journal):
        self.browser = browser
        self.journal = journal
        self.lock = threading.RLock()
        browser.project_store = journal

    def create_project(self, body):
        """Mkdir is never replayed if its effect's inode was not journaled."""
        with self.lock:
            path = body['path']
            _segments(path)
            root = self._identity(os.fstat(self.browser._root_fd))
            fingerprint = hashlib.sha256(json.dumps(['project', body], ensure_ascii=False,
                sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
            row = self.journal.project_lookup(body['operation_id'], fingerprint, root)
            if row and row['response'] is not None:
                return json.loads(row['response'])
            fd, name = self._parent(path)
            try:
                if row is None:
                    try:
                        os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise WorkspaceError(409, 'Workspace entry already exists')
                    project_id = uuid.uuid4().hex
                    self.journal.project_intent(body['operation_id'], fingerprint, root, path, project_id)
                    self._checkpoint('after_intent')
                    os.mkdir(name, 0o700, dir_fd=fd)
                    os.fsync(fd)
                    self._checkpoint('after_mkdir')
                    directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        identity = self._identity(os.fstat(directory))
                        self.journal.project_directory(body['operation_id'], identity)
                    finally:
                        os.close(directory)
                else:
                    project_id = row['project_id']
                    if row['dir_inode'] is None:
                        raise WorkspaceError(409, 'Project creation uncertain; inspect the folder before a new action')
                    identity = (row['dir_device'], row['dir_inode'])
                directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if self._identity(os.fstat(directory)) != identity:
                        raise WorkspaceError(409, 'Project directory changed before binding')
                    contents = json.dumps({'format': 'europa-project', 'version': 1, 'id': project_id},
                                          sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
                    try:
                        handle = os.open('.europa-project.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o600, dir_fd=directory)
                    except FileExistsError:
                        handle = None
                    if handle is not None:
                        try:
                            with os.fdopen(handle, 'wb', closefd=False) as stream:
                                stream.write(contents)
                                stream.flush()
                                os.fsync(handle)
                        finally:
                            os.close(handle)
                        os.fsync(directory)
                    self._checkpoint('after_descriptor')
                    # Recheck the linked directory and file after the filesystem effect.
                    current = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        if self._identity(os.fstat(current)) != identity:
                            raise WorkspaceError(409, 'Project directory changed before binding')
                        desc = os.open('.europa-project.json', os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=current)
                        try:
                            info = os.fstat(desc)
                            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != len(contents) or os.read(desc, len(contents) + 1) != contents:
                                raise WorkspaceError(409, 'Project descriptor changed before binding')
                            descriptor_identity = self._identity(info)
                        finally:
                            os.close(desc)
                        if self._identity(os.fstat(current)) != identity:
                            raise WorkspaceError(409, 'Project directory changed before binding')
                        self._checkpoint('before_binding')
                        result = {'id': project_id, 'path': path, 'type': 'project'}
                        self.journal.project_confirm(body['operation_id'], root, path, identity, descriptor_identity, result)
                        return result
                    finally:
                        os.close(current)
                finally:
                    os.close(directory)
            except OSError as exc:
                if exc.errno == 17:
                    raise WorkspaceError(409, 'Workspace entry already exists') from None
                raise _error(exc) from None
            finally:
                os.close(fd)

    def _checkpoint(self, stage):
        """A no-op seam for failure-injection tests; never changes production flow."""

    def _parent(self, path):
        self.browser.check_path(path)
        parts = _segments(path)
        if not parts:
            raise WorkspaceError(400, 'A workspace entry name is required')
        fd = os.dup(self.browser._root_fd)
        try:
            self.browser.check_private(os.fstat(fd))
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
                self.browser.check_private(os.fstat(fd))
            return fd, parts[-1]
        except OSError as exc:
            os.close(fd)
            raise _error(exc) from None

    @staticmethod
    def _identity(info):
        return info.st_dev, info.st_ino

    def _existing(self, fd, name):
        try:
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                info = os.fstat(file_fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise WorkspaceError(400, 'Unsafe workspace entry')
                return info
            finally:
                os.close(file_fd)
        except OSError as exc:
            raise _error(exc) from None

    def _reconcile(self, row):
        if row['state'] == 'confirmed':
            return json.loads(row['response'])
        if row['expected_inode'] is None:
            raise WorkspaceError(409, 'Workspace operation uncertain; inspect the folder before a new action')
        fd, name = self._parent(row['path'])
        try:
            try:
                info = self._existing(fd, name)
                result = self.browser.read_text(row['path'])
                after = self._existing(fd, name)
            except WorkspaceError:
                raise WorkspaceError(409, 'Workspace operation uncertain; inspect the file before a new action') from None
            if (self._identity(info) != self._identity(after) or
                    (info.st_size, info.st_mtime_ns, info.st_ctime_ns) !=
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or
                    self._identity(info) != (row['expected_device'], row['expected_inode']) or
                    hashlib.sha256(result['content'].encode('utf-8')).hexdigest() != row['expected_digest']):
                raise WorkspaceError(409, 'Workspace operation uncertain; inspect the file before a new action')
            self.journal.workspace_confirm(row['id'], result)
            return result
        finally:
            os.close(fd)

    def _begin(self, kind, body, root):
        try:
            fingerprint = hashlib.sha256(json.dumps([kind, body], ensure_ascii=False, sort_keys=True,
                                               separators=(',', ':')).encode('utf-8')).hexdigest()
        except UnicodeError:
            raise WorkspaceError(400, 'Invalid UTF-8 workspace input') from None
        prior = self.journal.workspace_lookup(body['operation_id'], fingerprint, root)
        if prior:
            return fingerprint, self._reconcile(prior)
        return fingerprint, None

    def create(self, kind, body):
        with self.lock:
            if kind == 'file' and body['path'].lower().endswith('.ipynb'):
                raise WorkspaceError(400, 'Notebook creation requires the notebook workflow')
            root = self._identity(os.fstat(self.browser._root_fd))
            fingerprint, previous = self._begin(kind, body, root)
            if previous is not None:
                return previous
            fd, name = self._parent(body['path'])
            try:
                try:
                    os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise WorkspaceError(409, 'Workspace entry already exists')
                if kind == 'folder':
                    self.journal.workspace_intent(body['operation_id'], fingerprint, root, kind, body['path'], None, None)
                    self._checkpoint('after_intent')
                    os.mkdir(name, 0o700, dir_fd=fd)
                    os.fsync(fd)
                    self._checkpoint('after_effect')
                    result = {'path': body['path'], 'type': 'directory'}
                else:
                    tmp, info = self._stage(fd, b'')
                    try:
                        self.journal.workspace_intent(body['operation_id'], fingerprint, root, kind, body['path'], None,
                                                      (*self._identity(info), hashlib.sha256(b'').hexdigest()))
                        self._checkpoint('after_intent')
                        os.link(tmp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                        os.fsync(fd)
                        os.unlink(tmp, dir_fd=fd)
                        os.fsync(fd)
                        self._checkpoint('after_effect')
                        row = self.journal.workspace_lookup(body['operation_id'], fingerprint, root)
                        result = self._reconcile(row)
                    finally:
                        # Only remove our private staging entry; never the user's target.
                        try: os.unlink(tmp, dir_fd=fd)
                        except FileNotFoundError: pass
                self.journal.workspace_confirm(body['operation_id'], result)
                return result
            except OSError as exc:
                if exc.errno == 17:
                    raise WorkspaceError(409, 'Workspace entry already exists') from None
                raise _error(exc) from None
            finally:
                os.close(fd)

    @staticmethod
    def _stage(fd, data):
        tmp = '.europa-write-' + uuid.uuid4().hex
        handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(handle, 'wb', closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(handle)
            info = os.fstat(handle)
            os.fsync(fd)
            return tmp, info
        except BaseException:
            os.unlink(tmp, dir_fd=fd)
            raise
        finally:
            os.close(handle)

    def save(self, body):
        with self.lock:
            if body['path'].lower().endswith('.ipynb'):
                raise WorkspaceError(400, 'Notebook edits require the notebook workflow')
            root = self._identity(os.fstat(self.browser._root_fd))
            fingerprint, previous = self._begin('save', body, root)
            if previous is not None:
                return previous
            try:
                data = body['content'].encode('utf-8')
            except UnicodeError:
                raise WorkspaceError(400, 'Invalid UTF-8 text content') from None
            if b'\x00' in data or len(data) > 1024 * 1024:
                raise WorkspaceError(413 if len(data) > 1024 * 1024 else 415, 'Invalid UTF-8 text content')
            fd, name = self._parent(body['path'])
            try:
                original = self._existing(fd, name)
                if self.browser.read_text(body['path'])['revision'] != body['revision']:
                    raise WorkspaceError(409, 'Text revision is stale')
                tmp, info = self._stage(fd, data)
                try:
                    self.journal.workspace_intent(body['operation_id'], fingerprint, root, 'save', body['path'],
                                                  body['revision'], (*self._identity(info), hashlib.sha256(data).hexdigest()))
                    self._checkpoint('after_intent')
                    # Detect changes after the read and staging before atomic replace.
                    now = self._existing(fd, name)
                    if (self._identity(now), now.st_size, now.st_mtime_ns, now.st_ctime_ns) != (
                        self._identity(original), original.st_size, original.st_mtime_ns, original.st_ctime_ns):
                        raise WorkspaceError(409, 'Text changed before replacement; operation uncertain')
                    os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
                    os.fsync(fd)
                    self._checkpoint('after_effect')
                    row = self.journal.workspace_lookup(body['operation_id'], fingerprint, root)
                    return self._reconcile(row)
                finally:
                    try: os.unlink(tmp, dir_fd=fd)
                    except FileNotFoundError: pass
            except OSError as exc:
                raise _error(exc) from None
            finally:
                os.close(fd)
