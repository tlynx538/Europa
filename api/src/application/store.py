"""Single-owner SQLite application journal, independent of runtime ownership.

Lock order is notebook mutex -> application mutex -> SQLite BEGIN IMMEDIATE.
Notebook files are never modified by this store. Catalog entries are durable
historical identities; present/missing is reconciled against files on every call.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from notebooks.store import NotebookError, NotebookStore, source, source_hash
from application.models import Task


class ApplicationError(NotebookError):
    pass


def encoded(value):
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))
        result.encode('utf-8')
        return result
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ApplicationError('Invalid portable JSON') from exc


def clean_summary(value):
    # Plain text only. Rendering still must escape this as all other owner text.
    value = re.sub(r'<[^>]*>', '', value)
    value = ''.join(c for c in value if c in '\n\t' or ord(c) >= 32)
    return value.strip()


SCHEMA = [
    """CREATE TABLE catalog (id TEXT PRIMARY KEY, present INTEGER NOT NULL CHECK(present IN (0,1)))""",
    """CREATE TABLE records (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL CHECK(kind IN ('project','conversation','message','roadmap','comment','activity')),
        project_id TEXT REFERENCES records(id), notebook_id TEXT REFERENCES catalog(id),
        parent_id TEXT REFERENCES records(id), revision INTEGER NOT NULL CHECK(revision>0),
        created_at REAL NOT NULL, updated_at REAL NOT NULL, data TEXT NOT NULL CHECK(json_valid(data)),
        CHECK((kind='project' AND project_id IS NULL AND notebook_id IS NULL) OR
              (kind IN ('conversation','message') AND project_id IS NULL AND notebook_id IS NOT NULL) OR
              (kind!='project' AND project_id IS NOT NULL)))""",
    """CREATE TABLE memberships (notebook_id TEXT PRIMARY KEY REFERENCES catalog(id),
        project_id TEXT NOT NULL REFERENCES records(id), associated INTEGER NOT NULL CHECK(associated IN (0,1)))""",
    """CREATE TABLE versions (roadmap_id TEXT NOT NULL REFERENCES records(id), revision INTEGER NOT NULL,
        created_at REAL NOT NULL, data TEXT NOT NULL CHECK(json_valid(data)), PRIMARY KEY(roadmap_id,revision))""",
    """CREATE TABLE operations (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL CHECK(json_valid(response)))""",
    """CREATE TABLE canonical_notebook_conversations (
        notebook_id TEXT PRIMARY KEY REFERENCES catalog(id),
        conversation_id TEXT NOT NULL UNIQUE REFERENCES records(id))""",
    'CREATE INDEX history ON records(kind,project_id,sequence)',
    'CREATE INDEX replies ON records(parent_id,sequence)',
    """CREATE TRIGGER immutable_records BEFORE UPDATE ON records WHEN OLD.kind IN ('message','activity')
        BEGIN SELECT RAISE(ABORT,'append-only record'); END""",
    """CREATE TRIGGER retain_records BEFORE DELETE ON records BEGIN SELECT RAISE(ABORT,'historical record'); END""",
    """CREATE TRIGGER immutable_versions BEFORE UPDATE ON versions BEGIN SELECT RAISE(ABORT,'immutable version'); END""",
    """CREATE TRIGGER retain_versions BEFORE DELETE ON versions BEGIN SELECT RAISE(ABORT,'historical version'); END""",
    """CREATE TABLE canonical_message_placements (
        message_id TEXT PRIMARY KEY REFERENCES records(id),
        after_cell_id TEXT)""",
    """CREATE TABLE recent_opens (
        notebook_id TEXT PRIMARY KEY REFERENCES catalog(id), opened_at REAL NOT NULL)""",
]

WORKSPACE_JOURNAL = """CREATE TABLE workspace_operations (
    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, root_device INTEGER NOT NULL,
    root_inode INTEGER NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
    precondition TEXT, expected_device INTEGER, expected_inode INTEGER,
    expected_digest TEXT, state TEXT NOT NULL CHECK(state IN ('pending','confirmed')),
    response TEXT CHECK(response IS NULL OR json_valid(response)))"""

PROJECT_INTENTS = """CREATE TABLE project_intents (
    operation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
    root_device INTEGER NOT NULL, root_inode INTEGER NOT NULL, path TEXT NOT NULL,
    project_id TEXT NOT NULL UNIQUE, dir_device INTEGER, dir_inode INTEGER,
    response TEXT CHECK(response IS NULL OR json_valid(response)))"""
PROJECT_BINDINGS = """CREATE TABLE project_bindings (
    project_id TEXT PRIMARY KEY, root_device INTEGER NOT NULL, root_inode INTEGER NOT NULL,
    path TEXT NOT NULL, dir_device INTEGER NOT NULL, dir_inode INTEGER NOT NULL,
    descriptor_device INTEGER NOT NULL, descriptor_inode INTEGER NOT NULL,
    UNIQUE(root_device,root_inode,path))"""



class ApplicationStore:
    def __init__(self, root, notebooks):
        self.notebooks = notebooks
        self.mutex = threading.RLock()
        self.db = self.boundary = None
        # Reuse the audited descriptor-walking private-directory/owner-lock boundary.
        self.boundary = NotebookStore(root)
        try:
            for name in ('application.sqlite3', 'application.sqlite3-wal', 'application.sqlite3-shm', 'application.sqlite3-journal'):
                try:
                    fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.boundary.fd)
                except FileNotFoundError:
                    if name != 'application.sqlite3': continue
                    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=self.boundary.fd)
                try:
                    # Database size is not a notebook size; ownership/type/link rules apply.
                    import stat
                    s = os.fstat(fd)
                    if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077 or s.st_nlink != 1:
                        raise ApplicationError('Unsafe application database', 503)
                finally:
                    os.close(fd)
            self.db = sqlite3.connect(Path(root).expanduser().absolute() / 'application.sqlite3', timeout=5, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5, 6): raise ApplicationError('Unsupported application schema version', 503)
            # A v1 table rebuild must disable FK enforcement outside BEGIN; all
            # original rows and dependent tables are checked before commit.
            if version == 1: self.db.execute('PRAGMA foreign_keys=OFF')
            try:
                with self.transaction():
                    if version == 0:
                        for statement in SCHEMA: self.db.execute(statement)
                        self.db.execute('PRAGMA user_version=4')
                    elif version == 1:
                        self._migrate_v1()
                    elif version == 2:
                        self.db.execute(SCHEMA[-2])
                        self.db.execute('PRAGMA user_version=3')
                    if version in (1, 2, 3):
                        self.db.execute(SCHEMA[-1])
                        self.db.execute('PRAGMA user_version=4')
                    if version < 5:
                        self.db.execute(WORKSPACE_JOURNAL)
                        self.db.execute('PRAGMA user_version=5')
                    if version < 6:
                        self.db.execute(PROJECT_INTENTS)
                        self.db.execute(PROJECT_BINDINGS)
                        self.db.execute('PRAGMA user_version=6')
                    if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='workspace_operations'").fetchone() is None:
                        raise ApplicationError('Missing workspace journal', 503)
                    for name in ('project_intents', 'project_bindings'):
                        if self.db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone() is None:
                            raise ApplicationError('Missing project identity table', 503)
                    if self.db.execute('PRAGMA foreign_key_check').fetchall():
                        raise ApplicationError('Application database integrity check failed', 503)
            finally:
                if version == 1: self.db.execute('PRAGMA foreign_keys=ON')
            if self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or self.db.execute('PRAGMA foreign_key_check').fetchall():
                raise ApplicationError('Application database integrity check failed', 503)
            os.fsync(self.boundary.fd)
            with self.transaction(): pass  # Reconcile existing notebooks without rewriting files.
        except BaseException:
            self.close()
            raise

    def _migrate_v1(self):
        self.db.execute(SCHEMA[1].replace('CREATE TABLE records', 'CREATE TABLE records_v2'))
        columns = 'sequence,id,kind,project_id,notebook_id,parent_id,revision,created_at,updated_at,data'
        self.db.execute(f'INSERT INTO records_v2 ({columns}) SELECT {columns} FROM records')
        self.db.execute('DROP TABLE records')
        self.db.execute('ALTER TABLE records_v2 RENAME TO records')
        for statement in (SCHEMA[5], *SCHEMA[6:10]):
            self.db.execute(statement)
        self.db.execute(SCHEMA[-2])
        self.db.execute('PRAGMA user_version=3')

    @contextmanager
    def transaction(self):
        with self.notebooks.locked(), self.mutex:
            if self.db is None: raise ApplicationError('Application storage is closed', 503)
            try:
                self.db.execute('BEGIN IMMEDIATE')
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='catalog'").fetchone():
                    self.db.execute('UPDATE catalog SET present=0')
                    for notebook in self.notebooks.list():
                        self.db.execute('INSERT INTO catalog VALUES (?,1) ON CONFLICT(id) DO UPDATE SET present=1', (notebook['notebook_id'],))
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self):
        with self.mutex:
            if self.db is not None:
                self.db.close(); self.db = None
            if self.boundary is not None:
                self.boundary.close(); self.boundary = None

    def _row(self, identity, kind=None):
        row = self.db.execute('SELECT * FROM records WHERE id=?', (identity,)).fetchone()
        if row is None or kind is not None and row['kind'] != kind:
            raise ApplicationError('Application resource not found', 404)
        return row

    @staticmethod
    def _record(row):
        return {key: json.loads(row[key]) if key == 'data' else row[key]
                for key in ('id', 'project_id', 'notebook_id', 'revision', 'created_at', 'updated_at', 'sequence', 'data')}

    def _project(self, project, writable=True):
        row = self._row(project, 'project')
        if writable and json.loads(row['data'])['status'] != 'active':
            raise ApplicationError('Project is archived or deleted', 409)
        return row

    def _scope(self, project, notebook):
        self._project(project)
        if notebook is not None:
            row = self.db.execute('SELECT 1 FROM memberships JOIN catalog ON catalog.id=notebook_id '
                'WHERE project_id=? AND notebook_id=? AND associated=1 AND present=1', (project, notebook)).fetchone()
            if not row: raise ApplicationError('Notebook is not a present project member', 409)

    def _notebook_scope(self, notebook):
        self.notebooks.open(notebook)
        if not self.db.execute('SELECT 1 FROM catalog WHERE id=? AND present=1', (notebook,)).fetchone():
            raise ApplicationError('Notebook is not present', 404)

    def record_notebook_open(self, notebook):
        """Explicit navigation event; never called by notebook reads or execution."""
        with self._mutation_transaction():
            self._notebook_scope(notebook)
            now = time.time()
            self.db.execute('INSERT INTO recent_opens VALUES (?,?) ON CONFLICT(notebook_id) '
                            'DO UPDATE SET opened_at=MAX(excluded.opened_at, recent_opens.opened_at + 0.000001)',
                            (notebook, now))
            self.db.execute('DELETE FROM recent_opens WHERE notebook_id NOT IN '
                            '(SELECT notebook_id FROM recent_opens ORDER BY opened_at DESC, notebook_id LIMIT 100)')
            row = self.db.execute('SELECT opened_at FROM recent_opens WHERE notebook_id=?', (notebook,)).fetchone()
            return {'notebook_id': notebook, 'opened_at': row['opened_at']}

    def recent_notebooks(self, *, limit=20):
        if not 1 <= limit <= 20: raise ApplicationError('Invalid pagination')
        with self.transaction():
            return [dict(row) for row in self.db.execute(
                'SELECT recent_opens.notebook_id,opened_at FROM recent_opens '
                'JOIN catalog ON catalog.id=recent_opens.notebook_id WHERE catalog.present=1 '
                'ORDER BY opened_at DESC,recent_opens.notebook_id LIMIT ?', (limit,))]

    def ensure_notebook_conversation(self, notebook):
        with self._mutation_transaction():
            self._notebook_scope(notebook)
            mapping = self.db.execute('SELECT conversation_id FROM canonical_notebook_conversations WHERE notebook_id=?',
                                      (notebook,)).fetchone()
            if mapping:
                self._adopt_unplaced(mapping['conversation_id'], notebook)
                return self._view(self._row(mapping['conversation_id'], 'conversation'))
            record = self._insert('conversation', None, notebook, {'title': 'Discussion'})
            self.db.execute('INSERT INTO canonical_notebook_conversations VALUES (?,?)', (notebook, record['id']))
            return record

    def _adopt_unplaced(self, conversation, notebook):
        # First current-layout observation, not reconstructed message/cell chronology.
        # The surrounding transaction holds the notebook lock through this snapshot.
        cells = self.notebooks.open(notebook)['cells']
        anchor = cells[-1]['id'] if cells else None
        self.db.execute("""INSERT INTO canonical_message_placements (message_id,after_cell_id)
            SELECT id,? FROM records WHERE parent_id=? AND kind='message'
              AND json_type(data,'$.placement_after_cell_id') IS NULL
            ON CONFLICT(message_id) DO NOTHING""", (anchor, conversation))

    def legacy_notebook_conversations(self, notebook, *, after=0, limit=50):
        if not 1 <= limit <= 100 or after < 0: raise ApplicationError('Invalid pagination')
        with self.transaction():
            self._notebook_scope(notebook)
            rows = self.db.execute("SELECT * FROM records WHERE kind='conversation' AND notebook_id=? "
                'AND id NOT IN (SELECT conversation_id FROM canonical_notebook_conversations) '
                'AND sequence>? ORDER BY sequence LIMIT ?', (notebook, after, limit+1)).fetchall()
            return {'items': [self._view(row) for row in rows[:limit]],
                    'next_cursor': rows[limit-1]['sequence'] if len(rows) > limit else None}

    def _insert(self, kind, project, notebook, data, parent=None):
        identity, now = uuid.uuid4().hex, time.time()
        self.db.execute('INSERT INTO records(id,kind,project_id,notebook_id,parent_id,revision,created_at,updated_at,data) '
            'VALUES (?,?,?,?,?,1,?,?,?)', (identity, kind, project, notebook, parent, now, now, encoded(data)))
        return self._view(self._row(identity))

    def _replace(self, row, data, revision):
        if row['revision'] != revision: raise ApplicationError('Stale application revision', 409)
        self.db.execute('UPDATE records SET revision=revision+1,updated_at=?,data=? WHERE id=?',
                        (time.time(), encoded(data), row['id']))
        return self._view(self._row(row['id']))

    def _mutate(self, operation, action, payload, function):
        fingerprint = hashlib.sha256(encoded([action, payload]).encode('utf-8')).hexdigest()
        with self._mutation_transaction():
            previous = self.db.execute('SELECT * FROM operations WHERE id=?', (operation,)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint: raise ApplicationError('Operation ID reused with different input', 409)
                return json.loads(previous['response'])
            result = function()
            response = encoded(result)
            if len(response.encode()) > 2 * 1024**2: raise ApplicationError('Application result too large', 413)
            self.db.execute('INSERT INTO operations VALUES (?,?,?)', (operation, fingerprint, response))
            return result

    def workspace_lookup(self, operation, fingerprint, root):
        with self._mutation_transaction():
            row = self.db.execute('SELECT * FROM workspace_operations WHERE id=?', (operation,)).fetchone()
            if row and (row['fingerprint'] != fingerprint or (row['root_device'], row['root_inode']) != root):
                raise ApplicationError('Operation ID reused with different input or workspace', 409)
            return dict(row) if row else None

    def workspace_intent(self, operation, fingerprint, root, kind, path, precondition, expected):
        with self._mutation_transaction():
            self.db.execute('INSERT INTO workspace_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)',
                (operation, fingerprint, *root, kind, path, precondition,
                 expected[0] if expected else None, expected[1] if expected else None,
                 expected[2] if expected else None, 'pending'))

    def workspace_confirm(self, operation, response):
        with self._mutation_transaction():
            self.db.execute("UPDATE workspace_operations SET state='confirmed',response=? WHERE id=? AND state='pending'",
                            (encoded(response), operation))

    def project_lookup(self, operation, fingerprint, root):
        with self.mutex:
            row = self.db.execute('SELECT * FROM project_intents WHERE operation_id=?', (operation,)).fetchone()
            if row and (row['fingerprint'] != fingerprint or (row['root_device'], row['root_inode']) != root):
                raise ApplicationError('Operation ID reused with different input or workspace', 409)
            # IDs are shared with the single-file journal; never allow a conflicting replay.
            if not row and self.db.execute('SELECT 1 FROM workspace_operations WHERE id=?', (operation,)).fetchone():
                raise ApplicationError('Operation ID reused with different input', 409)
            return dict(row) if row else None

    def project_intent(self, operation, fingerprint, root, path, project_id):
        with self._mutation_transaction():
            if self.db.execute('SELECT 1 FROM project_bindings WHERE root_device=? AND root_inode=? AND path=?', (*root, path)).fetchone():
                raise ApplicationError('Project already exists', 409)
            self.db.execute('INSERT INTO project_intents VALUES (?,?,?,?,?,?,NULL,NULL,NULL)',
                            (operation, fingerprint, *root, path, project_id))

    def project_directory(self, operation, identity):
        with self._mutation_transaction():
            self.db.execute('UPDATE project_intents SET dir_device=?,dir_inode=? WHERE operation_id=? AND dir_inode IS NULL',
                            (*identity, operation))

    def project_confirm(self, operation, root, path, dir_identity, descriptor_identity, result):
        with self._mutation_transaction():
            row = self.db.execute('SELECT * FROM project_intents WHERE operation_id=?', (operation,)).fetchone()
            if row is None or row['response'] is not None or (row['dir_device'], row['dir_inode']) != dir_identity:
                raise ApplicationError('Project identity changed before binding', 409)
            self.db.execute('INSERT INTO project_bindings VALUES (?,?,?,?,?,?,?,?)',
                            (row['project_id'], *root, path, *dir_identity, *descriptor_identity))
            self.db.execute('UPDATE project_intents SET response=? WHERE operation_id=?', (encoded(result), operation))

    def project_binding(self, root, path):
        with self.mutex:
            row = self.db.execute('SELECT * FROM project_bindings WHERE root_device=? AND root_inode=? AND path=?',
                                  (*root, path)).fetchone()
            return dict(row) if row else None


    @contextmanager
    def _mutation_transaction(self):
        try:
            with self.transaction(): yield
        except sqlite3.Error as exc:
            raise ApplicationError('Application storage transaction failed', 503) from exc

    def bind_physical_notebook(self, notebook, location):
        """Idempotent catalog association only after verified physical binding."""
        with self._mutation_transaction():
            self._notebook_scope(notebook)
            project = location.get('project_id')
            if project:
                if self.db.execute('SELECT 1 FROM records WHERE id=?', (project,)).fetchone() is None:
                    now = time.time()
                    data = {'title': location['project_path'].split('/')[-1], 'description': '', 'status': 'active', 'file_references': []}
                    self.db.execute('INSERT INTO records(id,kind,revision,created_at,updated_at,data) VALUES (?,?,1,?,?,?)',
                                    (project, 'project', now, now, encoded(data)))
                self._project(project)
                current = self.db.execute('SELECT * FROM memberships WHERE notebook_id=?', (notebook,)).fetchone()
                if current and (current['project_id'] != project or not current['associated']):
                    raise ApplicationError('Physical notebook project association changed; review required', 409)
                if not current:
                    self.db.execute('INSERT INTO memberships VALUES (?,?,1)', (notebook, project))
        return self.ensure_notebook_conversation(notebook)

    def project_create(self, body):
        def work():
            if not body.title.strip(): raise ApplicationError('Title cannot be blank')
            return self._insert('project', None, None, {'title': body.title, 'description': body.description,
                'status': 'active', 'file_references': []})
        return self._mutate(body.operation_id, 'project_create', body.model_dump(), work)

    def project_update(self, project, body):
        def work():
            row = self._row(project, 'project')
            if json.loads(row['data'])['status'] == 'deleted': raise ApplicationError('Deleted project is historical', 409)
            if not body.title.strip(): raise ApplicationError('Title cannot be blank')
            data = json.loads(row['data']); data.update(title=body.title, description=body.description, status=body.status)
            return self._replace(row, data, body.revision)
        return self._mutate(body.operation_id, 'project_update/' + project, body.model_dump(), work)

    def membership(self, project, body):
        def work():
            location = getattr(self.notebooks, 'location', lambda _: None)(body.notebook_id)
            if location and (not body.associated or location.get('project_id') != project):
                raise ApplicationError('Physical notebook membership follows its verified directory; moving is not supported', 409)
            # Explicit release remains possible for historical projects; adding
            # membership still requires an active project.
            row = self._project(project, writable=body.associated)
            if row['revision'] != body.revision: raise ApplicationError('Stale application revision', 409)
            if body.associated: self.notebooks.open(body.notebook_id)
            current = self.db.execute('SELECT * FROM memberships WHERE notebook_id=?', (body.notebook_id,)).fetchone()
            if current and current['associated'] and current['project_id'] != project:
                raise ApplicationError('Notebook already belongs to another project', 409)
            if not body.associated and (not current or current['project_id'] != project):
                raise ApplicationError('Notebook is not a project member', 409)
            self.db.execute('INSERT INTO memberships VALUES (?,?,?) ON CONFLICT(notebook_id) DO UPDATE '
                'SET project_id=excluded.project_id, associated=excluded.associated', (body.notebook_id, project, body.associated))
            return self._replace(row, json.loads(row['data']), body.revision)
        return self._mutate(body.operation_id, 'membership/' + project, body.model_dump(), work)

    def project_delete(self, project, body):
        def work():
            if self.db.execute('SELECT 1 FROM project_bindings WHERE project_id=?', (project,)).fetchone():
                raise ApplicationError('Physical project deletion is not supported', 409)
            row = self._row(project, 'project')
            data = json.loads(row['data'])
            if data['status'] == 'deleted': raise ApplicationError('Project is already deleted', 409)
            data['status'] = 'deleted'
            return self._replace(row, data, body.revision)
        return self._mutate(body.operation_id, 'project_delete/' + project, body.model_dump(), work)

    def _links(self, project, notebook, links, tasks=None):
        linked = links.get('notebook_id') or notebook
        if notebook and links.get('notebook_id') and notebook != links['notebook_id']:
            raise ApplicationError('Notebook reference differs from scope')
        if linked:
            if project is None: self._notebook_scope(linked)
            else: self._scope(project, linked)
        if links.get('task_id') and tasks is None:
            task_project = project
            if task_project is None and linked:
                membership = self.db.execute('SELECT project_id FROM memberships WHERE notebook_id=? AND associated=1',
                                             (linked,)).fetchone()
                task_project = membership['project_id'] if membership else None
                if task_project: self._scope(task_project, linked)
            if not task_project or not self._task_exists(task_project, links['task_id']):
                raise ApplicationError('Unknown project task')
        if links.get('message_id'):
            message = self._row(links['message_id'], 'message')
            if not self._message_in_scope(message, project, linked):
                raise ApplicationError('Message belongs to another conversation scope')
        if links.get('cell_id'):
            if not linked: raise ApplicationError('Cell reference requires notebook')
            self.notebooks.cell(self.notebooks.open(linked), links['cell_id'])
        if links.get('document_revision'):
            if not linked or links['document_revision'] > self.notebooks.open(linked)['metadata']['europa']['revision']:
                raise ApplicationError('Invalid document revision')
        if links.get('execution_receipt_id'):
            if not linked: raise ApplicationError('Receipt reference requires notebook')
            receipt = self.notebooks.execution(linked, links['execution_receipt_id'])
            if links.get('cell_id') and receipt['cell_id'] != links['cell_id']:
                raise ApplicationError('Receipt belongs to another cell')
        return linked

    def _message_in_scope(self, message, project, notebook):
        if message['project_id'] == project and (notebook is None or message['notebook_id'] == notebook):
            return project is not None or notebook is not None
        return (project is not None and notebook is not None and message['project_id'] is None
                and message['notebook_id'] == notebook and self.db.execute(
                    'SELECT 1 FROM canonical_notebook_conversations WHERE notebook_id=? AND conversation_id=?',
                    (notebook, message['parent_id'])).fetchone() is not None)

    def _task_exists(self, project, identity):
        return any(identity == t['id'] for row in self.db.execute("SELECT versions.data FROM versions JOIN records ON records.id=roadmap_id WHERE records.project_id=?", (project,))
                   for t in json.loads(row[0])['tasks'])

    def conversation_create(self, body):
        def work():
            if body.notebook_id is not None:
                raise ApplicationError('Notebook conversations use the canonical notebook endpoint', 409)
            self._scope(body.project_id, body.notebook_id)
            return self._insert('conversation', body.project_id, body.notebook_id, {'title': body.title})
        return self._mutate(body.operation_id, 'conversation_create', body.model_dump(), work)

    def message_append(self, conversation, body, *, author_role='user'):
        # Only trusted Python callers can select non-user authorship. REST has no role field.
        if author_role not in {'user', 'coordinator', 'coding_agent', 'system/tool'}:
            raise ApplicationError('Invalid server author role')
        def work():
            row = self._row(conversation, 'conversation')
            if row['project_id'] is not None and row['notebook_id'] is not None:
                raise ApplicationError('Historical notebook conversation is read-only', 409)
            if row['project_id'] is None:
                self._notebook_scope(row['notebook_id'])
                canonical = self.db.execute('SELECT conversation_id FROM canonical_notebook_conversations WHERE notebook_id=?',
                                            (row['notebook_id'],)).fetchone()
                if canonical is None or canonical['conversation_id'] != conversation:
                    raise ApplicationError('Historical notebook conversation is read-only', 409)
                self._adopt_unplaced(conversation, row['notebook_id'])
            else: self._scope(row['project_id'], row['notebook_id'])
            notebook = self._links(row['project_id'], row['notebook_id'], body.model_dump())
            if row['project_id'] is None and body.message_id:
                referenced = self._row(body.message_id, 'message')
                if referenced['parent_id'] != conversation:
                    raise ApplicationError('Message reference belongs to another conversation')
            if body.parent_id:
                parent = self._row(body.parent_id, 'message')
                if parent['parent_id'] != conversation: raise ApplicationError('Reply belongs to another conversation')
            data = body.model_dump(exclude={'operation_id'})
            data.update(author_role=author_role, conversation_id=conversation)
            if row['project_id'] is None:
                cells = self.notebooks.open(row['notebook_id'])['cells']
                data['placement_after_cell_id'] = cells[-1]['id'] if cells else None
            return self._insert('message', row['project_id'], notebook, data, conversation)
        return self._mutate(body.operation_id, 'message_append/' + conversation + '/' + author_role, body.model_dump(), work)

    def _validate_plan(self, project, notebook, tasks, roadmap=None):
        by_id = {t['id']: t for t in tasks}
        previous = {} if roadmap is None else {t['id']: t for t in json.loads(self._row(roadmap, 'roadmap')['data'])['tasks']}
        if len(by_id) != len(tasks): raise ApplicationError('Duplicate task IDs')
        for other in self.db.execute('SELECT records.id,versions.data FROM versions JOIN records ON records.id=roadmap_id '
                                     'WHERE records.project_id=?', (project,)):
            if other['id'] != roadmap and set(by_id) & {t['id'] for t in json.loads(other['data'])['tasks']}:
                raise ApplicationError('Task IDs must be unique within the project')
        visiting, visited = set(), set()
        def visit(identity):
            if identity in visiting: raise ApplicationError('Task dependency/parent cycle')
            if identity in visited: return
            if identity not in by_id: raise ApplicationError('Unknown task dependency/parent')
            visiting.add(identity)
            task = by_id[identity]
            for target in task['dependencies'] + ([task['parent_id']] if task['parent_id'] else []): visit(target)
            visiting.remove(identity); visited.add(identity)
        for task in tasks:
            Task.model_validate(task)
            visit(task['id'])
            # Existing references remain intelligible after cell deletion or notebook
            # disassociation. New/changed references require current scoped validation.
            if task['id'] in previous and previous[task['id']]['links'] == task['links']:
                linked = task['links'].get('notebook_id') or notebook
            else:
                linked = self._links(project, notebook, task['links'], tasks=by_id)
            if task['links'].get('task_id') and task['links']['task_id'] not in by_id:
                raise ApplicationError('Unknown linked task')
            if task['status'] == 'completed':
                if any(by_id[d]['status'] != 'completed' for d in task['dependencies']):
                    raise ApplicationError('Dependencies must be completed first')
                if any(c['status'] not in {'completed', 'cancelled'} for c in tasks if c['parent_id'] == task['id']):
                    raise ApplicationError('Subtasks must finish before parent acceptance')
                for evidence in task['evidence']:
                    if evidence.get('message_id'):
                        message = self._row(evidence['message_id'], 'message')
                        if not self._message_in_scope(message, project, linked):
                            raise ApplicationError('Evidence belongs to another project or notebook')
                    if evidence.get('execution_receipt_id'):
                        if not linked: raise ApplicationError('Execution evidence requires notebook')
                        receipt = self.notebooks.execution(linked, evidence['execution_receipt_id'])
                        if receipt['state'] != 'applied' or receipt.get('result', {}).get('status') != 'ok':
                            raise ApplicationError('Execution evidence must be applied and successful')
                        if task['links'].get('cell_id') and receipt['cell_id'] != task['links']['cell_id']:
                            raise ApplicationError('Execution evidence belongs to another cell')
            # Owner acceptance is an explicit attestation, not an agent success assertion.
            task['accepted_by'] = 'user' if task['accepted'] else None

    def _snapshot(self, record):
        self.db.execute('INSERT INTO versions VALUES (?,?,?,?)',
            (record['id'], record['revision'], record['updated_at'], self._row(record['id'])['data']))

    def roadmap_create(self, body):
        def work():
            self._scope(body.project_id, body.notebook_id)
            data = {'objective': body.objective, 'tasks': [t.model_dump() for t in body.tasks]}
            self._validate_plan(body.project_id, body.notebook_id, data['tasks'])
            record = self._insert('roadmap', body.project_id, body.notebook_id, data)
            self._snapshot(record)
            return record
        return self._mutate(body.operation_id, 'roadmap_create', body.model_dump(), work)

    def roadmap_update(self, roadmap, body, task_only=False):
        def work():
            row = self._row(roadmap, 'roadmap'); self._scope(row['project_id'], row['notebook_id'])
            data = json.loads(row['data'])
            if task_only:
                match = next((i for i,t in enumerate(data['tasks']) if t['id'] == body.task.id), None)
                if match is None: raise ApplicationError('Task not found', 404)
                data['tasks'][match] = body.task.model_dump()
            else: data = {'objective': body.objective, 'tasks': [t.model_dump() for t in body.tasks]}
            # Presentation fields are recomputed; snapshots hold the underlying plan only.
            for t in data['tasks']: t.pop('accepted_by', None)
            self._validate_plan(row['project_id'], row['notebook_id'], data['tasks'], roadmap)
            result = self._replace(row, data, body.revision)
            self._snapshot(result)
            return result
        return self._mutate(body.operation_id, ('task_update/' if task_only else 'roadmap_update/') + roadmap, body.model_dump(), work)

    def comment_create(self, notebook, body):
        def work():
            if notebook != body.notebook_id: raise ApplicationError('Comment notebook must match path')
            thread = None
            if body.parent_id:
                # Replies inherit the historical anchor, even when its cell/document
                # has since disappeared. They never silently retarget edited source.
                self._project(body.project_id)
                parent = self._row(body.parent_id, 'comment'); anchor = json.loads(parent['data'])
                if parent['project_id'] != body.project_id or parent['notebook_id'] != notebook or anchor['cell_id'] != body.cell_id:
                    raise ApplicationError('Reply belongs to another comment scope')
                thread = anchor['thread_id']
                fingerprint, revision, code_range = anchor['source_fingerprint'], anchor['document_revision'], anchor['code_range']
                if ((body.document_revision is not None and body.document_revision != revision)
                        or (body.source_fingerprint is not None and body.source_fingerprint != fingerprint)
                        or (body.code_range is not None and body.code_range.model_dump() != code_range)):
                    raise ApplicationError('Reply must retain the original thread anchor', 409)
            else:
                self._scope(body.project_id, notebook)
                doc = self.notebooks.open(notebook); cell = self.notebooks.cell(doc, body.cell_id)
                revision = doc['metadata']['europa']['revision']
                if body.document_revision and body.document_revision != revision:
                    raise ApplicationError('Comment anchor revision is stale', 409)
                fingerprint = source_hash(cell)
                if body.source_fingerprint and body.source_fingerprint != fingerprint:
                    raise ApplicationError('Comment source fingerprint is stale', 409)
                if body.code_range and not 0 <= body.code_range.start < body.code_range.end <= len(source(cell)):
                    raise ApplicationError('Invalid source character range')
                code_range = body.code_range.model_dump() if body.code_range else None
            data = body.model_dump(exclude={'operation_id'})
            data.update(author_role='user', resolved=False, source_fingerprint=fingerprint,
                        document_revision=revision, code_range=code_range, thread_id=thread)
            # Reserve ID before insertion so thread identity is immutable.
            result = self._insert('comment', body.project_id, notebook, data, body.parent_id)
            if thread is None:
                data['thread_id'] = result['id']
                self.db.execute('UPDATE records SET data=? WHERE id=?', (encoded(data), result['id']))
                result = self._view(self._row(result['id']))
            return result
        return self._mutate(body.operation_id, 'comment_create/' + notebook, body.model_dump(), work)

    def resolve(self, comment, body):
        def work():
            row = self._row(comment, 'comment'); self._project(row['project_id'])
            data = json.loads(row['data']); data['resolved'] = body.resolved
            if data['thread_id'] != comment: raise ApplicationError('Resolve the root thread')
            return self._replace(row, data, body.revision)
        return self._mutate(body.operation_id, 'resolve/' + comment, body.model_dump(), work)

    def activity_append(self, project, body, *, actor='user'):
        if actor not in {'user', 'coordinator', 'coding_agent', 'system/tool'}:
            raise ApplicationError('Invalid server actor')
        if actor == 'user' and body.event_type != 'user_intervention':
            raise ApplicationError('This event type requires server-controlled authorship', 403)
        def work():
            if project != body.project_id: raise ApplicationError('Activity project must match path')
            self._scope(project, body.notebook_id)
            self._links(project, body.notebook_id, body.model_dump())
            summary = clean_summary(body.summary)
            if not summary: raise ApplicationError('Activity summary cannot be blank')
            data = body.model_dump(exclude={'operation_id'}); data.update(actor=actor, summary=summary)
            return self._insert('activity', project, body.notebook_id, data)
        return self._mutate(body.operation_id, 'activity_append/' + project + '/' + actor, body.model_dump(), work)

    @staticmethod
    def _progress(tasks):
        def measure(items):
            eligible = [t for t in items if t['status'] != 'cancelled']
            total = sum(t['weight'] for t in eligible)
            return round(100 * sum(t['weight'] for t in eligible if t['status'] == 'completed') / total, 2) if total else 0.0
        overall = measure(tasks)
        for task in tasks:
            descendants = {task['id']}
            for _ in tasks:
                descendants.update(t['id'] for t in tasks if t['parent_id'] in descendants)
            task['progress'] = measure([t for t in tasks if t['id'] in descendants])
            task['display'] = {'compact': task['status'] != 'in_progress', 'checkmark': task['status'] == 'completed',
                               'show_progress': task['status'] == 'in_progress'}
        return overall

    def _view(self, row):
        result = self._record(row); data = result['data']
        if row['kind'] == 'message' and 'placement_after_cell_id' not in data:
            placement = self.db.execute('SELECT after_cell_id FROM canonical_message_placements WHERE message_id=?',
                                        (row['id'],)).fetchone()
            if placement is not None: data['placement_after_cell_id'] = placement['after_cell_id']
        if row['kind'] == 'project':
            data['notebooks'] = [dict(r) for r in self.db.execute('SELECT notebook_id,present FROM memberships '
                'JOIN catalog ON catalog.id=notebook_id WHERE project_id=? AND associated=1 ORDER BY notebook_id', (row['id'],))]
        elif row['kind'] == 'roadmap':
            data['progress'] = self._progress(data['tasks'])
            data['overall_display'] = {'label': 'Overall progress', 'percentage_position': 'right'}
        elif row['kind'] == 'comment':
            try:
                cell = self.notebooks.cell(self.notebooks.open(row['notebook_id']), data['cell_id'])
                data['anchor_status'] = 'current' if source_hash(cell) == data['source_fingerprint'] else 'outdated'
            except NotebookError as exc:
                if exc.status_code != 404: raise
                data['anchor_status'] = 'orphaned'
            if data['thread_id'] and data['thread_id'] != row['id']:
                data['resolved'] = json.loads(self._row(data['thread_id'], 'comment')['data'])['resolved']
        return result

    def get(self, identity, kind):
        with self.transaction(): return self._view(self._row(identity, kind))

    def listing(self, kind, *, project=None, parent=None, notebook=None, after=0, limit=50):
        if not 1 <= limit <= 100 or after < 0: raise ApplicationError('Invalid pagination')
        with self.transaction():
            if project: self._project(project, writable=False)
            if parent:
                conversation = self._row(parent, 'conversation')
                if kind == 'message' and conversation['project_id'] is None:
                    canonical = self.db.execute('SELECT 1 FROM canonical_notebook_conversations WHERE notebook_id=? AND conversation_id=?',
                                                (conversation['notebook_id'], parent)).fetchone()
                    if canonical:
                        self._notebook_scope(conversation['notebook_id'])
                        self._adopt_unplaced(parent, conversation['notebook_id'])
            clauses, args = ['kind=?', 'sequence>?'], [kind, after]
            for name, value in [('project_id', project), ('parent_id', parent), ('notebook_id', notebook)]:
                if value is not None: clauses.append(name + '=?'); args.append(value)
            rows = self.db.execute('SELECT * FROM records WHERE ' + ' AND '.join(clauses) + ' ORDER BY sequence LIMIT ?', args + [limit+1]).fetchall()
            return {'items': [self._view(row) for row in rows[:limit]], 'next_cursor': rows[limit-1]['sequence'] if len(rows) > limit else None}

    def versions(self, roadmap, after=0, limit=50):
        if not 1 <= limit <= 100 or after < 0: raise ApplicationError('Invalid pagination')
        with self.transaction():
            self._row(roadmap, 'roadmap')
            rows = self.db.execute('SELECT * FROM versions WHERE roadmap_id=? AND revision>? ORDER BY revision LIMIT ?',
                                   (roadmap, after, limit+1)).fetchall()
            return {'items': [{'revision': r['revision'], 'created_at': r['created_at'], 'data': json.loads(r['data'])} for r in rows[:limit]],
                    'next_cursor': rows[limit-1]['revision'] if len(rows) > limit else None}
