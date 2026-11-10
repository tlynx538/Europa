"""Physical document adapter for NotebookStore; receipts remain in its private root.

A private atomic manifest journals path bindings and document replacement hashes.
No workspace metadata is an identity grant. External edits block managed writes.
"""
import copy
import hashlib
import json
import os
import stat
import uuid

import nbformat

from notebooks.store import NotebookStore, NotebookError, ID, LIMIT, identifier
from workspace.browser import WorkspaceError, _error, _segments

MANIFEST = '.workspace-notebooks.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


class WorkspaceNotebookStore(NotebookStore):
    def __init__(self, root, browser):
        self.browser = browser
        super().__init__(root)

    def _manifest(self):
        try:
            return super()._read(MANIFEST)
        except NotebookError as exc:
            if exc.status_code == 404:
                return {}
            raise

    def _journal(self, mappings):
        super()._write(MANIFEST, mappings)

    def _checkpoint(self, stage):
        """Failure-injection seam."""

    def _parent(self, path):
        self.browser.check_path(path)
        parts = _segments(path)
        if not parts or not parts[-1].lower().endswith('.ipynb'):
            raise WorkspaceError(400, 'An .ipynb workspace path is required')
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
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def _bytes(fd, name):
        try:
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise _error(exc) from None
        try:
            before = os.fstat(handle)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.getuid():
                raise NotebookError('Unsafe physical notebook', 400)
            if before.st_size > LIMIT:
                raise NotebookError('Notebook exceeds size limit', 413)
            with os.fdopen(handle, 'rb', closefd=False) as stream:
                data = stream.read(LIMIT + 1)
            after = os.fstat(handle)
            if len(data) > LIMIT:
                raise NotebookError('Notebook exceeds size limit', 413)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise NotebookError('Physical notebook changed while reading', 409)
            return data
        finally:
            os.close(handle)

    def _bound_parent(self, row):
        root = os.fstat(self.browser._root_fd)
        if row['root'] != [root.st_dev, root.st_ino]:
            raise NotebookError('Notebook belongs to another workspace root', 404)
        fd, name = self._parent(row['path'])
        info = os.fstat(fd)
        if row['parent'] != [info.st_dev, info.st_ino]:
            os.close(fd)
            raise NotebookError('Notebook directory identity changed', 409)
        return fd, name

    def _physical_read(self, notebook, mappings):
        row = mappings[notebook]
        fd, name = self._bound_parent(row)
        try:
            data = self._bytes(fd, name)
        finally:
            os.close(fd)
        if data is None:
            raise NotebookError('Physical notebook not found', 404)
        observed = digest(data)
        if row.get('pending') == observed:
            row.update(digest=observed, confirmed=True)
            row.pop('pending', None)
            self._journal(mappings)
        elif observed != row['digest']:
            raise NotebookError('Physical notebook changed outside Europa; managed writes blocked', 409)
        elif row.get('pending'):
            # No effect committed. Never replay an execution or filesystem write.
            row.pop('pending')
            self._journal(mappings)
        if not row['confirmed']:
            raise NotebookError('Notebook association incomplete; retry the exact workspace open', 409)
        try:
            return json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise NotebookError('Invalid physical notebook JSON', 422) from exc

    def _read(self, name):
        if name.endswith('.ipynb') and ID.fullmatch(name[:-6]):
            mappings = self._manifest()
            if name[:-6] in mappings:
                return self._physical_read(name[:-6], mappings)
        return super()._read(name)

    def _write(self, name, value):
        mappings = self._manifest() if name.endswith('.ipynb') else {}
        row = mappings.get(name[:-6])
        if row is None:
            return super()._write(name, value)
        data = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=1) + '\n').encode()
        if len(data) > LIMIT:
            raise NotebookError('Document too large', 413)
        fd, target = self._bound_parent(row)
        temporary = '.europa-notebook-' + uuid.uuid4().hex + '.tmp'
        try:
            old = self._bytes(fd, target)
            if (digest(old) if old is not None else None) != row['digest']:
                raise NotebookError('Physical notebook changed before save', 409)
            row['pending'] = digest(data)
            self._journal(mappings)
            self._checkpoint('after_intent')
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(handle, 'wb', closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(handle)
            finally:
                os.close(handle)
            current, _ = self._bound_parent(row)
            os.close(current)
            if self._bytes(fd, target) != old:
                raise NotebookError('Physical notebook changed before commit', 409)
            if old is None:
                # No-replace publication: an external collision never gets overwritten.
                os.link(temporary, target, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                os.unlink(temporary, dir_fd=fd)
            else:
                os.replace(temporary, target, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
            self._checkpoint('after_effect')
            row.update(digest=digest(data), confirmed=True)
            row.pop('pending', None)
            self._journal(mappings)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
            os.close(fd)

    def list(self):
        with self.locked():
            result = super().list()
            for notebook in self._manifest():
                try:
                    result.append(dict(self._meta(self._open(notebook))))
                except (NotebookError, WorkspaceError, OSError):
                    # A missing/conflicted physical file must not disable private history.
                    continue
            return result

    def location(self, notebook):
        with self.locked():
            row = self._manifest().get(identifier(notebook))
            if row is None:
                return None
            self._open(notebook)
            return {key: row.get(key) for key in ('path', 'project_id', 'project_path')}

    def dashboard_info(self, notebook):
        """Observe without reconciling a pending filesystem journal or adopting a path."""
        identifier(notebook)
        with self.locked():
            row = self._manifest().get(notebook)
            if row is None:
                return super()._read(notebook + '.ipynb'), None
            if not row['confirmed'] or row.get('pending'):
                raise NotebookError('Physical notebook write requires reconciliation', 409)
            fd, name = self._bound_parent(row)
            try:
                data = self._bytes(fd, name)
            finally:
                os.close(fd)
            if data is None or digest(data) != row['digest']:
                raise NotebookError('Physical notebook is unavailable or changed', 409)
            document = json.loads(data)
            self._validate(document)
            if self._meta(document)['notebook_id'] != notebook:
                raise NotebookError('Physical identity changed', 409)
            if row.get('project_id'):
                if self.browser.project(row['project_path'])['id'] != row['project_id']:
                    raise NotebookError('Project identity changed', 409)
            return document, {key: row.get(key) for key in ('path', 'project_id', 'project_path')}

    def associate(self, path, operation_id, create=False):
        with self.locked():
            mappings = self._manifest()
            root = os.fstat(self.browser._root_fd)
            scope = [root.st_dev, root.st_ino]
            for row in mappings.values():
                if row['operation_id'] == operation_id and (row['path'] != path or row['create'] != create or row['root'] != scope):
                    raise NotebookError('Operation ID reused with different input', 409)
            for notebook, row in mappings.items():
                if row['path'] == path and row['root'] == scope:
                    if create and row['operation_id'] != operation_id:
                        raise NotebookError('Notebook already exists', 409)
                    if row['confirmed'] or row.get('pending'):
                        try:
                            self._physical_read(notebook, mappings)
                            return notebook
                        except NotebookError as exc:
                            if row['confirmed'] or (exc.status_code != 409 and not (create and row['digest'] is None and exc.status_code == 404)):
                                raise
                    break
            else:
                notebook, row = uuid.uuid4().hex, None
            fd, name = self._parent(path)
            try:
                raw = self._bytes(fd, name)
                if row is None and create and raw is not None:
                    raise NotebookError('Workspace entry already exists', 409)
                if not create and raw is None:
                    raise NotebookError('Physical notebook not found', 404)
                if row is not None and (digest(raw) if raw is not None else None) != row['digest']:
                    raise NotebookError('Notebook association outcome requires inspection', 409)
                if raw is None:
                    document = nbformat.v4.new_notebook(metadata={'kernelspec': {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}})
                else:
                    try:
                        document = json.loads(raw)
                        self._validate(document)
                    except (ValueError, UnicodeError) as exc:
                        raise NotebookError('Invalid notebook JSON', 422) from exc
                document = copy.deepcopy(document)
                # Imported metadata is untrusted; allocate a fresh private identity once.
                document['metadata']['europa'] = {'notebook_id': notebook, 'title': name[:-6], 'revision': 1}
                self._title(name[:-6])
                for cell in document['cells']:
                    cell['metadata'].pop('europa', None)
                self._validate(document)
                if row is None:
                    info = os.fstat(fd)
                    row = {'path': path, 'root': scope, 'parent': [info.st_dev, info.st_ino], 'digest': digest(raw) if raw is not None else None,
                           'confirmed': False, 'operation_id': operation_id, 'create': create}
                    parts = _segments(path)[:-1]
                    for length in range(len(parts), 0, -1):
                        try:
                            project = self.browser.project('/'.join(parts[:length]))
                        except WorkspaceError as exc:
                            if exc.status_code == 404:
                                continue
                            raise
                        row.update(project_id=project['id'], project_path=project['path'])
                        break
                    mappings[notebook] = row
                    self._journal(mappings)
                self._write(notebook + '.ipynb', document)
                return notebook
            except OSError as exc:
                raise _error(exc) from None
            finally:
                os.close(fd)

    def delete(self, notebook, revision):
        with self.locked():
            if notebook in self._manifest():
                raise NotebookError('Delete of physical notebooks is not supported', 409)
            return super().delete(notebook, revision)
