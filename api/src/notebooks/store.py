"""Private flat nbformat storage. All commits use fsync + atomic replace + directory fsync."""
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

import nbformat

LIMIT = 16 * 1024**2
ID = re.compile(r'^[a-f0-9]{32}$')
CELL_ID = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')


class NotebookError(Exception):
    def __init__(self, detail, status=422):
        super().__init__(detail)
        self.status_code = status


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise NotebookError('Invalid notebook or execution ID')
    return value


def source(cell):
    value = cell['source']
    return ''.join(value) if isinstance(value, list) else value


def source_hash(cell):
    return hashlib.sha256(source(cell).encode()).hexdigest()


class NotebookStore:
    def __init__(self, root):
        self.mutex = threading.RLock()
        self.fd = self.guard = None
        # Walk with directory descriptors: reject symlinks in every path component.
        path = Path(root).expanduser().absolute()
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:]:
                if part in {'.', '..'}:
                    raise NotebookError('Unsafe notebook storage root')
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = child
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise NotebookError('Notebook storage directory must be private and user-owned')
            self.fd, fd = fd, None
            self.guard = os.open('.owner.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self.fd)
            self._safe_file(self.guard)
            try:
                fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise NotebookError('Notebook storage already has a control-plane owner', 503) from exc
            self.recover()
        except OSError as exc:
            self.close()
            raise NotebookError('Unsafe or unavailable notebook storage directory', 503) from exc
        except BaseException:
            self.close()
            raise
        finally:
            if fd is not None: os.close(fd)

    @staticmethod
    def _safe_file(fd):
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise NotebookError('Unsafe notebook storage file', 503)
        if info.st_size > LIMIT:
            raise NotebookError('Notebook storage file exceeds size limit', 413)

    @contextmanager
    def locked(self):
        with self.mutex:
            if self.fd is None:
                raise NotebookError('Notebook storage is closed', 503)
            yield

    def _read(self, name):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except FileNotFoundError as exc:
            raise NotebookError('Notebook or execution not found', 404) from exc
        except OSError as exc:
            raise NotebookError('Unsafe or unavailable notebook storage file', 503) from exc
        try:
            self._safe_file(fd)
            with os.fdopen(fd, 'r', encoding='utf-8', closefd=False) as stream:
                raw = stream.read(LIMIT + 1)
            if len(raw.encode()) > LIMIT: raise NotebookError('Document too large', 413)
            return json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise NotebookError('Invalid stored JSON document', 503) from exc
        finally:
            os.close(fd)

    def _write(self, name, value):
        try:
            data = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=1) + '\n').encode()
        except (ValueError, TypeError, UnicodeError) as exc:
            raise NotebookError('Document must contain finite JSON values and valid Unicode') from exc
        if len(data) > LIMIT: raise NotebookError('Document too large', 413)
        # Never replace a link or an untrusted destination, even inside the private root.
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise NotebookError('Unsafe notebook destination', 503) from exc
        else:
            try: self._safe_file(fd)
            finally: os.close(fd)
        temporary = '.write-' + uuid.uuid4().hex + '.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data); stream.flush(); os.fsync(fd)
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            os.close(fd)
            try: os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError: pass

    @staticmethod
    def _validate(document):
        try:
            if document['nbformat'] != 4 or document['nbformat_minor'] != 5:
                raise ValueError('Expected nbformat 4.5')
            ids = [c['id'] for c in document['cells']]
            if any(not isinstance(i, str) or not CELL_ID.fullmatch(i) for i in ids) or len(set(ids)) != len(ids):
                raise ValueError('Invalid/duplicate cell IDs')
            if any('europa' in c['metadata'] and not isinstance(c['metadata']['europa'], dict) for c in document['cells']):
                raise ValueError('Europa cell metadata must be an object')
            if any(c['cell_type'] not in {'code', 'markdown'} for c in document['cells']):
                raise ValueError('Only code and markdown cells are supported')
            nbformat.validate(document)
        except (ValueError, KeyError, TypeError, nbformat.ValidationError) as exc:
            raise NotebookError('Invalid nbformat 4.5 notebook or cell IDs') from exc

    @staticmethod
    def _meta(document):
        try:
            meta = document['metadata']['europa']
            if not isinstance(meta, dict): raise ValueError()
            return meta
        except (KeyError, TypeError, ValueError) as exc:
            raise NotebookError('Notebook lacks managed identity metadata') from exc

    def _open(self, notebook):
        document = self._read(identifier(notebook) + '.ipynb')
        self._validate(document)
        meta = self._meta(document)
        if meta.get('notebook_id') != notebook or type(meta.get('revision')) is not int or meta['revision'] < 1:
            raise NotebookError('Invalid durable notebook identity/revision', 503)
        return document

    @staticmethod
    def _check(document, revision):
        if type(revision) is not int or revision != document['metadata']['europa']['revision']:
            raise NotebookError('Stale notebook revision', 409)

    def _commit(self, document):
        self._meta(document)['revision'] += 1
        self._validate(document)
        self._write(self._meta(document)['notebook_id'] + '.ipynb', document)
        return copy.deepcopy(document)

    def create(self, title='Untitled'):
        with self.locked():
            self._title(title)
            notebook = uuid.uuid4().hex
            document = nbformat.v4.new_notebook(metadata={'europa': {'notebook_id': notebook, 'title': title, 'revision': 1},
                'kernelspec': {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}})
            self._validate(document)
            self._write(notebook + '.ipynb', document)
            return document

    @staticmethod
    def _title(title):
        if not isinstance(title, str) or not title.strip() or len(title) > 256:
            raise NotebookError('Title must be nonblank and at most 256 characters')

    def open(self, notebook):
        with self.locked(): return self._open(notebook)

    def list(self):
        with self.locked():
            return [dict(self._meta(self._open(name[:-6]))) for name in sorted(os.listdir(self.fd))
                    if name.endswith('.ipynb') and ID.fullmatch(name[:-6])]

    def save(self, notebook, revision, document):
        with self.locked():
            current = self._open(notebook); self._check(current, revision)
            document = copy.deepcopy(document); self._validate(document)
            if self._meta(document) != self._meta(current):
                raise NotebookError('Notebook identity/title/revision are server-managed; use rename')
            previous = {c['id']: c for c in current['cells']}
            for cell in document['cells']:
                if previous.get(cell['id']) != cell:
                    cell['metadata'].pop('europa', None)
            return self._commit(document)

    def rename(self, notebook, revision, title):
        with self.locked():
            self._title(title)
            document = self._open(notebook); self._check(document, revision)
            self._meta(document)['title'] = title
            return self._commit(document)

    def delete(self, notebook, revision):
        with self.locked():
            document = self._open(notebook); self._check(document, revision)
            os.unlink(notebook + '.ipynb', dir_fd=self.fd); os.fsync(self.fd)
            # Receipts remain private history; deleting the document never destroys a runtime.

    @staticmethod
    def cell(document, cell_id):
        for cell in document['cells']:
            if cell['id'] == cell_id: return cell
        raise NotebookError('Cell not found', 404)

    def edit_cell(self, notebook, revision, action, cell_id=None, *, cell_type='code', text='', metadata=None, index=None):
        with self.locked():
            document = self._open(notebook); self._check(document, revision)
            cells = document['cells']
            if action == 'insert':
                if cell_type not in {'code','markdown'}: raise NotebookError('Unsupported cell type')
                cell = (nbformat.v4.new_code_cell if cell_type == 'code' else nbformat.v4.new_markdown_cell)(source=text, metadata=metadata or {}, id=uuid.uuid4().hex)
                if index is None: index = len(cells)
                if type(index) is not int or not 0 <= index <= len(cells): raise NotebookError('Invalid cell position')
                cells.insert(index, cell)
            else:
                cell = self.cell(document, cell_id)
                if action == 'delete': cells.remove(cell)
                elif action == 'move':
                    if type(index) is not int or not 0 <= index < len(cells): raise NotebookError('Invalid cell position')
                    cells.remove(cell); cells.insert(index, cell)
                elif action == 'edit':
                    cell['source'] = text
                    if metadata is not None: cell['metadata'] = metadata
                elif action == 'type':
                    if cell_type not in {'code','markdown'}: raise NotebookError('Unsupported cell type')
                    replacement = (nbformat.v4.new_code_cell if cell_type == 'code' else nbformat.v4.new_markdown_cell)(
                        source=source(cell), metadata=cell['metadata'], id=cell['id'])
                    cells[cells.index(cell)] = replacement
                elif action == 'clear':
                    if cell['cell_type'] != 'code': raise NotebookError('Only code cells have outputs')
                    cell['outputs'] = []; cell['execution_count'] = None
                else: raise NotebookError('Unknown cell operation')
                if action in {'edit','type','clear'}:
                    for c in cells:
                        if c['id'] == cell_id: c['metadata'].pop('europa', None)
            return self._commit(document)

    @staticmethod
    def _receipt_name(notebook, execution):
        return identifier(notebook) + '.' + identifier(execution) + '.execution.json'

    def execution(self, notebook, execution):
        with self.locked(): return self._read(self._receipt_name(notebook, execution))

    def executions(self, notebook):
        with self.locked():
            identifier(notebook)
            return sorted([self._read(name) for name in os.listdir(self.fd)
                if name.startswith(notebook + '.') and name.endswith('.execution.json') and ID.fullmatch(name[33:-15])],
                key=lambda r: r['created_at'])

    def begin(self, notebook, cell_id, revision):
        with self.locked():
            document = self._open(notebook); self._check(document, revision)
            cell = self.cell(document, cell_id)
            if cell['cell_type'] != 'code' or not source(cell).strip() or len(source(cell)) > 1_000_000:
                raise NotebookError('Execution requires a nonblank code cell of at most 1000000 characters')
            execution = uuid.uuid4().hex
            receipt = {'notebook_id': notebook, 'cell_id': cell_id, 'execution_id': execution,
                'expected_revision': revision + 1, 'source_hash': source_hash(cell), 'created_at': time.time(), 'state': 'prepared'}
            self._write(self._receipt_name(notebook, execution), receipt)
            cell['metadata']['europa'] = {'execution_id': execution, 'status': 'running'}
            cell['outputs'] = []; cell['execution_count'] = None
            self._commit(document)
            return receipt, source(cell)

    def update_execution(self, notebook, execution, **fields):
        with self.locked():
            receipt = self.execution(notebook, execution); receipt.update(fields)
            self._write(self._receipt_name(notebook, execution), receipt)
            return receipt

    def uncertain(self, notebook, execution, failure=None):
        with self.locked():
            receipt = self.update_execution(notebook, execution, state='uncertain', failure=failure)
            try:
                document = self._open(notebook)
                cell = self.cell(document, receipt['cell_id'])
                if (self._meta(document)['revision'] == receipt['expected_revision'] and
                        cell['metadata'].get('europa', {}).get('execution_id') == execution):
                    cell['metadata']['europa']['status'] = 'uncertain'
                    self._commit(document)
            except NotebookError as exc:
                if exc.status_code not in {404,409}: raise
            return receipt

    def finish(self, notebook, execution, result):
        with self.locked():
            receipt = self.update_execution(notebook, execution, state='completed', result=result)
            return self._apply(receipt)

    def _apply(self, receipt):
        notebook, execution = receipt['notebook_id'], receipt['execution_id']
        try:
            document = self._open(notebook)
            cell = self.cell(document, receipt['cell_id'])
            marker = cell['metadata'].get('europa', {})
            if (self._meta(document)['revision'] == receipt['expected_revision'] + 1
                    and marker == {'execution_id': execution, 'status': receipt['result']['status']}
                    and cell.get('outputs') == receipt['result']['outputs']
                    and cell.get('execution_count') == receipt['result']['execution_count']
                    and source_hash(cell) == receipt['source_hash']):
                return self.update_execution(notebook, execution, state='applied', applied_revision=self._meta(document)['revision'])
            if (self._meta(document)['revision'] != receipt['expected_revision'] or cell['cell_type'] != 'code'
                    or source_hash(cell) != receipt['source_hash'] or marker.get('execution_id') != execution):
                raise NotebookError('Document changed while execution was running', 409)
            # Resolve updates only into previously committed displays belonging to this
            # exact runtime/kernel/generation and unchanged cell execution identity.
            for previous in self.executions(notebook):
                if (previous['execution_id'] == execution or previous['state'] != 'applied'
                        or any(previous.get(k) != receipt.get(k) for k in ('runtime_id','generation','kernel_id'))
                        or not receipt['result'].get('kernel_session')
                        or previous['result'].get('kernel_session') != receipt['result']['kernel_session']): continue
                try: target = self.cell(document, previous['cell_id'])
                except NotebookError: continue
                if (target['metadata'].get('europa', {}).get('execution_id') != previous['execution_id']
                        or source_hash(target) != previous['source_hash']): continue
                for display, update in receipt['result'].get('display_updates', {}).items():
                    for index in previous['result'].get('display_map', {}).get(display, []):
                        if index < len(target.get('outputs', [])) and target['outputs'][index]['output_type'] in {'display_data','execute_result'}:
                            target['outputs'][index].update(copy.deepcopy(update))
            cell['outputs'] = receipt['result']['outputs']
            cell['execution_count'] = receipt['result']['execution_count']
            cell['metadata']['europa'] = {'execution_id': execution, 'status': receipt['result']['status']}
            committed = self._commit(document)
        except NotebookError as exc:
            if exc.status_code not in {404,409}: raise
            return self.update_execution(notebook, execution, state='conflict')
        return self.update_execution(notebook, execution, state='applied', applied_revision=self._meta(committed)['revision'])

    def recover(self):
        with self.locked():
            for name in os.listdir(self.fd):
                if re.fullmatch(r'[a-f0-9]{32}\.[a-f0-9]{32}\.execution\.json', name):
                    receipt = self._read(name)
                    if self._receipt_name(receipt['notebook_id'],receipt['execution_id']) != name:
                        raise NotebookError('Execution journal identity mismatch', 503)
                    if receipt['state'] == 'completed': self._apply(receipt)
                    elif receipt['state'] in {'prepared','running'}:
                        self.uncertain(receipt['notebook_id'], receipt['execution_id'])
                # Interrupted temp writes never become committed documents/receipts.
                elif re.fullmatch(r'\.write-[a-f0-9]{32}\.tmp', name):
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
                    try: self._safe_file(fd)
                    finally: os.close(fd)
                    os.unlink(name, dir_fd=self.fd); os.fsync(self.fd)

    def close(self):
        with self.mutex:
            if self.guard is not None: os.close(self.guard); self.guard = None
            if self.fd is not None: os.close(self.fd); self.fd = None
