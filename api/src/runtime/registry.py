"""Private SQLite ownership journal. Each mutation commits before runtime side effects."""
import hashlib
import json
import os
import re
import stat
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager

from runtime.admission import validate_resources
from runtime.capacity import SingleWriterGuard
from runtime.contracts import ResourceRequests, RuntimeErrorBase

PHASES = {'provisioning', 'ready', 'claimed', 'active', 'deleting', 'reconciliation', 'destroyed'}


class RuntimeRegistry:
    def __init__(self, path, scope):
        self.path = Path(path).expanduser().absolute()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # A private directory is required: WAL/SHM and lock files share this trust boundary.
        directory_status = self.path.parent.stat()
        if directory_status.st_uid != os.getuid() or directory_status.st_mode & 0o077:
            raise RuntimeErrorBase('Runtime registry directory must be owned by this user with mode 0700')
        self.guard = SingleWriterGuard(str(self.path) + '.lock')
        self.db = None
        self.guard.acquire()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                file_status = os.fstat(fd)
                if not stat.S_ISREG(file_status.st_mode) or file_status.st_uid != os.getuid() or file_status.st_mode & 0o077:
                    raise RuntimeErrorBase('Runtime registry must be a private user-owned file')
            finally:
                os.close(fd)
            self.db = sqlite3.connect(self.path, timeout=1)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            if self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeErrorBase('Runtime registry integrity check failed')
            with self.transaction():
                self.db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                existing = dict(self.db.execute('SELECT key,value FROM metadata').fetchall())
                binding = json.dumps(scope, sort_keys=True)
                if existing and (existing.get('scope') != binding or existing.get('version') not in {'1', '2'}):
                    raise RuntimeErrorBase('Registry schema or containerd scope differs; operator reconciliation required')
                if existing.get('version') == '1':
                    self.db.execute('DROP INDEX IF EXISTS live_notebook')
                    self.db.execute('ALTER TABLE allocations RENAME TO allocations_v1')
                self.db.execute('''CREATE TABLE IF NOT EXISTS allocations (
                    runtime_id TEXT PRIMARY KEY, allocation_key TEXT NOT NULL,
                    notebook_id TEXT, kind TEXT NOT NULL CHECK(kind IN ('assigned','warm')),
                    owner_id TEXT NOT NULL, provider TEXT NOT NULL CHECK(provider='containerd'),
                    profile_id TEXT, cpu REAL NOT NULL CHECK(cpu>0), memory INTEGER NOT NULL CHECK(memory>0),
                    pids INTEGER NOT NULL CHECK(pids>0), image TEXT NOT NULL, handler TEXT NOT NULL,
                    generation INTEGER NOT NULL CHECK(generation>=0), token_hash TEXT, initial_kernel_id TEXT,
                    phase TEXT NOT NULL CHECK(phase IN ('provisioning','ready','claimed','active','deleting','reconciliation','destroyed')),
                    reason TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    CHECK((kind='warm' AND notebook_id IS NULL) OR (kind='assigned' AND notebook_id IS NOT NULL)))''')
                if existing.get('version') == '1':
                    self.db.execute('''INSERT INTO allocations SELECT runtime_id, notebook_id, notebook_id, 'assigned',
                        owner_id, provider, profile_id, cpu, memory, pids, image, handler, generation, token_hash,
                        NULL, phase, reason, created_at, updated_at FROM allocations_v1''')
                    self.db.execute('DROP TABLE allocations_v1')
                self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS live_notebook ON allocations(notebook_id) WHERE phase!='destroyed'")
                if not existing:
                    for key, value in {'scope': binding, 'owner_id': uuid.uuid4().hex}.items():
                        self.db.execute('INSERT INTO metadata VALUES (?,?)', (key, value))
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('version','2')")
                self.owner_id = self.db.execute("SELECT value FROM metadata WHERE key='owner_id'").fetchone()[0]
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            self.close()
            raise

    @contextmanager
    def transaction(self):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def create(self, notebook, identity, profile, resources, config, *, warm=False):
        validate_resources(resources)
        now = time.time()
        with self.transaction():
            self.db.execute('''INSERT INTO allocations
                (runtime_id,allocation_key,notebook_id,kind,owner_id,provider,profile_id,cpu,memory,pids,
                 image,handler,generation,token_hash,initial_kernel_id,phase,reason,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identity, identity if warm else notebook, None if warm else notebook,
                 'warm' if warm else 'assigned', self.owner_id, 'containerd', profile, resources.cpu_cores,
                 resources.memory_bytes, resources.pids_limit, config.image, config.handler,
                 0, None, None, 'provisioning', None, now, now))

    @staticmethod
    def key(row):
        return ('warm', row['runtime_id']) if row['kind'] == 'warm' else row['notebook_id']

    def claim(self, identity, notebook):
        with self.transaction():
            changed = self.db.execute("UPDATE allocations SET kind='assigned',notebook_id=?,phase='claimed',updated_at=? "
                "WHERE runtime_id=? AND kind='warm' AND phase='ready'", (notebook, time.time(), identity)).rowcount
            if changed != 1:
                raise RuntimeErrorBase('Warm allocation is no longer claimable')

    def set_initial_kernel(self, identity, kernel_id):
        with self.transaction():
            row = self.get(identity)
            if row['kind'] != 'assigned' or row['phase'] != 'claimed':
                raise RuntimeErrorBase('Kernel bootstrap requires a durably claimed notebook')
            self.db.execute('UPDATE allocations SET initial_kernel_id=? WHERE runtime_id=?', (kernel_id, identity))

    def consume_initial_kernel(self, identity, kernel_id):
        with self.transaction():
            changed = self.db.execute("UPDATE allocations SET initial_kernel_id=NULL WHERE runtime_id=? AND initial_kernel_id=? AND phase IN ('active','claimed','deleting')",
                                      (identity, kernel_id)).rowcount
            if changed != 1:
                raise RuntimeErrorBase('Initial kernel ownership changed')

    def transition(self, identity, phase, reason=None):
        if phase not in PHASES:
            raise ValueError('Invalid registry lifecycle state')
        with self.transaction():
            row = self.get(identity)
            if row is None or (row['phase'] == 'destroyed' and phase != 'destroyed'):
                raise RuntimeErrorBase('Invalid durable lifecycle transition')
            if phase in {'active', 'ready'} and (row['generation'] < 1 or not row['token_hash'] or row['phase'] == 'deleting'):
                raise RuntimeErrorBase('Incomplete runtime cannot become active')
            if phase == 'ready' and row['kind'] != 'warm':
                raise RuntimeErrorBase('Assigned runtimes cannot return to the warm pool')
            if phase == 'active' and row['kind'] != 'assigned':
                raise RuntimeErrorBase('Unassigned runtimes cannot become active notebooks')
            self.db.execute('UPDATE allocations SET phase=?,reason=?,updated_at=? WHERE runtime_id=?',
                            (phase, reason, time.time(), identity))

    def prepare_start(self, identity, generation, token):
        with self.transaction():
            row = self.get(identity)
            if row is None or row['phase'] not in {'provisioning', 'active'} or generation != row['generation'] + 1:
                raise RuntimeErrorBase('Invalid durable runtime generation')
            self.db.execute("UPDATE allocations SET generation=?,token_hash=?,initial_kernel_id=NULL,phase='provisioning',updated_at=? WHERE runtime_id=?",
                            (generation, hashlib.sha256(token.encode()).hexdigest(), time.time(), identity))

    def new_session(self):
        epoch = uuid.uuid4().hex
        with self.transaction():
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('session_epoch', ?)", (epoch,))
        return epoch

    def get(self, identity):
        row = self.db.execute('SELECT * FROM allocations WHERE runtime_id=?', (identity,)).fetchone()
        return None if row is None else dict(row)

    def records(self):
        rows = [dict(row) for row in self.db.execute("SELECT * FROM allocations WHERE phase!='destroyed' ORDER BY created_at")]
        for row in rows:
            if ((row['kind'] == 'warm' and (row['phase'] in {'claimed', 'active'} or row['initial_kernel_id'] is not None))
                    or (row['kind'] == 'assigned' and row['phase'] == 'ready')):
                raise RuntimeErrorBase('Ambiguous warm/assigned lifecycle; operator reconciliation required')
            if (row['owner_id'] != self.owner_id or not re.fullmatch(r'europa-[a-f0-9]{32}', row['runtime_id'])
                    or (row['kind'] == 'assigned' and (not row['notebook_id'].strip() or len(row['notebook_id']) > 128))
                    or not row['allocation_key'].strip()
                    or row['provider'] != 'containerd' or row['handler'] not in {'runc', 'runsc'}):
                raise RuntimeErrorBase('Invalid durable identity; operator reconciliation required')
            self.resources(row)
        return rows

    @staticmethod
    def resources(row):
        value = ResourceRequests(row['cpu'], row['memory'], row['pids'])
        validate_resources(value)
        return value

    @staticmethod
    def enforced_resources(row):
        """OCI CPU quota uses integer microseconds; reservations retain full precision."""
        limits = vars(RuntimeRegistry.resources(row)).copy()
        limits['cpu_cores'] = int(limits['cpu_cores'] * 100000) / 100000
        return limits

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        self.guard.close()
